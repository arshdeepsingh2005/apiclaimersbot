"""
Telegram Mini App authentication — initData validation + stateless session tokens.

Security model (see the Mini App plan):
  * initData: validate Telegram's HMAC-SHA256 signature with the bot token,
    reject a stale ``auth_date`` (replay protection), and extract the trusted
    Telegram user id. Identity comes ONLY from validated initData / a verified
    session token — NEVER from a request body.
  * session token: a stdlib HS256 JWT (no external dependency) carrying
    ``{tg_id, is_admin, jti, sst, iat, exp}``. Verified by signature + expiry
    only (stateless, no DB hit). Refresh is JWT-to-JWT, bounded by a hard
    24h session cap using the original session-start (``sst``); every refresh
    recomputes ``is_admin``.

This module proves IDENTITY only. Authorization state (license active, balance,
account exists) is re-checked live by the API layer on every request — a valid
token never implies current authorization.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl

# ── Tunables ────────────────────────────────────────────────────────────────
INITDATA_MAX_AGE_SECONDS = 3600            # reject initData older than this (replay)
SESSION_TTL_SECONDS = 30 * 60              # access-token lifetime (30 min)
SESSION_HARD_CAP_SECONDS = 24 * 60 * 60    # max total session regardless of refresh


class AuthError(Exception):
    """Auth failure carrying a stable taxonomy ``code`` (see plan)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ── Config accessors (env-only; never in the client bundle) ─────────────────
def _bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _session_secret() -> str:
    return os.environ.get("MINIAPP_SESSION_SECRET", "")


# ── initData validation (Telegram WebApp spec) ──────────────────────────────
def validate_init_data(
    init_data: str,
    *,
    bot_token: str | None = None,
    max_age: int = INITDATA_MAX_AGE_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """
    Validate a Telegram WebApp ``initData`` query string.

    Returns the parsed ``user`` dict (with an int ``id``) on success; raises
    :class:`AuthError` otherwise.

    Spec: ``secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)`` and the
    expected hash is ``HMAC_SHA256(key=secret_key, msg=data_check_string)`` where
    ``data_check_string`` is every ``k=v`` pair EXCEPT ``hash``, sorted by key,
    joined with ``"\\n"``. Compared in constant time.
    """
    token = bot_token if bot_token is not None else _bot_token()
    if not token:
        raise AuthError("AUTH_INVALID", "bot token not configured")
    if not init_data:
        raise AuthError("AUTH_INVALID", "empty initData")

    # parse_qsl URL-decodes values, which is what the check-string must use.
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    provided_hash = data.pop("hash", None)
    if not provided_hash:
        raise AuthError("AUTH_INVALID", "missing hash")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, provided_hash):
        raise AuthError("AUTH_INVALID", "bad initData signature")

    # Replay protection — reject stale launches.
    try:
        auth_date = int(data.get("auth_date", "0"))
    except (TypeError, ValueError):
        raise AuthError("AUTH_INVALID", "bad auth_date")
    now_i = int(now if now is not None else time.time())
    if auth_date <= 0 or (now_i - auth_date) > max_age:
        raise AuthError("AUTH_EXPIRED", "initData too old")

    # Extract the trusted identity.
    user_raw = data.get("user")
    if not user_raw:
        raise AuthError("AUTH_INVALID", "no user in initData")
    try:
        user = json.loads(user_raw)
        user["id"] = int(user["id"])
    except (TypeError, ValueError, KeyError):
        raise AuthError("AUTH_INVALID", "bad user payload")
    return user


# ── Minimal stdlib HS256 JWT (no external dependency) ───────────────────────
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(signing_input: bytes, secret: str) -> str:
    return _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())


def issue_session(
    tg_id: int,
    is_admin: bool,
    *,
    secret: str | None = None,
    now: float | None = None,
    session_start: int | None = None,
) -> dict[str, Any]:
    """
    Mint a session token. ``session_start`` (``sst``) is preserved across refresh
    so the 24h hard cap is measured from the original login, not each refresh.
    """
    sec = secret if secret is not None else _session_secret()
    if not sec:
        raise AuthError("AUTH_INVALID", "session secret not configured")
    iat = int(now if now is not None else time.time())
    sst = int(session_start if session_start is not None else iat)
    payload = {
        "tg_id": int(tg_id),
        "is_admin": bool(is_admin),
        "jti": secrets.token_urlsafe(12),
        "sst": sst,
        "iat": iat,
        "exp": iat + SESSION_TTL_SECONDS,
    }
    header = _b64url(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = _sign(f"{header}.{body}".encode(), sec)
    return {
        "token": f"{header}.{body}.{sig}",
        "expires_in": SESSION_TTL_SECONDS,
        "jti": payload["jti"],
        "payload": payload,
    }


def verify_session(
    token: str, *, secret: str | None = None, now: float | None = None
) -> dict[str, Any]:
    """Verify signature + expiry only (stateless). Returns the payload or raises."""
    sec = secret if secret is not None else _session_secret()
    if not sec:
        raise AuthError("AUTH_INVALID", "session secret not configured")
    if not token or token.count(".") != 2:
        raise AuthError("AUTH_INVALID", "malformed token")

    header, body, sig = token.split(".")
    expected = _sign(f"{header}.{body}".encode(), sec)
    if not hmac.compare_digest(expected, sig):
        raise AuthError("AUTH_INVALID", "bad token signature")

    try:
        payload = json.loads(_b64url_decode(body))
    except Exception:
        raise AuthError("AUTH_INVALID", "bad token payload")

    now_i = int(now if now is not None else time.time())
    if int(payload.get("exp", 0)) <= now_i:
        raise AuthError("AUTH_EXPIRED", "token expired")
    return payload


def refresh_session(
    token: str,
    *,
    is_admin_now: bool,
    secret: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """
    JWT-to-JWT refresh. The current token must still verify (signature + not
    expired). Enforces the 24h hard cap from the original ``sst``. Caller passes
    the freshly recomputed ``is_admin_now`` so a demoted admin loses privileges
    on the next refresh — never trust the old token's ``is_admin`` for this.
    """
    payload = verify_session(token, secret=secret, now=now)
    now_i = int(now if now is not None else time.time())
    sst = int(payload.get("sst", payload.get("iat", now_i)))
    if (now_i - sst) > SESSION_HARD_CAP_SECONDS:
        raise AuthError("AUTH_EXPIRED", "session cap reached — reopen required")
    return issue_session(
        int(payload["tg_id"]),
        bool(is_admin_now),
        secret=secret,
        now=now_i,
        session_start=sst,
    )
