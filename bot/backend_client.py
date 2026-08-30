"""
HTTP client for the main backend's internal API (Service 1).

All routes use the /api/xr9k/ prefix and require the x-internal-token header.
Request timeouts are tuned per endpoint:
  - Standard calls (register, info, drop): 8 s
  - Collector calls (reload, browsers): 12 s
    (Backend holds the connection open while collecting WS responses.)

Connection errors are caught and logged; callers receive None on failure.
"""

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

_STANDARD_TIMEOUT = 8
_COLLECTOR_TIMEOUT = 12   # reload + browsers wait for WS responses

# Strip anything that looks like a token, JWT, or license key from error bodies
# before logging — defense against accidentally leaking secrets via tracebacks.
_REDACT_PATTERNS = [
    re.compile(r'eyJ[A-Za-z0-9_\-\.=]{20,}'),                     # JWT
    re.compile(r'THECLAIMERS-[A-Za-z0-9\-]{8,}'),                 # license key
    re.compile(r'\b[A-Fa-f0-9]{32,}\b'),                          # long hex secret
]


def _redact_body(text: str, limit: int = 100) -> str:
    if not text:
        return ""
    redacted = text
    for pat in _REDACT_PATTERNS:
        redacted = pat.sub("<redacted>", redacted)
    return redacted[:limit]


def _base_url() -> str:
    return os.environ.get("BACKEND_INTERNAL_URL", "").rstrip("/")


def _headers() -> dict:
    return {
        "x-internal-token": os.environ.get("INTERNAL_API_SECRET", ""),
        "Content-Type": "application/json",
    }


def _get(path: str, params: dict | None = None, timeout: int = _STANDARD_TIMEOUT) -> dict | None:
    url = f"{_base_url()}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params or {}, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"GET {path} → HTTP {resp.status_code}: {_redact_body(resp.text)}")
        return None
    except requests.exceptions.Timeout:
        logger.error(f"GET {path}: timed out after {timeout}s")
        return None
    except Exception as exc:
        logger.error(f"GET {path}: {exc}")
        return None


