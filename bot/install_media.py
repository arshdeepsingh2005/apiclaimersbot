"""
Per-language installation-video file_ids.

Each value is a Telegram file_id captured by uploading the language's video ONCE
(see get_install_file_ids.py). Sending by file_id is instant and keeps the media
on Telegram's CDN — the bot host never stores or re-uploads the video.

IMPORTANT: a file_id is tied to the exact BOT TOKEN that uploaded it. These were
captured with the main bot token. If the main bot token ever changes, re-run
get_install_file_ids.py and replace these values.
"""

from bot.i18n import DEFAULT_LANG, SUPPORTED

INSTALL_VIDEO_FILE_IDS: dict[str, str] = {
    "en": "BAACAgUAAyEGAATrE_sVAAMDan1mSgxEFnSnj5oyzEFChHVTrMMAAjYfAAKk_fFX82dr_KbTYh09BA",
    "ja": "BAACAgUAAyEGAATrE_sVAAMEan1mYBdSBalCipo9rRZ6_ZUiiYYAAjgfAAKk_fFXiW0UsKIb7iY9BA",
    "zh": "BAACAgUAAyEGAATrE_sVAAMFan1maEYCsmvhE3JWVrb6qEwtTsUAAjkfAAKk_fFX_5-TQv2hbY49BA",
    "ko": "BAACAgUAAyEGAATrE_sVAAMGan1mdqhEKMCrheoNqidrt1dFqiAAAjofAAKk_fFXk63VC6f5Uf89BA",
    "hi": "BAACAgUAAyEGAATrE_sVAAMHan1mfsK3zOgHPq8kFqLhL1WvRzEAAjsfAAKk_fFX3iW-fykzpwQ9BA",
    "pl": "BAACAgUAAyEGAATrE_sVAAMIan1mhejABjRZcMzzFQG-p8AdNtcAAjwfAAKk_fFXQWMJczpW1g49BA",
    "vi": "BAACAgUAAyEGAATrE_sVAAMJan1mi3Hr0LpRW_vaCQ3aPhU1zQMAAj0fAAKk_fFXKlrd9cPLZG89BA",
    "es": "BAACAgUAAyEGAATrE_sVAAMKan1mqwIVXYtl42j1aPhkF4szpeMAAkAfAAKk_fFXx101AAFN-eG2PQQ",
    "it": "BAACAgUAAyEGAATrE_sVAAMLan1mvfdnqMb9twHEY75XYKVlssQAAkEfAAKk_fFXXJSXY0r1r6Y9BA",
    "pt": "BAACAgUAAyEGAATrE_sVAAMMan1m50Fa7wABmHG9s52heCU6AooRAAJCHwACpP3xVwAB9ciwQuUM0T0E",
    "fr": "BAACAgUAAyEGAATrE_sVAAMNan1nIKA8l2NLXYhBUIa12Kj5EvoAAkcfAAKk_fFXzv4oznleBw09BA",
    "tr": "BAACAgUAAyEGAATrE_sVAAMOan1nV-Xw_E9VXNfAwx3uihWbvz4AAkofAAKk_fFX3FY7q35Rcrw9BA",
}


def install_video_for(lang: str | None) -> str | None:
    """Return the install-video file_id for `lang`, falling back to English,
    then to any available video. Returns None only if the map is empty."""
    if lang and lang in INSTALL_VIDEO_FILE_IDS:
        return INSTALL_VIDEO_FILE_IDS[lang]
    if DEFAULT_LANG in INSTALL_VIDEO_FILE_IDS:
        return INSTALL_VIDEO_FILE_IDS[DEFAULT_LANG]
    return next(iter(INSTALL_VIDEO_FILE_IDS.values()), None)


# Fail loudly at import if a supported language somehow lacks any fallback.
assert INSTALL_VIDEO_FILE_IDS.get(DEFAULT_LANG), "English install video file_id is required as fallback"
_ = SUPPORTED  # kept for reference / future validation
