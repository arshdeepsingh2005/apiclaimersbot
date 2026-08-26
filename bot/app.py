"""
Flask application factory for The Claimers Telegram Bot (Service 2).

Endpoints:
  POST /wh/z7q2/tg        — Telegram webhook (validated by secret token)
  POST /nx/v3/push        — Backend → bot notification relay
  POST /nx/v3/lic-sync    — Backend → bot license cache update
  GET  /health            — Render/UptimeRobot health check

Security:
  - Telegram webhook: X-Telegram-Bot-Api-Secret-Token header (hmac.compare_digest)
  - Internal endpoints: x-internal-token header (hmac.compare_digest)
  - All validation failures → 401/403 (no detail leakage)

Update processing:
  Updates are dispatched to a daemon thread immediately after we return 200
  to Telegram. This prevents Telegram from retrying if a handler takes too
  long (e.g. /reload or /connected with 12-second backend waits).

  The is_ready() gate has been removed. It caused a persistent "Bot is
  starting up" loop because gunicorn's sync worker reads _ready from a
  different memory context than the startup daemon thread that writes it.
  All command handlers already handle an empty cache gracefully by falling
  back to live backend fetches, so the gate provided no real safety benefit.
"""

import hmac
import logging
import os
import threading

from flask import Flask, jsonify, request, send_from_directory

from bot.handlers import (
    handle_api,
    handle_api_callback,
    handle_balance,
    handle_callback_query,
    handle_claimcount,
    handle_claimdelay,
    handle_connected,
    handle_count,
    handle_count_callback,
    handle_count_text_input,
    handle_drop,
    handle_everycodesame,
    handle_force_reply,
    handle_language,
    handle_language_callback,
    handle_license,
    handle_licenselivecount,
    handle_maskcode,
    handle_reload,
    handle_start,
    handle_topup,
    handle_topup_callback,
    handle_topup_text_input,
    handle_valuefornextcode,
)
from bot import admin_routing, i18n, notify_queue
from bot.license_cache import license_cache
from bot.telegram_api import use_token

logger = logging.getLogger(__name__)

# Commands served by the admin bot (Bot 2). On the main bot (Bot 1) these are
# skipped when the admin bot is ready, and served as a fallback when it is not.
_ADMIN_CMDS = frozenset({
    "api", "valuefornextcode", "maskcode", "claimdelay",
    "licenselivecount", "claimcount", "everycodesame",
})


# ---------------------------------------------------------------------------
# Security validators
# ---------------------------------------------------------------------------

def _validate_webhook_secret(req) -> bool:
    """Validate X-Telegram-Bot-Api-Secret-Token header (main bot / Bot 1)."""
    provided = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _validate_admin_webhook_secret(req) -> bool:
    """Validate the admin bot (Bot 2) webhook secret token."""
    provided = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = os.environ.get("ADMIN_WEBHOOK_SECRET", "")
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _validate_internal_token(req) -> bool:
    """Validate x-internal-token header for Service 1 → Service 2 calls."""
    provided = req.headers.get("x-internal-token", "")
    expected = os.environ.get("INTERNAL_API_SECRET", "")
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


# ---------------------------------------------------------------------------
# Update router
# ---------------------------------------------------------------------------

def _role_tag(role: str) -> str:
    return "ADMIN BOT" if role == "admin" else "MAIN BOT"


def _process_update(update: dict, role: str = "main", token: str = "") -> None:
    """
    Route a Telegram update to the correct handler for the given bot ROLE.

    role="main"  -> Bot 1: user commands + /count + generic callbacks/flows.
    role="admin" -> Bot 2: admin commands + /api callbacks + /api ForceReply only.

    `token` binds the send token for this update so replies go out on the SAME bot
    that received it (Bot 2 replies with Bot 2's token). use_token() sets AND resets
    it, so nothing leaks. Runs in a daemon thread; all exceptions are caught here —
    an unhandled exception in a daemon thread would die silently otherwise.
    """
    try:
        with use_token(token):
            _route_update(update, role)
    except Exception:
        logger.exception(f"[{_role_tag(role)}] _process_update: unhandled exception")


