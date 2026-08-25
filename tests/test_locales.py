"""Locale-catalog validation + English-value regression pins.

validate_locales() enforces: key parity vs en, placeholder-token parity, HTML
tag allowlist + parity. The English pins guard en.json against edits that would
change the current (byte-identical) English output.
"""

import unittest

from bot import i18n


class TestCatalogConsistency(unittest.TestCase):
    def test_english_loaded(self):
        self.assertTrue(i18n._MESSAGES.get("en"), "en.json must load and be non-empty")

    def test_no_validation_errors(self):
        errors, _warnings = i18n.validate_locales()
        self.assertEqual(errors, [], "locale catalog has consistency errors:\n" + "\n".join(errors))


class TestEnglishPins(unittest.TestCase):
    """Exact English output — must stay byte-identical to the pre-i18n bot."""

    def test_status(self):
        self.assertEqual(i18n.t("status.active"), "🟢 Active")
        self.assertEqual(i18n.t("status.inactive"), "🔴 Inactive")

    def test_welcome_body(self):
        got = i18n.t("welcome.body", user_id=123, license_key="THECLAIMERS-x",
                     status="🟢 Active", active_now=2, maximum_usernames=5)
        self.assertEqual(
            got,
            "<b>⚡ The Claimers</b>\n"
            "<i>Automated Stake code claiming — fast, hands-free, 24×7.</i>\n"
            "━━━━━━━━━━━━━━━\n"
            "👤 User ID  <code>123</code>\n"
            "🔑 License  <code>THECLAIMERS-x</code>\n"
            "📶 Status  <b>🟢 Active</b>\n"
            "👥 Claimers  <b>2/5</b>",
        )

    def test_rate_limit(self):
        got = i18n.t("rate_limit.body", command="drop",
                     limit_desc=i18n.t("rate_limit.limits.drop"), wait_secs=42)
        self.assertEqual(
            got,
            "⏳ <b>Easy there!</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<code>/drop</code> is limited to <b>5 per minute</b>.\n"
            "Please try again in <b>42s</b>.",
        )

    def test_error_default(self):
        got = i18n.t("errors.generic", reason=i18n.t("errors.default_reason"))
        self.assertEqual(
            got,
            "⚠️ <b>Something went wrong</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "We couldn't reach the service.\n"
            "Please try again in a moment.",
        )

    def test_no_license(self):
        self.assertEqual(
            i18n.t("license_status.no_license"),
            "👋 <b>Welcome!</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "You don't have a license yet. Send /start to set one up in seconds.",
        )


class TestEnglishPinsCommands(unittest.TestCase):
    """Byte-identical pins for /drop, /reload, /connected, /license output."""

    def test_drop_success(self):
        self.assertEqual(
            i18n.t("drop.success", code="ABC", count=3),
            "⚡ <b>Code Dropped</b>\n━━━━━━━━━━━━━━━\n"
            "🎟 Code  <code>ABC</code>\n📡 Sent to  <b>3</b> claimer(s)\n\n"
            "<i>Results arrive in about 5 seconds…</i>",
        )

    def test_drop_no_claimers(self):
        self.assertEqual(
            i18n.t("drop.no_claimers"),
            "⚠️ <b>No Claimers Connected</b>\n━━━━━━━━━━━━━━━\n"
            "Start the Tampermonkey script on Stake, then try again. "
            "Run /connected to check who's online.",
        )

    def test_drop_invalid_reserved_wrapped(self):
        self.assertEqual(
            i18n.t("drop.invalid_wrap", err=i18n.t("drop.invalid_reserved", kw="broadcast")),
            "❌ Code contains a reserved word: <code>broadcast</code>.",
        )

    def test_reload_lines_and_header(self):
        self.assertEqual(i18n.t("reload.available", username="alice", time_left="3m"),
                         "✅  <code>alice</code>  ·  3m")
        self.assertEqual(i18n.t("reload.not_yet", username="bob"),
                         "⏳  <code>bob</code>  ·  not yet")
        self.assertEqual(i18n.t("reload.header", lines="L1\nL2"),
                         "🔄 <b>Reload Status</b>\n━━━━━━━━━━━━━━━\nL1\nL2")

    def test_connected(self):
        self.assertEqual(
            i18n.t("connected.account", username="u", tokens=5, claims=2),
            "•  <code>u</code>  ·  <b>5</b> tokens  ·  <b>2</b>/10",
        )
        self.assertEqual(
            i18n.t("connected.header", license="K", browsers=3, accounts="A1\nA2"),
            "👥 <b>Connected Claimers</b>\n━━━━━━━━━━━━━━━\n"
            "🔑 License  <code>K</code>\n🖥️ Browsers  <b>3</b>\n\nA1\nA2",
        )

    def test_license_body(self):
        self.assertEqual(
            i18n.t("license.body", license_key="K", status="🟢 Active",
                   active_now=1, maximum_usernames=5),
            "🔑 <b>Your License</b>\n━━━━━━━━━━━━━━━\n"
            "Key  <code>K</code>\nStatus  <b>🟢 Active</b>\nClaimers  <b>1/5</b>",
        )


