"""
Thread-safe in-memory license cache for the Telegram bot.

Stores the mapping: telegram_id ↔ license data.

Two lookup indexes are maintained in sync:
  _by_telegram_id : { telegram_id (int) → LicenseEntry }
  _by_license_key : { license_key (str) → telegram_id (int) }

Loaded from the main backend on startup via GET /api/xr9k/lic/list.
Kept current via /nx/v3/lic-sync push notifications from the backend scanner.

This cache is AUTHORITATIVE within the bot process. All command handlers
check it first. A cache miss triggers a live backend fetch (get_license_info).

_ready flag:
  Starts False. Set to True once load_all_from_backend() completes —
  whether the load succeeded (has entries) or failed (empty cache).
  An empty cache is safe; all handlers fall back to live backend fetches.
  is_ready() returning False permanently blocks every bot command, so
  mark_ready() must always be called after the startup attempt finishes.
"""

import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

_BACKEND_TIMEOUT = 10  # seconds


class LicenseEntry:
    """Lightweight data container for a single license record."""

    __slots__ = (
        "license_key",
        "telegram_id",
        "active",
        "theclaimers_count",
        "active_now",
        "maximum_usernames",
        "available_balance",
        "deduction_percentage",
        "language",
    )

    def __init__(
        self,
        license_key: str,
        telegram_id: int,
        active: bool,
        theclaimers_count: int = 0,
        maximum_usernames: int = 100,
        active_now: int = 0,
        available_balance: float = 0.0,
        deduction_percentage=None,
        language=None,
    ):
        self.license_key = license_key
        self.telegram_id = int(telegram_id)
        self.active = bool(active)
        self.theclaimers_count = int(theclaimers_count)
        self.active_now = int(active_now)
        self.maximum_usernames = int(maximum_usernames)
        # Prepaid balance (USD). None-safe: a missing/null value means 0.
        self.available_balance = (
            float(available_balance) if available_balance is not None else 0.0
        )
        # Per-claim deduction rate in percent. None => "After Claims Payment".
        self.deduction_percentage = (
            float(deduction_percentage) if deduction_percentage is not None else None
        )
        # Preferred bot UI language (ISO 639-1) or None => auto-detect from the
        # Telegram client's language_code. Empty/falsy is normalized to None.
        self.language = language if language else None

    def to_dict(self) -> dict:
        return {
            "license_key": self.license_key,
            "telegram_id": self.telegram_id,
            "active": self.active,
            "theclaimers_count": self.theclaimers_count,
            "active_now": self.active_now,
            "maximum_usernames": self.maximum_usernames,
            "available_balance": self.available_balance,
            "deduction_percentage": self.deduction_percentage,
            "language": self.language,
        }


