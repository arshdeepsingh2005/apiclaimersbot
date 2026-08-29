"""
Telegram Mini App HTTP API — thin adapters over ``backend_client`` behind a
strict auth layer. Mounted at ``/app/api/v1`` by ``create_app``.

Auth chain per request:
    require_session       → verifies the session JWT (identity only)
    require_active_license → live-checks the caller's license still exists
    require_admin          → live-rechecks admin status (admin routes only)

Identity comes ONLY from the verified token. Authorization state is re-checked
live on every request — a valid token never implies current authorization.
Uniform envelope + a stable error-code taxonomy; no sensitive material logged.
"""
from __future__ import annotations

import hashlib
import logging
import os
from functools import wraps

from flask import Blueprint, g, jsonify, request

from bot import admin_routing, backend_client
from bot import miniapp_auth as auth
from bot.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

miniapp_bp = Blueprint("miniapp", __name__, url_prefix="/app/api/v1")

# Dedicated limiter instance → mini-app budgets are separate from bot commands.
_rl = RateLimiter()


# ── Response envelope (always {ok, data} / {ok, error, code}) ────────────────
def ok(data=None, status: int = 200):
    body = {"ok": True, "data": data if data is not None else {}}
    # Proactive nudge: if this user is nearing a rate limit, tell the client to
    # show a "slow down" panel BEFORE they ever hit the (opaque) hard limit.
    # Envelope-only + no numbers/window → helps a genuine user without handing an
    # attacker a precise gauge.
    if getattr(g, "slow_down", False):
        body["warn"] = "slow_down"
    return jsonify(body), status


def err(code: str, message: str = "", status: int = 400):
    return jsonify({"ok": False, "error": message or code, "code": code}), status


def _busy():
    """Opaque throttle response — indistinguishable from a transient backend
    hiccup. Reveals NOTHING about the rate limit (no code, no window, no 429)."""
    return err("BACKEND_UNAVAILABLE", "Please try again in a moment.", 503)


# ── Rate limiting (string keys hashed to ints for the int-keyed limiter) ─────
_WARN_RATIO = 0.75   # flag g.slow_down once a window is >=75% full


def _rl_int(key: str) -> int:
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=7).digest(), "big")


def _gate(key, command: str) -> bool:
    """Per-user/per-key rate check that ALSO flags ``g.slow_down`` when nearing
    the cap. Returns True if allowed; on denial the caller returns ``_busy()``."""
    uid = key if isinstance(key, int) else _rl_int(str(key))
    allowed, _wait, near = _rl.check(uid, command, warn_ratio=_WARN_RATIO)
    if near:
        g.slow_down = True
    return allowed


def _client_ip() -> str:
    # Render/Cloud sets X-Forwarded-For; take the first hop.
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def _bearer() -> str:
    hdr = request.headers.get("Authorization", "")
    if hdr.startswith("Bearer "):
        return hdr[7:].strip()
    return ""


# ── Decorators ──────────────────────────────────────────────────────────────
def require_session(fn):
    """Verify the session JWT; populate g.tg_id / g.is_admin / g.jti."""
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            payload = auth.verify_session(_bearer())
        except auth.AuthError as e:
            return err(e.code, "authentication required", 401)
        g.tg_id = int(payload["tg_id"])
        g.is_admin = bool(payload.get("is_admin"))
        g.jti = payload.get("jti", "")
        return fn(*a, **kw)
    return wrapper


def require_active_license(fn):
    """
    Live-check that the caller's license still exists (JWT != authorization).
    Populates g.license (record) and g.license_key. Reads may accept this record
    as-is; write endpoints must additionally re-validate active + balance via
    ``ensure_writable()`` immediately before mutating.
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        info = backend_client.get_license_info(g.tg_id)
        if not info or not info.get("license_key"):
            return err("LICENSE_REVOKED", "no active license for this account", 403)
        g.license = info
        g.license_key = info["license_key"]
        return fn(*a, **kw)
    return wrapper


def require_admin(fn):
    """Re-check admin status live on every admin call (never trust the token alone)."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not admin_routing.is_admin_id(g.tg_id):
            return err("ADMIN_REQUIRED", "admin privileges required", 403)
        return fn(*a, **kw)
    return wrapper


