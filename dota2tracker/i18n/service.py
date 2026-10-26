"""Runtime translation service (port of the Koishi ``I18NService``).

Lookup order follows ``I18NService.$t``:

1. the constants dictionary (``<locale>.constants.json``) – raw strings such as
   hero names, ranks, lobby types;
2. the message dictionary (``<locale>.json``) rendered with the Koishi/Satori
   template syntax.

Missing keys return ``None`` from :meth:`I18nService.t` (the original returned
``undefined``) while :meth:`I18nService.text` mirrors ``session.text`` and
returns the key itself.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from markupsafe import Markup, escape

from ..constants import LANGUAGE_TAGS, LOCALES_DIR, SUPPORTED_LOCALES
from ..utils.koishi_random import KoishiRandom, seeded_random
from .template import parse_template, render_html, render_text

__all__ = ["I18nService"]

_MISSING = object()


@lru_cache(maxsize=8)
def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(data: Any, key: str) -> Any:
    current = data
    for part in key.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


class I18nService:
    """Translations, locale resolution and hero name helpers."""

    def __init__(
        self,
        *,
        default_locale: str = "zh-CN",
        use_hero_nicknames: bool = True,
        hero_ids: Callable[[], Iterable[int]] = lambda: (),
        logger: logging.Logger | None = None,
        locales_dir: Path = LOCALES_DIR,
    ) -> None:
        self.default_locale = default_locale if default_locale in SUPPORTED_LOCALES else SUPPORTED_LOCALES[0]
        self.use_hero_nicknames = use_hero_nicknames
        self._hero_ids = hero_ids
        self._logger = logger or logging.getLogger(__name__)
        self._messages = {locale: _load_json(locales_dir / f"{locale}.json") for locale in SUPPORTED_LOCALES}
        self._constants = {locale: _load_json(locales_dir / f"{locale}.constants.json") for locale in SUPPORTED_LOCALES}
        self._nickname_cache: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------ locales
    def resolve_locale(self, channel_locale: str | None = None) -> str:
        """Channel locale first, then the configured default (``getLanguageTag``)."""
        for candidate in (channel_locale, self.default_locale):
            normalized = self.normalize(candidate)
            if normalized:
                return normalized
        return SUPPORTED_LOCALES[0]

    @staticmethod
    def normalize(locale: str | None) -> str | None:
        if not locale:
            return None
        lowered = locale.replace("_", "-").lower()
        for supported in SUPPORTED_LOCALES:
            if lowered == supported.lower():
                return supported
        prefix = lowered.split("-")[0]
        for supported in SUPPORTED_LOCALES:
            if supported.lower().startswith(prefix + "-"):
                return supported
        return None

    @staticmethod
    def graphql_language(locale: str) -> str:
        return LANGUAGE_TAGS.get(locale, LANGUAGE_TAGS["zh-CN"])["graphql"]

    @staticmethod
    def valve_language(locale: str) -> str:
        return LANGUAGE_TAGS.get(locale, LANGUAGE_TAGS["zh-CN"])["valve"]

    def constant_locale(self, locale: str) -> dict[str, Any]:
        return self._constants.get(locale) or self._constants[SUPPORTED_LOCALES[0]]

    def raw(self, locale: str, key: str) -> Any:
        """Raw message entry (``ctx.i18n._data[locale][key]``) or ``None``."""
        value = _lookup(self._messages.get(locale, {}), key)
        return None if value is _MISSING else value

    # -------------------------------------------------------------- translate
    def _constant(self, locale: str, key: str) -> str | None:
        value = _lookup(self._constants.get(locale, {}), key)
        if isinstance(value, str) and value:
            return value
        return None

    def t(self, locale: str, key: str, params: Any = None) -> str | None:
        """Port of ``I18NService.$t`` returning plain text (``None`` when missing)."""
        constant = self._constant(locale, key)
        if constant is not None:
            return _replace_positional(constant, params)
        template = self.raw(locale, key)
        if not isinstance(template, str):
            return None
        return render_text(template, {} if params is None else params)

    def t_html(self, locale: str, key: str, params: Any = None) -> Markup | None:
        """Like :meth:`t` but keeps inline markup (escaped parameters, raw tags)."""
        constant = self._constant(locale, key)
        if constant is not None:
            return Markup(_replace_positional(constant, params))
        template = self.raw(locale, key)
        if not isinstance(template, str):
            return None
        return Markup(render_html(template, {} if params is None else params))

    def text(self, locale: str, key: str, params: Any = None) -> str:
        """``session.text`` semantics: the key itself is returned when missing."""
        value = self.t(locale, key, params)
        return key if value is None else value

    def gt(self, key: str, params: Any = None) -> str:
        """Translate with the global (default) locale, used for log messages."""
        return self.text(self.default_locale, key, params)

    # ------------------------------------------------------------ hero names
    def hero_name(self, hero_id: Any, locale: str) -> str | None:
        return self.t(locale, f"dota2tracker.template.hero_names.{hero_id}")

    def _hero_nicknames(self, hero_id: Any, locale: str) -> list[str]:
        raw = self.raw(locale, f"dota2tracker.heroes_nicknames.{hero_id}")
        if not isinstance(raw, str) or not raw:
            return []
        nodes = parse_template(raw, {})
        content = nodes[0].content if nodes and nodes[0].type == "text" else ""
        if not content:
            return []
        try:
            parsed = json.loads(f"[{content}]")
        except json.JSONDecodeError as exc:
            self._logger.error("Failed to parse nicknames for heroId %s: %s", hero_id, exc)
            return []
        return [str(name) for name in parsed]

    def all_hero_names(self, hero_id: Any, locale: str) -> list[Any]:
        official = self.hero_name(hero_id, locale)
        names: dict[Any, None] = {official: None}
        for nickname in self._hero_nicknames(hero_id, locale):
            names.setdefault(nickname, None)
        return list(names)

    def display_name_for_hero(
        self,
        hero_id: Any,
        locale: str,
        *,
        random: KoishiRandom | None = None,
        seed: str | None = None,
        force_official_name: bool = False,
    ) -> str:
        """Official name or (when enabled) a random nickname, as in the original."""
        if force_official_name or not self.use_hero_nicknames:
            return self.hero_name(hero_id, locale) or ""
        names = self.all_hero_names(hero_id, locale)
        if not names:
            return self.hero_name(hero_id, locale) or ""
        if random is not None:
            randomizer = random
        elif seed:
            randomizer = seeded_random(seed)
        else:
            randomizer = KoishiRandom()
        picked = randomizer.pick(names)
        return "" if picked is None else str(picked)

    def _nickname_map(self, locale: str) -> dict[str, int]:
        cached = self._nickname_cache.get(locale)
        if cached is None:
            cached = {}
            for hero_id in sorted(int(h) for h in self._hero_ids()):
                for name in self.all_hero_names(hero_id, locale):
                    if isinstance(name, str):
                        cached[name.lower()] = hero_id
            self._nickname_cache[locale] = cached
        return cached

    def find_hero_id(self, value: Any) -> int | None:
        """Hero ID from an ID, official name or nickname in any supported locale."""
        if value is None or value == "":
            return None
        text = str(value).lower()
        if re.fullmatch(r"[0-9]+", text):
            hero_id = int(text)
            if hero_id in {int(h) for h in self._hero_ids()}:
                return hero_id
        for locale in ("en-US", "zh-CN"):
            hero_id = self._nickname_map(locale).get(text)
            if hero_id is not None:
                return hero_id
        return None


def _replace_positional(template: str, params: Any) -> str:
    """Constants only support ``{0}`` placeholders (``params[+index] || ""``)."""
    values = params if isinstance(params, list) else [params]

    def substitute(match: re.Match[str]) -> str:
        index = int(match.group(1))
        value = values[index] if index < len(values) else None
        if value is None or value == "" or value == 0 or value is False:
            return ""
        return str(value)

    return re.sub(r"\{(\d+)\}", substitute, template)


def html_text(value: Any) -> Markup:
    """Escape helper for view models that build small HTML fragments."""
    return escape("" if value is None else value)
