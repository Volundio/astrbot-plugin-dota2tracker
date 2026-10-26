"""Fixture based previews shared by the WebUI ``templates`` view and the tests.

Each template has a ``preview_<name>(ctx)`` function in its view-model module
(``presentation/viewmodels/<module>.py``) that returns the template context
built from the anonymised fixtures in ``dota2tracker/fixtures``. The same view
model builders are used for real chat output, so a preview shows exactly what
users receive.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from functools import lru_cache
from typing import Any

from ..constants import FIXTURES_DIR
from ..data.static import DotaConstants
from ..i18n.service import I18nService
from .assets import ImageAssets

__all__ = ["PREVIEWS", "PreviewContext", "build_preview", "load_fixture"]

#: template name → (view-model module, preview function, description key)
PREVIEWS: dict[str, tuple[str, str]] = {
    "match_1": ("match", "preview_match_1"),
    "match_2": ("match", "preview_match_2"),
    "match_2+": ("match", "preview_match_2_plus"),
    "player_1": ("player", "preview_player_1"),
    "hero_1": ("hero", "preview_hero_1"),
    "item": ("item", "preview_item"),
    "itemlist": ("item", "preview_itemlist"),
    "daily": ("report", "preview_daily"),
    "daily_legacy": ("report", "preview_daily_legacy"),
    "rank_fun": ("rank", "preview_rank_fun"),
    "help": ("misc", "preview_help"),
    "members": ("misc", "preview_members"),
    "hero_of_the_day": ("misc", "preview_hero_of_the_day"),
}


@lru_cache(maxsize=32)
def _read_fixture(name: str) -> str:
    path = (FIXTURES_DIR / name).resolve()
    if FIXTURES_DIR.resolve() not in path.parents or path.suffix != ".json":
        raise ValueError(f"invalid fixture: {name}")
    return path.read_text(encoding="utf-8")


def load_fixture(name: str) -> Any:
    """Fresh copy of a fixture (callers may mutate the result)."""
    return json.loads(_read_fixture(name))


@dataclass
class PreviewContext:
    constants: DotaConstants
    i18n: I18nService
    assets: ImageAssets
    locale: str
    tz: tzinfo | None = None
    now: datetime | None = None
    #: optional variant selector, e.g. ``opendota`` for match previews
    variant: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def t(self, key: str, params: Any = None) -> str | None:
        return self.i18n.t(self.locale, key, params)

    def text(self, key: str, params: Any = None) -> str:
        return self.i18n.text(self.locale, key, params)


def build_preview(template: str, ctx: PreviewContext) -> dict[str, Any]:
    if template not in PREVIEWS:
        raise KeyError(template)
    module_name, function_name = PREVIEWS[template]
    module = importlib.import_module(f"{__package__}.viewmodels.{module_name}")
    builder: Callable[[PreviewContext], dict[str, Any]] = getattr(module, function_name)
    return builder(ctx)