def ensure_writable():
    """
    Fresh, cache-bypassing authorization re-check for state-changing endpoints:
    the license must exist AND be active. Returns (ok, error_response). Balance
    checks that require an amount are enforced by the backend on the mutation
    itself (drop/topup) plus the per-endpoint validation.
    """
    info = backend_client.get_license_info(g.tg_id)
    if not info or not info.get("license_key"):
        return False, err("LICENSE_REVOKED", "license no longer active", 403)
    if not info.get("active", False):
        return False, err("LICENSE_REVOKED", "license is not active", 403)
    g.license = info
    g.license_key = info["license_key"]
    return True, None


# ── Auth endpoints (P0) ─────────────────────────────────────────────────────
@miniapp_bp.post("/auth")
def auth_login():
    """Validate Telegram initData → issue a session token. Per-IP rate limited."""
    if not _gate(_client_ip(), "app_auth"):
        return _busy()

    body = request.get_json(force=True, silent=True) or {}
    init_data = body.get("initData") or ""
    try:
        user = auth.validate_init_data(init_data)
    except auth.AuthError as e:
        logger.info("miniapp auth failed | code=%s", e.code)  # no initData logged
        return err(e.code, "invalid Telegram session", 401)

    tg_id = int(user["id"])
    is_admin = admin_routing.is_admin_id(tg_id)
    try:
        issued = auth.issue_session(tg_id, is_admin)
    except auth.AuthError as e:
        return err(e.code, "auth not configured", 500)

    logger.info("miniapp auth ok | tg_id=%s admin=%s jti=%s", tg_id, is_admin, issued["jti"])
    return ok({"token": issued["token"], "is_admin": is_admin,
               "expires_in": issued["expires_in"]})


@miniapp_bp.post("/refresh")
@require_session
def auth_refresh():
    """JWT-to-JWT refresh (24h hard cap). Recomputes is_admin live."""
    is_admin_now = admin_routing.is_admin_id(g.tg_id)
    try:
        issued = auth.refresh_session(_bearer(), is_admin_now=is_admin_now)
    except auth.AuthError as e:
        return err(e.code, "session refresh failed", 401)
    return ok({"token": issued["token"], "is_admin": is_admin_now,
               "expires_in": issued["expires_in"]})


# ── Slot-sales endpoints ─────────────────────────────────────────────────────
# Identity = g.tg_id from the verified session JWT (server-derived). It is passed
# to the backend over the trusted internal channel; the browser never asserts it.
from bot import apiclaimer_client  # noqa: E402


def _cust_err(resp):
    """Map a None/failed apiclaimer_client response to the Mini App envelope."""
    if resp is None:
        return err("BACKEND_UNAVAILABLE", "backend unreachable", 502)
    if not resp.get("ok"):
        return err(str(resp.get("code") or "ERROR"), str(resp.get("error") or "request failed"), 400)
    return None


@miniapp_bp.get("/me")
@require_session
def me():
    return ok({"tg_id": g.tg_id, "is_admin": g.is_admin})


@miniapp_bp.get("/capacity")
@require_session
def capacity():
    data = apiclaimer_client.get_capacity()
    e = _cust_err(data)
    if e:
        return e
    return ok({k: data.get(k) for k in ("total", "occupied", "reserved", "available", "plans")})


