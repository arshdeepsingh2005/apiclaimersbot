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


def get_capacity():
    """→ {ok, total, occupied, reserved, available, plans:[...]} or None."""
    return _get("/api/cust/capacity")


def verify_token(telegram_id: int, token: str):
    """→ {ok, valid, username?|reason?} — check a key without saving it."""
    return _post("/api/cust/verify-token", {"telegram_id": int(telegram_id), "token": token})


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


def order_allocate(order_id: str, paid_amount=None, paid_currency=None,
                   track_id=None, status=None):
    return _post("/api/cust/order/allocate", {
        "order_id": order_id, "paid_amount": paid_amount,
        "paid_currency": paid_currency, "track_id": track_id, "status": status,
    })


def set_next_value(value):
    """Admin /valuefornextcode → set the per-code value on the backend."""
    return _post("/api/xr9k/admin/next_value", {"value": value})
