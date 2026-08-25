"""
OxaPay v1 Merchant API client — BOT side (Service 2).

The bot now owns ALL OxaPay network I/O (invoice creation, payment inquiry,
webhook signature verification) so the payment-provider load never touches the
main backend. Main is called only to move money, and it independently
re-verifies before crediting.

Auth model (v1): the Merchant API Key is sent in the `merchant_api_key` request
header. Base URL, paths, key, timeouts and invoice options all come from env —
nothing about the provider is hardcoded.

Network safety: the bot runs gunicorn SYNC workers (no eventlet), so `requests`
is an ordinary blocking client. These calls are therefore made ONLY from
background worker/reconciliation threads (never from the webhook request
handler, which just verifies + enqueues + returns 200) so a slow provider can
never tie up the single web worker. Every call has a hard timeout. Responses
are parsed DEFENSIVELY (v1 wraps the payload in `data`, but flat bodies and
alternate key spellings are tolerated) so a minor provider-shape change
degrades to a clean error instead of a crash. No function here raises.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ── Env-driven config (read live so a redeploy isn't needed to rotate) ────────
def _key() -> str:
    return os.environ.get("OXAPAY_MERCHANT_KEY", "")


def _base() -> str:
    return os.environ.get("OXAPAY_API_BASE", "https://api.oxapay.com/v1").rstrip("/")


def _invoice_path() -> str:
    return os.environ.get("OXAPAY_INVOICE_PATH", "/payment/invoice")


def _inquiry_path() -> str:
    return os.environ.get("OXAPAY_INQUIRY_PATH", "/payment")


def _callback_url() -> str:
    return os.environ.get("OXAPAY_CALLBACK_URL", "")


def _return_url() -> str:
    return os.environ.get("OXAPAY_RETURN_URL", "")


def _currency() -> str:
    return os.environ.get("OXAPAY_CURRENCY", "USD")


def _lifetime_min() -> int:
    try:
        return int(os.environ.get("OXAPAY_INVOICE_LIFETIME_MIN", "30"))
    except (TypeError, ValueError):
        return 30


def _fee_paid_by_payer() -> int:
    try:
        return int(os.environ.get("OXAPAY_FEE_PAID_BY_PAYER", "1"))
    except (TypeError, ValueError):
        return 1


def _underpaid_coverage() -> float:
    try:
        return float(os.environ.get("OXAPAY_UNDERPAID_COVERAGE", "0"))
    except (TypeError, ValueError):
        return 0.0


def _timeout() -> int:
    try:
        return int(os.environ.get("OXAPAY_HTTP_TIMEOUT", "20"))
    except (TypeError, ValueError):
        return 20


def is_valid_track_id(track_id) -> bool:
    return bool(track_id) and bool(_TRACK_ID_RE.match(str(track_id)))


def is_configured() -> bool:
    return bool(_key() and _base() and _callback_url())


def _headers() -> dict:
    return {
        "merchant_api_key": _key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _unwrap(body: dict) -> dict:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
def create_invoice(amount: float, order_id: str, description: str = "") -> dict:
    """Create a payment invoice. Returns:
      {ok:True, track_id, pay_url, pay_address, expired_at(epoch|None), raw}
      {ok:False, error}
    """
    if not is_configured():
        return {"ok": False, "error": "oxapay_not_configured"}

    url = f"{_base()}{_invoice_path()}"
    payload = {
        "amount": float(amount),
        "currency": _currency(),
        "lifetime": _lifetime_min(),
        "fee_paid_by_payer": _fee_paid_by_payer(),
        "under_paid_coverage": _underpaid_coverage(),
        "callback_url": _callback_url(),
        "order_id": order_id,
        "description": description or "Balance top-up",
    }
    if _return_url():
        payload["return_url"] = _return_url()

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=_timeout())
    except requests.exceptions.Timeout:
        logger.error(f"OxaPay create_invoice timeout order_id={order_id}")
        return {"ok": False, "error": "timeout"}
    except Exception as exc:
        logger.error(f"OxaPay create_invoice network error order_id={order_id}: {exc}")
        return {"ok": False, "error": "network_error"}

    try:
        body = resp.json()
    except Exception:
        return {"ok": False, "error": f"bad_response_{resp.status_code}"}

    if resp.status_code not in (200, 201):
        err = ""
        if isinstance(body, dict):
            err = (
                (body.get("error") or {}).get("message")
                if isinstance(body.get("error"), dict)
                else body.get("message")
            ) or ""
        logger.error(f"OxaPay create_invoice HTTP {resp.status_code} order_id={order_id}: {str(err)[:200]}")
        return {"ok": False, "error": f"http_{resp.status_code}"}

    data = _unwrap(body)
    track_id = data.get("track_id") or data.get("trackId")
    pay_url = data.get("payment_url") or data.get("payLink") or data.get("pay_url")
    if not track_id or not pay_url:
        logger.error(f"OxaPay create_invoice missing track_id/pay_url order_id={order_id}: {str(body)[:200]}")
        return {"ok": False, "error": "incomplete_response"}

    return {
        "ok": True,
        "track_id": str(track_id),
        "pay_url": str(pay_url),
        "pay_address": data.get("address") or data.get("pay_address"),
        "expired_at": data.get("expired_at") or data.get("expiredAt"),
        "raw": data,
    }


def get_payment(track_id: str) -> dict:
    """Authoritative payment state (the only source trusted before crediting).
    Returns {ok:True, status(lowercased), amount, currency, tx_hash, raw} | {ok:False, error}."""
    if not _key() or not _base():
        return {"ok": False, "error": "oxapay_not_configured"}
    if not is_valid_track_id(track_id):
        return {"ok": False, "error": "bad_track_id"}

    url = f"{_base()}{_inquiry_path()}/{track_id}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=_timeout())
    except requests.exceptions.Timeout:
        logger.error(f"OxaPay get_payment timeout track_id={track_id}")
        return {"ok": False, "error": "timeout"}
    except Exception as exc:
        logger.error(f"OxaPay get_payment network error track_id={track_id}: {exc}")
        return {"ok": False, "error": "network_error"}

    try:
        body = resp.json()
    except Exception:
        return {"ok": False, "error": f"bad_response_{resp.status_code}"}

    if resp.status_code != 200:
        return {"ok": False, "error": f"http_{resp.status_code}"}

    data = _unwrap(body)
    status = str(data.get("status") or "").strip().lower()
    return {
        "ok": True,
        "status": status,
        "amount": data.get("amount"),
        "currency": data.get("currency"),
        "raw": data,
    }


def verify_hmac(raw_body: bytes, signature: Optional[str]) -> bool:
    """Verify the webhook signature: HMAC-SHA512 of the EXACT raw request body,
    keyed by the Merchant API Key, constant-time compared to the `HMAC` header.
    Without the key a forged body cannot produce a valid signature."""
    key = _key()
    if not key or not signature or not raw_body:
        return False
    try:
        expected = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, str(signature).strip())
    except Exception:
        return False


# Terminal/positive statuses as reported by OxaPay v1 (lowercased).
PAID_STATUSES = frozenset({"paid", "confirmed", "complete", "completed"})
EXPIRED_STATUSES = frozenset({"expired"})
FAILED_STATUSES = frozenset({"failed", "cancelled", "canceled"})
