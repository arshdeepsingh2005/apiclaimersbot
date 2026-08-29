"""
HTTP client for the API-Claimer backend's customer API (/api/cust/*).

All routes require x-internal-token = INTERNAL_API_SECRET and point at
BACKEND_INTERNAL_URL (the apiclaimers-backend). The buyer's telegram_id is
server-derived by THIS bot from the validated Mini App JWT and forwarded over
this trusted channel — the browser never asserts an identity.

Reuses backend_client's _get/_post (same base URL, headers, redaction, timeouts).
"""
import logging

from bot.backend_client import _get, _post

logger = logging.getLogger(__name__)

# The backend holds the request open while it asks a userscript to verify the key
# (backend VERIFY_TIMEOUT_S ≈ 8s). This HTTP timeout must be a SAFETY MARGIN above
# that — not 8+1 — because after the backend's worker-wait there is still response
# serialization + the return network hop. Too-tight (== 8s) makes the bot time out
# just as the backend answers → a spurious "Verification failed". Mirrors the
# 12s _COLLECTOR_TIMEOUT used for other "backend holds the connection open" relays.
VERIFY_HTTP_TIMEOUT = 12


def get_capacity():
    """→ {ok, total, occupied, reserved, available, plans:[...]} or None."""
    return _get("/api/cust/capacity")


def verify_token(telegram_id: int, token: str):
    """→ {ok, valid, username?|reason?} — check a key without saving it."""
    return _post("/api/cust/verify-token", {"telegram_id": int(telegram_id), "token": token},
                 timeout=VERIFY_HTTP_TIMEOUT)


def get_slots(telegram_id: int):
    return _get("/api/cust/slots", params={"telegram_id": int(telegram_id)})


def set_slot_config(telegram_id: int, slot_id: int, config: dict):
    body = dict(config or {})
    body["telegram_id"] = int(telegram_id)
    return _post(f"/api/cust/slots/{int(slot_id)}/config", body)


def get_stats(telegram_id: int, window: str = "24h", ctype: str = "all"):
    return _get("/api/cust/stats",
                params={"telegram_id": int(telegram_id), "window": window, "type": ctype})


def drop(telegram_id: int, code: str, coupon_type: str = "drop"):
    return _post("/api/cust/drop", {"telegram_id": int(telegram_id),
                                    "code": code, "couponType": coupon_type})


def order_begin(telegram_id: int, plan_code: str, token: str, config: dict):
    return _post("/api/cust/order/begin", {"telegram_id": int(telegram_id),
                                           "plan_code": plan_code, "token": token,
                                           "config": config or {}})


def order_get(order_id: str, telegram_id: int = None):
    params = {"telegram_id": int(telegram_id)} if telegram_id else None
    return _get(f"/api/cust/order/{order_id}", params=params)


def order_set_track(order_id: str, track_id: str):
    """Persist the OxaPay track_id on the order (enables missed-webhook recovery)."""
    return _post("/api/cust/order/track", {"order_id": order_id, "track_id": track_id})


# ── Multi-slot cart ──────────────────────────────────────────────────────────
def cart_begin(telegram_id: int, items: list):
    """Create a cart of pending orders (one per item). Each item = {token, plan_code,
    config}. The backend re-verifies every token + prices server-side; the timeout is
    longer because it holds the request open for the concurrent worker verifications."""
    return _post("/api/cust/order/cart-begin",
                 {"telegram_id": int(telegram_id), "items": items or []},
                 timeout=VERIFY_HTTP_TIMEOUT)


def cart_get(cart_id: str, telegram_id: int = None):
    """Server-authoritative cart summary {ok, total_usd, count, all_allocated}."""
    params = {"telegram_id": int(telegram_id)} if telegram_id else None
    return _get(f"/api/cust/order/cart/{cart_id}", params=params)


def cart_set_track(cart_id: str, track_id: str):
    """Persist the OxaPay track_id on ALL pending orders of the cart."""
    return _post("/api/cust/order/cart-track", {"cart_id": cart_id, "track_id": track_id})


def order_allocate(order_id: str, paid_amount=None, paid_currency=None,
                   track_id=None, status=None):
    return _post("/api/cust/order/allocate", {
        "order_id": order_id, "paid_amount": paid_amount,
        "paid_currency": paid_currency, "track_id": track_id, "status": status,
    })


def order_allocate_full(order_id: str, paid_amount=None, paid_currency=None,
                        track_id=None, status=None):
    """Like order_allocate but returns (http_status, body_dict) so the payment
    worker can distinguish transient (retry) from terminal (alert) outcomes —
    _post() swallows the body on non-200, which loses the backend's error code."""
    import os
    import requests
    url = os.environ.get("BACKEND_INTERNAL_URL", "").rstrip("/") + "/api/cust/order/allocate"
    headers = {"x-internal-token": os.environ.get("INTERNAL_API_SECRET", ""),
               "Content-Type": "application/json"}
    body = {"order_id": order_id, "paid_amount": paid_amount,
            "paid_currency": paid_currency, "track_id": track_id, "status": status}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        try:
            j = r.json()
        except Exception:
            j = None
        return r.status_code, j
    except Exception as exc:
        logger.error(f"order_allocate_full: {exc}")
        return None, None


def set_next_value(value):
    """Admin /valuefornextcode → set the per-code value on the backend."""
    return _post("/api/xr9k/admin/next_value", {"value": value})