class LicenseCache:
    """Thread-safe dual-index in-memory cache."""

    def __init__(self):
        self._lock = threading.RLock()
        self._by_tid: dict[int, LicenseEntry] = {}    # telegram_id → entry
        self._by_key: dict[str, int] = {}              # license_key → telegram_id
        # _ready: False until startup load attempt finishes (success OR failure).
        # NEVER left False permanently — that would block all bot commands forever.
        self._ready: bool = False

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def mark_ready(self) -> None:
        """
        Signal that the startup load attempt has completed.
        Must be called regardless of whether the load succeeded.
        """
        with self._lock:
            self._ready = True

    def is_ready(self) -> bool:
        """
        Returns True once the startup load has finished (cache may be empty).
        Returns False only during the initial 3-second startup window.
        """
        with self._lock:
            return self._ready

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_by_telegram_id(self, telegram_id: int) -> LicenseEntry | None:
        with self._lock:
            return self._by_tid.get(int(telegram_id))

    def get_by_license_key(self, license_key: str) -> LicenseEntry | None:
        with self._lock:
            tid = self._by_key.get(license_key)
            if tid is None:
                return None
            return self._by_tid.get(tid)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def set(
        self,
        telegram_id: int,
        license_key: str,
        active: bool,
        theclaimers_count: int = 0,
        maximum_usernames: int = 100,
        active_now: int = 0,
        available_balance: float = 0.0,
        deduction_percentage=None,
        language=None,
    ) -> LicenseEntry:
        """Upsert a license entry. Handles key changes atomically."""
        tid = int(telegram_id)
        with self._lock:
            existing = self._by_tid.get(tid)
            if existing and existing.license_key != license_key:
                self._by_key.pop(existing.license_key, None)

            entry = LicenseEntry(
                license_key=license_key,
                telegram_id=tid,
                active=active,
                theclaimers_count=theclaimers_count,
                maximum_usernames=maximum_usernames,
                active_now=active_now,
                available_balance=available_balance,
                deduction_percentage=deduction_percentage,
                language=language,
            )
            self._by_tid[tid] = entry
            self._by_key[license_key] = tid
            return entry

    def set_from_dict(self, telegram_id: int, data: dict) -> LicenseEntry:
        """Convenience wrapper that accepts a dict from backend JSON."""
        return self.set(
            telegram_id=telegram_id,
            license_key=data["license_key"],
            active=data.get("active", False),
            theclaimers_count=data.get("theclaimers_count", 0),
            maximum_usernames=data.get("maximum_usernames", 100),
            active_now=data.get("active_now", 0),
            available_balance=data.get("available_balance", 0.0),
            deduction_percentage=data.get("deduction_percentage", None),
            language=data.get("language"),
        )

    def deactivate(self, license_key: str) -> None:
        """Mark a license as inactive without removing it from cache."""
        with self._lock:
            tid = self._by_key.get(license_key)
            if tid and tid in self._by_tid:
                self._by_tid[tid].active = False
                logger.info(f"Cache: deactivated {license_key[:20]}...")

    def activate(self, license_key: str) -> None:
        """Mark a license as active."""
        with self._lock:
            tid = self._by_key.get(license_key)
            if tid and tid in self._by_tid:
                self._by_tid[tid].active = True
                logger.info(f"Cache: activated {license_key[:20]}...")

    def remove_by_license_key(self, license_key: str) -> None:
        """Fully remove a license (e.g. on deletion by admin)."""
        with self._lock:
            tid = self._by_key.pop(license_key, None)
            if tid:
                self._by_tid.pop(tid, None)
                logger.info(f"Cache: removed {license_key[:20]}...")

    def size(self) -> int:
        with self._lock:
            return len(self._by_tid)

    # ------------------------------------------------------------------
    # Backend load
    # ------------------------------------------------------------------

    def load_all_from_backend(self) -> bool:
        """
        Bulk-load all licenses from the main backend on startup.

        Returns True on success, False on any error.

        IMPORTANT: This method calls mark_ready() on EVERY exit path
        (success, env-var missing, HTTP error, timeout, exception).
        This guarantees the bot is never permanently stuck in "starting up"
        mode due to a transient backend issue at boot time.
        """
        backend_url = os.environ.get("BACKEND_INTERNAL_URL", "").rstrip("/")
        secret = os.environ.get("INTERNAL_API_SECRET", "")

        if not backend_url or not secret:
            logger.warning(
                "BACKEND_INTERNAL_URL or INTERNAL_API_SECRET missing "
                "— license cache not pre-loaded; bot will fetch on demand"
            )
            self.mark_ready()  # Mark ready so commands aren't permanently blocked
            return False

        try:
            resp = requests.get(
                f"{backend_url}/api/xr9k/lic/list",
                headers={"x-internal-token": secret},
                timeout=_BACKEND_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.error(
                    f"load_all_from_backend: backend returned HTTP {resp.status_code}"
                )
                self.mark_ready()  # Mark ready; empty cache is safe
                return False

            data = resp.json()
            licenses: list = data.get("licenses", [])

            with self._lock:
                self._by_tid.clear()
                self._by_key.clear()
                for lic in licenses:
                    # Route through the single population path (set_from_dict ->
                    # set) so every field lives in exactly ONE construction site;
                    # a future field (e.g. language) added there is picked up here
                    # automatically instead of being silently dropped on the bulk
                    # startup load. _lock is an RLock, so re-acquiring it inside
                    # set() while held here is safe.
                    self.set_from_dict(int(lic["telegram_id"]), lic)

            self.mark_ready()  # Success path — mark ready with real data
            logger.info(f"License cache loaded: {len(licenses)} entries from backend")
            return True

        except requests.exceptions.Timeout:
            logger.error(
                f"load_all_from_backend: request timed out after {_BACKEND_TIMEOUT}s"
            )
            self.mark_ready()  # Mark ready; empty cache is safe
            return False
        except Exception as exc:
            logger.error(f"load_all_from_backend: {exc}", exc_info=True)
            self.mark_ready()  # Mark ready; empty cache is safe
            return False


# Module-level singleton
license_cache = LicenseCache()
