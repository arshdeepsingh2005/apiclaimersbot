"""
Admin-bot (Bot 2) routing decisions — PURELY DECLARATIVE.

This module holds only:
  * configuration     — ADMIN_BOT_TOKEN, the admin telegram-id allow-list,
  * readiness state    — admin_bot_ready (set by startup after Bot 2 registers),
  * routing decisions  — admin_enabled(), is_admin_id(), token_for().

It contains NO send logic and NO handler logic, so it stays trivially
unit-testable and single-responsibility. Both the /nx/v3/push relay and the
command-role gate import these helpers so they always agree on the decision.

Why a second bot: during peak the single Telegram token (~30 msg/s) is saturated
by per-user notifications, so admin command replies + the CODE BROADCAST DM queue
behind the flood. Bot 2 is a second token (independent rate bucket) served by a
second webhook in the SAME process. Admin-directed traffic rides Bot 2; the heavy
per-user flood stays on Bot 1.

Fail-safe: routing is gated on the RUNTIME `admin_bot_ready` flag, which is set
true ONLY after Bot 2's getMe + setWebhook both succeed at startup. If Bot 2 can't
initialize, admin_enabled() stays False and everything falls back to Bot 1 — never
a half-configured state where DMs/commands are routed to a bot whose webhook is
not live.
"""
import os

# Bot 2 token. Empty => the whole admin-bot feature is disabled (identical to the
# pre-feature single-bot behavior).
ADMIN_BOT_TOKEN: str = os.environ.get("ADMIN_BOT_TOKEN", "").strip()


def _parse_admin_ids() -> set[int]:
    """Allow-list of admin telegram-ids whose DMs route to Bot 2 and who may use
    the admin commands. Parsed from ADMIN_USER_ID (comma/space separated), mirroring
    the backend's ADMIN_TELEGRAM_ID parsing. Future admins/mods/devs: just add ids.
    """
    ids: set[int] = set()
    raw = (os.environ.get("ADMIN_USER_ID") or "").replace(",", " ").split()
    for part in raw:
        if part.isdigit():
            ids.add(int(part))
    return ids


_admin_ids: set[int] = _parse_admin_ids()

# Runtime readiness — flipped True by bot_wsgi startup only after Bot 2's
# getMe + setWebhook both succeed. Kept module-global so the request handlers and
# the startup thread (same process) share one value.
admin_bot_ready: bool = False


def mark_admin_bot_ready(ok: bool) -> None:
    global admin_bot_ready
    admin_bot_ready = bool(ok)


def admin_enabled() -> bool:
    """True only when Bot 2 is configured AND verified-ready at startup."""
    return bool(ADMIN_BOT_TOKEN) and admin_bot_ready


def is_admin_id(telegram_id) -> bool:
    try:
        return int(telegram_id) in _admin_ids
    except (TypeError, ValueError):
        return False


def admin_ids() -> set[int]:
    """The parsed admin allow-list (copy). Single source of truth for BOTH command
    authorization (_is_admin) and DM routing, so the two can never disagree."""
    return set(_admin_ids)


def token_for(telegram_id) -> str:
    """Which bot token a message to `telegram_id` should be sent with.

    Bot 2's token for an allow-listed admin when the admin bot is ready; otherwise
    the main token (empty string => telegram_api falls back to TELEGRAM_BOT_TOKEN).
    """
    if admin_enabled() and is_admin_id(telegram_id):
        return ADMIN_BOT_TOKEN
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")
