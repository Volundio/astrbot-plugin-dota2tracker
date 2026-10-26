"""High level image builders: view model + template + render.

Commands and background tasks only talk to :class:`ImageFactory`; view model
modules are imported lazily so a broken template never prevents the plugin
from loading.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from ..application.models import RenderedImage
from ..commands.registry import help_rows
from ..data.static import DotaConstants
from ..i18n.service import I18nService
from .assets import ImageAssets
from .renderer import Renderer

__all__ = ["ImageFactory"]


def _builder(module: str, name: str) -> Callable[..., dict[str, Any]]:
    return getattr(importlib.import_module(f"{__package__}.viewmodels.{module}"), name)


class ImageFactory:
    def __init__(
        self,
        renderer: Renderer,
        i18n: I18nService,
        constants: DotaConstants,
        assets: ImageAssets,
        tz: tzinfo | None = None,
    ) -> None:
        self.renderer = renderer
        self._i18n = i18n
        self._constants = constants
        self._assets = assets
        self._tz = tz

    def _t(self, locale: str) -> Callable[..., Any]:
        return lambda key, params=None: self._i18n.t(locale, key, params)

    def _t_html(self, locale: str) -> Callable[..., Any]:
        return lambda key, params=None: self._i18n.t_html(locale, key, params)

    def _text(self, locale: str) -> Callable[..., str]:
        return lambda key, params=None: self._i18n.text(locale, key, params)

    async def match(self, match: dict[str, Any], locale: str, template: str) -> RenderedImage:
        build = _builder("match", "build_match_view")
        view = build(match, self._t(locale), self._constants, self._assets, locale, self._tz)
        return await self.renderer.render_image(template, view, locale)

    async def player(self, player: dict[str, Any], locale: str, template: str) -> RenderedImage:
        view = _builder("player", "build_player_view")(player, self._t(locale), self._assets, locale, self._tz)
        return await self.renderer.render_image(template, view, locale)

    async def hero(self, hero: dict[str, Any], locale: str, template: str) -> RenderedImage:
        view = _builder("hero", "build_hero_view")(hero, self._constants, self._t(locale), self._assets, locale)
        return await self.renderer.render_image(template, view, locale)

    async def item(self, item: dict[str, Any], locale: str) -> RenderedImage:
        view = _builder("item", "build_item_view")(item, self._constants, self._t(locale), self._assets, locale)
        return await self.renderer.render_image("item", view, locale)

    async def itemlist(self, items: list[dict[str, Any]], locale: str) -> RenderedImage:
        view = _builder("item", "build_itemlist_view")(items, self._assets)
        return await self.renderer.render_image("itemlist", view, locale)

    async def help(self, locale: str) -> RenderedImage:
        rows = help_rows(lambda key: self._i18n.raw(locale, key), self._t(locale))
        header = self._i18n.raw(locale, "commands.dota2tracker.help.messages.header") or ""
        view = _builder("misc", "build_help_view")(rows, header)
        return await self.renderer.render_image("help", view, locale)

    async def members(self, members: list[dict[str, Any]], locale: str, now: datetime) -> RenderedImage:
        view = _builder("misc", "build_members_view")(
            members, locale, now, self._tz, self._t(locale), self._text(locale)
        )
        return await self.renderer.render_image("members", view, locale)

    async def hero_of_the_day(self, personal: dict[str, Any], meta: dict[str, Any], locale: str) -> RenderedImage:
        view = _builder("misc", "build_hero_of_the_day_view")(personal, meta, locale, self._i18n)
        return await self.renderer.render_image("hero_of_the_day", view, locale)

    async def daily(self, report: dict[str, Any], locale: str) -> RenderedImage:
        view = _builder("report", "build_daily_view")(report)
        return await self.renderer.render_image("daily", view, locale)

    async def daily_legacy(
        self,
        title: str,
        players: list[dict[str, Any]],
        combinations: list[dict[str, Any]],
        show_combi: bool,
        locale: str,
    ) -> RenderedImage:
        view = _builder("report", "build_daily_legacy_view")(title, players, combinations, show_combi, self._assets)
        return await self.renderer.render_image("daily_legacy", view, locale)

    async def rank_fun(
        self,
        name: str,
        avatar: str,
        rising: bool,
        previous: dict[str, Any],
        current: dict[str, Any],
        date_text: str,
        locale: str,
    ) -> RenderedImage:
        view = _builder("rank", "build_rank_fun_view")(
            name, avatar, rising, previous, current, date_text, self._t(locale), self._t_html(locale), self._assets
        )
        return await self.renderer.render_image("rank_fun", view, locale)
