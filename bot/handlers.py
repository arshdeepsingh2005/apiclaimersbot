"""
Command and callback-query handlers for The Claimers Telegram bot.

Handler naming: handle_<command>(user_id, chat_id, ...) — pure Python,
no Flask context required. All Telegram sends go through telegram_api,
all backend calls through backend_client.

ForceReply tracking:
  When /drop is sent without a code, the bot replies with ForceReply.
  The bot's message_id is stored in _pending_drops. When the user replies,
  _process_update (in app.py) calls handle_force_reply() with that ID.

Thread safety:
  _pending_drops is protected by _pending_lock.
  LicenseCache and RateLimiter are thread-safe internally.
"""

import html
import logging
import os
import threading
import time
import uuid

from bot.backend_client import (
    admin_claimer_detail,
    admin_list_claimers,
    admin_remove_api,
    admin_set_api,
    admin_set_currency,
    admin_set_filters,
    admin_set_next_value,
    drop_code,
    get_all_connected,
    get_browsers,
    get_claims_by_license,
    get_claims_by_user,
    get_claims_by_username,
    get_code_claim_count,
    get_license_info,
    get_live_counts,
    get_reload_status,
    get_topup_status,
    register_license,
    set_language,
    set_runtime_setting,
)
from bot.payments import start_topup
from bot.helpers import format_time_left, shorten_key
from bot.i18n import SUPPORTED, display_name, get_lang, t
from bot.license_cache import LicenseEntry, license_cache
from bot.rate_limiter import rate_limiter
from bot import admin_routing
from bot.telegram_api import (
    answer_callback_query,
    delete_message,
    edit_message_text,
    send_document,
    send_message,
    send_video,
)
from bot.install_media import install_video_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword safety (spec §7) — codes containing these are rejected at bot layer
# ---------------------------------------------------------------------------
_FORBIDDEN_CODE_KEYWORDS = frozenset({
    "new_code",
    "broadcast",
    "relay_websocket_message",
    "iframe_sse_message",
    "code_received",
})

# ---------------------------------------------------------------------------
# ForceReply pending tracker
# ---------------------------------------------------------------------------
# { bot_message_id (int): { user_id, chat_id, expires_at } }
_pending_drops: dict[int, dict] = {}
_pending_lock = threading.Lock()
_DROP_TTL_SECS = 300  # 5 minutes


def _register_pending_drop(bot_message_id: int, user_id: int, chat_id: int) -> None:
    with _pending_lock:
        _evict_expired_pending()
        _pending_drops[bot_message_id] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "expires_at": time.monotonic() + _DROP_TTL_SECS,
        }


def _pop_pending_drop(bot_message_id: int) -> dict | None:
    with _pending_lock:
        entry = _pending_drops.pop(bot_message_id, None)
        if entry and entry["expires_at"] < time.monotonic():
            return None  # Expired
        return entry


def _evict_expired_pending() -> None:
    """Prune stale entries (called under lock)."""
    now = time.monotonic()
    stale = [mid for mid, v in _pending_drops.items() if v["expires_at"] < now]
    for mid in stale:
        del _pending_drops[mid]


# ForceReply pending tracker for the admin /api menu (Set API / Filters).
# { bot_message_id: { user_id, chat_id, claimer_id, action, expires_at } }
_pending_api: dict[int, dict] = {}


def _register_pending_api(bot_message_id: int, user_id: int, chat_id: int,
                          claimer_id: str, action: str) -> None:
    with _pending_lock:
        now = time.monotonic()
        for mid in [m for m, v in _pending_api.items() if v["expires_at"] < now]:
            del _pending_api[mid]
        _pending_api[bot_message_id] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "claimer_id": claimer_id,
            "action": action,
            "expires_at": now + _DROP_TTL_SECS,
        }


def _pop_pending_api(bot_message_id: int) -> dict | None:
    with _pending_lock:
        entry = _pending_api.pop(bot_message_id, None)
        if entry and entry["expires_at"] < time.monotonic():
            return None
        return entry


# ---------------------------------------------------------------------------
# Inline keyboard builders
# ---------------------------------------------------------------------------

# Public links surfaced in the UI (kept here so every keyboard stays consistent).
SUPPORT_URL = "https://t.me/adityaofficial96"
CHANNEL_URL = "https://t.me/stakeclaimercodes"
USERSCRIPT_URL = "https://storage.googleapis.com/theclaimers/theclaimers.user.js"

# The Saturday-discount promo (/balance percentage plans and /topup) now lives
# in the locale catalog under fee.notice.


def _approx_claimable(balance: float, deduction_percentage: float) -> float | None:
    """How much MORE in code value the user can claim with their balance.

    A claim of $V deducts deduction_percentage% of V, so the balance B sustains
    roughly  B / (pct/100)  in claimed value. Pure display math — it mirrors the
    backend's deduction but changes no balance and no logic. Returns None when it
    isn't meaningful (no percentage, or non-positive balance/percentage)."""
    try:
        b = float(balance)
        p = float(deduction_percentage)
    except (TypeError, ValueError):
        return None
    if b <= 0 or p <= 0:
        return None
    return b * 100.0 / p


def _miniapp_url() -> str:
    """Public https URL of the Mini App (/app/), or '' if no base URL is set."""
    base = (
        os.environ.get("MINIAPP_BASE_URL", "").rstrip("/")
        or os.environ.get("BOT_PUBLIC_URL", "").rstrip("/")
        or os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    )
    return f"{base}/app/" if base else ""


def _main_keyboard() -> dict:
    """Slot-bot start keyboard: the Mini App is the dashboard, so we surface the
    'Open Mini App' web_app button first, then language + support/channel."""
    rows = []
    _app = _miniapp_url()
    if _app:
        rows.append([{"text": t("buttons.open_app"), "web_app": {"url": _app}}])
    rows.extend([
            [
                {"text": t("buttons.choose_language"), "callback_data": "cb_language"},
            ],
            [
                {"text": t("buttons.support"), "url": SUPPORT_URL},
                {"text": t("buttons.channel"), "url": CHANNEL_URL},
            ],
    ])
    return {"inline_keyboard": rows}


# ---------------------------------------------------------------------------
# Shared message text builders
# ---------------------------------------------------------------------------

# Commands with a fixed per-command rate-limit description (see rate_limit.limits).
_RL_LIMIT_KEYS = frozenset(
    {"drop", "reload", "connected", "license", "start", "balance", "topup"}
)


def _welcome_text(user_id: int, entry: LicenseEntry) -> str:
    status_str = t("status.active") if entry.active else t("status.inactive")
    text = t(
        "welcome.body",
        user_id=user_id,
        license_key=entry.license_key,
        status=status_str,
        active_now=entry.active_now,
        maximum_usernames=entry.maximum_usernames,
    )
    if not entry.active:
        text += t("welcome.inactive_extra")
    else:
        text += t("welcome.active_extra", maximum_usernames=entry.maximum_usernames)
    text += t("welcome.footer")
    return text


def _rate_limit_text(command: str, wait_secs: int) -> str:
    key = command if command in _RL_LIMIT_KEYS else "default"
    limit_desc = t(f"rate_limit.limits.{key}")
    return t("rate_limit.body", command=command, limit_desc=limit_desc, wait_secs=wait_secs)


def _inactive_license_text() -> str:
    return t("license_status.inactive")


def _no_license_text() -> str:
    return t("license_status.no_license")