def _route_update(update: dict, role: str) -> None:
    """Role-aware dispatch body (runs inside use_token())."""
    # Resolve the caller's language ONCE for this update (explicit override ->
    # Telegram auto-detect -> English) and bind it for t(). _process_update runs
    # on a fresh thread per update, so this ContextVar is naturally isolated
    # (same rationale as use_token's _current_token).
    _fu = (update.get("callback_query") or update.get("message") or {}).get("from") or {}
    try:
        i18n.set_lang(i18n.resolve_lang(int(_fu.get("id") or 0), _fu.get("language_code")))
    except Exception:
        pass

    # ── Callback queries (inline keyboard button taps) ─────────────────
    if "callback_query" in update:
        cq = update["callback_query"]
        from_user = cq.get("from", {})
        msg = cq.get("message", {})
        cb_data = cq.get("data", "") or ""
        cb_user_id = int(from_user["id"])
        cb_chat_id = int(msg.get("chat", {}).get("id", from_user["id"]))
        cb_msg_id = int(msg.get("message_id", 0))
        cb_id = str(cq["id"])

        # Admin bot (Bot 2): only the /api menu callbacks belong here.
        if role == "admin":
            if cb_data.startswith("api_"):
                handle_api_callback(
                    callback_id=cb_id, user_id=cb_user_id, chat_id=cb_chat_id,
                    message_id=cb_msg_id, data=cb_data,
                )
            return

        # Main bot (Bot 1).
        if cb_data.startswith("count_"):
            handle_count_callback(
                callback_id=cb_id, user_id=cb_user_id, chat_id=cb_chat_id,
                message_id=cb_msg_id, data=cb_data,
            )
            return
        if cb_data.startswith("topup_"):
            handle_topup_callback(
                callback_id=cb_id, user_id=cb_user_id, chat_id=cb_chat_id,
                message_id=cb_msg_id, data=cb_data,
            )
            return
        if cb_data.startswith("lang_"):
            handle_language_callback(
                callback_id=cb_id, user_id=cb_user_id, chat_id=cb_chat_id,
                message_id=cb_msg_id, data=cb_data,
            )
            return
        if cb_data.startswith("api_"):
            # /api lives on the admin bot; only handle here as a fallback when the
            # admin bot is not active, so /api never dead-ends.
            if not admin_routing.admin_enabled():
                handle_api_callback(
                    callback_id=cb_id, user_id=cb_user_id, chat_id=cb_chat_id,
                    message_id=cb_msg_id, data=cb_data,
                )
            return
        handle_callback_query(
            callback_id=cb_id, user_id=cb_user_id, chat_id=cb_chat_id,
            message_id=cb_msg_id, data=cb_data,
        )
        return

    # ── Text messages ──────────────────────────────────────────────────
    msg = update.get("message")
    if not msg:
        return  # Edited messages, polls, etc. — ignore silently

    from_user = msg.get("from", {})
    if from_user.get("is_bot"):
        return  # Never process messages from bots

    user_id = int(from_user.get("id", 0))
    chat = msg.get("chat", {}) or {}
    chat_id = int(chat.get("id", user_id))
    chat_type = chat.get("type", "private")
    text: str = msg.get("text", "").strip()
    first_name: str = from_user.get("first_name", "User")
    last_name: str = from_user.get("last_name", "") or ""
    profile_username: str = from_user.get("username", "") or ""

    if not text:
        return  # Media messages, stickers, etc.

    # Private-chat enforcement: reject group/supergroup/channel commands
    # to prevent license key leakage when the bot is added to a group.
    if chat_type != "private":
        if text.startswith("/"):
            try:
                from bot.telegram_api import send_message
                send_message(
                    chat_id,
                    "🔒 This bot only works in private chat. Please open me directly.",
                    parse_mode=None,
                )
            except Exception:
                pass
        return

    # ── ForceReply — reply to a bot message ────────────────────────────
    # Role-scoped: the admin bot resolves ONLY the /api ForceReply, the main bot
    # ONLY the /drop ForceReply. This prevents a cross-bot message_id collision
    # (both bots share the admin's chat_id but have independent id sequences)
    # from letting one bot consume the other's pending entry.
    if msg.get("reply_to_message"):
        reply_to_id = int(msg["reply_to_message"].get("message_id", 0))
        if reply_to_id:
            # Pass the reply's own id so the admin /api flow can delete a
            # pasted API token from the chat for confidentiality.
            handle_force_reply(user_id, chat_id, reply_to_id, text,
                               int(msg.get("message_id", 0)) or None, role=role)
            return

    # ── Plain text WITHIN an active /topup or /count flow ──────────────
    # Those multi-step flows live only on the main bot; the admin bot has none.
    if role == "main" and not text.startswith("/"):
        try:
            if handle_topup_text_input(user_id, chat_id, text):
                return
        except Exception as exc:
            logger.warning(f"topup text-input dispatch failed: {exc}", exc_info=True)
        try:
            if handle_count_text_input(user_id, chat_id, text):
                return
        except Exception as exc:
            logger.warning(f"count text-input dispatch failed: {exc}", exc_info=True)
        # Fall through to the standard "ignore plain text" behaviour below

    # ── Command routing ────────────────────────────────────────────────
    if not text.startswith("/"):
        return  # Plain text with no reply context — ignore

    parts = text.split(None, 1)
    cmd_raw = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    logger.info(f"[{_role_tag(role)}] cmd={cmd_raw!r}  user_id={user_id}  args={args!r}")

    # Role gate: the admin bot serves ONLY admin commands; the main bot serves
    # everything EXCEPT admin commands once the admin bot is ready (and serves
    # them as a fallback when it is not, preserving single-bot behavior).
    if role == "admin":
        if cmd_raw not in _ADMIN_CMDS:
            return
    elif cmd_raw in _ADMIN_CMDS and admin_routing.admin_enabled():
        return  # main bot defers admin commands to the admin bot

    # Slot-product command policy: normal users may use ONLY /start — everything
    # else (drop, manage, stats, buy, language) lives in the Mini App. Admins keep
    # /valuefornextcode + the admin command set. This replaces the legacy surface.
    if not admin_routing.is_admin_id(user_id) and cmd_raw != "start":
        return  # ignore /drop /reload /connected /balance /topup /count /license …

    if cmd_raw == "start":
        handle_start(user_id, chat_id, first_name, last_name, profile_username)

    elif cmd_raw == "drop":
        code = args if args else None
        handle_drop(user_id, chat_id, code)

    elif cmd_raw == "reload":
        handle_reload(user_id, chat_id)

    elif cmd_raw == "api":
        handle_api(user_id, chat_id)

    elif cmd_raw == "valuefornextcode":
        handle_valuefornextcode(user_id, chat_id, args)

    elif cmd_raw == "connected":
        handle_connected(user_id, chat_id)

    elif cmd_raw == "license":
        handle_license(user_id, chat_id)

    elif cmd_raw == "count":
        handle_count(user_id, chat_id)

    elif cmd_raw == "balance":
        handle_balance(user_id, chat_id)

    elif cmd_raw == "topup":
        handle_topup(user_id, chat_id)

    elif cmd_raw == "language":
        handle_language(user_id, chat_id)

    # ── Admin commands (served by the admin bot; on the main bot only as a
    # fallback when the admin bot is not ready). Each still runs its own
    # _is_admin() guard internally.
    elif cmd_raw == "licenselivecount":
        handle_licenselivecount(user_id, chat_id)

    elif cmd_raw == "claimcount":
        handle_claimcount(user_id, chat_id, args if args else None)

    elif cmd_raw == "maskcode":
        handle_maskcode(user_id, chat_id, args)

    elif cmd_raw == "claimdelay":
        handle_claimdelay(user_id, chat_id, args)

    elif cmd_raw == "everycodesame":
        handle_everycodesame(user_id, chat_id, args)

    # Unknown commands → silently ignored


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)

    # ── Health check ────────────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "theclaimers-bot",
            "cache_entries": license_cache.size(),
            "cache_ready": license_cache.is_ready(),
        }), 200

    # ── Telegram webhook — MAIN bot (Bot 1) ─────────────────────────────────
    @app.route("/wh/z7q2/tg", methods=["POST"])
    def telegram_webhook():
        """
        Receives all Telegram updates for the MAIN bot (Bot 1).

        Returns 200 immediately regardless of outcome — Telegram retries
        on non-200 responses, which would cause duplicate processing.
        """
        if not _validate_webhook_secret(request):
            logger.warning("[MAIN BOT] Webhook: rejected — invalid or missing secret token")
            return jsonify({"error": "Forbidden"}), 403

        update = request.get_json(force=True, silent=True)
        if not update:
            return jsonify({"ok": True}), 200

        update_id = update.get("update_id", "?")
        logger.info(f"[MAIN BOT] Webhook: received update_id={update_id}")

        # Dispatch to background thread — return 200 immediately.
        # role="main", token="" -> _token() falls back to TELEGRAM_BOT_TOKEN.
        threading.Thread(
            target=_process_update,
            args=(update, "main", ""),
            daemon=True,
            name=f"tg-update-{update_id}",
        ).start()

        return jsonify({"ok": True}), 200

    # ── Telegram webhook — ADMIN bot (Bot 2) ────────────────────────────────
    @app.route("/wh/admin/tg", methods=["POST"])
    def telegram_webhook_admin():
        """
        Receives Telegram updates for the ADMIN bot (Bot 2). Only registered when
        ADMIN_BOT_TOKEN + ADMIN_WEBHOOK_SECRET are set; dispatches with role="admin"
        so only admin commands / the /api flow are served, and replies go out on
        Bot 2's token.
        """
        if not _validate_admin_webhook_secret(request):
            logger.warning("[ADMIN BOT] Webhook: rejected — invalid or missing secret token")
            return jsonify({"error": "Forbidden"}), 403

        update = request.get_json(force=True, silent=True)
        if not update:
            return jsonify({"ok": True}), 200

        update_id = update.get("update_id", "?")
        logger.info(f"[ADMIN BOT] Webhook: received update_id={update_id}")

        threading.Thread(
            target=_process_update,
            args=(update, "admin", admin_routing.ADMIN_BOT_TOKEN),
            daemon=True,
            name=f"tg-admin-update-{update_id}",
        ).start()

        return jsonify({"ok": True}), 200

    # ── Backend → Bot notification relay ────────────────────────────────────
    @app.route("/nx/v3/push", methods=["POST"])
    def notify_push():
        """
        Called by Service 1 when it needs to send a Telegram message to a
        user (connect/disconnect notifications, claim results, reload alerts).

        Body: { "telegram_id": int, "message": str }
        """
        if not _validate_internal_token(request):
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True, silent=True) or {}
        telegram_id = data.get("telegram_id")
        message_text = str(data.get("message", "")).strip()

        if not telegram_id or not message_text:
            return jsonify({"error": "Missing telegram_id or message"}), 400

        if len(message_text) > 4096:
            return jsonify({"error": "message_too_long"}), 400

        # NON-BLOCKING: validate + enqueue only, then return 200 immediately.
        # The actual Telegram delivery (token routing, 429/retry handling, send)
        # happens in bot.notify_queue's background sender pool — NEVER on this
        # gunicorn request thread. This keeps the 8 request threads free during a
        # notification flood so both bots' webhooks (including admin commands) are
        # always accepted in parallel.
        #
        # Semantic note (intentional, verified safe): the backend invokes this via
        # a DETACHED curl (start_new_session, output discarded) and never reads the
        # response; no caller uses the relay helper's return value either. So
        # returning 200-on-enqueue (instead of on-send) changes nothing any caller
        # depends on. Delivery failures are logged loudly inside the worker.
        status = notify_queue.enqueue(telegram_id, message_text)
        return jsonify({"ok": True, "queued": status}), 200

    # ── License cache sync (from backend 10-second scanner) ─────────────────
    @app.route("/nx/v3/lic-sync", methods=["POST"])
    def lic_sync():
        """
        Called by Service 1 scanner when a license is activated, deactivated,
        or deleted.

        Body: {
          "license_key": str,
          "telegram_id": int,
          "active": bool | null,
          "theclaimers_count": int,
          "maximum_usernames": int
        }
        """
        if not _validate_internal_token(request):
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True, silent=True) or {}
        license_key = data.get("license_key", "").strip()
        raw_tid = data.get("telegram_id")
        active = data.get("active")

        if not license_key or raw_tid is None:
            return jsonify({"error": "Missing license_key or telegram_id"}), 400

        try:
            tid = int(raw_tid)

            if active is None:
                license_cache.remove_by_license_key(license_key)
                logger.info(f"lic-sync: removed {license_key[:20]}...")

            elif active is False:
                license_cache.deactivate(license_key)
                logger.info(f"lic-sync: deactivated {license_key[:20]}...")

            else:
                # available_balance / deduction_percentage are only present when
                # the backend has a value to report; when absent, preserve what
                # we already cached so a plain activation sync can't wipe them.
                existing = license_cache.get_by_license_key(license_key)
                if "available_balance" in data and data["available_balance"] is not None:
                    avail_bal = float(data["available_balance"])
                elif existing is not None:
                    avail_bal = existing.available_balance
                else:
                    avail_bal = 0.0
                if "deduction_percentage" in data:
                    dp_raw = data["deduction_percentage"]
                    ded_pct = float(dp_raw) if dp_raw is not None else None
                elif existing is not None:
                    ded_pct = existing.deduction_percentage
                else:
                    ded_pct = None
                # language is a per-user preference the scanner doesn't send;
                # preserve what we already cached so a plain activation sync
                # can't wipe the override (mirrors the balance/deduction rule).
                if "language" in data:
                    lang = data["language"] or None
                elif existing is not None:
                    lang = existing.language
                else:
                    lang = None

                license_cache.set(
                    telegram_id=tid,
                    license_key=license_key,
                    active=bool(active),
                    theclaimers_count=int(data.get("theclaimers_count", 0)),
                    maximum_usernames=int(data.get("maximum_usernames", 100)),
                    available_balance=avail_bal,
                    deduction_percentage=ded_pct,
                    language=lang,
                )
                logger.info(f"lic-sync: upserted {license_key[:20]}... active={active}")

            return jsonify({"ok": True}), 200

        except Exception as exc:
            logger.error(f"lic_sync: {exc}", exc_info=True)
            return jsonify({"error": "Internal error"}), 500

    # ── Public OxaPay webhook ───────────────────────────────────────────────
    @app.route("/pay/oxapay/webhook", methods=["POST"])
    def oxapay_webhook():
        """
        Public payment callback from OxaPay. Security + performance posture:
          * The raw body is HMAC-SHA512 verified against the Merchant API Key
            BEFORE any work; a forged callback is rejected in O(1) (no DB, no
            outbound call, no queueing) so it cannot amplify load.
          * This handler does NO network or DB work and credits NO money. It
            only enqueues the order id; a background worker asks the main
            backend to independently re-verify with OxaPay and credit
            idempotently. Returning fast keeps the single sync web worker free.
          * Non-2xx makes OxaPay retry; our pipeline is idempotent so that's safe.
        """
        from bot import oxapay as _oxapay
        from bot import payments as _payments
        from bot import slot_payments as _slot_payments

        raw_body = request.get_data() or b""
        signature = request.headers.get("HMAC") or request.headers.get("hmac") or ""

        if not _oxapay.verify_hmac(raw_body, signature):
            logger.warning("oxapay_webhook: rejected — bad/missing HMAC signature")
            _payments.admin_alert(
                "bad_signature",
                "🚨 <b>OxaPay webhook signature rejected</b>\n\n"
                "An incoming callback failed HMAC verification (possible forged "
                "callback or a misconfigured merchant key).",
            )
            return ("invalid signature", 401)

        try:
            import json as _json
            body = _json.loads(raw_body.decode("utf-8", "replace")) if raw_body else {}
        except Exception:
            logger.warning("oxapay_webhook: signature ok but body not JSON")
            return ("bad json", 400)
        if not isinstance(body, dict):
            return ("bad json", 400)

        # OxaPay v1 wraps the payload in a "data" envelope (identical to the
        # inquiry API), so order_id lives at body["data"]["order_id"], not at the
        # top level. Read from either place so we always find it. HMAC was
        # verified over the RAW body above, so this parsing change is safe.
        _inner = body.get("data") if isinstance(body.get("data"), dict) else {}
        order_id = str(
            body.get("order_id") or body.get("orderId")
            or _inner.get("order_id") or _inner.get("orderId") or ""
        ).strip()
        track_id = str(
            body.get("track_id") or body.get("trackId")
            or _inner.get("track_id") or _inner.get("trackId") or ""
        ).strip()
        if order_id:
            # Slot-sales: the paid order maps to a backend ApiOrder → verify with
            # OxaPay and allocate the slot (idempotent + payment-integrity gated
            # on the backend). Pass the track_id so the worker can re-verify.
            status = _slot_payments.enqueue_allocate(order_id, track_id)
            logger.info(
                f"oxapay_webhook: received order={order_id} track={track_id or '?'} enqueue={status}"
            )
        else:
            # No order id on the callback — reconciliation will still finalise it.
            logger.info("oxapay_webhook: signature ok, no order_id (reconciliation will recover)")
        return ("ok", 200)

    # ── Telegram Mini App — API blueprint (/app/api/v1/*) ────────────────────
    # Additive: does not touch webhook/command handling. Auth is initData→JWT
    # with a live authorization re-check on every request (see miniapp_api.py).
    from bot.miniapp_api import miniapp_bp
    app.register_blueprint(miniapp_bp)

    # ── Mini App SPA (served by the bot, same-origin as the API) ─────────────
    _APP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "app")

    def _spa_security_headers(resp):
        # Strict CSP: nothing external except the Telegram SDK; API is same-origin;
        # framing allowed ONLY inside Telegram (anti-clickjacking).
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "script-src 'self' https://telegram.org; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; "
            "frame-ancestors https://web.telegram.org https://*.telegram.org"
        )
        resp.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), usb=(), payment=()"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    @app.route("/app/")
    @app.route("/app")
    def miniapp_index():
        # index.html is always revalidated so a redeploy is picked up immediately.
        resp = send_from_directory(_APP_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return _spa_security_headers(resp)

    @app.route("/favicon.ico")
    def favicon():
        # Telegram's webview requests this; return 204 instead of a noisy 404.
        return ("", 204)

    @app.route("/app/static/<path:fname>")
    def miniapp_asset(fname):
        # Assets carry ?v= for cache-busting; ETag/conditional handled by Flask.
        # Short max-age so clients pick up a redeploy within minutes even if a
        # cached index.html briefly points at an old ?v=.
        resp = send_from_directory(_APP_DIR, fname)
        resp.headers["Cache-Control"] = "public, max-age=300"
        return _spa_security_headers(resp)

    return app
