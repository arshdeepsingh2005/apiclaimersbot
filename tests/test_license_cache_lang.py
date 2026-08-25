"""L1 regression: the `language` field must thread through the single cache
construction path (LicenseEntry -> set -> set_from_dict) and drive resolve_lang.

Guards HIGH-7 (a refresh must not silently drop the override) and the
resolution precedence (override > Telegram auto-detect > English).
"""

import sys
import types
import unittest

# license_cache imports `requests` at module load. It's present in CI/prod;
# stub it ONLY when genuinely absent (e.g. a minimal sandbox) so this test runs
# everywhere without masking a real install.
try:  # pragma: no cover - environment dependent
    import requests  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("requests")
    _stub.exceptions = types.SimpleNamespace(Timeout=Exception, RequestException=Exception)
    sys.modules["requests"] = _stub

from bot.license_cache import LicenseEntry, license_cache  # noqa: E402
from bot import i18n  # noqa: E402


class TestLicenseEntryLanguage(unittest.TestCase):
    def test_slot_and_to_dict(self):
        e = LicenseEntry("K", 1, True, language="ja")
        self.assertEqual(e.language, "ja")
        self.assertEqual(e.to_dict()["language"], "ja")

    def test_default_none(self):
        self.assertIsNone(LicenseEntry("K", 1, True).language)

    def test_empty_normalized_to_none(self):
        self.assertIsNone(LicenseEntry("K", 1, True, language="").language)


class TestCachePopulation(unittest.TestCase):
    def test_set_from_dict_threads_language(self):
        ent = license_cache.set_from_dict(9001, {"license_key": "K", "active": True, "language": "hi"})
        self.assertEqual(ent.language, "hi")

    def test_refresh_reflects_backend_not_stale(self):
        # Backend is authoritative: a refresh WITHOUT language clears the cache
        # copy (rather than dropping it silently while the DB still has it).
        license_cache.set_from_dict(9002, {"license_key": "K", "active": True, "language": "hi"})
        ent = license_cache.set_from_dict(9002, {"license_key": "K", "active": True})
        self.assertIsNone(ent.language)

    def test_set_direct_threads_language(self):
        d = license_cache.set(telegram_id=9003, license_key="K", active=True, language="pl")
        self.assertEqual(d.language, "pl")


class TestResolvePrecedence(unittest.TestCase):
    def test_override_beats_autodetect(self):
        license_cache.set_from_dict(9101, {"license_key": "K", "active": True, "language": "hi"})
        self.assertEqual(i18n.resolve_lang(9101, "en"), "hi")

    def test_autodetect_when_no_override(self):
        license_cache.set_from_dict(9102, {"license_key": "K", "active": True})
        self.assertEqual(i18n.resolve_lang(9102, "ja"), "ja")
        self.assertEqual(i18n.resolve_lang(9102, "pt-BR"), "pt")

    def test_unsupported_override_ignored(self):
        license_cache.set_from_dict(9103, {"license_key": "K", "active": True, "language": "zz"})
        self.assertEqual(i18n.resolve_lang(9103, "ja"), "ja")

    def test_no_entry_uses_autodetect(self):
        self.assertEqual(i18n.resolve_lang(999999, None), "en")
        self.assertEqual(i18n.resolve_lang(999999, "ko"), "ko")


if __name__ == "__main__":
    unittest.main()
