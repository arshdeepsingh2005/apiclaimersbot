"""
Shared utility helpers for the Telegram bot service.

All timestamps returned in IST (UTC+5:30) to match the spec examples.
No third-party timezone library is needed — Python's stdlib datetime is sufficient.
"""

from datetime import datetime, timezone, timedelta

# IST = UTC + 5 hours 30 minutes
_IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> str:
    """Return current time as a formatted IST string, e.g. '2026-05-19 18:30:00 IST'."""
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S IST")


def shorten_key(key: str) -> str:
    """Return first-8 chars + '...' + last-4 chars of a license key.

    Example:
        'THECLAIMERS-14abb8fd-6f03-4a2b-8c91-cf44'
        → 'THECLAI...cf44'
    """
    if not key:
        return "N/A"
    if len(key) <= 12:
        return key
    return f"{key[:8]}...{key[-4:]}"


def format_time_left(seconds: int) -> str:
    """Human-readable 'Xm Ys left' string used in /reload responses."""
    secs = max(0, int(seconds))
    if secs == 0:
        return "Ready"
    minutes, sec = divmod(secs, 60)
    if minutes > 0:
        return f"{minutes}m {sec}s left"
    return f"{sec}s left"


def format_session_duration(seconds: int) -> str:
    """Convert seconds to 'Xh Ym' or 'Xm Ys' for disconnect notifications."""
    secs = max(0, int(seconds))
    if secs < 60:
        return f"{secs}s"
    minutes, rem = divmod(secs, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"