@miniapp_bp.post("/verify-token")
@require_session
def verify_token():
    if not _gate(g.tg_id, "app_verify"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    token = (body.get("token") or "").strip()
    if not token:
        return err("INVALID_INPUT", "API key required", 400)
    data = apiclaimer_client.verify_token(g.tg_id, token)
    if data is None:
        return err("BACKEND_UNAVAILABLE", "verification service unreachable", 502)
    # {valid, username?|reason?} — no token echoed.
    return ok({"valid": bool(data.get("valid")), "username": data.get("username"),
               "reason": data.get("reason")})


@miniapp_bp.get("/slots")
@require_session
def slots():
    data = apiclaimer_client.get_slots(g.tg_id)
    e = _cust_err(data)
    if e:
        return e
    return ok({"slots": data.get("slots", [])})


@miniapp_bp.post("/slots/<int:slot_id>/config")
@require_session
def slot_config(slot_id):
    if not _gate(g.tg_id, "app_config"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    allowed = {"withdrawal_currency", "reload_currency", "value_filter",
               "auto_vault", "auto_bonus", "auto_reload", "stake_access_token"}
    cfg = {k: v for k, v in body.items() if k in allowed}
    data = apiclaimer_client.set_slot_config(g.tg_id, slot_id, cfg)
    e = _cust_err(data)
    if e:
        return e
    return ok({})


@miniapp_bp.get("/stats")
@require_session
def stats():
    window = (request.args.get("window") or "24h").lower()
    if window not in ("24h", "7d", "30d"):
        return err("INVALID_INPUT", "window must be 24h|7d|30d", 400)
    ctype = (request.args.get("type") or "all").lower()
    data = apiclaimer_client.get_stats(g.tg_id, window, ctype)
    e = _cust_err(data)
    if e:
        return e
    return ok({k: data.get(k) for k in ("window", "type", "earned",
                                        "successful_claims", "recent_codes")})


@miniapp_bp.post("/drop")
@require_session
def drop():
    if not _gate(g.tg_id, "app_drop") or not _gate(g.jti, "app_drop"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    code = (body.get("code") or "").strip()
    from bot.handlers import _validate_drop_code
    valid, _verr = _validate_drop_code(code)
    if not valid:
        return err("INVALID_CODE", "invalid code (max 64 chars, no reserved words)", 400)
    coupon = "bonus" if str(body.get("couponType") or "").lower() == "bonus" else "drop"
    data = apiclaimer_client.drop(g.tg_id, code, coupon)
    e = _cust_err(data)
    if e:
        return e
    return ok({"code": code, "slots": data.get("slots", 0),
               "delivered": data.get("delivered", 0)})


# ── Purchase: begin → pay (OxaPay) → poll status ─────────────────────────────
@miniapp_bp.post("/order/begin")
@require_session
def order_begin():
    if not _gate(g.tg_id, "app_order"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    plan_code = (body.get("plan_code") or "").strip()
    token = (body.get("token") or "").strip()
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    if not plan_code or not token:
        return err("INVALID_INPUT", "plan and API key required", 400)
    data = apiclaimer_client.order_begin(g.tg_id, plan_code, token, config)
    e = _cust_err(data)
    if e:
        return e
    return ok({"order_id": data.get("order_id"), "price_usd": data.get("price_usd"),
               "plan": data.get("plan"), "stake_username": data.get("stake_username")})


@miniapp_bp.post("/order/pay")
@require_session
def order_pay():
    if not _gate(g.tg_id, "app_order"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    if not order_id:
        return err("INVALID_INPUT", "order_id required", 400)
    # Fetch the order (ownership-checked on the backend) to read the authoritative
    # price — the browser can never set the amount.
    od = apiclaimer_client.order_get(order_id, g.tg_id)
    if od is None:
        return err("BACKEND_UNAVAILABLE", "backend unreachable", 502)
    if not od.get("ok"):
        return err("NOT_FOUND", "order not found", 404)
    if str(od.get("status")) == "allocated":
        return ok({"already": True, "status": "allocated", "slot_id": od.get("slot_id")})
    price = od.get("price_usd")
    from bot.slot_payments import create_invoice_for_order
    inv = create_invoice_for_order(order_id, price)
    if not inv.get("ok"):
        return err("BACKEND_UNAVAILABLE", "could not create invoice", 502)
    # Persist the track_id on the order so a MISSED webhook can be reconciled.
    try:
        if inv.get("track_id"):
            apiclaimer_client.order_set_track(order_id, inv.get("track_id"))
    except Exception:
        pass
    logger.info("miniapp order/pay | tg_id=%s order=%s", g.tg_id, order_id)
    return ok({"pay_url": inv.get("pay_url"), "order_id": order_id, "amount": price})


@miniapp_bp.post("/order/cart-begin")
@require_session
def order_cart_begin():
    """Create a multi-slot cart. Body: {items:[{token, plan_code, config}]}. The backend
    re-verifies every token + computes prices server-side (client prices are ignored)."""
    if not _gate(g.tg_id, "app_order"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return err("INVALID_INPUT", "items required", 400)
    # Forward only the fields the server trusts — strip any client price/total/duration.
    clean = []
    for it in items:
        if not isinstance(it, dict):
            return err("INVALID_INPUT", "bad item", 400)
        clean.append({"token": (it.get("token") or "").strip(),
                      "plan_code": (it.get("plan_code") or "").strip(),
                      "config": it.get("config") if isinstance(it.get("config"), dict) else {}})
    data = apiclaimer_client.cart_begin(g.tg_id, clean)
    e = _cust_err(data)
    if e:
        return e
    return ok({"cart_id": data.get("cart_id"), "total_usd": data.get("total_usd"),
               "items": data.get("items")})


@miniapp_bp.post("/order/cart-pay")
@require_session
def order_cart_pay():
    """Create ONE combined OxaPay invoice for a cart. The amount is the SERVER-stored
    cart total (never the browser's)."""
    if not _gate(g.tg_id, "app_order"):
        return _busy()
    body = request.get_json(force=True, silent=True) or {}
    cart_id = (body.get("cart_id") or "").strip()
    if not cart_id:
        return err("INVALID_INPUT", "cart_id required", 400)
    summary = apiclaimer_client.cart_get(cart_id, g.tg_id)
    if summary is None:
        return err("BACKEND_UNAVAILABLE", "backend unreachable", 502)
    if not summary.get("ok"):
        return err("NOT_FOUND", "cart not found", 404)
    if summary.get("all_allocated"):
        return ok({"already": True, "status": "allocated"})
    total = summary.get("total_usd")
    from bot.slot_payments import create_invoice_for_cart
    inv = create_invoice_for_cart(cart_id, total)
    if not inv.get("ok"):
        return err("BACKEND_UNAVAILABLE", "could not create invoice", 502)
    try:
        if inv.get("track_id"):
            apiclaimer_client.cart_set_track(cart_id, inv.get("track_id"))
    except Exception:
        pass
    logger.info("miniapp order/cart-pay | tg_id=%s cart=%s total=%s", g.tg_id, cart_id, total)
    return ok({"pay_url": inv.get("pay_url"), "cart_id": cart_id, "amount": total})


@miniapp_bp.get("/order/cart/<cart_id>")
@require_session
def order_cart_status(cart_id):
    data = apiclaimer_client.cart_get(cart_id, g.tg_id)
    if data is None:
        return err("BACKEND_UNAVAILABLE", "backend unreachable", 502)
    if not data.get("ok"):
        return err("NOT_FOUND", "cart not found", 404)
    return ok({"total_usd": data.get("total_usd"), "count": data.get("count"),
               "allocated": data.get("allocated"), "activating": data.get("activating"),
               "all_allocated": data.get("all_allocated"), "settled": data.get("settled")})


@miniapp_bp.get("/order/<order_id>")
@require_session
def order_status(order_id):
    data = apiclaimer_client.order_get(order_id, g.tg_id)
    if data is None:
        return err("BACKEND_UNAVAILABLE", "backend unreachable", 502)
    if not data.get("ok"):
        return err("NOT_FOUND", "order not found", 404)
    return ok({"status": data.get("status"), "slot_id": data.get("slot_id"),
               "stake_username": data.get("stake_username")})


# ── P5 blueprint-wide hardening (before / after / error) ─────────────────────
_MAX_BODY_BYTES = 8 * 1024   # API bodies are tiny (one code or one amount)


@miniapp_bp.before_request
def _api_guard():
    g.slow_down = False
    # Coarse cap keyed PER-USER when a valid session is present (immune to shared
    # carrier-grade NAT, where many real users share one mobile IP); only pre-auth
    # traffic (/auth) falls back to per-IP. The fine per-user limits in the write
    # endpoints do the real fairness work. Generous so legit use never trips it.
    tok = _bearer()
    subject = None
    if tok:
        try:
            subject = "u:%d" % int(auth.verify_session(tok)["tg_id"])
        except auth.AuthError:
            subject = None
    if subject is None:
        subject = "ip:" + _client_ip()
    allowed, _wait, near = _rl.check(_rl_int(subject), "app_req", warn_ratio=_WARN_RATIO)
    if near:
        g.slow_down = True
    if not allowed:
        return _busy()
    # DoS guard: reject oversized bodies (API payloads are a few bytes).
    if (request.content_length or 0) > _MAX_BODY_BYTES:
        return err("INVALID_INPUT", "request body too large", 413)


@miniapp_bp.after_request
def _api_headers(resp):
    # JSON responses carry tokens / private data → never cache; harden sniffing.
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@miniapp_bp.errorhandler(Exception)
def _api_error(e):
    # Never leak a stack trace to the client; map to the stable envelope.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        code = e.code or 500
        tax = {400: "INVALID_INPUT", 404: "NOT_FOUND", 405: "NOT_FOUND",
               413: "INVALID_INPUT", 429: "BACKEND_UNAVAILABLE"}.get(code, "BACKEND_UNAVAILABLE")
        return err(tax, "request failed", code)
    logger.exception("miniapp unhandled error")   # stack stays server-side only
    return err("BACKEND_UNAVAILABLE", "internal error", 500)
