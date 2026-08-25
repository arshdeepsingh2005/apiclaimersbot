"""
setup_webhook.py — One-shot utility to register the Telegram webhook.

Run this after deploying the bot to Render when BOT_PUBLIC_URL is known:

    python setup_webhook.py

Or to delete the webhook (switch to polling for local dev):

    python setup_webhook.py --delete

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET to be set
in the environment (or a .env file in the same directory).
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import requests

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
_BASE = f"https://api.telegram.org/bot{_TOKEN}"


def _check_token():
    if not _TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)


def set_webhook(url: str) -> None:
    _check_token()
    if not _SECRET:
        print("ERROR: TELEGRAM_WEBHOOK_SECRET is not set.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "url": url,
        "secret_token": _SECRET,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
        "max_connections": 40,
    }
    resp = requests.post(f"{_BASE}/setWebhook", json=payload, timeout=15)
    data = resp.json()
    if data.get("result"):
        print(f"✅  Webhook registered: {url}")
    else:
        print(f"❌  setWebhook failed: {data}", file=sys.stderr)
        sys.exit(1)


def delete_webhook() -> None:
    _check_token()
    resp = requests.post(
        f"{_BASE}/deleteWebhook",
        json={"drop_pending_updates": True},
        timeout=15,
    )
    data = resp.json()
    if data.get("result"):
        print("✅  Webhook deleted (polling mode)")
    else:
        print(f"❌  deleteWebhook failed: {data}", file=sys.stderr)
        sys.exit(1)


def get_info() -> None:
    _check_token()
    resp = requests.get(f"{_BASE}/getWebhookInfo", timeout=15)
    data = resp.json().get("result", {})
    print("── Webhook Info ──────────────────────────────────")
    print(f"  URL             : {data.get('url', '(none)')}")
    print(f"  Pending updates : {data.get('pending_update_count', 0)}")
    print(f"  Last error      : {data.get('last_error_message', 'none')}")
    print(f"  Has secret      : {'yes' if data.get('has_custom_certificate') else 'check manually'}")
    print("──────────────────────────────────────────────────")


def set_commands() -> None:
    _check_token()
    commands = [
        {"command": "start",     "description": "Open your dashboard"},
        {"command": "drop",      "description": "Broadcast a code to your claimers"},
        {"command": "reload",    "description": "Check reload availability"},
        {"command": "connected", "description": "View connected browsers & tokens"},
        {"command": "count",     "description": "Your claim totals (last 24h)"},
        {"command": "balance",   "description": "Prepaid balance & usage"},
        {"command": "topup",     "description": "Recharge your balance"},
        {"command": "license",   "description": "Your license & status"},
    ]
    resp = requests.post(
        f"{_BASE}/setMyCommands",
        json={"commands": commands},
        timeout=15,
    )
    data = resp.json()
    if data.get("result"):
        print("✅  Command menu registered")
    else:
        print(f"❌  setMyCommands failed: {data}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram webhook setup utility")
    parser.add_argument("--delete", action="store_true", help="Delete webhook (polling mode)")
    parser.add_argument("--info",   action="store_true", help="Print current webhook info")
    parser.add_argument("--commands", action="store_true", help="Register command menu only")
    parser.add_argument("--url", default="", help="Override webhook URL")
    args = parser.parse_args()

    if args.info:
        get_info()
        sys.exit(0)

    if args.delete:
        delete_webhook()
        sys.exit(0)

    if args.commands:
        set_commands()
        sys.exit(0)

    # Default: set webhook
    url = args.url
    if not url:
        bot_public = (
            os.environ.get("BOT_PUBLIC_URL", "").rstrip("/")
            or os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        )
        if not bot_public:
            print(
                "ERROR: provide --url or set BOT_PUBLIC_URL / RENDER_EXTERNAL_URL",
                file=sys.stderr,
            )
            sys.exit(1)
        url = f"{bot_public}/wh/z7q2/tg"

    get_info()           # Show current state before changing
    set_webhook(url)
    set_commands()
    get_info()           # Show final state
