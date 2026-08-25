"""
Gunicorn WSGI entry point for The Claimers Telegram Bot (Service 2).

Start command (Render):
    gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 60 bot_wsgi:app

CRITICAL — Single worker only:
    Rate limiter state lives in-process memory. Multiple workers would
    give each worker an independent rate limit counter, meaning a user
    could send 5×N /drop commands before being throttled.
    If horizontal scaling is ever required, migrate rate_limiter to Redis
    with ZADD/ZCOUNT atomic operations.

Startup sequence (runs in a daemon thread after gunicorn binds the port):
    1. Verify bot token (getMe)
    2. Load license cache from backend  ← mark_ready() ALWAYS called after
    3. Register webhook with Telegram
    4. Set command menu with Telegram
"""

import logging
import os
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()

# Configure logging before importing app modules so all loggers inherit this
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Enforce single-worker deployment — rate limiter + license cache live in
# in-process memory and would diverge across multiple workers.
_web_concurrency = os.environ.get("WEB_CONCURRENCY", "1")
try:
    if int(_web_concurrency) > 1:
        logger.error(
            f"WEB_CONCURRENCY={_web_concurrency} but the bot REQUIRES "
            "exactly 1 worker (rate limiter + cache are in-process). "
            "Set WEB_CONCURRENCY=1 in Render env or migrate to Redis."
        )
except ValueError:
    pass

from bot import admin_routing
from bot.app import create_app
from bot.license_cache import license_cache
from bot.telegram_api import (
    get_me,
    set_chat_menu_button,
    set_my_commands,
    set_webhook,
    use_token,
)

# Create the Flask application (this is what gunicorn imports)
app = create_app()


# ---------------------------------------------------------------------------
# Startup routine
# ---------------------------------------------------------------------------