class TestEnglishPinsPayments(unittest.TestCase):
    """Byte-identical pins for /balance and /topup output + button labels."""

    def test_fee_notice(self):
        self.assertEqual(
            i18n.t("fee.notice"),
            "💡 <i>Our regular rate is 4% daily, but every Saturday you only pay "
            "3.5% on claimed codes. Don't miss out!</i>",
        )

    def test_balance_after_claims(self):
        self.assertEqual(
            i18n.t("balance.after_claims", license_key="K", status="🟢 Active"),
            "💰 <b>Your Balance</b>\n━━━━━━━━━━━━━━━\n🔑 <code>K</code>\n"
            "💳 Plan  <b>Pay After Claims</b>\n📶 Status  <b>🟢 Active</b>\n\n"
            "<i>You're on the pay-after-claims plan, so there's no prepaid "
            "balance to track here. 🎉</i>",
        )

    def test_balance_header(self):
        self.assertEqual(
            i18n.t("balance.header", license_key="K", amount="1,234.56", pct="4", status="🟢 Active"),
            "💰 <b>Your Balance</b>\n━━━━━━━━━━━━━━━\n🔑 <code>K</code>\n"
            "💵 Available  <b>$1,234.56</b>\n📉 Per-claim fee  <b>4%</b>\n📶 Status  <b>🟢 Active</b>",
        )

    def test_topup_prompt_fee_toggle(self):
        base = ("💳 <b>Top Up Balance</b>\n━━━━━━━━━━━━━━━\nHow much would you like to add?\n"
                "Enter an amount from <b>$1</b> to <b>$100</b> (USD).\n\n"
                "💡 <i>Example:</i> <code>10</code>\n🔒 Paid securely in crypto via OxaPay.")
        self.assertEqual(i18n.t("topup.prompt", min="1", max="100", fee_line=""), base)
        self.assertEqual(i18n.t("topup.prompt", min="1", max="100", fee_line="\n\nX"), base + "\n\nX")

    def test_topup_invoice_and_confirmed(self):
        self.assertEqual(
            i18n.t("topup.invoice", amount="10.00"),
            "🧾 <b>Invoice Ready</b>\n━━━━━━━━━━━━━━━\n💵 Amount  <b>$10.00</b>\n\n"
            "Tap <b>💳 Pay Now</b> to complete payment in crypto.\n"
            "Your balance is credited <b>automatically</b> the moment it "
            "confirms — you'll get a message right here.\n\n<i>Use 🔄 Check Status anytime.</i>",
        )
        self.assertEqual(
            i18n.t("topup.confirmed", amount="10.00"),
            "✅ <b>Payment Confirmed</b>\n━━━━━━━━━━━━━━━\n"
            "💵 <b>$10.00</b> has been added to your balance.\n"
            "Check /balance to see your updated total. 🚀",
        )

    def test_topup_error_default_and_known(self):
        self.assertEqual(i18n.t("topup.errors.default", min="1", max="100"),
                         "Couldn't start the payment. Please try again.")
        self.assertEqual(i18n.t("topup.errors.amount_too_small", min="1", max="100"),
                         "The minimum top-up is <b>$1</b>.")

    def test_buttons(self):
        self.assertEqual(i18n.t("buttons.cancel"), "❌ Cancel")
        self.assertEqual(i18n.t("buttons.check_status"), "🔄 Check Status")
        self.assertEqual(i18n.t("buttons.pay_now"), "💳 Pay Now")


class TestEnglishPinsPanel(unittest.TestCase):
    """Pins for the /start panel: keyboard labels, install guide, commands, key-change."""

    def test_panel_buttons(self):
        self.assertEqual(i18n.t("buttons.open_app"), "🚀 Open App")
        self.assertEqual(i18n.t("buttons.install"), "📥 Install")
        self.assertEqual(i18n.t("buttons.commands"), "📋 Commands")
        self.assertEqual(i18n.t("buttons.my_claims"), "📊 My Claims")
        self.assertEqual(i18n.t("buttons.install_userscript"), "📜 Install Userscript")
        self.assertEqual(i18n.t("buttons.copy_link"), "📋 Copy Link")
        self.assertEqual(i18n.t("buttons.stake_offers"), "🎁 Stake Offers")
        self.assertEqual(i18n.t("buttons.vip_club"), "👑 VIP Club")

    def test_commands_list(self):
        self.assertEqual(
            i18n.t("commands.list"),
            "📋 <b>Commands</b>\n━━━━━━━━━━━━━━━\n"
            "/start — open your dashboard\n"
            "/drop <code>[code]</code> — broadcast a code to your claimers\n"
            "/reload — check reload availability\n"
            "/connected — view connected browsers &amp; tokens\n"
            "/count — your claim totals (last 24h)\n"
            "/balance — prepaid balance &amp; usage\n"
            "/topup — recharge your balance\n"
            "/license — your license &amp; status\n"
            "/language — change your language\n\n"
            "<i>Tip: most actions are one tap away from /start.</i>",
        )

    def test_license_changed(self):
        self.assertEqual(
            i18n.t("license_changed.body", old="A", new="B"),
            "🔑 <b>License Key Updated</b>\n━━━━━━━━━━━━━━━\n"
            "Old  <code>A</code>\nNew  <code>B</code>\n\n"
            "Open the Tampermonkey popup and replace your key with the new one to "
            "keep claiming without interruption.",
        )

    def test_install_guide_bounds(self):
        s = i18n.t("install.setup")
        self.assertTrue(s.startswith("📥 <b>Setup Guide</b>\n<i>Takes about 2 minutes"))
        self.assertTrue(s.endswith("<i>(any supported Stake mirror works too)</i>"))
        o = i18n.t("install.optimize")
        self.assertTrue(o.startswith("⚙️ <b>Optimization &amp; Best Practices</b>"))
        self.assertTrue(o.endswith("Tap <b>🛟 Support</b> anytime."))


