"""Player queries: profiles, guild roster, last match and SteamID validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from ..data.static import DotaConstants
from ..domain.player import build_guild_members, extend_player_data
from ..i18n.service import I18nService

__all__ = ["LastMatch", "PlayerService", "SteamValidation"]


@dataclass(frozen=True)
class LastMatch:
    id: int = 0
    is_anonymous: bool = False


@dataclass(frozen=True)
class SteamValidation:
    status: Literal["VALID", "NO_DOTA_PROFILE", "API_ERROR"]
    is_anonymous: bool = False
    reason: str = ""


class PlayerService:
    def __init__(
        self,
        *,
        stratz,
        constants: DotaConstants,
        i18n: I18nService,
        logger: logging.Logger,
        estimate_rank: bool,
    ) -> None:
        self._stratz = stratz
        self._constants = constants
        self._i18n = i18n
        self._logger = logger
        self._estimate_rank = estimate_rank

    async def validate_steam_id(self, steam_id: int) -> SteamValidation:
        try:
            result = await self._stratz.query_verifying_player(int(steam_id))
        except Exception as error:
            self._logger.debug("Failed to validate SteamID %s: %s", steam_id, error)
            return SteamValidation("API_ERROR", reason=".reason_fetch_failed")
        player = (result or {}).get("player") or {}
        if player.get("matchCount"):
            return SteamValidation("VALID", is_anonymous=bool((player.get("steamAccount") or {}).get("isAnonymous")))
        return SteamValidation("NO_DOTA_PROFILE", reason=".reason_without_match")

    async def last_match(self, steam_id: int) -> LastMatch:
        try:
            result = await self._stratz.query_players_last_match_rank_info([int(steam_id)])
            players = (result or {}).get("players") or []
            first = players[0]
            if (first.get("steamAccount") or {}).get("isAnonymous"):
                return LastMatch(0, True)
            matches = first.get("matches") or []
            return LastMatch(int(matches[0].get("id") or 0) if matches else 0)
        except Exception as error:
            self._logger.error("Failed to get last match of %s: %s", steam_id, error)
            return LastMatch(0)

    async def formatted_player_data(self, steam_id: int, hero_id: int | None, locale: str) -> dict[str, Any]:
        player_query = await self._stratz.query_player_info_with_25_matches(int(steam_id), hero_id)
        player = (player_query or {}).get("player") or {}
        if not player:
            raise LookupError(f"player {steam_id} not found")
        if not (player.get("steamAccount") or {}).get("isAnonymous"):
            extra = await self._stratz.query_player_extra_info(int(steam_id), player.get("matchCount") or 0, hero_id)
        else:
            extra = {"player": {"heroesPerformance": [], "dotaPlus": None}}
        gen_hero = None
        if hero_id:
            names = self._i18n.constant_locale(locale)["dota2tracker"]["template"]["hero_names"]
            gen_hero = {"heroId": hero_id, "name": names.get(str(hero_id))}
        return extend_player_data(
            player_query, extra, self._constants, gen_hero=gen_hero, estimate_rank=self._estimate_rank
        )

    async def guild_members(self, bindings: list[Any]) -> list[dict[str, Any]]:
        if not bindings:
            return []
        result = await self._stratz.query_players_info_with_10_matches_for_guild([b.steam_id for b in bindings])
        return build_guild_members((result or {}).get("players") or [], bindings)