def _error_text(reason: str | None = None) -> str:
    return t(
        "errors.generic",
        reason=reason if reason is not None else t("errors.default_reason"),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_or_fetch_license(user_id: int) -> LicenseEntry | None:
    """
    Return entry from cache, or fall back to a live backend fetch.
    On backend hit, updates cache before returning.
    """
    entry = license_cache.get_by_telegram_id(user_id)
    if entry:
        return entry

    # Cache miss — live lookup
    data = get_license_info(user_id)
    if data:
        entry = license_cache.set_from_dict(user_id, data)
        return entry

    return None


def _validate_drop_code(code: str) -> tuple[bool, str]:
    """
    Returns (valid, error_message).
    Enforces keyword safety (spec §7) and length limit.
    """
    code = code.strip()
    if not code:
        return False, t("drop.invalid_empty")
    if len(code) > 64:
        return False, t("drop.invalid_too_long")
    lower = code.lower()
    for kw in _FORBIDDEN_CODE_KEYWORDS:
        if kw in lower:
            return False, t("drop.invalid_reserved", kw=kw)
    return True, ""


def _edit_or_send(chat_id: int, sent_result: dict, text: str) -> None:
    """
    Attempt to edit an earlier message; fall back to sending a new one.
    Used to replace '⏳ Checking...' messages with real results.
    """
    if sent_result.get("ok") and sent_result.get("result"):
        msg_id = sent_result["result"]["message_id"]
        res = edit_message_text(chat_id, msg_id, text, parse_mode="HTML")
        if res.get("ok"):
            return
    # Edit failed (message too old, deleted, etc.) — send fresh
    send_message(chat_id, text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

def _notify_admin_new_user(user_id: int, first_name: str, last_name: str,
                           profile_username: str, license_key: str) -> None:
    """Send an admin-only notification when a brand-new user generates a license.

    Uses the shared allow-list (admin_routing) so it stays consistent with
    _is_admin — a multi-id ADMIN_USER_ID notifies every admin instead of breaking.
    """
    admins = admin_routing.admin_ids()
    if not admins:
        return

    display_name = " ".join(p for p in (first_name, last_name) if p).strip() or "Unknown"
    safe_name = html.escape(display_name)
    safe_username = html.escape(profile_username) if profile_username else ""

    lines = [
        "<b>🆕 New user</b>",
        "",
        f"Name: <b>{safe_name}</b>",
    ]
    if safe_username:
        lines.append(f"Username: @{safe_username}")
    lines.append(f"User ID: <code>{user_id}</code>")
    lines.append(f"License: <code>{license_key}</code>")

    body = "\n".join(lines)
    for admin_id in admins:
        try:
            send_message(admin_id, body, parse_mode="HTML")
        except Exception as exc:
            logger.warning(f"admin notify failed for {admin_id}: {exc}")


def handle_start(user_id: int, chat_id: int, first_name: str,
                 last_name: str = "", profile_username: str = "") -> None:
    logger.info(f"/start  user_id={user_id}")

    allowed, wait_secs = rate_limiter.check(user_id, "start")
    if not allowed:
        send_message(chat_id, _rate_limit_text("start", wait_secs), parse_mode="HTML")
        return

    # Live free-slot capacity for the "Only X slots remaining!" line.
    slots_available = "—"
    try:
        from bot import apiclaimer_client
        cap = apiclaimer_client.get_capacity()
        if cap and cap.get("ok"):
            slots_available = cap.get("available", "—")
    except Exception:
        pass

    # Message 1 — the welcome / value pitch (ends with live slots remaining).
    send_message(
        chat_id,
        t("start.welcome", slots=slots_available),
        parse_mode="HTML",
    )
    # Message 2 — the call to action with the Mini App button.
    send_message(
        chat_id,
        t("start.open_prompt"),
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
    )


# ---------------------------------------------------------------------------
# /drop
# ---------------------------------------------------------------------------

def handle_drop(user_id: int, chat_id: int, code: str | None) -> None:
    logger.info(f"/drop  user_id={user_id}  code={code!r}")

    entry = _get_or_fetch_license(user_id)
    if entry is None:
        send_message(chat_id, _no_license_text(), parse_mode="HTML")
        return

    if not entry.active:
        send_message(chat_id, _inactive_license_text(), parse_mode="HTML")
        return

    allowed, wait_secs = rate_limiter.check(user_id, "drop")
    if not allowed:
        send_message(chat_id, _rate_limit_text("drop", wait_secs), parse_mode="HTML")
        return

    if not code:
        # ForceReply — ask user to type the code
        result = send_message(
            chat_id,
            t("drop.prompt"),
            parse_mode="HTML",
            reply_markup={"force_reply": True, "selective": True},
        )
        if result.get("ok") and result.get("result"):
            bot_msg_id = result["result"]["message_id"]
            _register_pending_drop(bot_msg_id, user_id, chat_id)
        return

    _execute_drop(user_id, chat_id, entry.license_key, code)


def _execute_drop(user_id: int, chat_id: int, license_key: str, code: str) -> None:
    """Validate the code, call the backend, and reply."""
    valid, err = _validate_drop_code(code)
    if not valid:
        send_message(chat_id, t("drop.invalid_wrap", err=err), parse_mode="HTML")
        return

    result = drop_code(license_key, code.strip())

    if result is None:
        send_message(chat_id, _error_text(t("drop.backend_unreachable")), parse_mode="HTML")
        return

    connected_count = result.get("connected_clients", 0)

    if connected_count == 0:
        send_message(chat_id, t("drop.no_claimers"), parse_mode="HTML")
        return

    send_message(
        chat_id,
        t("drop.success", code=html.escape(code.strip()), count=connected_count),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /reload
# ---------------------------------------------------------------------------

def handle_reload(user_id: int, chat_id: int) -> None:
    logger.info(f"/reload  user_id={user_id}")

    entry = _get_or_fetch_license(user_id)
    if entry is None:
        send_message(chat_id, _no_license_text(), parse_mode="HTML")
        return

    if not entry.active:
        send_message(chat_id, _inactive_license_text(), parse_mode="HTML")
        return

    allowed, wait_secs = rate_limiter.check(user_id, "reload")
    if not allowed:
        send_message(chat_id, _rate_limit_text("reload", wait_secs), parse_mode="HTML")
        return

    thinking = send_message(chat_id, t("reload.checking"), parse_mode="HTML")

    result = get_reload_status(entry.license_key)

    if result is None:
        _edit_or_send(chat_id, thinking, _error_text(t("reload.no_response_reason")))
        return

    accounts: list = result.get("results", [])
    if not accounts:
        _edit_or_send(chat_id, thinking, t("reload.no_response"))
        return

    lines = []
    for acct in accounts:
        # Escape the claimer-supplied username: it lands inside <code>…</code>
        # under parse_mode=HTML, so an unescaped '<'/'&' would make Telegram
        # reject the whole message. No-op for normal usernames.
        username = html.escape(str(acct.get("username", "unknown")))
        available = acct.get("reloadAvailable", False)
        time_left = acct.get("timeLeft", 0)
        if available:
            lines.append(t("reload.available", username=username,
                           time_left=format_time_left(time_left)))
        else:
            lines.append(t("reload.not_yet", username=username))

    _edit_or_send(chat_id, thinking, t("reload.header", lines="\n".join(lines)))


# ---------------------------------------------------------------------------
# /connected
# ---------------------------------------------------------------------------

def _format_connected_overview(data: dict) -> list[str]:
    """Build the admin /connected overview as one or more HTML messages.

    Splits into multiple messages when a single one would approach Telegram's 4096
    char limit (we flush at ~3800). One license block is kept intact per message
    where possible; a single license with a huge userscript list still splits safely."""
    licenses = data.get("licenses") or []
    totals = data.get("totals") or {}

    header = (
        f"🖥 <b>Connected overview</b>\n"
        f"Licenses: <b>{totals.get('licenses', len(licenses))}</b> · "
        f"Userscripts: <b>{totals.get('userscripts', 0)}</b>\n"
        f"━━━━━━━━━━━━━━━"
    )

    chunks: list[str] = []
    cur = header
    LIMIT = 3800

    def flush():
        nonlocal cur
        if cur.strip():
            chunks.append(cur)
        cur = ""

    for lic in licenses:
        key = html.escape(str(lic.get("license_key", "?")))
        scripts = lic.get("userscripts") or []
        connected = lic.get("connected", len(scripts))
        block_lines = [f"\n🔑 <code>{key}</code>  ·  🖥 {connected} userscript(s)"]
        if scripts:
            for u in scripts:
                uname = html.escape(str(u.get("username", "?")))
                tokens = u.get("tokens", 0)
                claims = u.get("claims24h")
                claims_txt = "" if claims is None else f"  ·  24h: {claims}"
                block_lines.append(f"• {uname} — {tokens} tokens{claims_txt}")
        else:
            block_lines.append("• (no userscript reply)")
        block = "\n".join(block_lines)

        # If adding this block would overflow, flush first. If the block itself is
        # larger than the limit, split it line-by-line so we never exceed 4096.
        if len(cur) + len(block) + 1 > LIMIT:
            flush()
        if len(block) > LIMIT:
            for line in block.split("\n"):
                if len(cur) + len(line) + 1 > LIMIT:
                    flush()
                cur = (cur + "\n" + line) if cur else line
        else:
            cur = (cur + "\n" + block) if cur else block

    flush()
    return chunks or [header + "\n\nNo userscripts connected."]


def handle_connected(user_id: int, chat_id: int) -> None:
    logger.info(f"/connected  user_id={user_id}")

    # Admin-only overview across ALL connected licenses. The dispatch already gates
    # this to admins (app.py), so we do NOT require the CALLER to own a license.
    allowed, wait_secs = rate_limiter.check(user_id, "connected")
    if not allowed:
        send_message(chat_id, _rate_limit_text("connected", wait_secs), parse_mode="HTML")
        return

    thinking = send_message(chat_id, t("connected.fetching"), parse_mode="HTML")

    result = get_all_connected()
    if result is None:
        _edit_or_send(chat_id, thinking, _error_text(t("connected.no_response_reason")))
        return

    licenses = result.get("licenses") or []
    if not licenses:
        _edit_or_send(chat_id, thinking, "🖥 <b>Connected overview</b>\n\nNo userscripts connected.")
        return

    messages = _format_connected_overview(result)
    # First message replaces the "fetching…" placeholder; the rest are sent fresh.
    _edit_or_send(chat_id, thinking, messages[0])
    for extra in messages[1:]:
        send_message(chat_id, extra, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /license
# ---------------------------------------------------------------------------

def handle_license(user_id: int, chat_id: int) -> None:
    logger.info(f"/license  user_id={user_id}")

    allowed, wait_secs = rate_limiter.check(user_id, "license")
    if not allowed:
        send_message(chat_id, _rate_limit_text("license", wait_secs), parse_mode="HTML")
        return

    entry = _get_or_fetch_license(user_id)
    if entry is None:
        send_message(chat_id, _no_license_text(), parse_mode="HTML")
        return

    # Always refresh active_now from the backend so the displayed count is live.
    try:
        fresh = get_license_info(user_id)
        if fresh:
            entry = license_cache.set_from_dict(user_id, fresh)
    except Exception:
        pass

    status_str = t("status.active") if entry.active else t("status.inactive")
    text = t("license.body", license_key=entry.license_key, status=status_str,
             active_now=entry.active_now, maximum_usernames=entry.maximum_usernames)
    if not entry.active:
        text += t("license.inactive_extra")

    send_message(chat_id, text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /balance
# ---------------------------------------------------------------------------

def handle_balance(user_id: int, chat_id: int) -> None:
    """
    Show the user their prepaid balance.

    Two license models:
      • Percentage license (deduction_percentage set) → show available_balance,
        the per-claim deduction rate, and status. Adds a recharge note when the
        balance is below 0 (license auto-paused) or a "running low" note below 0.5.
      • After-Claims license (deduction_percentage is None) → show
        "After Claims Payment" instead of a balance.

    Data is fetched live from the backend on every invocation (like /license),
    and the backend's license scanner keeps balances/percentages in sync on its
    own refresh cadence (≤30s), so the figure shown is always current.
    """
    logger.info(f"/balance  user_id={user_id}")

    allowed, wait_secs = rate_limiter.check(user_id, "balance")
    if not allowed:
        send_message(chat_id, _rate_limit_text("balance", wait_secs), parse_mode="HTML")
        return

    entry = _get_or_fetch_license(user_id)
    if entry is None:
        send_message(chat_id, _no_license_text(), parse_mode="HTML")
        return

    # Refresh live so the displayed balance is up-to-the-second.
    try:
        fresh = get_license_info(user_id)
        if fresh:
            entry = license_cache.set_from_dict(user_id, fresh)
    except Exception:
        pass

    status_str = t("status.active") if entry.active else t("status.inactive")

    if entry.deduction_percentage is None:
        # No percentage configured → "After Claims Payment" license.
        text = t("balance.after_claims", license_key=entry.license_key, status=status_str)
    else:
        bal = float(entry.available_balance or 0)
        pct = float(entry.deduction_percentage)
        text = t("balance.header", license_key=entry.license_key,
                 amount=f"{bal:,.2f}", pct=f"{pct:g}", status=status_str)
        claimable = _approx_claimable(bal, pct)
        if claimable is not None:
            text += t("balance.claimable", claimable=f"{claimable:,.0f}")
        if bal < 0:
            text += t("balance.below_zero")
        elif bal < 0.5:
            text += t("balance.running_low")
        text += "\n\n" + t("fee.notice")

    send_message(chat_id, text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /topup — OxaPay balance recharge
#
# Flow:
#   /topup (or 💳 Top-Up button) → "enter an amount $1–$50" (ForceReply-free
#   state machine, mirrors /count) → user types amount → bot validates → asks
#   the backend to create an OxaPay invoice → shows the pay link + a
#   "Check status" button. The backend owns persistence + crediting; the bot
#   only relays. Amount bounds come from env (and are re-checked server-side).
# ---------------------------------------------------------------------------

import os as _os  # local alias; avoids touching the module's other os usage

_TOPUP_MIN = float(_os.environ.get("TOPUP_MIN_USD", "1"))
_TOPUP_MAX = float(_os.environ.get("TOPUP_MAX_USD", "100"))

_TOPUP_STEP_AMOUNT = "topup_amount"
_TOPUP_STATE_TTL_S = 5 * 60

_topup_state: dict[int, dict] = {}
_topup_state_lock = threading.Lock()


def _topup_state_set(user_id: int, step: str) -> None:
    with _topup_state_lock:
        _topup_state_evict_expired_locked()
        _topup_state[user_id] = {"step": step, "ts": time.time()}


def _topup_state_get(user_id: int) -> dict | None:
    with _topup_state_lock:
        _topup_state_evict_expired_locked()
        return _topup_state.get(user_id)


def _topup_state_clear(user_id: int) -> None:
    with _topup_state_lock:
        _topup_state.pop(user_id, None)


def _topup_state_evict_expired_locked() -> None:
    cutoff = time.time() - _TOPUP_STATE_TTL_S
    stale = [uid for uid, st in _topup_state.items() if st["ts"] < cutoff]
    for uid in stale:
        _topup_state.pop(uid, None)


def _kb_topup_cancel() -> dict:
    return {"inline_keyboard": [[{"text": t("buttons.cancel"), "callback_data": "topup_cancel"}]]}


def _kb_topup_status(order_id: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": t("buttons.check_status"), "callback_data": f"topup_status:{order_id}"}],
        ]
    }


def _parse_topup_amount(text: str):
    """Returns (value, None) on success or (None, error_html) on failure."""
    raw = (text or "").strip().lstrip("$").strip().replace(",", "")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None, t("topup.parse_not_number")
    if val != val or val in (float("inf"), float("-inf")):
        return None, t("topup.parse_invalid")
    val = round(val, 2)
    if val < _TOPUP_MIN:
        return None, t("topup.parse_too_small", min=f"{_TOPUP_MIN:g}")
    if val > _TOPUP_MAX:
        return None, t("topup.parse_too_large", max=f"{_TOPUP_MAX:g}")
    return val, None


# Known start_topup error codes → localized under topup.errors.<code>; anything
# else maps to topup.errors.default.
_TOPUP_ERROR_CODES = frozenset({
    "amount_not_number", "amount_too_small", "amount_too_large", "no_license",
    "license_banned", "payments_unavailable", "invoice_failed", "db_error",
    "backend_unreachable",
})


def handle_topup(user_id: int, chat_id: int) -> None:
    logger.info(f"/topup  user_id={user_id}")

    allowed, wait_secs = rate_limiter.check(user_id, "topup")
    if not allowed:
        send_message(chat_id, _rate_limit_text("topup", wait_secs), parse_mode="HTML")
        return

    entry = _get_or_fetch_license(user_id)
    if entry is None:
        send_message(chat_id, _no_license_text(), parse_mode="HTML")
        return

    # Promo only applies to percentage-plan licenses (after-claims pay no %).
    fee_line = ("\n\n" + t("fee.notice")) if entry.deduction_percentage is not None else ""
    _topup_state_set(user_id, _TOPUP_STEP_AMOUNT)
    send_message(
        chat_id,
        t("topup.prompt", min=f"{_TOPUP_MIN:g}", max=f"{_TOPUP_MAX:g}", fee_line=fee_line),
        parse_mode="HTML",
        reply_markup=_kb_topup_cancel(),
    )


def handle_topup_text_input(user_id: int, chat_id: int, text: str) -> bool:
    """Consume a plain-text amount if the user is mid-/topup. Returns True iff
    handled (so app.py short-circuits normal dispatch)."""
    state = _topup_state_get(user_id)
    if not state or state.get("step") != _TOPUP_STEP_AMOUNT:
        return False

    val, err = _parse_topup_amount(text)
    if err:
        send_message(
            chat_id,
            t("topup.retry_wrap", err=err, min=f"{_TOPUP_MIN:g}", max=f"{_TOPUP_MAX:g}"),
            parse_mode="HTML",
            reply_markup=_kb_topup_cancel(),
        )
        return True

    # Orchestrate on the bot: begin on main (reuse|allocate) → create the OxaPay
    # invoice here → record it back on main. Provider I/O stays off the backend.
    resp = start_topup(user_id, val)
    _topup_state_clear(user_id)

    if resp is None:
        send_message(chat_id, _error_text(t("topup.pay_reason")), parse_mode="HTML")
        return True
    if not resp.get("ok"):
        err_code = resp.get("error")
        key = f"topup.errors.{err_code}" if err_code in _TOPUP_ERROR_CODES else "topup.errors.default"
        msg = t(key, min=f"{_TOPUP_MIN:g}", max=f"{_TOPUP_MAX:g}")
        send_message(chat_id, t("topup.error_wrap", msg=msg), parse_mode="HTML")
        return True

    pay_url = resp.get("pay_url")
    order_id = resp.get("order_id")
    amount = resp.get("amount")
    kb = {
        "inline_keyboard": [
            [{"text": t("buttons.pay_now"), "url": pay_url}],
            [{"text": t("buttons.check_status"), "callback_data": f"topup_status:{order_id}"}],
        ]
    }
    if resp.get("reuse"):
        # Point 3: the user already had an open invoice — reuse it rather than
        # piling up unpaid invoices. Tell them its (possibly different) amount.
        send_message(
            chat_id,
            t("topup.reuse", amount=f"{float(amount):.2f}"),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return True

    send_message(
        chat_id,
        t("topup.invoice", amount=f"{float(amount):.2f}"),
        parse_mode="HTML",
        reply_markup=kb,
    )
    return True


def handle_topup_callback(
    callback_id: str, user_id: int, chat_id: int, message_id: int, data: str
) -> None:
    """Dispatch callback_data prefixed with 'topup_'."""
    answer_callback_query(callback_id)

    if data == "topup_cancel":
        _topup_state_clear(user_id)
        cancel_msg = t("topup.cancelled")
        try:
            edit_message_text(chat_id, message_id, cancel_msg, parse_mode="HTML")
        except Exception:
            send_message(chat_id, cancel_msg, parse_mode="HTML")
        return

    if data.startswith("topup_status:"):
        order_id = data.split(":", 1)[1].strip()
        resp = get_topup_status(user_id, order_id=order_id)
        if resp is None:
            send_message(chat_id, _error_text(t("topup.status_reason")), parse_mode="HTML")
            return
        if not resp.get("ok"):
            send_message(chat_id, t("topup.not_found"), parse_mode="HTML")
            return

        status = (resp.get("status") or "").lower()
        amount = float(resp.get("amount") or 0)
        if resp.get("credited") or status == "paid":
            body = t("topup.confirmed", amount=f"{amount:.2f}")
            kb = None
        elif status == "expired":
            body = t("topup.expired")
            kb = None
        elif status in ("cancelled", "failed"):
            body = t("topup.pay_cancelled")
            kb = None
        else:
            body = t("topup.waiting", amount=f"{amount:.2f}")
            kb = _kb_topup_status(order_id)
        send_message(chat_id, body, parse_mode="HTML", reply_markup=kb)
        return


# ---------------------------------------------------------------------------
# ForceReply handler (reply to /drop prompt)
# ---------------------------------------------------------------------------

def handle_force_reply(
    user_id: int, chat_id: int, reply_to_message_id: int, text: str,
    reply_message_id: int | None = None, role: str = "main",
) -> None:
    """
    Called when a message is a reply to a previous message.
    Checks if that previous message was a /drop or an admin /api ForceReply
    prompt. If yes — executes it. Otherwise — silently ignores.

    Role-scoped tracker access (two-bot safety): the /api ForceReply lives ONLY on
    the admin bot and /drop ONLY on the main bot. Both trackers are keyed by bare
    Telegram message_id, and the two bots share the admin's chat_id but have
    independent id sequences — so consulting the wrong tracker could let one bot
    consume the other's pending entry on an id collision. Each role therefore
    touches only its own tracker.
    """
    # Admin bot (Bot 2): /api ForceReply only — it never runs /drop.
    if role == "admin":
        api_pending = _pop_pending_api(reply_to_message_id)
        if api_pending is not None:
            if api_pending["user_id"] != user_id or not _is_admin(user_id):
                return
            _handle_api_forcereply(api_pending, user_id, chat_id, text, reply_message_id)
        return

    # Main bot (Bot 1). The /api ForceReply lives on the admin bot when it is
    # ENABLED — so skip _pending_api here to avoid a cross-bot message_id collision.
    # When the admin bot is DISABLED (single-bot fallback), /api runs on the main
    # bot, so resolve _pending_api here too — exactly the pre-two-bot behavior.
    if not admin_routing.admin_enabled():
        api_pending = _pop_pending_api(reply_to_message_id)
        if api_pending is not None:
            if api_pending["user_id"] != user_id or not _is_admin(user_id):
                return
            _handle_api_forcereply(api_pending, user_id, chat_id, text, reply_message_id)
            return

    pending = _pop_pending_drop(reply_to_message_id)
    if pending is None:
        return  # Not a tracked /drop prompt

    if pending["user_id"] != user_id:
        logger.warning(
            f"ForceReply: user_id mismatch "
            f"(expected {pending['user_id']}, got {user_id})"
        )
        return  # Security: different user replied

    entry = _get_or_fetch_license(user_id)
    if entry is None:
        send_message(chat_id, _no_license_text(), parse_mode="HTML")
        return

    if not entry.active:
        send_message(chat_id, _inactive_license_text(), parse_mode="HTML")
        return

    # Re-check rate limit (spec: limit applies at execution time)
    allowed, wait_secs = rate_limiter.check(user_id, "drop")
    if not allowed:
        send_message(chat_id, _rate_limit_text("drop", wait_secs), parse_mode="HTML")
        return

    _execute_drop(user_id, chat_id, entry.license_key, text.strip())


# ---------------------------------------------------------------------------
# Callback query handler (inline keyboard buttons)
# ---------------------------------------------------------------------------

def handle_callback_query(
    callback_id: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    data: str,
) -> None:
    logger.info(f"callback  user_id={user_id}  data={data!r}")

    # Always answer immediately to remove the loading spinner from the button
    answer_callback_query(callback_id)

    if data == "cb_install":
        _send_installation_instructions(chat_id)

    elif data == "cb_commands":
        _send_commands_list(chat_id)

    elif data == "cb_license":
        handle_license(user_id, chat_id)

    elif data == "cb_count":
        # Launch the /count flow straight from the welcome menu.
        handle_count(user_id, chat_id)

    elif data == "cb_balance":
        handle_balance(user_id, chat_id)

    elif data == "cb_topup":
        handle_topup(user_id, chat_id)

    elif data == "cb_language":
        handle_language(user_id, chat_id)


# ---------------------------------------------------------------------------
# /language — pick the bot's UI language (user command)
# ---------------------------------------------------------------------------

def handle_language(user_id: int, chat_id: int) -> None:
    """Show the current language and a picker of all supported ones.

    Buttons show NATIVE names (universal across languages); `callback_data`
    stays `lang_<code>` — never translated. The command name is never
    translated either (routing matches the literal '/language')."""
    logger.info(f"/language  user_id={user_id}")
    current_name = display_name(get_lang())
    codes = list(SUPPORTED.keys())
    rows = [
        [{"text": display_name(c), "callback_data": f"lang_{c}"} for c in codes[i:i + 2]]
        for i in range(0, len(codes), 2)
    ]
    send_message(
        chat_id,
        t("language.header", current=current_name),
        parse_mode="HTML",
        reply_markup={"inline_keyboard": rows},
    )


def handle_language_callback(callback_id: str, user_id: int, chat_id: int,
                             message_id: int, data: str) -> None:
    """Dispatch `lang_<code>`: persist the choice, then confirm IN that language."""
    answer_callback_query(callback_id)
    code = data.split("_", 1)[1] if "_" in data else ""
    if code not in SUPPORTED:
        return  # ignore junk / stale taps

    # Slot bot: no legacy license table — persist the choice in the in-memory
    # override (process-lifetime). The Mini App keeps its own language client-side.
    try:
        from bot import i18n as _i18n
        _i18n.set_user_lang(user_id, code)
    except Exception:
        pass

    # Confirm in the NEWLY chosen language (pass lang explicitly — the router's
    # context still holds the language resolved at the START of this update).
    body = t("language.saved", lang=code, name=display_name(code))
    try:
        edit_message_text(chat_id, message_id, body, parse_mode="HTML")
    except Exception:
        send_message(chat_id, body, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /api — admin remote claimer management (interactive menu)
# ---------------------------------------------------------------------------
_API_CURRENCIES = ["usdt", "btc", "eth", "ltc", "trx", "sol", "usdc", "doge"]


def _fmt_blocked(filters: dict | None) -> str:
    if not filters:
        return "none"
    keys = [str(k) for k, v in filters.items() if v]
    return ", ".join(keys) if keys else "none"


def _cfg_line(state: str) -> str:
    return {
        "synced": "✓ Synced",
        "needs_sync": "⚠ Needs Sync",
        "push_failed": "❌ Push Failed",
    }.get(state or "synced", state or "synced")


def _api_state_line(c: dict) -> str:
    if not c.get("has_api"):
        return "— Empty"
    return "✓ Valid" if c.get("api_valid") else "⚠ Unverified"


def _render_claimer_card(c: dict) -> tuple[str, dict]:
    name = html.escape(str(c.get("claimer_name") or c.get("claimer_id") or "?"))
    uname = html.escape(str(c.get("stake_username") or "—"))
    cur = html.escape(str(c.get("currency") or "—").upper())
    text = (
        f"🖥 <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"Connection:  {'🟢 Online' if c.get('online') else '🔴 Offline'}\n"
        f"Configuration:  {_cfg_line(c.get('config_state'))}\n\n"
        f"Stake user:  <code>{uname}</code>\n"
        f"API:  {_api_state_line(c)}\n"
        f"Currency:  {cur}\n"
        f"Blocked:  {html.escape(_fmt_blocked(c.get('filters')))}"
    )
    cid = c.get("claimer_id")
    kb = {"inline_keyboard": [
        [{"text": "🔑 Set API", "callback_data": f"api_setapi:{cid}"},
         {"text": "💱 Currency", "callback_data": f"api_cur:{cid}"}],
        [{"text": "🧮 Filters", "callback_data": f"api_filt:{cid}"},
         {"text": "🗑 Remove API", "callback_data": f"api_rmapi:{cid}"}],
        [{"text": "🔄 Refresh", "callback_data": f"api_view:{cid}"},
         {"text": "◀ Back", "callback_data": "api_list"}],
    ]}
    return text, kb


def handle_valuefornextcode(user_id: int, chat_id: int, args: str) -> None:
    """/valuefornextcode <n> | reset — admin-only persistent value override."""
    if not _is_admin(user_id):
        return
    arg = (args or "").strip().lower()
    if arg in ("reset", "off", "clear"):
        res = admin_set_next_value(reset=True)
        if res and res.get("ok"):
            send_message(chat_id, "✅ Value override cleared — codes behave normally.",
                         parse_mode="HTML")
        else:
            send_message(chat_id, "⚠️ Backend unavailable — override not changed.",
                         parse_mode="HTML")
        return
    # Parse a positive number (int when whole, else float).
    try:
        num = float(arg)
        if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
            raise ValueError
    except (TypeError, ValueError):
        send_message(
            chat_id,
            "Usage: <code>/valuefornextcode &lt;value&gt;</code> (e.g. "
            "<code>/valuefornextcode 4</code>) or <code>/valuefornextcode reset</code>.",
            parse_mode="HTML",
        )
        return
    if not (0 < num <= 1_000_000):
        send_message(chat_id, "⚠️ Value must be a positive number up to 1,000,000.",
                     parse_mode="HTML")
        return
    num = int(num) if float(num).is_integer() else num
    res = admin_set_next_value(value=num)
    if res and res.get("ok"):
        shown = res.get("override", num)
        send_message(
            chat_id,
            f"✅ Override active — every eligible code now gets value <b>{shown}</b>.\n"
            "Codes starting with stakecom/stakepy/staketr, or that already carry a "
            "value, are untouched. Send <code>/valuefornextcode reset</code> to stop.",
            parse_mode="HTML",
        )
    else:
        send_message(chat_id, "⚠️ Not applied — backend rejected the value or is unavailable.",
                     parse_mode="HTML")


def _api_list_markup(claimers: list) -> dict:
    rows = []
    for c in claimers:
        dot = "🟢" if c.get("online") else "🔴"
        name = str(c.get("claimer_name") or c.get("claimer_id") or "?")
        uname = c.get("stake_username")
        # Show both name and username; cap the name so the username stays visible
        # within Telegram's ~64-byte button-label limit.
        label = (f"{dot} {name[:24]} — {uname}" if uname else f"{dot} {name}")[:60]
        rows.append([{"text": label, "callback_data": f"api_view:{c.get('claimer_id')}"}])
    return {"inline_keyboard": rows}


def handle_api(user_id: int, chat_id: int) -> None:
    """/api — list the admin's claimers (interactive)."""
    if not _is_admin(user_id):
        return
    resp = admin_list_claimers(user_id)
    claimers = (resp or {}).get("claimers", []) if resp else []
    if not claimers:
        send_message(
            chat_id,
            "🖥 <b>Claimers</b>\n━━━━━━━━━━━━━━━\n"
            "No claimers registered yet. Deploy the userscript on a Stake tab "
            "(set <code>CLAIMER_NAME</code>) and it will appear here.",
            parse_mode="HTML",
        )
        return
    online = sum(1 for c in claimers if c.get("online"))
    send_message(
        chat_id,
        f"🖥 <b>Your Claimers</b>  ({online}/{len(claimers)} online)\n"
        "━━━━━━━━━━━━━━━\nTap one to manage its API, currency and filters.",
        parse_mode="HTML",
        reply_markup=_api_list_markup(claimers),
    )


def _show_claimer(chat_id: int, message_id: int | None, user_id: int,
                  claimer_id: str, note: str = "") -> None:
    resp = admin_claimer_detail(user_id, claimer_id)
    if not resp or not resp.get("ok"):
        txt = "⚠️ Claimer not found (it may have never connected)."
        if message_id:
            edit_message_text(chat_id, message_id, txt, parse_mode="HTML")
        else:
            send_message(chat_id, txt, parse_mode="HTML")
        return
    text, kb = _render_claimer_card(resp["claimer"])
    if note:
        text = note + "\n\n" + text
    if message_id:
        edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=kb)
    else:
        send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def _apply_note(res: dict, ok_text: str) -> str:
    if not res:
        return "⚠️ Backend unavailable."
    if not res.get("online"):
        return "💤 Claimer offline — saved; it will apply on the next refresh."
    if res.get("applied") is False or res.get("valid") is False:
        return "❌ Not applied — API token is invalid."
    return ok_text


def handle_api_callback(callback_id: str, user_id: int, chat_id: int,
                        message_id: int, data: str) -> None:
    """Dispatch api_* callbacks. Admin-gated."""
    answer_callback_query(callback_id)
    if not _is_admin(user_id):
        return
    action, _, rest = data.partition(":")

    if data == "api_list":
        resp = admin_list_claimers(user_id)
        claimers = (resp or {}).get("claimers", []) if resp else []
        online = sum(1 for c in claimers if c.get("online"))
        edit_message_text(
            chat_id, message_id,
            f"🖥 <b>Your Claimers</b>  ({online}/{len(claimers)} online)\n"
            "━━━━━━━━━━━━━━━\nTap one to manage it.",
            parse_mode="HTML", reply_markup=_api_list_markup(claimers),
        )
        return

    cid = rest.split(":")[0] if rest else ""
    if not cid:
        return

    if action == "api_view":
        _show_claimer(chat_id, message_id, user_id, cid)

    elif action == "api_setapi":
        sent = send_message(
            chat_id,
            f"🔑 Reply with the <b>x-access-token</b> for <code>{html.escape(cid)}</code>.\n"
            "It is validated against Stake before it's applied; your reply is deleted after.",
            parse_mode="HTML",
            reply_markup={"force_reply": True, "selective": True},
        )
        mid = (sent or {}).get("result", {}).get("message_id")
        if mid:
            _register_pending_api(int(mid), user_id, chat_id, cid, "set_api")

    elif action == "api_rmapi":
        res = admin_remove_api(user_id, cid)
        _show_claimer(chat_id, message_id, user_id, cid, _apply_note(res, "🗑 API removed."))

    elif action == "api_cur":
        rows, row = [], []
        for cur in _API_CURRENCIES:
            row.append({"text": cur.upper(), "callback_data": f"api_setcur:{cid}:{cur}"})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "◀ Back", "callback_data": f"api_view:{cid}"}])
        edit_message_text(chat_id, message_id, "💱 Choose a currency:",
                          parse_mode="HTML", reply_markup={"inline_keyboard": rows})

    elif action == "api_setcur":
        parts = rest.split(":")
        currency = parts[1] if len(parts) > 1 else ""
        res = admin_set_currency(user_id, cid, currency)
        _show_claimer(chat_id, message_id, user_id, cid,
                      _apply_note(res, f"💱 Currency set to {currency.upper()}."))

    elif action == "api_filt":
        sent = send_message(
            chat_id,
            f"🧮 Reply with the values to <b>block</b> for <code>{html.escape(cid)}</code>, "
            "comma-separated (e.g. <code>1,2,5,high</code>). Send <code>none</code> to clear.",
            parse_mode="HTML",
            reply_markup={"force_reply": True, "selective": True},
        )
        mid = (sent or {}).get("result", {}).get("message_id")
        if mid:
            _register_pending_api(int(mid), user_id, chat_id, cid, "set_filters")


def _handle_api_forcereply(pending: dict, user_id: int, chat_id: int, text: str,
                           reply_message_id: int | None = None) -> None:
    """Process a ForceReply reply to a Set API / Filters prompt."""
    cid = pending["claimer_id"]
    action = pending["action"]
    # Delete the admin's reply (may contain a token) for confidentiality.
    if reply_message_id:
        try:
            delete_message(chat_id, reply_message_id)
        except Exception:
            pass
    if action == "set_api":
        token = (text or "").strip()
        if not token:
            send_message(chat_id, "⚠️ Empty token — ignored.", parse_mode="HTML")
            return
        res = admin_set_api(user_id, cid, token)
        _show_claimer(chat_id, None, user_id, cid, _apply_note(res, "🔑 API set."))
    elif action == "set_filters":
        raw = (text or "").strip().lower()
        if raw in ("none", "clear", "-", ""):
            filters = {}
        else:
            filters = {k.strip(): True for k in raw.split(",") if k.strip()}
        res = admin_set_filters(user_id, cid, filters)
        _show_claimer(chat_id, None, user_id, cid,
                      _apply_note(res, f"🧮 Blocked: {_fmt_blocked(filters)}."))


# ---------------------------------------------------------------------------
# Static text responses for inline button callbacks
# ---------------------------------------------------------------------------

def _send_installation_instructions(chat_id: int) -> None:
    """Beginner-friendly setup guide: a short install video in the user's
       language, then two tidy messages —
       1) Install & connect   2) Optimization & best practices."""

    # ── Message 0 — install video (localized; English fallback) ──────────
    # Sent by file_id, so it's instant and never touches the bot host. No
    # caption (the video is self-contained); the setup text follows below.
    # Best-effort: a video failure must never block the install instructions.
    _vid = install_video_for(get_lang())
    if _vid:
        try:
            send_video(chat_id, _vid, caption=None, parse_mode=None)
        except Exception:
            logger.exception("install video send failed (ignored)")

    # ── Message 1 — Install & connect ────────────────────────────────────
    kb_setup = {
        "inline_keyboard": [
            [
                {"text": t("buttons.install_userscript"), "url": USERSCRIPT_URL},
                {"text": t("buttons.copy_link"), "copy_text": {"text": USERSCRIPT_URL}},
            ],
            [
                {"text": t("buttons.stake_offers"), "url": "https://stake.com/settings/offers"},
                {"text": t("buttons.vip_club"), "url": "https://stake.com/vip-club"},
            ],
        ]
    }
    send_message(chat_id, t("install.setup"), parse_mode="HTML", reply_markup=kb_setup)

    # ── Message 2 — Optimization & best practices ────────────────────────
    kb_optimize = {
        "inline_keyboard": [
            [
                {"text": t("buttons.support"), "url": SUPPORT_URL},
                {"text": t("buttons.channel"), "url": CHANNEL_URL},
            ],
        ]
    }
    send_message(chat_id, t("install.optimize"), parse_mode="HTML", reply_markup=kb_optimize)


def _send_commands_list(chat_id: int) -> None:
    send_message(chat_id, t("commands.list"), parse_mode="HTML")


# ---------------------------------------------------------------------------
# License key changed notification (spec §A — v3.1)
# ---------------------------------------------------------------------------

def handle_license_key_changed(
    user_id: int,
    chat_id: int,
    old_key: str,
    new_key: str,
) -> None:
    """
    Deliver a license key rotation notification to the user.

    Called when Service 1 pushes a key-rotation event via /nx/v3/push.
    Service 1 pre-formats the full message text and delivers it through
    notify_push, so this function is an explicit fallback used only if
    the bot needs to construct the message itself (e.g. during testing).

    The new_key is shown in full so the user can copy it into the
    Tampermonkey popup.
    """
    logger.info(
        f"License key changed: telegram_id={user_id} "
        f"old={shorten_key(old_key)} new={shorten_key(new_key)}"
    )

    text = t("license_changed.body", old=shorten_key(old_key), new=new_key)

    # Update local cache with new key (if not already done by lic-sync)
    existing = license_cache.get_by_telegram_id(user_id)
    if existing and existing.license_key == old_key:
        license_cache.set(
            telegram_id=user_id,
            license_key=new_key,
            active=True,
            theclaimers_count=existing.theclaimers_count,
            maximum_usernames=existing.maximum_usernames,
            # Same user, new key — preserve their language override.
            language=existing.language,
        )

    send_message(chat_id, text, parse_mode="HTML")


# ===========================================================================
# Admin-only commands
# ===========================================================================
# /licenselivecount  — per-license live snapshot of connected users
# /claimcount <code> — lifetime claim count for a single code
#
# Both commands check the caller's user_id against the ADMIN_USER_ID env var.
# Non-admins get a silent "command not found"-equivalent response (we just
# log and return) so the existence of these commands is not advertised to
# regular users via an error message.
# ===========================================================================

def _is_admin(user_id: int) -> bool:
    """
    True iff `user_id` is in the ADMIN_USER_ID allow-list (one or more ids,
    comma/space separated). Returns False if the env var is missing or contains no
    valid ids — admin commands silently no-op rather than allowing anyone.

    Delegates to admin_routing.is_admin_id() so command AUTHORIZATION and DM ROUTING
    share ONE parsed allow-list and can never disagree (a comma-separated value must
    not authorize routing while breaking authorization, or vice-versa).
    """
    return admin_routing.is_admin_id(user_id)


# Telegram message hard limit is 4096 chars. We aim well under to leave
# headroom for the per-part header text and any HTML markup overhead.
_MAX_TG_MSG_CHARS = 3800

# Reserve for the "— Part X/Y" suffix we inject into the header line when
# multiple parts are sent (so per-message budgeting accounts for it).
_PART_SUFFIX_RESERVE = 40


def _build_licensecount_csv(licenses: list, totals: dict) -> str:
    """Render the live-license snapshot as CSV text (one row per username).

    Columns: license_key, telegram_id, username, tabs, ip_count, ips, versions.
    Lists (ips, versions) are joined with "; " inside a single quoted cell;
    csv.writer handles all quoting/escaping. A trailing blank line + TOTALS
    rows carry the system-wide figures.
    """
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["license_key", "manager_id", "telegram_id", "username", "tabs",
                "ip_count", "ips", "versions"])
    for lic in licenses:
        lic_key = str(lic.get("license_key") or "?")
        mgr = str(lic.get("manager_id") or "")
        tg_id = int(lic.get("telegram_id") or 0)
        usernames = lic.get("usernames") or []
        if not usernames:
            w.writerow([lic_key, mgr, tg_id, "(no named users)", 0, 0, "", ""])
            continue
        for u in usernames:
            uname = str(u.get("username") or "?")
            sess = int(u.get("sessions") or 0)
            ips = u.get("ips") or []
            vers = u.get("versions") or []
            w.writerow([
                lic_key, mgr, tg_id, uname, sess, len(ips),
                "; ".join(str(ip) for ip in ips),
                "; ".join(str(v) for v in vers),
            ])
    w.writerow([])
    w.writerow(["TOTALS"])
    w.writerow(["licenses_with_active_users",
                int(totals.get("licenses_with_active_users") or 0)])
    w.writerow(["total_unique_usernames",
                int(totals.get("total_unique_usernames") or 0)])
    w.writerow(["total_sessions",
                int(totals.get("total_sessions") or 0)])
    return buf.getvalue()


def _manager_summary_lines(licenses: list, max_named: int = 15) -> str:
    """Aggregate currently-connected unique users by manager label.

    Named managers (non-empty manager_id) are summed across all their
    licenses; licenses with no manager_id roll into one 'Unnamed' line.
    Returns an HTML snippet for the caption, capped to max_named entries so
    the Telegram caption stays well under its 1024-char limit.
    """
    named: dict = {}            # manager_id -> [unique_users, license_count]
    unnamed_users = 0
    unnamed_lics = 0
    for lic in licenses:
        mgr = (str(lic.get("manager_id") or "")).strip()
        uu = int(lic.get("unique_usernames") or 0)
        if mgr:
            agg = named.setdefault(mgr, [0, 0])
            agg[0] += uu
            agg[1] += 1
        else:
            unnamed_users += uu
            unnamed_lics += 1
    if not named and not unnamed_users:
        return ""
    ordered = sorted(named.items(), key=lambda kv: (-kv[1][0], kv[0]))
    out = ["", "<b>\U0001f464 By manager:</b>"]
    for i, (mgr, (uu, lc)) in enumerate(ordered):
        if i >= max_named:
            out.append(f"  \u2022 <i>+{len(ordered) - max_named} more \u2014 see CSV</i>")
            break
        lic_note = f" ({lc} licenses)" if lc > 1 else ""
        out.append(f"  \u2022 {html.escape(mgr)} \u2014 {uu} users{lic_note}")
    if unnamed_users:
        out.append(f"  \u2022 <i>Unnamed</i> \u2014 {unnamed_users} users ({unnamed_lics} lic.)")
    return "\n".join(out)


def handle_maskcode(user_id: int, chat_id: int, arg: str) -> None:
    """Admin-only: toggle first-claim code masking at runtime — /maskcode on|off.
    In-memory on the backend (resets to the MASK_CODE env default on restart)."""
    if not _is_admin(user_id):
        logger.info(f"/maskcode denied (non-admin) user_id={user_id}")
        return
    a = (arg or "").strip().lower()
    if a in ("on", "yes", "true", "1"):
        enabled = True
    elif a in ("off", "no", "false", "0"):
        enabled = False
    else:
        send_message(
            chat_id,
            "Usage: <code>/maskcode on</code> or <code>/maskcode off</code>",
            parse_mode="HTML",
        )
        return
    resp = set_runtime_setting(maskcode=enabled)
    if not resp or not resp.get("ok"):
        send_message(chat_id, "⚠️ Backend did not apply the change. Try again.", parse_mode="HTML")
        return
    state = bool((resp.get("settings") or {}).get("maskcode"))
    send_message(chat_id, f"✅ Code masking is now <b>{'ON' if state else 'OFF'}</b>.", parse_mode="HTML")


def handle_claimdelay(user_id: int, chat_id: int, arg: str) -> None:
    """Admin-only: set the first-claim group-notification delay in seconds —
    /claimdelay <n> (0–300, 0 = instant). In-memory on the backend."""
    if not _is_admin(user_id):
        logger.info(f"/claimdelay denied (non-admin) user_id={user_id}")
        return
    a = (arg or "").strip()
    try:
        seconds = float(a)
        if seconds != seconds or seconds < 0 or seconds > 300:
            raise ValueError
    except (TypeError, ValueError):
        send_message(
            chat_id,
            "Usage: <code>/claimdelay 0</code> — seconds (0–300). <b>0 = instant.</b>",
            parse_mode="HTML",
        )
        return
    resp = set_runtime_setting(first_claim_delay=seconds)
    if not resp or not resp.get("ok"):
        send_message(chat_id, "⚠️ Backend did not apply the change. Try again.", parse_mode="HTML")
        return
    val = float((resp.get("settings") or {}).get("first_claim_delay") or 0)
    suffix = " (instant)" if val == 0 else ""
    send_message(chat_id, f"✅ First-claim delay is now <b>{val:g}s</b>{suffix}.", parse_mode="HTML")


def _everycodesame_status_text(enabled: bool) -> str:
    if enabled:
        return ("Every Code Same: <b>ON</b>\n"
                "(Broadcast duplicate detection is case-insensitive)")
    return ("Every Code Same: <b>OFF</b>\n"
            "(Broadcast duplicate detection is case-sensitive — current behaviour)")


def handle_everycodesame(user_id: int, chat_id: int, arg: str) -> None:
    """Admin-only: case-insensitive broadcast duplicate detection — /everycodesame
    on|off, or no arg to show the current mode. In-memory on the backend (resets to
    OFF on restart). Affects ONLY the broadcast dedup key; the code is still
    broadcast exactly as received."""
    if not _is_admin(user_id):
        logger.info(f"/everycodesame denied (non-admin) user_id={user_id}")
        return
    a = (arg or "").strip().lower()

    if a == "":
        # Status read: no keys sent -> backend returns the current snapshot.
        resp = set_runtime_setting()
        settings = (resp or {}).get("settings") or {}
        if not resp or not resp.get("ok"):
            send_message(chat_id, "⚠️ Backend unavailable — could not read the setting.",
                         parse_mode="HTML")
            return
        send_message(chat_id, _everycodesame_status_text(bool(settings.get("every_code_same"))),
                     parse_mode="HTML")
        return

    if a in ("on", "yes", "true", "1"):
        enabled = True
    elif a in ("off", "no", "false", "0"):
        enabled = False
    else:
        send_message(
            chat_id,
            "Usage: <code>/everycodesame on</code> or <code>/everycodesame off</code> "
            "(or <code>/everycodesame</code> to see the current mode).",
            parse_mode="HTML",
        )
        return

    resp = set_runtime_setting(every_code_same=enabled)
    if not resp or not resp.get("ok"):
        send_message(chat_id, "⚠️ Backend did not apply the change. Try again.", parse_mode="HTML")
        return
    # Echo the LIVE state from the backend snapshot (single source of truth).
    state = bool((resp.get("settings") or {}).get("every_code_same"))
    send_message(chat_id, "✅ " + _everycodesame_status_text(state), parse_mode="HTML")


def handle_licenselivecount(user_id: int, chat_id: int) -> None:
    """
    Admin-only: send the admin a per-license live connection snapshot
    plus system-wide totals.

    For datasets that exceed a single Telegram message (4096 chars), this
    handler sends MULTIPLE messages in sequence. Each part has its header
    rewritten to include "— Part X/Y" so the admin knows when the sequence
    is complete. Per-license blocks are NEVER split across messages — each
    license's full info (header + every username with its tab count) stays
    together for legibility.

    System totals always appear at the END of the LAST message (or as their
    own message if there's no room in the last licenses message).

    Output layout (multi-part HTML):

      📊 Live connection snapshot — Part 1/2
      ─────────────────────
      🔑 THECLAIMERS-aaaa…1111
         👤 TG: 12345  •  Cap: 3/50
         🟢 6 sessions across 3 users
            • charlie  ×3 tabs
            • alice    ×2 tabs
            • bob      ×1 tab
      …

      📊 Live connection snapshot — Part 2/2
      ─────────────────────
      🔑 THECLAIMERS-bbbb…2222
         …
      ─────────────────────
      📈 TOTALS
         Licenses with users: 13
         Unique usernames:    51
         Total sessions:      56
    """
    logger.info(f"/licenselivecount  user_id={user_id}")

    if not _is_admin(user_id):
        # Silent — do not advertise that the command exists.
        logger.info(f"/licenselivecount denied (non-admin) user_id={user_id}")
        return

    snapshot = get_live_counts()
    if snapshot is None:
        send_message(
            chat_id,
            "⚠️ Backend did not respond. Try again in a moment.",
            parse_mode="HTML",
        )
        return

    licenses = snapshot.get("licenses") or []
    totals = snapshot.get("totals") or {}

    # Empty-state — single-message fast path.
    if not licenses:
        send_message(
            chat_id,
            (
                "<b>📊 Live connection snapshot</b>\n"
                "─────────────────────\n"
                "<i>No active connections right now.</i>\n"
                "─────────────────────\n"
                "<b>📈 TOTALS</b>\n"
                "  • Licenses with users: <b>0</b>\n"
                "  • Unique usernames:    <b>0</b>\n"
                "  • Total sessions:      <b>0</b>"
            ),
            parse_mode="HTML",
        )
        return

    # ── Render the snapshot as a CSV file and send it as a document ──────
    # One attachment instead of multi-part messages keeps the chat clean,
    # and a CSV lets the admin sort/filter by user, IP, or version.
    csv_text = _build_licensecount_csv(licenses, totals)
    fname = "licenselivecount_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
    caption = (
        "<b>📊 Live connection snapshot</b>\n"
        "  • Licenses with users: <b>"
        f"{int(totals.get('licenses_with_active_users') or 0)}</b>\n"
        "  • Unique usernames:    <b>"
        f"{int(totals.get('total_unique_usernames') or 0)}</b>\n"
        "  • Total sessions:      <b>"
        f"{int(totals.get('total_sessions') or 0)}</b>"
    )
    mgr_block = _manager_summary_lines(licenses)
    if mgr_block:
        caption += "\n" + mgr_block
    # Telegram caption hard limit is 1024 chars; keep well under it.
    if len(caption) > 1024:
        caption = caption[:1010] + "\n…"
    result = send_document(
        chat_id,
        fname,
        csv_text.encode("utf-8"),
        caption=caption,
    )
    if not result.get("ok"):
        # send_document swallows errors and returns {} / a not-ok dict per the
        # telegram_api convention, so check the result rather than try/except.
        # On failure, still give the admin the headline totals as text.
        logger.warning(f"/licenselivecount CSV upload failed: {result}")
        send_message(
            chat_id,
            caption + "\n<i>(could not attach the CSV file)</i>",
            parse_mode="HTML",
        )



def handle_claimcount(user_id: int, chat_id: int, code: str | None) -> None:
    """
    Admin-only: report the lifetime claim count for a specific code.

    Usage: /claimcount <code>
    """
    logger.info(f"/claimcount  user_id={user_id}  code={code!r}")

    if not _is_admin(user_id):
        logger.info(f"/claimcount denied (non-admin) user_id={user_id}")
        return

    code = (code or "").strip()
    if not code:
        send_message(
            chat_id,
            (
                "<b>Usage:</b> <code>/claimcount &lt;code&gt;</code>\n\n"
                "<b>Example:</b> <code>/claimcount ABC123</code>"
            ),
            parse_mode="HTML",
        )
        return
    if len(code) > 64:
        send_message(
            chat_id,
            "❌ Code is too long (max 64 characters).",
            parse_mode="HTML",
        )
        return

    result = get_code_claim_count(code)
    if result is None:
        send_message(
            chat_id,
            "⚠️ Backend did not respond. Try again in a moment.",
            parse_mode="HTML",
        )
        return

    echoed_code = str(result.get("code") or code)
    total = int(result.get("total_claims_count") or 0)

    if total == 0:
        body = (
            f"<b>📊 Claim count</b>\n"
            f"─────────────────────\n"
            f"🎟  <code>{html.escape(echoed_code)}</code>\n"
            f"📈  <b>0 claims</b> recorded\n\n"
            f"<i>Either this code has never been claimed yet, or "
            f"the case/spelling doesn't match any record (lookup is "
            f"case-insensitive).</i>"
        )
    else:
        plural = "claims" if total != 1 else "claim"
        body = (
            f"<b>📊 Claim count</b>\n"
            f"─────────────────────\n"
            f"🎟  <code>{html.escape(echoed_code)}</code>\n"
            f"📈  <b>{total} {plural}</b> recorded"
        )

    send_message(chat_id, body, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /count — 24-hour rolling claim history
#
# Multi-step interactive flow:
#
#   REGULAR USER:
#     /count → "Pick a time range" buttons [3h] [12h] [24h] [Custom] [Cancel]
#       tap 3h/12h/24h → fetch user's records → report
#       tap Custom → "Send me a number 1-24" → text → fetch → report
#
#   ADMIN:
#     /count → "Query type" buttons [By License] [By Username] [Cancel]
#       tap By License → "Send me the license key" → text → time buttons → fetch → report
#       tap By Username → "Send me the Stake username" → text → time buttons → fetch → report
#
# Per-user state machine (TTL 5 min, drops abandoned conversations).
# Rate-limited to 5/60s via the existing rate_limiter.
# Long reports are split into multiple Telegram messages (4096 char cap)
# with "— Part X/Y" suffix injected, matching /licenselivecount.
# ---------------------------------------------------------------------------

# Step constants — string identifiers used inside the state dict.
_COUNT_STEP_REG_TIME_SELECT      = "reg_time_select"
_COUNT_STEP_REG_CUSTOM_RANGE     = "reg_custom_range"
_COUNT_STEP_ADMIN_TYPE_SELECT    = "admin_type_select"
_COUNT_STEP_ADMIN_KEY_ENTRY      = "admin_key_entry"
_COUNT_STEP_ADMIN_NAME_ENTRY     = "admin_name_entry"
_COUNT_STEP_ADMIN_TIME_SELECT    = "admin_time_select"
_COUNT_STEP_ADMIN_CUSTOM_RANGE   = "admin_custom_range"

# State TTL: any flow that the user abandons mid-way auto-clears after 5 min.
_COUNT_STATE_TTL_S = 5 * 60

_count_state: dict[int, dict] = {}
_count_state_lock = threading.Lock()


def _count_state_set(user_id: int, step: str, payload: dict | None = None) -> None:
    with _count_state_lock:
        _count_state_evict_expired_locked()
        _count_state[user_id] = {
            "step": step,
            "payload": payload or {},
            "ts": time.time(),
        }


def _count_state_get(user_id: int) -> dict | None:
    """Returns the state for user_id, or None if no flow or expired."""
    with _count_state_lock:
        _count_state_evict_expired_locked()
        return _count_state.get(user_id)


def _count_state_clear(user_id: int) -> None:
    with _count_state_lock:
        _count_state.pop(user_id, None)


def _count_state_evict_expired_locked() -> None:
    """Drop entries older than TTL. Caller must hold _count_state_lock."""
    cutoff = time.time() - _COUNT_STATE_TTL_S
    stale = [uid for uid, st in _count_state.items() if st["ts"] < cutoff]
    for uid in stale:
        _count_state.pop(uid, None)


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------

from datetime import datetime, timezone, timedelta

# IST time-window helpers (bot side). The bot is a separate service from the
# backend, so it computes the [start, end] window in IST itself and passes
# epoch-millisecond bounds (start_ms/end_ms) to the backend /claims endpoints.
# IST is a fixed UTC+5:30 (India has no DST) -> exact, dependency-free.
_IST = timezone(timedelta(hours=5, minutes=30))
_STREAM_START = (6, 0)      # 06:00 IST
_STREAM_END = (9, 30)       # 09:30 IST
_RETENTION_DAYS = 30
_CUSTOM_MAX_DAYS = 7


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _stream_window_ms() -> tuple:
    """Today's 06:00-09:30 IST as (start_ms, end_ms). Before 06:00 IST → previous day."""
    ref = _now_utc().astimezone(_IST)
    day = ref.date()
    start_ist = datetime(day.year, day.month, day.day, _STREAM_START[0], _STREAM_START[1], 0, tzinfo=_IST)
    if ref < start_ist:
        day = (ref - timedelta(days=1)).date()
        start_ist = datetime(day.year, day.month, day.day, _STREAM_START[0], _STREAM_START[1], 0, tzinfo=_IST)
    end_ist = datetime(day.year, day.month, day.day, _STREAM_END[0], _STREAM_END[1], 0, tzinfo=_IST)
    return _to_ms(start_ist), _to_ms(end_ist)


def _rolling_window_ms(days: float) -> tuple:
    end = _now_utc()
    return _to_ms(end - timedelta(days=days)), _to_ms(end)


def _custom_window_ms(from_str: str, to_str: str) -> tuple:
    """
    Parse 'YYYY-MM-DD' from/to (IST), validate, return (start_ms, end_ms).
    Raises ValueError with a short user-facing message on any problem.
    Inclusive: [from 00:00 IST, to 23:59:59.999999 IST].
    """
    try:
        d_from = datetime.strptime(from_str.strip(), "%Y-%m-%d").date()
        d_to = datetime.strptime(to_str.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        raise ValueError(t("count.err_date_format"))
    if d_to < d_from:
        raise ValueError(t("count.err_to_before_from"))
    gap = (d_to - d_from).days
    if gap > _CUSTOM_MAX_DAYS:
        raise ValueError(t("count.err_range_too_long", gap=gap, max=_CUSTOM_MAX_DAYS))
    today_ist = _now_utc().astimezone(_IST).date()
    oldest = today_ist - timedelta(days=_RETENTION_DAYS)
    if d_from < oldest:
        raise ValueError(t("count.err_too_old", days=_RETENTION_DAYS))
    if d_from > today_ist:
        raise ValueError(t("count.err_future"))
    start_ist = datetime(d_from.year, d_from.month, d_from.day, 0, 0, 0, tzinfo=_IST)
    end_ist = datetime(d_to.year, d_to.month, d_to.day, 23, 59, 59, 999999, tzinfo=_IST)
    return _to_ms(start_ist), _to_ms(end_ist)


def _window_for(choice: str) -> tuple:
    """Map a preset choice → (start_ms, end_ms, label). choice ∈ {stream,1d,7d}."""
    if choice == "stream":
        s, e = _stream_window_ms()
        return s, e, t("count.win_stream")
    if choice == "1d":
        s, e = _rolling_window_ms(1)
        return s, e, t("count.win_1d")
    if choice == "7d":
        s, e = _rolling_window_ms(7)
        return s, e, t("count.win_7d")
    # default safety
    s, e = _rolling_window_ms(1)
    return s, e, t("count.win_1d")


def _kb_reg_time_select() -> dict:
    """Time-range picker shown to a regular user as the first step."""
    return {
        "inline_keyboard": [
            [
                {"text": t("buttons.count_stream"), "callback_data": "count_w_stream"},
            ],
            [
                {"text": t("buttons.count_1d"),  "callback_data": "count_w_1d"},
                {"text": t("buttons.count_7d"), "callback_data": "count_w_7d"},
            ],
            [
                {"text": t("buttons.count_custom"), "callback_data": "count_w_custom"},
            ],
            [
                {"text": t("buttons.cancel"), "callback_data": "count_cancel"},
            ],
        ]
    }


def _kb_admin_type_select() -> dict:
    """First-step picker shown to an admin: by license or by username."""
    return {
        "inline_keyboard": [
            [
                {"text": t("buttons.by_license"),  "callback_data": "count_admin_lic"},
                {"text": t("buttons.by_username"), "callback_data": "count_admin_user"},
            ],
            [
                {"text": t("buttons.cancel"), "callback_data": "count_cancel"},
            ],
        ]
    }


def _kb_admin_time_select() -> dict:
    """Time-range picker shown to the admin after entering license/username."""
    return {
        "inline_keyboard": [
            [
                {"text": t("buttons.count_stream"), "callback_data": "count_aw_stream"},
            ],
            [
                {"text": t("buttons.count_1d"),  "callback_data": "count_aw_1d"},
                {"text": t("buttons.count_7d"), "callback_data": "count_aw_7d"},
            ],
            [
                {"text": t("buttons.count_custom"), "callback_data": "count_aw_custom"},
            ],
            [
                {"text": t("buttons.cancel"), "callback_data": "count_cancel"},
            ],
        ]
    }


def _kb_cancel_only() -> dict:
    """Single Cancel button — shown while we wait for text input."""
    return {"inline_keyboard": [[{"text": t("buttons.cancel"), "callback_data": "count_cancel"}]]}


def _custom_range_prompt() -> str:
    """Engaging prompt asking the user to send a custom date range (IST)."""
    today_ist = _now_utc().astimezone(_IST).date()
    oldest = today_ist - timedelta(days=_RETENTION_DAYS)
    ex_to = today_ist
    ex_from = today_ist - timedelta(days=6)
    return t(
        "count.custom_prompt",
        ex_from=ex_from.isoformat(),
        ex_to=ex_to.isoformat(),
        max=_CUSTOM_MAX_DAYS,
        oldest=oldest.isoformat(),
        retention=_RETENTION_DAYS,
    )


# ---------------------------------------------------------------------------
# Entry point — /count
# ---------------------------------------------------------------------------

def handle_count(user_id: int, chat_id: int) -> None:
    """
    Entry point for /count. Branches on admin status.

    Rate-limited to 5/60s. Hitting the limit shows a friendly note
    without consuming the limiter slot of subsequent calls.
    """
    logger.info(f"/count  user_id={user_id}")

    allowed, retry_after = rate_limiter.check(user_id, "count")
    if not allowed:
        send_message(
            chat_id,
            t("count.rate_limit", secs=int(retry_after) + 1),
            parse_mode="HTML",
        )
        return

    if _is_admin(user_id):
        # Admin gets the type-picker first
        _count_state_set(user_id, _COUNT_STEP_ADMIN_TYPE_SELECT)
        send_message(
            chat_id,
            t("count.admin_header"),
            parse_mode="HTML",
            reply_markup=_kb_admin_type_select(),
        )
    else:
        # Regular user — straight to time-range picker (for their own claims)
        _count_state_set(user_id, _COUNT_STEP_REG_TIME_SELECT)
        send_message(
            chat_id,
            t("count.user_header"),
            parse_mode="HTML",
            reply_markup=_kb_reg_time_select(),
        )


# ---------------------------------------------------------------------------
# Callback-query dispatcher (called from app.py for count_* data)
# ---------------------------------------------------------------------------

def handle_count_callback(
    callback_id: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    data: str,
) -> None:
    """
    Dispatches callback_data prefixed with "count_". Always answers the
    callback immediately to clear the spinner.
    """
    answer_callback_query(callback_id)

    # ────────── Cancel (works from any step) ──────────
    if data == "count_cancel":
        _count_state_clear(user_id)
        try:
            edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=t("count.cancelled"),
                parse_mode="HTML",
            )
        except Exception:
            # If edit fails (e.g. message too old), send a fresh one.
            send_message(chat_id, t("count.cancelled"), parse_mode="HTML")
        return

    state = _count_state_get(user_id)
    if not state:
        send_message(
            chat_id,
            t("count.expired"),
            parse_mode="HTML",
        )
        return

    step = state["step"]
    payload = state["payload"]

    # ────────── Regular user: window preset buttons ──────────
    if data in ("count_w_stream", "count_w_1d", "count_w_7d"):
        if step != _COUNT_STEP_REG_TIME_SELECT:
            return  # ignore stale taps
        choice = data.rsplit("_", 1)[1]
        start_ms, end_ms, label = _window_for(choice)
        _count_state_clear(user_id)
        _run_user_query(user_id, chat_id, start_ms, end_ms, label)
        return

    if data == "count_w_custom":
        if step != _COUNT_STEP_REG_TIME_SELECT:
            return
        _count_state_set(user_id, _COUNT_STEP_REG_CUSTOM_RANGE)
        send_message(
            chat_id,
            _custom_range_prompt(),
            parse_mode="HTML",
            reply_markup=_kb_cancel_only(),
        )
        return

    # ────────── Admin: type-picker ──────────
    if data == "count_admin_lic":
        if step != _COUNT_STEP_ADMIN_TYPE_SELECT:
            return
        _count_state_set(user_id, _COUNT_STEP_ADMIN_KEY_ENTRY)
        send_message(
            chat_id,
            t("count.admin_lic_prompt"),
            parse_mode="HTML",
            reply_markup=_kb_cancel_only(),
        )
        return

    if data == "count_admin_user":
        if step != _COUNT_STEP_ADMIN_TYPE_SELECT:
            return
        _count_state_set(user_id, _COUNT_STEP_ADMIN_NAME_ENTRY)
        send_message(
            chat_id,
            t("count.admin_user_prompt"),
            parse_mode="HTML",
            reply_markup=_kb_cancel_only(),
        )
        return

    # ────────── Admin: window preset buttons (after key/name entry) ──────────
    if data in ("count_aw_stream", "count_aw_1d", "count_aw_7d"):
        if step != _COUNT_STEP_ADMIN_TIME_SELECT:
            return
        choice = data.rsplit("_", 1)[1]
        start_ms, end_ms, label = _window_for(choice)
        _count_state_clear(user_id)
        _run_admin_query(chat_id, payload, start_ms, end_ms, label)
        return

    if data == "count_aw_custom":
        if step != _COUNT_STEP_ADMIN_TIME_SELECT:
            return
        _count_state_set(user_id, _COUNT_STEP_ADMIN_CUSTOM_RANGE, payload)
        send_message(
            chat_id,
            _custom_range_prompt(),
            parse_mode="HTML",
            reply_markup=_kb_cancel_only(),
        )
        return


# ---------------------------------------------------------------------------
# Text-input dispatcher — called from app.py when user has a pending step
# ---------------------------------------------------------------------------

def handle_count_text_input(user_id: int, chat_id: int, text: str) -> bool:
    """
    Process a plain-text message in the context of a /count flow.

    Returns True iff we DID handle the message (i.e. there was a pending
    /count step). The caller in app.py uses this to short-circuit normal
    command dispatch. Returns False otherwise → app.py keeps processing.
    """
    state = _count_state_get(user_id)
    if not state:
        return False

    step = state["step"]
    payload = state["payload"]
    text = (text or "").strip()

    if step == _COUNT_STEP_REG_CUSTOM_RANGE:
        pair = _split_range(text)
        if not pair:
            send_message(
                chat_id,
                t("count.range_invalid"),
                parse_mode="HTML",
                reply_markup=_kb_cancel_only(),
            )
            return True
        try:
            start_ms, end_ms = _custom_window_ms(pair[0], pair[1])
        except ValueError as ve:
            send_message(
                chat_id,
                t("count.error_wrap", error=html.escape(str(ve))),
                parse_mode="HTML",
                reply_markup=_kb_cancel_only(),
            )
            return True
        label = t("count.label_custom", d_from=pair[0], d_to=pair[1])
        _count_state_clear(user_id)
        _run_user_query(user_id, chat_id, start_ms, end_ms, label)
        return True

    if step == _COUNT_STEP_ADMIN_KEY_ENTRY:
        key = text[:128]
        if not key:
            send_message(chat_id, t("count.key_empty"), parse_mode="HTML",
                         reply_markup=_kb_cancel_only())
            return True
        _count_state_set(
            user_id, _COUNT_STEP_ADMIN_TIME_SELECT,
            {"type": "license", "value": key},
        )
        send_message(
            chat_id,
            t("count.key_got", key=html.escape(key)),
            parse_mode="HTML",
            reply_markup=_kb_admin_time_select(),
        )
        return True

    if step == _COUNT_STEP_ADMIN_NAME_ENTRY:
        name = text[:64]
        if not name:
            send_message(chat_id, t("count.name_empty"), parse_mode="HTML",
                         reply_markup=_kb_cancel_only())
            return True
        _count_state_set(
            user_id, _COUNT_STEP_ADMIN_TIME_SELECT,
            {"type": "username", "value": name},
        )
        send_message(
            chat_id,
            t("count.name_got", name=html.escape(name)),
            parse_mode="HTML",
            reply_markup=_kb_admin_time_select(),
        )
        return True

    if step == _COUNT_STEP_ADMIN_CUSTOM_RANGE:
        pair = _split_range(text)
        if not pair:
            send_message(
                chat_id,
                t("count.range_invalid"),
                parse_mode="HTML",
                reply_markup=_kb_cancel_only(),
            )
            return True
        try:
            start_ms, end_ms = _custom_window_ms(pair[0], pair[1])
        except ValueError as ve:
            send_message(
                chat_id,
                t("count.error_wrap", error=html.escape(str(ve))),
                parse_mode="HTML",
                reply_markup=_kb_cancel_only(),
            )
            return True
        label = t("count.label_custom", d_from=pair[0], d_to=pair[1])
        _count_state_clear(user_id)
        _run_admin_query(chat_id, payload, start_ms, end_ms, label)
        return True

    return False


def _split_range(text: str):
    """
    Extract two YYYY-MM-DD date tokens from free-form text. Returns
    (from_str, to_str) or None. Robust to 'to', '-', en/em-dash and comma
    separators because it just pulls the two date-shaped tokens out.
    """
    import re
    dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", text or "")
    if len(dates) >= 2:
        return dates[0], dates[1]
    return None


def _parse_hours_input(text: str) -> int | None:
    """Parse a user-typed hours value. Returns int in [1,24] or None."""
    try:
        n = int(text)
    except (TypeError, ValueError):
        try:
            n = int(float(text))
        except (TypeError, ValueError):
            return None
    if 1 <= n <= 24:
        return n
    return None


# ---------------------------------------------------------------------------
# Query execution — fetch + format + multi-part send
# ---------------------------------------------------------------------------

def _run_user_query(user_id: int, chat_id: int, start_ms: int, end_ms: int, label: str) -> None:
    """Fetch and render a regular user's own history — per-username totals."""
    logger.info(f"/count user query user_id={user_id} window=[{start_ms},{end_ms}] label={label!r}")
    resp = get_claims_by_user(user_id, start_ms, end_ms)
    if not resp or not resp.get("ok"):
        send_message(
            chat_id,
            t("count.server_error"),
            parse_mode="HTML",
        )
        return
    text_parts = _format_aggregated_report(
        title=t("count.title_user", label=label),
        subtitle=None,
        rows=resp.get("by_username") or [],
        grand_total_usd=float(resp.get("total_usd") or 0),
        grand_total_claims=int(resp.get("total_records") or 0),
        personal=True,
    )
    _send_multi_part(chat_id, text_parts)


def _run_admin_query(chat_id: int, payload: dict, start_ms: int, end_ms: int, label: str) -> None:
    """Fetch and render an admin's by-license or by-username history."""
    qtype = payload.get("type")
    value = payload.get("value")
    logger.info(f"/count admin query type={qtype} value={value[:32]!r} window=[{start_ms},{end_ms}] label={label!r}")

    if qtype == "license":
        resp = get_claims_by_license(value, start_ms, end_ms)
        title = t("count.title_license", label=label)
        subtitle = t("count.subtitle_license", value=html.escape(value))
    elif qtype == "username":
        resp = get_claims_by_username(value, start_ms, end_ms)
        title = t("count.title_username", label=label)
        subtitle = t("count.subtitle_username", value=html.escape(value))
    else:
        send_message(chat_id, t("count.unknown_type"),
                     parse_mode="HTML")
        return

    if not resp or not resp.get("ok"):
        send_message(
            chat_id,
            t("count.server_error"),
            parse_mode="HTML",
        )
        return

    text_parts = _format_aggregated_report(
        title=title,
        subtitle=subtitle,
        rows=resp.get("by_username") or [],
        grand_total_usd=float(resp.get("total_usd") or 0),
        grand_total_claims=int(resp.get("total_records") or 0),
        personal=False,
    )
    _send_multi_part(chat_id, text_parts)


# ---------------------------------------------------------------------------
# Report formatter — produces a list of message strings under the
# Telegram 4096-char cap, with per-record blocks never split mid-record.
# ---------------------------------------------------------------------------

def _fmt_usd(x) -> str:
    """Money formatting tuned for engagement: thousands separators, 2 dp,
    but tiny non-zero amounts keep more precision so they don't read as $0.00."""
    x = float(x or 0)
    if 0 < x < 0.01:
        return f"${x:.4f}"
    return f"${x:,.2f}"


def _format_aggregated_report(
    title: str,
    subtitle: str | None,
    rows: list,
    grand_total_usd: float,
    grand_total_claims: int,
    personal: bool = False,
) -> list[str]:
    """
    Render a per-username totals report — usernames and the amount each
    claimed, with NO codes. `rows` is a list of {username, total_usd, count}
    (already sorted highest-first by the backend). Returns a list of message
    strings, each within the Telegram char cap; long lists split into parts.
    """
    header_lines = [f"<b>{html.escape(title)}</b>", "━━━━━━━━━━━━━━━"]
    if subtitle:
        header_lines.append(subtitle)
    header_lines.append("")  # blank line
    base_header = "\n".join(header_lines)

    # ── Empty window ──
    if not rows or grand_total_claims == 0:
        empty = base_header + (
            t("count.empty_personal") if personal else t("count.empty_other")
        ) + t("count.updated_suffix")
        return [empty]

    # ── Footer (last part only): grand totals ──
    user_word = (t("count.word_account_one") if len(rows) == 1
                 else t("count.word_account_other"))
    claim_word = (t("count.word_claim_one") if grand_total_claims == 1
                  else t("count.word_claim_other"))
    footer = t(
        "count.footer",
        usd=_fmt_usd(grand_total_usd),
        claims=grand_total_claims,
        claim_word=claim_word,
        users=len(rows),
        user_word=user_word,
    )

    # ── Per-username blocks (engaging: medals for the top 3) ──
    medals = ["🥇", "🥈", "🥉"]
    blocks: list[str] = []
    for i, row in enumerate(rows):
        uname = html.escape(str(row.get("username") or "?"))
        amt = _fmt_usd(row.get("total_usd"))
        cnt = int(row.get("count") or 0)
        claims_txt = (t("count.claims_count_one", n=cnt) if cnt == 1
                      else t("count.claims_count_other", n=cnt))
        if personal:
            blocks.append(t("count.block_personal", uname=uname, amt=amt, claims_txt=claims_txt))
        else:
            rank = medals[i] if i < 3 else f"<b>#{i + 1}</b>"
            blocks.append(t("count.block_ranked", rank=rank, uname=uname, amt=amt, claims_txt=claims_txt))

    # ── Pack blocks into parts under the budget (footer on last part only) ──
    msg_budget = _MAX_TG_MSG_CHARS - _PART_SUFFIX_RESERVE - len(footer) - 8
    parts: list[list[str]] = [[]]
    current_size = len(base_header)
    for block in blocks:
        block_size = len(block) + 2  # "\n\n" separator
        if parts[-1] and current_size + block_size > msg_budget:
            parts.append([])
            current_size = len(base_header)
        parts[-1].append(block)
        current_size += block_size

    total_parts = len(parts)
    final_messages: list[str] = []
    for idx, part_blocks in enumerate(parts, start=1):
        if total_parts > 1:
            header = t("count.part_header", title=html.escape(title),
                       idx=idx, total=total_parts)
            if subtitle:
                header += subtitle + "\n"
            header += "\n"
        else:
            header = base_header
        msg = header + "\n\n".join(part_blocks)
        if idx == total_parts:
            msg += "\n\n" + footer
        final_messages.append(msg)
    return final_messages


def _format_report(
    title: str,
    subtitle: str | None,
    records: list,
    total_usd: float,
    total_records: int,
    is_admin_view: bool,
) -> list[str]:
    """
    Returns a list of strings, each ≤ _MAX_TG_MSG_CHARS. Layout:

      <title>
      ━━━━━━━━━━━━━━━
      [<subtitle>]
      <empty line>
      <record block>
      <record block>
      ...
      <empty line>
      <summary footer: totals>

    For multi-part messages, the title becomes "<title> — Part X/Y"
    in each part. The summary footer is placed in the LAST part only.
    """
    # ─── Build the header (used at the top of every part) ───────────────
    header_lines = [
        f"<b>{html.escape(title)}</b>",
        "━━━━━━━━━━━━━━━",
    ]
    if subtitle:
        header_lines.append(subtitle)
    header_lines.append("")  # blank line
    base_header = "\n".join(header_lines)

    # ─── Build the footer (summary; only in the last part) ─────────────
    if total_records == 0:
        # Empty result → single-message report
        empty_msg = base_header + (
            "<i>No claims recorded in this window.</i>\n\n"
            "🕐 Updated just now"
        )
        return [empty_msg]

    footer = (
        "━━━━━━━━━━━━━━━\n"
        f"📦 <b>{total_records}</b> claim{'s' if total_records != 1 else ''}"
        f"  •  💰 <b>${total_usd:.4f}</b>"
    )

    # ─── Build per-record blocks ────────────────────────────────────────
    blocks: list[str] = []
    for rec in records:
        blocks.append(_format_record_block(rec, show_username=is_admin_view))

    # ─── Pack blocks into messages under the budget ─────────────────────
    # We reserve room for the per-part "— Part X/Y" suffix AND the footer
    # (which only goes on the last part). The packing is a first-fit:
    # always pack into current part; if it overflows, open a new part.
    msg_budget = _MAX_TG_MSG_CHARS - _PART_SUFFIX_RESERVE - len(footer) - 8

    parts: list[list[str]] = [[]]
    current_size = len(base_header)
    for block in blocks:
        block_size = len(block) + 2  # for the "\n\n" separator
        if parts[-1] and current_size + block_size > msg_budget:
            parts.append([])
            current_size = len(base_header)
        parts[-1].append(block)
        current_size += block_size

    # ─── Render each part into a final message string ───────────────────
    total_parts = len(parts)
    final_messages: list[str] = []
    for idx, part_blocks in enumerate(parts, start=1):
        if total_parts > 1:
            # Inject "— Part X/Y" into the title header line
            header = (
                f"<b>{html.escape(title)} — Part {idx}/{total_parts}</b>\n"
                "━━━━━━━━━━━━━━━\n"
            )
            if subtitle:
                header += subtitle + "\n"
            header += "\n"
        else:
            header = base_header

        body = "\n\n".join(part_blocks)
        msg = header + body

        # Footer ONLY on the last part
        if idx == total_parts:
            msg += "\n\n" + footer

        final_messages.append(msg)

    return final_messages


def _format_record_block(rec: dict, show_username: bool) -> str:
    """One record formatted as a compact 2-3 line block."""
    code = html.escape(str(rec.get("code") or "?"))
    amount_usd = float(rec.get("amount_usd") or 0)
    currency = html.escape(str(rec.get("currency") or ""))
    orig = float(rec.get("original_amount") or 0)
    ts = float(rec.get("ts") or 0)
    rel = _format_relative_time(ts)

    if show_username:
        username = html.escape(str(rec.get("username") or "?"))
        return (
            f"👤 <b>{username}</b>  •  🎟 <code>{code}</code>\n"
            f"   💰 <b>${amount_usd:.4f}</b>"
            f"  ({orig:g} {currency})  •  🕐 {rel}"
        )
    return (
        f"🎟 <code>{code}</code>  •  💰 <b>${amount_usd:.4f}</b>"
        f"  ({orig:g} {currency})\n"
        f"   🕐 {rel}"
    )


def _format_relative_time(ts: float) -> str:
    """Human-friendly relative time, e.g. '2h 15m ago', '45s ago'."""
    if not ts:
        return "?"
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m {delta % 60}s ago"
    hours = delta // 3600
    minutes = (delta % 3600) // 60
    return f"{hours}h {minutes}m ago"


def _send_multi_part(chat_id: int, parts: list[str]) -> None:
    """
    Send each part as a separate message with a small inter-send gap
    so Telegram clients render them in order on slower connections.
    Per-send try/except so a mid-list failure doesn't cancel the rest.
    """
    for idx, msg in enumerate(parts, start=1):
        try:
            send_message(chat_id, msg, parse_mode="HTML")
        except Exception as exc:
            logger.warning(f"/count part {idx}/{len(parts)} send failed: {exc}")
        # Small spacing between parts to keep order on slow networks
        if idx < len(parts):
            time.sleep(0.05)
