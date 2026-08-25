"""
Per-user, per-command in-memory sliding-window rate limiter.

Sliding window — NOT fixed window. Each request is recorded at the exact
moment it arrives; the window "slides" relative to that point. This means
a user who sends exactly 5 /drop commands at t=0 must wait a full 60 s
before the 6th is accepted, regardless of minute boundaries.

Limits:
  /drop      → 5 per 60 s
  /reload    → 1 per 10 s
  /connected → 5 per 60 s
  /license   → 5 per 60 s
  /start     → 5 per 60 s
  /count     → 5 per 60 s
  /balance   → 5 per 60 s
  /topup     → 5 per 60 s

Thread safety: a single RLock protects the internal window dict.
Single-worker deployment on Render — no Redis needed. If multi-worker
is ever required, migrate this to Redis with atomic ZADD/ZCOUNT.
"""

import threading
import time
from collections import deque
from typing import Tuple

# (max_calls, window_seconds) per command
_LIMITS: dict[str, tuple[int, int]] = {
    "drop": (5, 60),
    "reload": (1, 10),
    "connected": (5, 60),
    "license": (5, 60),
    "start": (5, 60),
    "count": (5, 60),
    "balance": (5, 60),
    "topup": (5, 60),
    # Mini App surface (separate budgets from the bot commands, used by a
    # dedicated RateLimiter instance in miniapp_api.py; keys are hashed to ints).
    "app_auth": (30, 60),      # per-IP: auth + silent refresh
    "app_drop": (5, 60),       # per-tg_id AND per-jti
    "app_topup": (5, 60),      # per-tg_id AND per-jti
    "app_reload": (1, 10),     # per-tg_id AND per-jti
    "app_req": (120, 60),      # per-IP coarse cap on ALL Mini App API requests
}

# Cleanup stale keys every N checks (amortised O(1) per call)
_CLEANUP_EVERY = 500


class RateLimiter:
    """Sliding-window rate limiter keyed by (user_id, command)."""

    def __init__(self):
        self._lock = threading.RLock()
        # { (user_id: int, command: str): deque([timestamp, ...]) }
        self._windows: dict[tuple, deque] = {}
        self._call_count = 0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def check(self, user_id: int, command: str) -> Tuple[bool, int]:
        """Check and, if allowed, record a command attempt.

        Returns:
            (allowed, wait_seconds)
            - allowed=True  → request is permitted; attempt has been recorded.
            - allowed=False → request is denied; wait_seconds > 0.
        """
        command = command.lower()
        if command not in _LIMITS:
            # Not rate-limited (e.g. /start, /license)
            return True, 0

        max_calls, window_secs = _LIMITS[command]
        now = time.monotonic()
        cutoff = now - window_secs
        key = (int(user_id), command)

        with self._lock:
            self._call_count += 1

            if key not in self._windows:
                self._windows[key] = deque()

            dq = self._windows[key]

            # Evict expired timestamps
            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= max_calls:
                # Denied — calculate exact wait time
                # Oldest timestamp + window = when that slot expires
                oldest = dq[0]
                wait_secs = int(oldest + window_secs - now) + 1
                return False, max(1, wait_secs)

            # Allowed — record this attempt
            dq.append(now)

            # Amortised cleanup of stale keys
            if self._call_count % _CLEANUP_EVERY == 0:
                self._cleanup_locked(now)

            return True, 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cleanup_locked(self, now: float) -> None:
        """Remove keys whose windows are fully expired (called under lock)."""
        stale = []
        for key, dq in self._windows.items():
            _, window_secs = _LIMITS.get(key[1], (0, 60))
            cutoff = now - window_secs
            while dq and dq[0] < cutoff:
                dq.popleft()
            if not dq:
                stale.append(key)
        for key in stale:
            del self._windows[key]


# Module-level singleton — shared across all Flask request threads
rate_limiter = RateLimiter()