def _startup() -> None:
    """
    Startup tasks executed in a daemon thread.

    The 3-second initial sleep gives gunicorn time to bind the port and
    complete its worker fork before we start making outbound HTTP requests.

    IMPORTANT: license_cache.mark_ready() is ALWAYS called at the end of
    this function, regardless of whether the backend load succeeded or failed.
    An empty cache is safe — all command handlers fall back to live backend
    fetches on cache miss. The alternative (never calling mark_ready on failure)
    permanently blocks all bot commands, which is worse than an empty cache.
    """
    time.sleep(3)

    logger.info("=" * 60)
    logger.info("THE CLAIMERS BOT — STARTUP")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Step 1: Verify bot token
    # ------------------------------------------------------------------
    try:
        me = get_me()
        if me.get("ok"):
            bot_info = me.get("result", {})
            logger.info(
                f"[MAIN BOT] identity: @{bot_info.get('username')} "
                f"(id={bot_info.get('id')})"
            )
        else:
            logger.error(
                "get_me() failed — check TELEGRAM_BOT_TOKEN. "
                f"Response: {me}"
            )
    except Exception as exc:
        logger.error(f"get_me() raised: {exc}")

    # ------------------------------------------------------------------
    # Step 2: Load license cache from backend
    #
    # mark_ready() is called in BOTH branches (success and failure) so
    # the webhook handler never gets permanently stuck showing
    # "Bot is starting up" to users.
    # ------------------------------------------------------------------
    logger.info("Loading license cache from backend...")
    try:
        success = license_cache.load_all_from_backend()
        if success:
            logger.info(f"License cache ready: {license_cache.size()} entries")
        else:
            logger.warning(
                "License cache pre-load failed — "
                "handlers will fetch from backend on first request"
            )
    except Exception as exc:
        logger.error(f"License cache load raised: {exc}", exc_info=True)
        success = False
    finally:
        # ALWAYS mark ready after the load attempt completes.
        # Without this, any load failure permanently blocks all commands.
        if not license_cache.is_ready():
            license_cache.mark_ready()
            if not success:
                logger.info(
                    "License cache marked ready (empty) — "
                    "bot will fetch license data from backend on demand"
                )

    # ------------------------------------------------------------------
    # Step 3: Register webhook with Telegram
    # ------------------------------------------------------------------
    bot_public_url = (
        os.environ.get("BOT_PUBLIC_URL", "").rstrip("/")
        or os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    )
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    if bot_public_url and webhook_secret:
        webhook_url = f"{bot_public_url}/wh/z7q2/tg"
        logger.info(f"[MAIN BOT] Registering webhook: {webhook_url}")
        try:
            wh_result = set_webhook(webhook_url, webhook_secret)
            if wh_result.get("result") is True:
                logger.info("[MAIN BOT] Webhook registered successfully")
            else:
                logger.error(f"Webhook registration failed: {wh_result}")
        except Exception as exc:
            logger.error(f"Webhook registration raised: {exc}")
    else:
        logger.warning(
            "BOT_PUBLIC_URL / RENDER_EXTERNAL_URL or TELEGRAM_WEBHOOK_SECRET "
            "not set — webhook NOT registered. "
            "Run setup_webhook.py manually after deployment."
        )

    # ------------------------------------------------------------------
    # Step 4: Register command menus (default English + per-language)
    #
    # Only DESCRIPTIONS are localized — command names stay identical, so routing
    # (which matches the literal command word) is unaffected. Telegram scopes
    # menus by language_code. Register the English/default menu FIRST (it must
    # always land), then each locale in isolation: one locale's failure logs and
    # continues, never blocking the others or aborting startup. Runs in this
    # background thread, so the extra calls never delay worker readiness.
    # ------------------------------------------------------------------
    from bot.i18n import SUPPORTED, log_validation, t

    # Validate the locale catalog once at boot — non-fatal (logs only).
    log_validation()

    # Slot product: normal users only ever need /start (the Mini App is the app).
    _USER_CMDS = ["start"]

    def _commands_for(lc: str) -> list:
        return [{"command": c, "description": t(f"cmd_desc.{c}", lang=lc)}
                for c in _USER_CMDS]

    try:
        cmd_result = set_my_commands(_commands_for("en"))
        if cmd_result.get("result") is True:
            logger.info("[MAIN BOT] Command menu registered successfully (default)")
        else:
            logger.warning(f"[MAIN BOT] Command menu registration result: {cmd_result}")
    except Exception as exc:
        logger.error(f"[MAIN BOT] Command menu registration raised: {exc}")

    for lc in SUPPORTED:
        if lc == "en":
            continue
        try:
            r = set_my_commands(_commands_for(lc), language_code=lc)
            if r.get("result") is not True:
                logger.warning(f"[MAIN BOT] Command menu ({lc}) result: {r}")
        except Exception as exc:
            logger.error(f"[MAIN BOT] Command menu ({lc}) raised: {exc}")

    # ------------------------------------------------------------------
    # Step 4a: Set the Mini App menu button (launches /app/ inside Telegram)
    # ------------------------------------------------------------------
    miniapp_base = (os.environ.get("MINIAPP_BASE_URL", "").rstrip("/") or bot_public_url)
    if miniapp_base:
        try:
            mb = set_chat_menu_button(f"{miniapp_base}/app/", "Open App")
            if mb.get("result") is True:
                logger.info("[MAIN BOT] Mini App menu button set")
            else:
                logger.warning(f"[MAIN BOT] Menu button result: {mb}")
        except Exception as exc:
            logger.error(f"[MAIN BOT] Menu button set raised: {exc}")
    else:
        logger.warning("[MAIN BOT] No base URL — Mini App menu button NOT set")

    # ------------------------------------------------------------------
    # Step 4b: Register the ADMIN bot (Bot 2), if configured.
    #
    # Same process, second Telegram token + second webhook path. All calls are
    # bound to ADMIN_BOT_TOKEN via use_token(). admin_bot_ready is set True ONLY
    # if getMe AND setWebhook both succeed — a DM only needs the token, but
    # RECEIVING admin commands needs the webhook, so both must be live or we fall
    # back entirely to Bot 1 (never a half-configured state).
    # ------------------------------------------------------------------
    if admin_routing.ADMIN_BOT_TOKEN:
        admin_secret = os.environ.get("ADMIN_WEBHOOK_SECRET", "")
        admin_getme_ok = False
        admin_webhook_ok = False
        with use_token(admin_routing.ADMIN_BOT_TOKEN):
            try:
                me2 = get_me()
                if me2.get("ok"):
                    admin_getme_ok = True
                    info2 = me2.get("result", {})
                    logger.info(
                        f"[ADMIN BOT] identity: @{info2.get('username')} (id={info2.get('id')})"
                    )
                else:
                    logger.error(f"[ADMIN BOT] get_me() failed — check ADMIN_BOT_TOKEN. Response: {me2}")
            except Exception as exc:
                logger.error(f"[ADMIN BOT] get_me() raised: {exc}")

            if admin_getme_ok and bot_public_url and admin_secret:
                admin_webhook_url = f"{bot_public_url}/wh/admin/tg"
                logger.info(f"[ADMIN BOT] Registering webhook: {admin_webhook_url}")
                try:
                    wh2 = set_webhook(admin_webhook_url, admin_secret)
                    if wh2.get("result") is True:
                        admin_webhook_ok = True
                        logger.info("[ADMIN BOT] Webhook registered successfully")
                    else:
                        logger.error(f"[ADMIN BOT] Webhook registration failed: {wh2}")
                except Exception as exc:
                    logger.error(f"[ADMIN BOT] Webhook registration raised: {exc}")
            elif admin_getme_ok:
                logger.error(
                    "[ADMIN BOT] BOT_PUBLIC_URL/RENDER_EXTERNAL_URL or ADMIN_WEBHOOK_SECRET "
                    "not set — admin webhook NOT registered."
                )

            # Bot 2's reduced menu — only the admin commands.
            if admin_getme_ok:
                try:
                    admin_commands = [
                        {"command": "api",              "description": "Manage your claimers (API/currency/filters)"},
                        {"command": "valuefornextcode", "description": "Persistent value override for codes"},
                        {"command": "maskcode",         "description": "Toggle first-claim code masking"},
                        {"command": "claimdelay",       "description": "First-claim notification delay (0–300s)"},
                        {"command": "licenselivecount", "description": "Per-license live connection snapshot"},
                        {"command": "claimcount",       "description": "Lifetime claim count for a code"},
                        {"command": "everycodesame",    "description": "Case-insensitive broadcast dedup on/off"},
                    ]
                    cmd2 = set_my_commands(admin_commands)
                    if cmd2.get("result") is True:
                        logger.info("[ADMIN BOT] Command menu registered successfully")
                    else:
                        logger.warning(f"[ADMIN BOT] Command menu registration result: {cmd2}")
                except Exception as exc:
                    logger.error(f"[ADMIN BOT] Command menu registration raised: {exc}")

        if admin_getme_ok and admin_webhook_ok:
            admin_routing.mark_admin_bot_ready(True)
            logger.info("[ADMIN BOT] READY — admin commands + admin DMs routed to Bot 2")
        else:
            admin_routing.mark_admin_bot_ready(False)
            logger.error(
                "[ADMIN BOT] ⚠ init FAILED — admin routing DISABLED, falling back to "
                "MAIN BOT for admin commands and admin DMs."
            )
    else:
        logger.info("[ADMIN BOT] ADMIN_BOT_TOKEN not set — admin bot disabled (single-bot mode).")

    # ------------------------------------------------------------------
    # Step 5: OxaPay payment workers (credit queue + reconciliation)
    #
    # NOT started here. They are started in gunicorn.conf.py `post_worker_init`
    # — i.e. INSIDE the forked gunicorn worker process. Starting them here (at
    # import time) ran them in the gunicorn MASTER, so the forked worker got a
    # SEPARATE copy of the credit queue: webhook-enqueued payments were never
    # dequeued and fell to the 7-minute reconciliation sweep. Threads do not
    # survive fork(), so post_worker_init is the correct place. Do NOT re-add a
    # start_workers() call here. See gunicorn.conf.py.
    # ------------------------------------------------------------------

    logger.info("=" * 60)
    logger.info("THE CLAIMERS BOT — READY")
    logger.info("=" * 60)


# Run startup asynchronously — gunicorn must not be blocked waiting for it
_startup_thread = threading.Thread(
    target=_startup,
    daemon=True,
    name="Bot-Startup",
)
_startup_thread.start()


# ---------------------------------------------------------------------------
# Gunicorn server hooks
# ---------------------------------------------------------------------------

def when_ready(server):
    """Called by gunicorn after the master process is ready."""
    server.log.info("Gunicorn master ready")


def worker_exit(server, worker):
    """Called when a gunicorn worker exits."""
    server.log.info(f"Worker {worker.pid} exited")