class TestEnglishPinsLanguage(unittest.TestCase):
    """Pins for /language picker + confirmation + command-menu descriptions."""

    def test_language_strings(self):
        self.assertEqual(
            i18n.t("language.header", current="日本語"),
            "🌐 <b>Language</b>\n━━━━━━━━━━━━━━━\nCurrent: <b>日本語</b>\n\nChoose your language:")
        self.assertEqual(
            i18n.t("language.saved", lang="en", name="日本語"),
            "✅ <b>Language updated</b>\nMessages will now be in <b>日本語</b>.")
        self.assertEqual(
            i18n.t("language.error"),
            "⚠️ Couldn't save your language right now. Please try again in a moment.")

    def test_command_menu_descriptions(self):
        # Default English menu must stay byte-identical to the pre-L2 menu (+language).
        self.assertEqual(i18n.t("cmd_desc.start"), "Open your dashboard")
        self.assertEqual(i18n.t("cmd_desc.connected"), "View connected browsers & tokens")
        self.assertEqual(i18n.t("cmd_desc.count"), "Your claim totals (last 24h)")
        self.assertEqual(i18n.t("cmd_desc.balance"), "Prepaid balance & usage")
        self.assertEqual(i18n.t("cmd_desc.license"), "Your license & status")
        self.assertEqual(i18n.t("cmd_desc.language"), "Change your language")

    def test_supported_has_twelve_native_names(self):
        self.assertEqual(len(i18n.SUPPORTED), 12)
        self.assertEqual(i18n.SUPPORTED["ja"], "日本語")
        self.assertEqual(i18n.SUPPORTED["en"], "English")


class TestEnglishPinsCount(unittest.TestCase):
    """Pins for /count — headers, prompts, report footer/blocks, pluralization."""

    def test_headers_and_prompts(self):
        self.assertEqual(i18n.t("count.rate_limit", secs=6),
                         "⏳ You're going a bit fast. Try /count again in <b>6s</b>.")
        self.assertEqual(i18n.t("count.cancelled"), "❎ Cancelled.")
        self.assertEqual(i18n.t("count.label_custom", d_from="2026-05-28", d_to="2026-06-02"),
                         "2026-05-28 → 2026-06-02 (IST)")
        self.assertEqual(i18n.t("count.key_got", key="K"),
                         "🔑 Got it: <code>K</code>\n\nPick a time range:")
        self.assertEqual(i18n.t("count.win_stream"), "Stream Special (06:00–09:30 IST)")

    def test_report_footer_plurals(self):
        self.assertEqual(
            i18n.t("count.footer", usd="$1,000.00", claims=5,
                   claim_word=i18n.t("count.word_claim_other"), users=3,
                   user_word=i18n.t("count.word_account_other")),
            "━━━━━━━━━━━━━━━\n💰 <b>$1,000.00</b> claimed  •  📦 <b>5</b> claims  •  👥 <b>3</b> accounts",
        )
        self.assertEqual(
            i18n.t("count.footer", usd="$1.00", claims=1,
                   claim_word=i18n.t("count.word_claim_one"), users=1,
                   user_word=i18n.t("count.word_account_one")),
            "━━━━━━━━━━━━━━━\n💰 <b>$1.00</b> claimed  •  📦 <b>1</b> claim  •  👥 <b>1</b> account",
        )

    def test_report_blocks_and_part_header(self):
        self.assertEqual(
            i18n.t("count.block_personal", uname="a", amt="$5.00", claims_txt="3 claims"),
            "👤 <b>a</b>\n   💰 <b>$5.00</b>  •  3 claims")
        self.assertEqual(
            i18n.t("count.block_ranked", rank="🥇", uname="b", amt="$9.00", claims_txt="1 claim"),
            "🥇  <b>b</b>\n      💰 <b>$9.00</b>  •  1 claim")
        self.assertEqual(
            i18n.t("count.part_header", title="X", idx=2, total=3),
            "<b>X — Part 2/3</b>\n━━━━━━━━━━━━━━━\n")


if __name__ == "__main__":
    unittest.main()
