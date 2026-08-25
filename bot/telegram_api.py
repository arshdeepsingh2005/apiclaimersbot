"""
Thin synchronous wrapper around the Telegram Bot API.

Design decisions:
- Uses `requests` directly (no python-telegram-bot) so we stay fully
  compatible with Flask's synchronous request handling and gunicorn's
  threading model — no async event-loop friction.
- Every call has a hard timeout so a slow Telegram response never hangs
  a gunicorn worker indefinitely.
- All errors are logged and silently swallowed; Telegram delivery is
  best-effort and should never crash the bot service.

parse_mode rules (Telegram API):
  Valid values:  "HTML"  |  "Markdown"  |  "MarkdownV2"
  Invalid:       None / null / "" / any other string → 400 Bad Request
  Rule:          Only include parse_mode in the payload when it is a
                 non-empty string. Omitting the key entirely is safe and
                 means Telegram treats the message as plain text.
"""

import contextvars
import logging
import os
from contextlib import contextmanager

import requests

logger = logging.getLogger(__name__)

# Which bot token the CURRENT request/thread should send with. Empty default =>
# fall back to TELEGRAM_BOT_TOKEN (Bot 1 / main). The admin bot (Bot 2) binds its
# own token per request via use_token(). Because each Telegram update is processed
# in its own thread (and the /nx/v3/push route runs in a reused gunicorn worker
# thread), callers MUST use the use_token() context manager so the value is reset
# afterwards and can never leak into the next request on a reused thread.
_current_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tg_token", default=""
)


@contextmanager
def use_token(token: str):
    """Bind `token` as the send token for the duration of the block, then reset.

    Set + reset (not a bare .set) so a reused worker thread never inherits a
    stale token. An empty/falsy token means 'use the main-token fallback'.
    """
    tok = _current_token.set(token or "")
    try:
        yield
    finally:
        _current_token.reset(tok)

# Hard timeout for every outbound Telegram API call (seconds)
_TIMEOUT = 10

# Longer timeout for file uploads (sendDocument) — multipart uploads take
# more time than a small JSON message, especially on a cold connection.
_UPLOAD_TIMEOUT = 30

# Valid parse modes accepted by Telegram (case-sensitive)
_VALID_PARSE_MODES = {"HTML", "Markdown", "MarkdownV2"}


def _token() -> str:
    # Per-request override (admin Bot 2) wins; otherwise the main Bot 1 token.
    return _current_token.get() or os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