def _post(path: str, body: dict, timeout: int = _STANDARD_TIMEOUT) -> dict | None:
    url = f"{_base_url()}{path}"
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=timeout)
        if resp.status_code in (200, 201):
            return resp.json()
        logger.error(f"POST {path} → HTTP {resp.status_code}: {_redact_body(resp.text)}")
        return None
    except requests.exceptions.Timeout:
        logger.error(f"POST {path}: timed out after {timeout}s")
        return None
    except Exception as exc:
        logger.error(f"POST {path}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_license_info(telegram_id: int) -> dict | None:
    """
    GET /api/xr9k/lic/info?telegram_id=XXX

    Returns the license record or None if not found / error.
    Expected response: { license_key, telegram_id, active, theclaimers_count, maximum_usernames }
    """
    result = _get("/api/xr9k/lic/info", params={"telegram_id": telegram_id})
    return result


def register_license(license_key: str, telegram_id: int) -> dict | None:
    """
    POST /api/xr9k/lic/reg

    Creates a new license in the DB.
    Expected response: { license_key, telegram_id, active: false }
    """
    return _post(
        "/api/xr9k/lic/reg",
        {"license_key": license_key, "telegram_id": telegram_id},
    )


def set_language(telegram_id: int, language: str | None) -> dict | None:
    """
    POST /api/xr9k/lic/set-lang

    Set the user's preferred bot language on ALL of their license rows.
    `language`=None (or "") clears the override (back to auto-detect).
    Expected response: { ok: true, telegram_id, language, updated }.
    Returns None on error so the caller can degrade gracefully.
    """
    return _post(
        "/api/xr9k/lic/set-lang",
        {"telegram_id": telegram_id, "language": language},
    )


def drop_code(license_key: str, code: str) -> dict | None:
    """
    POST /api/xr9k/lic/drop

    Emits 'fromTele' with task=drop to the license room on /_tmc.
    Expected response: { ok: true, connected_clients: N }
    Returns None if backend unreachable or no active license.
    """
    return _post(
        "/api/xr9k/lic/drop",
        {"license_key": license_key, "code": code},
    )


def get_reload_status(license_key: str) -> dict | None:
    """
    POST /api/xr9k/lic/rl

    Emits 'fromTele' with task=reload to the license room, then waits
    up to 5 s collecting 'userClaim' responses.
    Expected response:
    {
      "results": [
        { "username": "...", "reloadAvailable": bool, "timeLeft": int }
      ]
    }
    Uses _COLLECTOR_TIMEOUT since the backend holds the connection open.
    """
    return _post(
        "/api/xr9k/lic/rl",
        {"license_key": license_key},
        timeout=_COLLECTOR_TIMEOUT,
    )


def get_browsers(license_key: str) -> dict | None:
    """
    GET /api/xr9k/lic/browsers?license_key=THECLAIMERS-xxx

    Emits 'getBrowsers' to the license room, then waits up to 5 s
    collecting 'sendBrowsers' responses.
    Expected response:
    {
      "browsers": N,
      "accounts": [ { "username": "...", "tokens": N, "currency": "usdt" } ]
    }
    Uses _COLLECTOR_TIMEOUT since the backend holds the connection open.
    """
    return _get(
        "/api/xr9k/lic/browsers",
        params={"license_key": license_key},
        timeout=_COLLECTOR_TIMEOUT,
    )


def get_all_connected() -> dict | None:
    """
    GET /api/xr9k/admin/connected

    Admin overview across ALL connected licenses. The backend emits getBrowsers to
    every connected license and holds the connection for ~5 s, so use _COLLECTOR_TIMEOUT.
    Expected response:
    {
      "licenses": [
        { "license_key": "...", "connected": N,
          "userscripts": [ { "username": "...", "tokens": N, "claims24h": N|null } ] }
      ],
      "totals": { "licenses": N, "userscripts": N }
    }
    """
    return _get(
        "/api/xr9k/admin/connected",
        timeout=_COLLECTOR_TIMEOUT,
    )


def get_all_licenses() -> list:
    """
    GET /api/xr9k/lic/list

    Returns list of all license records (used to pre-load the bot cache).
    """
    result = _get("/api/xr9k/lic/list")
    if result:
        return result.get("licenses", [])
    return []


def get_live_counts() -> dict | None:
    """
    GET /api/xr9k/lic/livecount

    Returns the per-license live connection snapshot used by the admin-only
    /licenselivecount command. Includes both per-license breakdown and
    system-wide totals. Read-only — no state mutation.

    Expected response:
    {
      "licenses": [
        {
          "license_key": "THECLAIMERS-...",
          "telegram_id": 12345,
          "unique_usernames": 3,
          "total_sessions": 5,
          "max_usernames": 100,
          "usernames": [
            {"username": "alice", "sessions": 2}, ...
          ]
        }, ...
      ],
      "totals": {
        "licenses_with_active_users": 5,
        "total_unique_usernames":    12,
        "total_sessions":            18
      }
    }
    """
    return _get("/api/xr9k/lic/livecount")


def get_code_claim_count(code: str) -> dict | None:
    """
    GET /api/xr9k/code/claimcount?code=<code>

    Returns the lifetime claim count for a single code (lowercased on
    server side via the conversion worker), consumed by the admin-only
    /claimcount command.

    Expected response:
    {
      "code": "ABC123",              # echoed back in caller's case
      "code_normalised": "abc123",   # lowercase form used for lookup
      "total_claims_count": 42       # 0 if code has never been claimed
    }
    """
    if not code:
        return None
    return _get("/api/xr9k/code/claimcount", params={"code": code})


def get_claims_by_user(telegram_id: int, start_ms: int, end_ms: int) -> dict | None:
    """
    GET /api/xr9k/claims/user?telegram_id=<id>&start_ms=<S>&end_ms=<E>

    Returns the user's own claim records within the [start_ms, end_ms] window
    (epoch milliseconds, UTC). The bot computes the window in IST and passes
    the UTC millisecond bounds. Consumed by the regular-user /count command.

    Expected response:
    {
      "ok": true,
      "records": [
        {"ts": 1735742351.42, "license_key": "...", "telegram_id": 12345,
         "username": "alice", "code": "ABC", "amount_usd": 5.50,
         "currency": "USDT", "original_amount": 5.50},
        ...
      ],
      "by_username": [{"username": str, "total_usd": float, "count": int}, ...],
      "unique_users": int,
      "total_records": N,
      "total_usd": float,
      "start_ms": int,
      "end_ms": int,
      "telegram_id": int
    }
    """
    if not telegram_id or telegram_id <= 0:
        return None
    return _get(
        "/api/xr9k/claims/user",
        params={"telegram_id": int(telegram_id), "start_ms": int(start_ms), "end_ms": int(end_ms)},
    )


def get_claims_by_license(license_key: str, start_ms: int, end_ms: int) -> dict | None:
    """
    GET /api/xr9k/claims/license?key=<K>&start_ms=<S>&end_ms=<E>

    Admin-only on the bot side. Returns all claim records for the given
    license within the [start_ms, end_ms] window (epoch ms, UTC).
    """
    if not license_key:
        return None
    return _get(
        "/api/xr9k/claims/license",
        params={"key": str(license_key).strip(), "start_ms": int(start_ms), "end_ms": int(end_ms)},
    )


def _post_with_status(path: str, body: dict, timeout: int = _STANDARD_TIMEOUT):
    """POST returning (http_status:int|None, json:dict|None). Unlike _post(),
    this preserves the status code so callers can distinguish a terminal result
    from a transient (5xx / transport) failure that should be retried."""
    url = f"{_base_url()}{path}"
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = None
        if resp.status_code >= 400:
            logger.error(f"POST {path} → HTTP {resp.status_code}: {_redact_body(resp.text)}")
        return resp.status_code, data
    except requests.exceptions.Timeout:
        logger.error(f"POST {path}: timed out after {timeout}s")
        return None, None
    except Exception as exc:
        logger.error(f"POST {path}: {exc}")
        return None, None


def begin_topup(telegram_id: int, amount: float) -> dict | None:
    """
    POST /api/xr9k/topup/begin

    Main validates the amount, refuses banned/no-license users, and either
    REUSES the user's existing open invoice or ALLOCATES a fresh order for the
    bot to turn into an OxaPay invoice (provider I/O stays on the bot).

      {ok:true, reuse:true,  order_id, pay_url, amount, currency}
      {ok:true, reuse:false, order_id, amount, currency}
      {ok:false, error:"<key>"}
    Returns None only on transport failure.
    """
    if not telegram_id or telegram_id <= 0:
        return None
    return _post("/api/xr9k/topup/begin", {"telegram_id": int(telegram_id), "amount": amount})


def record_topup(telegram_id: int, order_id: str, track_id: str, pay_url: str,
                 pay_address: str = "", expires_at=None) -> dict | None:
    """
    POST /api/xr9k/topup/record

    Persists the OxaPay track_id / pay_url the bot obtained for a fresh order.
      {ok:true, order_id, track_id, expires_at} | {ok:false, error}
    """
    if not telegram_id or telegram_id <= 0:
        return None
    body = {
        "telegram_id": int(telegram_id),
        "order_id": str(order_id),
        "track_id": str(track_id),
        "pay_url": str(pay_url),
    }
    if pay_address:
        body["pay_address"] = str(pay_address)
    if expires_at is not None:
        body["expires_at"] = expires_at
    return _post("/api/xr9k/topup/record", body)


def credit_topup(order_id: str, verified: bool = False):
    """
    POST /api/xr9k/topup/credit  → (http_status:int|None, body:dict|None)

    Asks main to re-verify with OxaPay and credit idempotently. `verified=True`
    means the BOT already confirmed the invoice is PAID via its own OxaPay
    inquiry — main uses that as a fallback only when its own OxaPay call fails on
    a transport error. The HTTP status drives retry (5xx/None = transient):
      200 → handled (credited / already_credited / not_paid_yet / expired / …)
      5xx / None → transient, safe to retry (crediting is idempotent).
    """
    if not order_id:
        return None, None
    return _post_with_status(
        "/api/xr9k/topup/credit",
        {"order_id": str(order_id), "verified": bool(verified)},
    )


def set_runtime_setting(maskcode: bool | None = None,
                        first_claim_delay: float | None = None,
                        every_code_same: bool | None = None) -> dict | None:
    """
    POST /api/xr9k/settings — admin runtime toggles (in-memory on the backend).

    Sends only the keys provided. Called with NO keys, it POSTs an empty body,
    which the backend answers with the current snapshot (used as a status read,
    e.g. /everycodesame with no argument). Returns
    {ok, settings:{maskcode, first_claim_delay, every_code_same}} or None on
    transport failure / non-2xx.
    """
    body: dict = {}
    if maskcode is not None:
        body["maskcode"] = bool(maskcode)
    if first_claim_delay is not None:
        body["first_claim_delay"] = float(first_claim_delay)
    if every_code_same is not None:
        body["every_code_same"] = bool(every_code_same)
    return _post("/api/xr9k/settings", body)


def get_pending(limit: int = 200, lookback_hours: int = 72) -> list:
    """
    POST /api/xr9k/topup/pending → list of open invoices for reconciliation.
      [{order_id, track_id, telegram_id, amount}, ...]
    """
    result = _post(
        "/api/xr9k/topup/pending",
        {"limit": int(limit), "lookback_hours": int(lookback_hours)},
    )
    if result and result.get("ok"):
        return result.get("pending", [])
    return []


def get_topup_status(telegram_id: int, order_id: str = "", track_id: str = "") -> dict | None:
    """
    POST /api/xr9k/topup/status

    Returns the current status of one of the user's own invoices (scoped to
    telegram_id on the backend so a user can only see their own payments):
      {ok: true, status, credited, amount, currency, pay_url, order_id, track_id, expires_at}
      {ok: false, error}
    """
    if not telegram_id or telegram_id <= 0:
        return None
    body = {"telegram_id": int(telegram_id)}
    if order_id:
        body["order_id"] = str(order_id)
    if track_id:
        body["track_id"] = str(track_id)
    return _post("/api/xr9k/topup/status", body)


def get_claims_by_username(username: str, start_ms: int, end_ms: int) -> dict | None:
    """
    GET /api/xr9k/claims/username?name=<U>&start_ms=<S>&end_ms=<E>

    Admin-only on the bot side. Returns all claim records for the given
    Stake username within the [start_ms, end_ms] window (epoch ms, UTC).
    """
    if not username:
        return None
    return _get(
        "/api/xr9k/claims/username",
        params={"name": str(username).strip(), "start_ms": int(start_ms), "end_ms": int(end_ms)},
    )


# ---------------------------------------------------------------------------
# Admin remote claimer management (the /api menu). All scoped by telegram_id;
# the bot enforces the real _is_admin check before calling these.
#
# The set_/remove_ POSTs push a task to the claimer and block server-side on
# collect_claimer_result (~6s, incl. the userscript's Stake token validation).
# So they need a wider client timeout than the 8s default, or a slow Stake
# round-trip would false-fail at the bot while the change actually applied.
# ---------------------------------------------------------------------------
_CLAIMER_PUSH_TIMEOUT = 12


def admin_list_claimers(telegram_id: int) -> dict | None:
    """GET /api/xr9k/admin/claimers?telegram_id=… → {'claimers': [...]}"""
    return _get("/api/xr9k/admin/claimers", params={"telegram_id": telegram_id})


def admin_claimer_detail(telegram_id: int, claimer_id: str) -> dict | None:
    """GET /api/xr9k/admin/claimer/detail → {'ok': True, 'claimer': {...}}"""
    return _get(
        "/api/xr9k/admin/claimer/detail",
        params={"telegram_id": telegram_id, "claimer_id": claimer_id},
    )


def admin_set_api(telegram_id: int, claimer_id: str, token: str) -> dict | None:
    """POST /api/xr9k/admin/claimer/set_api → push + validate result."""
    return _post(
        "/api/xr9k/admin/claimer/set_api",
        {"telegram_id": telegram_id, "claimer_id": claimer_id, "token": token},
        timeout=_CLAIMER_PUSH_TIMEOUT,
    )


def admin_remove_api(telegram_id: int, claimer_id: str) -> dict | None:
    return _post(
        "/api/xr9k/admin/claimer/remove_api",
        {"telegram_id": telegram_id, "claimer_id": claimer_id},
        timeout=_CLAIMER_PUSH_TIMEOUT,
    )


def admin_set_currency(telegram_id: int, claimer_id: str, currency: str) -> dict | None:
    return _post(
        "/api/xr9k/admin/claimer/set_currency",
        {"telegram_id": telegram_id, "claimer_id": claimer_id, "currency": currency},
        timeout=_CLAIMER_PUSH_TIMEOUT,
    )


def admin_set_filters(telegram_id: int, claimer_id: str, filters: dict) -> dict | None:
    """filters = true-only dict of blocked keys, e.g. {'1': True, '5': True}."""
    return _post(
        "/api/xr9k/admin/claimer/set_filters",
        {"telegram_id": telegram_id, "claimer_id": claimer_id, "filters": filters},
        timeout=_CLAIMER_PUSH_TIMEOUT,
    )


def admin_set_next_value(value=None, reset: bool = False) -> dict | None:
    """Set (`value`) or clear (`reset=True`) the global 'value for next code'
    override. Backend validates the value authoritatively."""
    body = {"reset": True} if reset else {"value": value}
    return _post("/api/xr9k/admin/next_value", body)
