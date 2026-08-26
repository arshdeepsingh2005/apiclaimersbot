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
    # All per-user budgets are keyed by tg_id/jti (immune to shared carrier NAT)
    # and set FAR above any human's UI rate — normal use, incl. a 100-slot owner
    # and a 10-min payment poll, never reaches them; only scripted abuse does.
    "app_auth": (60, 60),      # per-IP, pre-auth only (initData is the real gate)
    "app_verify": (20, 60),    # per-tg_id — was UNLIMITED (missing key)
    "app_config": (30, 60),    # per-tg_id — was UNLIMITED (missing key)
    "app_order": (15, 60),     # per-tg_id — was UNLIMITED (missing key); mints invoices
    "app_drop": (20, 60),      # per-tg_id AND per-jti
    "app_topup": (15, 60),     # per-tg_id AND per-jti
    "app_reload": (3, 10),     # per-tg_id AND per-jti
    "app_req": (240, 60),      # coarse cap — keyed PER-USER in _api_guard (NAT-safe)
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

    def check(self, user_id: int, command: str, warn_ratio: float = None):
        """Check and, if allowed, record a command attempt.

        Returns (backward compatible):
            warn_ratio is None → (allowed, wait_seconds)
            warn_ratio set     → (allowed, wait_seconds, near) where ``near`` is
                                 True once the window is >= max_calls*warn_ratio
                                 full (used to proactively warn a user BEFORE the
                                 hard limit). Existing 2-tuple callers pass no
                                 warn_ratio and are unaffected.
        """
        want_near = warn_ratio is not None
        command = command.lower()
        if command not in _LIMITS:
            # Not rate-limited (e.g. /start, /license)
            return (True, 0, False) if want_near else (True, 0)

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
                wait_secs = max(1, wait_secs)
                return (False, wait_secs, True) if want_near else (False, wait_secs)

            # Allowed — record this attempt
            dq.append(now)
            near = want_near and (len(dq) >= max_calls * warn_ratio)

            # Amortised cleanup of stale keys
            if self._call_count % _CLEANUP_EVERY == 0:
                self._cleanup_locked(now)

            return (True, 0, near) if want_near else (True, 0)

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
