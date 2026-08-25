"""Unit tests for bot.i18n — pure stdlib, no network/handler imports."""

import unittest

from bot import i18n


class TestMapTgLang(unittest.TestCase):
    def test_regional_variants_map_down(self):
        self.assertEqual(i18n.map_tg_lang("pt-BR"), "pt")
        self.assertEqual(i18n.map_tg_lang("pt-PT"), "pt")
        self.assertEqual(i18n.map_tg_lang("zh-Hans"), "zh")
        self.assertEqual(i18n.map_tg_lang("zh-Hant"), "zh")
        self.assertEqual(i18n.map_tg_lang("en-us"), "en")

    def test_exact_supported_codes(self):
        for code in i18n.SUPPORTED:
            self.assertEqual(i18n.map_tg_lang(code), code)

    def test_unknown_and_empty_fall_back_to_english(self):
        for bad in ("xx", "klingon", "", None, "  ", "123"):
            self.assertEqual(i18n.map_tg_lang(bad), "en")


class TestTranslate(unittest.TestCase):
    def setUp(self):
        # Inject a controlled catalog so these tests don't depend on en.json content.
        self._saved = dict(i18n._MESSAGES)
        i18n._MESSAGES.clear()
        i18n._MESSAGES["en"] = {
            "greet": {"hi": "Hello {name}, you have {n} items"},
            "kt": {"x": "V=<code>{key}</code>"},
        }
        i18n._MESSAGES["ja"] = {"greet": {"hi": "こんにちは {name}"}}

    def tearDown(self):
        i18n._MESSAGES.clear()
        i18n._MESSAGES.update(self._saved)

    def test_basic_format(self):
        self.assertEqual(i18n.t("greet.hi", name="X", n=3), "Hello X, you have 3 items")

    def test_explicit_lang(self):
        self.assertEqual(i18n.t("greet.hi", lang="ja", name="X"), "こんにちは X")

    def test_missing_key_returns_key_no_raise(self):
        self.assertEqual(i18n.t("nope.absent"), "nope.absent")

    def test_missing_lang_falls_back_to_english(self):
        # 'ja' has no 'greet.bye'; fall back to en (also absent) -> key
        i18n._MESSAGES["en"]["greet"]["bye"] = "Bye {name}"
        self.assertEqual(i18n.t("greet.bye", lang="ja", name="Y"), "Bye Y")

    def test_unsupported_lang_uses_english(self):
        self.assertEqual(i18n.t("greet.hi", lang="zz", name="X", n=1),
                         "Hello X, you have 1 items")

    def test_mismatched_placeholder_does_not_crash(self):
        # translator typo: missing 'n' kwarg -> renders {n} literally, no KeyError
        self.assertEqual(i18n.t("greet.hi", name="X"), "Hello X, you have {n} items")

    def test_extra_kwargs_ignored(self):
        self.assertEqual(i18n.t("greet.hi", name="X", n=2, extra="z"),
                         "Hello X, you have 2 items")

    def test_placeholder_named_key_does_not_collide(self):
        # `key` is positional-only, so a {key} placeholder passed as key=... is
        # a format kwarg, not the message-path argument (regression: TypeError).
        self.assertEqual(i18n.t("kt.x", key="ABC"), "V=<code>ABC</code>")


class TestResolveLang(unittest.TestCase):
    def test_falls_back_to_telegram_autodetect(self):
        # No license override reachable here -> auto-detect from tg code.
        self.assertEqual(i18n.resolve_lang(999999, "ja"), "ja")
        self.assertEqual(i18n.resolve_lang(999999, "pt-BR"), "pt")
        self.assertEqual(i18n.resolve_lang(999999, "xx"), "en")
        self.assertEqual(i18n.resolve_lang(999999, None), "en")


class TestFlagsAndDisplayName(unittest.TestCase):
    def test_flag_for_every_supported_language(self):
        for code in i18n.SUPPORTED:
            self.assertIn(code, i18n.FLAGS, f"missing flag for {code}")

    def test_display_name_combines_flag_and_native(self):
        self.assertEqual(i18n.display_name("ja"), "🇯🇵 日本語")
        self.assertEqual(i18n.display_name("en"), "🇬🇧 English")
        self.assertEqual(i18n.display_name("pt"), "🇧🇷 Português")

    def test_display_name_unknown_falls_back_to_english(self):
        self.assertEqual(i18n.display_name("zz"), i18n.display_name("en"))


class TestContext(unittest.TestCase):
    def test_set_reset_lang(self):
        tok = i18n.set_lang("ja")
        try:
            self.assertEqual(i18n.get_lang(), "ja")
        finally:
            i18n.reset_lang(tok)
        self.assertEqual(i18n.get_lang(), "en")

    def test_unsupported_code_becomes_default(self):
        tok = i18n.set_lang("zz")
        try:
            self.assertEqual(i18n.get_lang(), "en")
        finally:
            i18n.reset_lang(tok)


if __name__ == "__main__":
    unittest.main()
