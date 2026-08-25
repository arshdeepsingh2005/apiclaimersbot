"""
Internationalization for the main Telegram bot.

Design (see plan):
  * JSON locale catalog in bot/locales/<code>.json, loaded ONCE at import.
  * English (en.json) is the source of truth and the fallback for every key.
  * Current-update language lives in a contextvars.ContextVar — mirroring the
    existing `use_token()` / `_current_token` pattern in telegram_api.py, which
    the bot already relies on for per-update request state under gthread
    (workers=1, threads=8, threads reused). Set-and-reset so a reused worker
    thread never inherits a stale language.
  * t() is CRASH-PROOF: missing key -> English fallback -> the key itself;
    formatting uses format_map(_SafeDict) so a bad/relocated placeholder renders
    literally instead of raising. t() NEVER escapes — callers pass values that
    are already html.escape()'d (the discipline already used in handlers.py).

Only USER commands are localized; admin commands stay English (handled by the
call sites, which simply don't route their strings through t()).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# ── Supported languages (ISO 639-1) → native display name (for the picker) ──
SUPPORTED: dict[str, str] = {
    "en": "English",
    "ja": "日本語",
    "zh": "中文",
    "ko": "한국어",
    "hi": "हिन्दी",
    "pl": "Polski",
    "vi": "Tiếng Việt",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
    "fr": "Français",
    "tr": "Türkçe",
}
DEFAULT_LANG = "en"

# Flag emoji shown next to each language in the picker. Note: flags are a
# country symbol, not a language one, so these are a pragmatic best-fit choice
# (en→UK, pt→Brazil as the largest Portuguese user base). Adjust freely.
FLAGS: dict[str, str] = {
    "en": "🇬🇧",
    "ja": "🇯🇵",
    "zh": "🇨🇳",
    "ko": "🇰🇷",
    "hi": "🇮🇳",
    "pl": "🇵🇱",
    "vi": "🇻🇳",
    "es": "🇪🇸",
    "it": "🇮🇹",
    "pt": "🇧🇷",
    "fr": "🇫🇷",
    "tr": "🇹🇷",
}


def display_name(code: str) -> str:
    """Flag + native name for the picker/confirmation, e.g. '🇯🇵 日本語'."""
    code = code if code in SUPPORTED else DEFAULT_LANG
    flag = FLAGS.get(code, "")
    return f"{flag} {SUPPORTED[code]}".strip()


_LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")


# ── Telegram language_code → our code ───────────────────────────────────────
def map_tg_lang(code: str | None) -> str:
    """Normalize a Telegram `language_code` (e.g. 'pt-BR', 'zh-Hant', 'en-us')
    to one of SUPPORTED, falling back to English for anything unknown."""
    if not code:
        return DEFAULT_LANG
    base = str(code).strip().lower().replace("_", "-").split("-", 1)[0]
    return base if base in SUPPORTED else DEFAULT_LANG


# ── Request-scoped current language (mirrors use_token) ─────────────────────
_lang_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bot_lang", default=DEFAULT_LANG
)


def set_lang(code: str | None):
    """Bind `code` as the current language for this update; returns a token.
    Caller MUST reset() it in a finally (as _process_update does for the token)."""
    use = code if code in SUPPORTED else DEFAULT_LANG
    return _lang_ctx.set(use)


def reset_lang(token) -> None:
    try:
        _lang_ctx.reset(token)
    except Exception:  # pragma: no cover - defensive
        pass


def get_lang() -> str:
    return _lang_ctx.get()


# In-memory per-user language override. The API-Claimer slot bot has no legacy
# license table, so /language stores the chosen code here (process-lifetime).
# The Mini App keeps its own language client-side.
_user_lang_override: dict[int, str] = {}


def set_user_lang(user_id: int, code: str) -> None:
    if code in SUPPORTED:
        _user_lang_override[int(user_id)] = code


def resolve_lang(user_id: int, tg_code: str | None) -> str:
    """Resolution order: explicit in-memory override (set via /language) ->
    Telegram auto-detect -> English. Zero network I/O."""
    try:
        override = _user_lang_override.get(int(user_id))
        if override in SUPPORTED:
            return override
    except Exception:  # pragma: no cover - never let resolution break a command
        pass
    return map_tg_lang(tg_code)


# ── Catalog load (ONCE at import) ───────────────────────────────────────────
_MESSAGES: dict[str, dict] = {}


def _load_locales() -> None:
    _MESSAGES.clear()
    for code in SUPPORTED:
        path = os.path.join(_LOCALES_DIR, f"{code}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _MESSAGES[code] = json.load(fh)
        except FileNotFoundError:
            _MESSAGES[code] = {}
            if code == DEFAULT_LANG:
                logger.error("i18n: en.json missing — English fallback unavailable")
            else:
                logger.info("i18n: %s.json not present yet (falls back to English)", code)
        except Exception as exc:
            _MESSAGES[code] = {}
            logger.error("i18n: failed to load %s.json: %s", code, exc)


_load_locales()


def _lookup(catalog: dict, key: str):
    """Dotted-path lookup into a nested dict; returns the string or None."""
    node = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


class _SafeDict(dict):
    """format_map helper: unknown placeholders render literally (never KeyError)."""

    def __missing__(self, key):
        return "{" + key + "}"


def t(key: str, /, lang: str | None = None, **kw) -> str:
    """Translate `key` for `lang` (or the current-update language), with English
    fallback and crash-proof formatting. NEVER raises; NEVER escapes.

    `key` is positional-only so a placeholder literally named ``{key}`` can be
    passed as ``t("some.key", key=...)`` without colliding with this parameter.
    (Avoid a placeholder named ``lang`` — that one is still reserved.)"""
    use = lang or get_lang()
    if use not in SUPPORTED:
        use = DEFAULT_LANG
    template = _lookup(_MESSAGES.get(use, {}), key)
    if template is None and use != DEFAULT_LANG:
        template = _lookup(_MESSAGES.get(DEFAULT_LANG, {}), key)
    if template is None:
        logger.warning("i18n: missing key %r (lang=%s)", key, use)
        return key
    if not kw:
        return template
    try:
        return template.format_map(_SafeDict(kw))
    except Exception as exc:  # stray brace etc. — show the raw template, never crash
        logger.warning("i18n: format failed for key %r: %s", key, exc)
        return template


# ── Validation (used by tests AND an optional non-fatal startup self-check) ──
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
_ALLOWED_TAGS = {"b", "i", "code"}

# Keys whose value is legitimately identical across languages (emoji/brand/etc.)
# — suppressed from the "suspicious duplicate" warning. Extend as needed.
_DUPLICATE_ALLOWLIST: frozenset[str] = frozenset()


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        if k == "__meta__":
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, str):
            out[key] = v
    return out


def validate_locales() -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Empty errors => catalog is consistent.
    Checks: key parity vs en, placeholder-token parity, HTML-tag allowlist +
    parity vs en, and a non-fatal suspicious-duplicate signal."""
    errors: list[str] = []
    warnings: list[str] = []

    en = _flatten(_MESSAGES.get(DEFAULT_LANG, {}))
    if not en:
        errors.append("en.json missing or empty — no source of truth")
        return errors, warnings

    en_ph = {k: set(_PLACEHOLDER_RE.findall(v)) for k, v in en.items()}
    en_tags = {k: sorted(t_.lower() for t_ in _TAG_RE.findall(v)) for k, v in en.items()}

    # English itself: enforce the tag allowlist so bad source is caught too.
    for k, v in en.items():
        bad = sorted({t_.lower() for t_ in _TAG_RE.findall(v)} - _ALLOWED_TAGS)
        if bad:
            errors.append(f"en.{k}: disallowed HTML tag(s) {bad}")

    for code in SUPPORTED:
        if code == DEFAULT_LANG:
            continue
        loc = _flatten(_MESSAGES.get(code, {}))
        if not loc:
            continue  # not translated yet (pre-L3) — English fallback covers it
        missing = sorted(set(en) - set(loc))
        extra = sorted(set(loc) - set(en))
        if missing:
            errors.append(f"{code}: missing {len(missing)} keys, e.g. {missing[:5]}")
        if extra:
            errors.append(f"{code}: extra {len(extra)} keys, e.g. {extra[:5]}")

        identical = checked = 0
        for k, v in loc.items():
            if k not in en:
                continue
            if set(_PLACEHOLDER_RE.findall(v)) != en_ph.get(k, set()):
                errors.append(f"{code}.{k}: placeholder mismatch (expected {sorted(en_ph[k])})")
            tags = [t_.lower() for t_ in _TAG_RE.findall(v)]
            bad = sorted(set(tags) - _ALLOWED_TAGS)
            if bad:
                errors.append(f"{code}.{k}: disallowed HTML tag(s) {bad}")
            elif sorted(tags) != en_tags.get(k, []):
                errors.append(f"{code}.{k}: HTML tags differ from English")
            checked += 1
            if v == en[k] and k not in _DUPLICATE_ALLOWLIST:
                identical += 1
        if checked and identical / checked > 0.60:
            warnings.append(
                f"{code}: {identical}/{checked} strings identical to English "
                "— likely untranslated (copy-paste?)"
            )

    return errors, warnings


def log_validation() -> bool:
    """Non-fatal startup self-check: log any problems, never raise. Returns True
    when the catalog is error-free."""
    errors, warnings = validate_locales()
    for w in warnings:
        logger.warning("i18n locale: %s", w)
    for e in errors:
        logger.error("i18n locale: %s", e)
    if not errors:
        logger.info("i18n: locale catalog validated (%d language file(s))",
                    sum(1 for c in SUPPORTED if _MESSAGES.get(c)))
    return not errors
