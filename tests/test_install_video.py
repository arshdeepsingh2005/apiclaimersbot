"""Tests for the localized install-video feature (handlers + install_media).

Drives the REAL _send_installation_instructions with mocked senders, so it
verifies actual behavior: video sent first, correct per-language file_id,
English fallback, and that a video failure never blocks the install text.
"""

import pytest

handlers = pytest.importorskip("bot.handlers")
from bot import i18n
from bot.install_media import INSTALL_VIDEO_FILE_IDS, install_video_for


@pytest.fixture
def capture(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers, "send_video",
                        lambda chat_id, video, caption=None, parse_mode=None: calls.append(("video", video)))
    monkeypatch.setattr(handlers, "send_message",
                        lambda chat_id, text, parse_mode="HTML", reply_markup=None: calls.append(("msg", None)))
    return calls


def _run(lang):
    tok = i18n.set_lang(lang)
    try:
        handlers._send_installation_instructions(999)
    finally:
        i18n.reset_lang(tok)


def test_all_languages_have_a_file_id():
    for code in i18n.SUPPORTED:
        assert code in INSTALL_VIDEO_FILE_IDS, f"missing install video for {code}"
    assert install_video_for("en"), "English fallback required"


def test_video_first_then_two_texts(capture):
    _run("ja")
    assert [c[0] for c in capture] == ["video", "msg", "msg"]


def test_exact_language_file_id(capture):
    _run("ja")
    assert capture[0] == ("video", INSTALL_VIDEO_FILE_IDS["ja"])
    capture.clear()
    _run("tr")
    assert capture[0] == ("video", INSTALL_VIDEO_FILE_IDS["tr"])


def test_unknown_language_falls_back_to_english(capture):
    _run("de")  # not a supported language
    assert capture[0] == ("video", INSTALL_VIDEO_FILE_IDS["en"])
    assert [c[0] for c in capture] == ["video", "msg", "msg"]


def test_video_failure_does_not_block_install_text(monkeypatch, capture):
    def boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(handlers, "send_video", boom)
    _run("en")
    # video raised, but the two install texts still went out
    assert [c[0] for c in capture] == ["msg", "msg"]