def _post(method: str, payload: dict) -> dict:
    """POST to Telegram API; return parsed JSON or empty dict on error."""
    try:
        resp = requests.post(
            _api_url(method),
            json=payload,
            timeout=_TIMEOUT,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.warning(f"Telegram/{method} not-ok: {data}")
        return data
    except requests.exceptions.Timeout:
        logger.error(f"Telegram/{method}: request timed out")
        return {}
    except Exception as exc:
        logger.error(f"Telegram/{method}: {exc}")
        return {}


def _get(method: str, params: dict | None = None) -> dict:
    """GET from Telegram API."""
    try:
        resp = requests.get(
            _api_url(method),
            params=params or {},
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.error(f"Telegram/{method}: {exc}")
        return {}


def _apply_parse_mode(payload: dict, parse_mode: str | None) -> None:
    """
    Add parse_mode to payload only when it is a known-valid string.

    Telegram rejects the request with 400 if parse_mode is present but
    set to null, an empty string, or any unrecognised value. Omitting
    the key entirely is the correct way to send plain-text messages.
    """
    if parse_mode and parse_mode in _VALID_PARSE_MODES:
        payload["parse_mode"] = parse_mode
    elif parse_mode and parse_mode not in _VALID_PARSE_MODES:
        # Log a warning so bad values are visible during development
        logger.warning(
            f"Invalid parse_mode={parse_mode!r} — omitting from payload. "
            f"Valid values: {_VALID_PARSE_MODES}"
        )
    # None → omit key silently (plain-text message)


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

def send_message(
    chat_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
) -> dict:
    """
    Send a text message to a chat.

    Pass parse_mode=None for plain-text messages (no HTML/Markdown).
    Default is "HTML" which is what almost every handler uses.
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
    }
    _apply_parse_mode(payload, parse_mode)
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return _post("sendMessage", payload)


def send_video(
    chat_id: int,
    video: str,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
    reply_markup: dict | None = None,
    supports_streaming: bool = True,
) -> dict:
    """
    Send a video by file_id (or a direct URL) via Telegram sendVideo.

    `video` is a Telegram file_id captured from a prior upload — so this is a
    plain JSON POST (no file bytes, no upload), which makes it instant and
    keeps the media on Telegram's CDN. parse_mode applies only to the caption
    and is added only when a caption is present. Best-effort like the rest of
    this module: a failure is logged and returns {} so it never crashes the
    caller (check the returned dict's "ok" if you need delivery status).
    """
    payload: dict = {"chat_id": chat_id, "video": video}
    if supports_streaming:
        payload["supports_streaming"] = True
    if caption:
        payload["caption"] = caption
        _apply_parse_mode(payload, parse_mode)
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("sendVideo", payload)


def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    reply_markup: dict | None = None,
) -> dict:
    """
    Edit the text of an already-sent message.

    Pass parse_mode=None for plain-text edit.
    """
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    _apply_parse_mode(payload, parse_mode)
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("editMessageText", payload)


def delete_message(chat_id: int, message_id: int) -> dict:
    """Delete a message (best-effort). Used to remove a sensitive reply (e.g. a
    pasted API token) from the chat after it has been processed."""
    return _post("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def send_document(
    chat_id: int,
    filename: str,
    file_bytes: bytes,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
) -> dict:
    """
    Upload a file to a chat as a document (Telegram sendDocument).

    Unlike the JSON endpoints above, sendDocument is a multipart/form-data
    upload, so we can't use `_post` (which sends JSON). The file goes in
    `files`; chat_id/caption/parse_mode go in `data` (form fields).

    `file_bytes` is the raw file content, e.g. csv_text.encode("utf-8").
    parse_mode only applies to the caption, so it is added only when a
    caption is present. Best-effort like the rest of this module: errors
    are logged and swallowed, returning {} so a failed upload never
    crashes the caller. Check the returned dict's "ok" key if you need to
    know whether delivery succeeded.
    """
    files = {"document": (filename, file_bytes)}
    data: dict = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        _apply_parse_mode(data, parse_mode)
    try:
        resp = requests.post(
            _api_url("sendDocument"),
            data=data,
            files=files,
            timeout=_UPLOAD_TIMEOUT,
        )
        result = resp.json()
        if not result.get("ok"):
            logger.warning(f"Telegram/sendDocument not-ok: {result}")
        return result
    except requests.exceptions.Timeout:
        logger.error("Telegram/sendDocument: request timed out")
        return {}
    except Exception as exc:
        logger.error(f"Telegram/sendDocument: {exc}")
        return {}


def answer_callback_query(
    callback_query_id: str,
    text: str | None = None,
    show_alert: bool = False,
) -> dict:
    """Acknowledge an inline keyboard button tap."""
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    return _post("answerCallbackQuery", payload)


def set_webhook(webhook_url: str, secret_token: str) -> dict:
    """Register the bot's webhook URL with Telegram."""
    payload = {
        "url": webhook_url,
        "secret_token": secret_token,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
        "max_connections": 40,
    }
    result = _post("setWebhook", payload)
    logger.info(f"setWebhook → {result}")
    return result


def delete_webhook() -> dict:
    """Remove the registered webhook (useful for switching to polling in dev)."""
    return _post("deleteWebhook", {"drop_pending_updates": True})


def set_my_commands(commands: list[dict], language_code: str | None = None) -> dict:
    """Register the command menu shown when users type '/'.

    When ``language_code`` is set, registers the localized menu for clients on
    that language (Telegram scopes command lists by ``language_code``; the
    ``command`` names stay identical — only descriptions are translated)."""
    payload: dict = {"commands": commands}
    if language_code:
        payload["language_code"] = language_code
    return _post("setMyCommands", payload)


def set_chat_menu_button(url: str, text: str = "Open App") -> dict:
    """
    Set the persistent menu button (bottom-left of the chat) to launch the
    Telegram Mini App at ``url``. Applies to the current token's bot.
    """
    return _post("setChatMenuButton", {
        "menu_button": {"type": "web_app", "text": text, "web_app": {"url": url}},
    })


def get_webhook_info() -> dict:
    """Retrieve current webhook configuration from Telegram."""
    return _get("getWebhookInfo")


def get_me() -> dict:
    """Return basic info about the bot (for health/startup checks)."""
    return _get("getMe")
