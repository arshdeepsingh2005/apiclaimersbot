"""
Unit tests for bot/miniapp_auth.py — the Mini App security core.

Runs on the standard library only (no Flask/cryptography/PyJWT), so it executes
in any environment. Covers: initData HMAC validation (valid / tampered / stale /
malformed), session token round-trip, tamper/expiry rejection, JWT-to-JWT
refresh, the 24h hard cap, and admin recomputation on refresh.
"""
import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

# Make `bot` importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import miniapp_auth as A  # noqa: E402

BOT_TOKEN = "123456:TEST-bot-token-abcdefghijklmnopqrstuvwxyz"
SECRET = "unit-test-session-secret-do-not-use-in-prod"


def build_init_data(user: dict, *, auth_date: int, token: str = BOT_TOKEN,
                    extra: dict | None = None, bad_hash: bool = False) -> str:
    """Construct a correctly-signed initData string (or a deliberately bad one)."""
    fields = {"user": json.dumps(user, separators=(",", ":")),
              "auth_date": str(auth_date)}
    if extra:
        fields.update(extra)
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, dcs.encode(), hashlib.sha256).hexdigest()
    if bad_hash:
        h = ("0" * len(h)) if not h.startswith("0") else ("1" * len(h))
    fields["hash"] = h
    return urlencode(fields)


class InitDataTests(unittest.TestCase):
    def setUp(self):
        self.user = {"id": 4242, "first_name": "Test", "username": "tester"}
        self.now = 1_700_000_000

    def test_valid(self):
        init = build_init_data(self.user, auth_date=self.now)
        got = A.validate_init_data(init, bot_token=BOT_TOKEN, now=self.now)
        self.assertEqual(got["id"], 4242)
        self.assertIsInstance(got["id"], int)

    def test_tampered_field_fails(self):
        init = build_init_data(self.user, auth_date=self.now)
        # Flip a byte in the user payload AFTER signing → signature must fail.
        tampered = init.replace("Test", "Evil")
        with self.assertRaises(A.AuthError) as cm:
            A.validate_init_data(tampered, bot_token=BOT_TOKEN, now=self.now)
        self.assertEqual(cm.exception.code, "AUTH_INVALID")

    def test_tampered_hash_fails(self):
        init = build_init_data(self.user, auth_date=self.now, bad_hash=True)
        with self.assertRaises(A.AuthError) as cm:
            A.validate_init_data(init, bot_token=BOT_TOKEN, now=self.now)
        self.assertEqual(cm.exception.code, "AUTH_INVALID")

    def test_wrong_token_fails(self):
        init = build_init_data(self.user, auth_date=self.now)
        with self.assertRaises(A.AuthError):
            A.validate_init_data(init, bot_token="999:other-token", now=self.now)

    def test_stale_auth_date_fails(self):
        init = build_init_data(self.user, auth_date=self.now - 4000)
        with self.assertRaises(A.AuthError) as cm:
            A.validate_init_data(init, bot_token=BOT_TOKEN, now=self.now, max_age=3600)
        self.assertEqual(cm.exception.code, "AUTH_EXPIRED")

    def test_missing_hash_fails(self):
        init = urlencode({"user": json.dumps(self.user), "auth_date": str(self.now)})
        with self.assertRaises(A.AuthError):
            A.validate_init_data(init, bot_token=BOT_TOKEN, now=self.now)

    def test_empty_and_no_token(self):
        with self.assertRaises(A.AuthError):
            A.validate_init_data("", bot_token=BOT_TOKEN, now=self.now)
        init = build_init_data(self.user, auth_date=self.now)
        with self.assertRaises(A.AuthError):
            A.validate_init_data(init, bot_token="", now=self.now)


class SessionTokenTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_700_000_000

    def test_round_trip(self):
        issued = A.issue_session(4242, True, secret=SECRET, now=self.now)
        payload = A.verify_session(issued["token"], secret=SECRET, now=self.now + 60)
        self.assertEqual(payload["tg_id"], 4242)
        self.assertTrue(payload["is_admin"])
        self.assertEqual(payload["jti"], issued["jti"])

    def test_unique_jti(self):
        a = A.issue_session(1, False, secret=SECRET, now=self.now)
        b = A.issue_session(1, False, secret=SECRET, now=self.now)
        self.assertNotEqual(a["jti"], b["jti"])

    def test_tampered_token_fails(self):
        issued = A.issue_session(4242, False, secret=SECRET, now=self.now)
        header, body, sig = issued["token"].split(".")
        forged = f"{header}.{body}.{'A' * len(sig)}"
        with self.assertRaises(A.AuthError) as cm:
            A.verify_session(forged, secret=SECRET, now=self.now)
        self.assertEqual(cm.exception.code, "AUTH_INVALID")

    def test_wrong_secret_fails(self):
        issued = A.issue_session(4242, False, secret=SECRET, now=self.now)
        with self.assertRaises(A.AuthError):
            A.verify_session(issued["token"], secret="other-secret", now=self.now)

    def test_expired_token_fails(self):
        issued = A.issue_session(4242, False, secret=SECRET, now=self.now)
        later = self.now + A.SESSION_TTL_SECONDS + 1
        with self.assertRaises(A.AuthError) as cm:
            A.verify_session(issued["token"], secret=SECRET, now=later)
        self.assertEqual(cm.exception.code, "AUTH_EXPIRED")

    def test_malformed_token_fails(self):
        with self.assertRaises(A.AuthError):
            A.verify_session("not-a-jwt", secret=SECRET, now=self.now)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_700_000_000

    def test_refresh_keeps_session_start(self):
        first = A.issue_session(7, False, secret=SECRET, now=self.now)
        later = self.now + 25 * 60  # within TTL
        refreshed = A.refresh_session(first["token"], is_admin_now=False,
                                      secret=SECRET, now=later)
        self.assertEqual(refreshed["payload"]["sst"], first["payload"]["sst"])
        self.assertGreater(refreshed["payload"]["exp"], first["payload"]["exp"])

    def test_refresh_recomputes_admin(self):
        # Issued as admin; refreshed after demotion → new token is non-admin.
        first = A.issue_session(7, True, secret=SECRET, now=self.now)
        later = self.now + 60
        refreshed = A.refresh_session(first["token"], is_admin_now=False,
                                      secret=SECRET, now=later)
        payload = A.verify_session(refreshed["token"], secret=SECRET, now=later)
        self.assertFalse(payload["is_admin"])

    def test_refresh_blocked_after_hard_cap(self):
        first = A.issue_session(7, False, secret=SECRET, now=self.now)
        # Keep refreshing within TTL up to just before the cap, then past it.
        past_cap = self.now + A.SESSION_HARD_CAP_SECONDS + 1
        # A token issued at sst=now, verified at past_cap, would be expired first;
        # emulate a still-valid token near the cap by issuing at cap boundary.
        near = A.issue_session(7, False, secret=SECRET, now=past_cap - 60,
                               session_start=self.now)
        with self.assertRaises(A.AuthError) as cm:
            A.refresh_session(near["token"], is_admin_now=False,
                              secret=SECRET, now=past_cap)
        self.assertEqual(cm.exception.code, "AUTH_EXPIRED")

    def test_refresh_rejects_expired_token(self):
        first = A.issue_session(7, False, secret=SECRET, now=self.now)
        expired_at = self.now + A.SESSION_TTL_SECONDS + 1
        with self.assertRaises(A.AuthError):
            A.refresh_session(first["token"], is_admin_now=False,
                              secret=SECRET, now=expired_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
