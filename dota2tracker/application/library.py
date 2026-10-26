"""Hero, item and hero-of-the-day use cases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ..config import CustomItemAlias
from ..data.cache import CacheService
from ..data.static import DotaConstants, StaticDataService
from ..domain.hero import (
    format_hero_details,
    process_weekly_meta,
    recommend_weekly_meta,
    weekly_meta_brackets,
    weekly_meta_cache_key,
)
from ..domain.item import format_item_details, format_item_list, search_items
from ..domain.player import estimate_weighted_rank, hero_recommendation
from ..i18n.service import I18nService
from ..utils.koishi_random import KoishiRandom
from ..utils.timefmt import local_now, luxon_format, minus_days

__all__ = ["HeroService", "ItemService", "RecommendationService"]

StaleCallback = Callable[[], Awaitable[None] | None] | None


class HeroService:
    def __init__(
        self,
        *,
        valve,
        stratz,
        static_data: StaticDataService,
        cache: CacheService,
        constants: DotaConstants,
        i18n: I18nService,
        clock: Callable[[], datetime] = local_now,
    ) -> None:
        self._valve = valve
        self._stratz = stratz
        self._static = static_data
        self._cache = cache
        self._constants = constants
        self._i18n = i18n
        self._clock = clock

    def resolve_hero_id(self, value: Any, *, random: bool = False) -> int | None:
        if random:
            value = KoishiRandom().pick([str(h) for h in self._constants.hero_ids()])
        return self._i18n.find_hero_id(value)

    async def hero_details(self, hero_id: int, locale: str, on_stale: StaleCallback = None) -> dict[str, Any]:
        patch_notes = await self._static.get_patch_notes(locale, on_stale)
        raw = await self._valve.query_hero_details(hero_id, locale)
        return format_hero_details(raw, patch_notes, self._clock())

    async def weekly_hero_meta(self, rank: int) -> dict[str, Any]:
        target_tiers, brackets = weekly_meta_brackets(rank)
        key = weekly_meta_cache_key(luxon_format(datetime.now(UTC), "yyyy-MM-dd"), brackets)
        meta = await self._cache.get_weekly_meta(key)
        if not meta:
            result = await self._stratz.query_weekly_meta_by_position(brackets)
            meta = process_weekly_meta((result or {}).get("heroStats") or {})
            await self._cache.set_weekly_meta(key, meta)
        return {"recommendation": recommend_weekly_meta(meta), "targetTiers": target_tiers}


class ItemService:
    def __init__(
        self,
        *,
        valve,
        static_data: StaticDataService,
        cache: CacheService,
        i18n: I18nService,
        custom_aliases: tuple[CustomItemAlias, ...],
        clock: Callable[[], datetime] = local_now,
    ) -> None:
        self._valve = valve
        self._static = static_data
        self._cache = cache
        self._i18n = i18n
        self._custom_aliases = custom_aliases
        self._clock = clock

    async def item_list(self, locale: str, on_cache_miss: StaleCallback = None) -> list[dict[str, Any]]:
        version = await self._valve.query_last_patch_number()
        cached = await self._cache.get_item_list(locale)
        if not cached or cached.get("gameVersion") != version:
            if on_cache_miss is not None:
                outcome = on_cache_miss()
                if outcome is not None:
                    await outcome
            items = format_item_list(await self._valve.query_item_list(locale))
            await self._cache.set_item_list(locale, items, version)
            return items
        return cached.get("itemList") or []

    def search(self, items: list[dict[str, Any]], keyword: str, locale: str) -> list[dict[str, Any]]:
        aliases = self._i18n.constant_locale(locale).get("dota2tracker", {}).get("items_alias")
        return search_items(items, keyword, aliases, self._custom_aliases)

    async def item_details(self, item_id: int, locale: str, on_stale: StaleCallback = None) -> dict[str, Any]:
        patch_notes = await self._static.get_patch_notes(locale, on_stale)
        raw = await self._valve.query_item_details(item_id, locale)
        return format_item_details(raw, patch_notes, self._clock())


class RecommendationService:
    """``hero-of-the-day``: personal picks plus weekly meta for the player's bracket."""

    def __init__(self, *, stratz, heroes: HeroService, clock: Callable[[], datetime] = local_now) -> None:
        self._stratz = stratz
        self._heroes = heroes
        self._clock = clock

    async def recommend(self, steam_id: int, days: float) -> tuple[dict[str, Any], dict[str, Any]]:
        now = self._clock()
        recent = int(minus_days(now, days).timestamp())
        result = await self._stratz.query_player_performance_for_hero_recommendation(int(steam_id), recent)
        player = (result or {}).get("player") or {}
        personal = hero_recommendation(steam_id, player, now)
        meta = await self._heroes.weekly_hero_meta(estimate_weighted_rank(player))
        return personal, meta
