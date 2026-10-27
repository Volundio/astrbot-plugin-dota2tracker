"""Stratz GraphQL client (port of ``data/stratz.api.ts``)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from ..constants import GRAPHQL_DIR
from ..errors import GraphQLQueryError, wrap_fetch_error
from .http import REQUEST_DEADLINE, RequestQueue, build_client

__all__ = ["StratzAPI"]

BASE_URL = "https://api.stratz.com/graphql"
CHUNK_SIZE = 5

Validator = Callable[[Any], bool]


@lru_cache(maxsize=32)
def load_graphql(name: str, directory: Path = GRAPHQL_DIR) -> str:
    """Load a ``.graphql`` file and collapse line breaks like the original."""
    text = (directory / f"{name}.graphql").read_text(encoding="utf-8")
    return re.sub(r"[\r\n]+", " ", text)


def _drop_none(variables: dict[str, Any]) -> dict[str, Any]:
    # ``JSON.stringify`` drops ``undefined`` values; GraphQL then uses defaults.
    return {key: value for key, value in variables.items() if value is not None}


class StratzAPI:
    """Rate limited Stratz client. All public methods return the ``data`` object."""

    service_name = "Stratz"

    def __init__(
        self,
        token: str,
        *,
        proxy: str | None = None,
        logger: logging.Logger | None = None,
        translate=None,
        interval: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._logger = logger or logging.getLogger(__name__)
        self._translate = translate or (lambda key, params=None: key)
        self._queue = RequestQueue(interval)
        self._client = build_client(
            proxy=proxy,
            transport=transport,
            headers={"User-Agent": "STRATZ_API", "Content-Type": "application/json"},
        )
        self.request_count = 0
        self.last_success: float | None = None
        self.last_error: str | None = None

    @property
    def queue(self) -> RequestQueue:
        return self._queue

    async def aclose(self) -> None:
        self._queue.dispose()
        await self._client.aclose()

    # ------------------------------------------------------------------ queries
    async def query_weekly_meta_by_position(self, bracket_ids: Sequence[str]) -> dict[str, Any]:
        return await self.query(
            "GetWeeklyMetaByPosition", {"bracketIds": list(bracket_ids)}, lambda d: bool(d and d.get("heroStats"))
        )

    async def query_player_performance_for_hero_recommendation(self, steam_id: int, recent_date_time: int):
        return await self.query(
            "PlayerPerformanceForHeroRecommendation",
            {"steamAccountId": steam_id, "recentDateTime": recent_date_time},
            lambda d: bool(d and d.get("player")),
        )

    async def query_players_matches_for_daily_legacy(self, steam_ids: Sequence[int], seconds: int):
        return await self.query(
            "PlayersMatchesForDaily_legacy",
            {"steamAccountIds": list(steam_ids), "seconds": seconds},
            lambda d: bool(d and d.get("players")),
        )

    async def query_players_matches_for_daily(self, steam_ids: Sequence[int], seconds: int):
        return await self.query(
            "PlayersMatchesForDaily",
            {"steamAccountIds": list(steam_ids), "seconds": seconds},
            lambda d: bool(d and d.get("players")),
        )

    async def query_verifying_player(self, steam_id: int):
        return await self.query("VerifyingPlayer", {"steamAccountId": steam_id}, lambda d: bool(d and d.get("player")))

    async def query_player_extra_info(self, steam_id: int, match_count: int, hero_ids: Any = None):
        return await self.query(
            "PlayerExtraInfo",
            {"steamAccountId": steam_id, "matchCount": match_count, "heroIds": hero_ids},
            lambda d: bool(d and d.get("player")),
        )

    async def query_players_info_with_10_matches_for_guild(self, steam_ids: Sequence[int]):
        return await self.query(
            "PlayersInfoWith10MatchesForGuild",
            {"steamAccountIds": list(steam_ids)},
            lambda d: bool(d and d.get("players")),
        )

    async def query_player_info_with_25_matches(self, steam_id: int, hero_ids: Any = None):
        return await self.query(
            "PlayerInfoWith25Matches",
            {"steamAccountId": steam_id, "heroIds": hero_ids},
            lambda d: bool(d and d.get("player")),
        )

    async def query_players_last_match_rank_info(self, steam_ids: Sequence[int]):
        return await self.query(
            "PlayersLastmatchRankinfo", {"steamAccountIds": list(steam_ids)}, lambda d: bool(d and d.get("players"))
        )

    async def query_match_info(self, match_id: int):
        return await self.query("MatchInfo", {"matchId": match_id}, lambda d: bool(d and d.get("match")))

    async def request_parse_match(self, match_id: int) -> Any:
        response = await self.query("RequestMatchDataAnalysis", {"matchId": match_id})
        return ((response or {}).get("stratz") or {}).get("matchRetry")

    # ------------------------------------------------------------------- core
    async def query(self, query_name: str, variables: dict[str, Any] | None = None, is_valid: Validator | None = None):
        validator = is_valid or (lambda _data: True)
        variables = dict(variables or {})
        steam_ids = variables.get("steamAccountIds")
        if query_name.startswith("Players") and isinstance(steam_ids, list) and len(steam_ids) > CHUNK_SIZE:
            players: list[Any] = []
            for start in range(0, len(steam_ids), CHUNK_SIZE):
                chunk_variables = {**variables, "steamAccountIds": steam_ids[start : start + CHUNK_SIZE]}
                result = await self._fetch(query_name, chunk_variables, validator)
                if result and isinstance(result.get("players"), list):
                    players.extend(result["players"])
            return {"players": players}
        return await self._fetch(query_name, variables, validator)

    async def _fetch(self, query_name: str, variables: dict[str, Any], validator: Validator) -> Any:
        payload = {"query": load_graphql(query_name), "variables": _drop_none(variables)}

        async def task() -> Any:
            self.request_count += 1
            try:
                # httpx's timeout applies per phase (connect/read/…); like the original's
                # axios timeout, the whole request is bounded so a trickling response
                # cannot hold the shared queue.
                async with asyncio.timeout(REQUEST_DEADLINE):
                    response = await self._client.post(
                        BASE_URL,
                        content=json.dumps(payload),
                        headers={"Authorization": f"Bearer {self._token}"},
                    )
                response.raise_for_status()
                result = response.json()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                wrapped = wrap_fetch_error(error, self.service_name, query_name)
                self.last_error = f"{type(wrapped).__name__}: {wrapped}"
                raise wrapped from error
            data = result.get("data") if isinstance(result, dict) else None
            errors = result.get("errors") if isinstance(result, dict) else None
            valid = bool(validator(data))
            if errors:
                messages = [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors]
                if valid:
                    self._logger.warning(
                        self._translate("dota2tracker.logger.stratz_api_query_error", {"cause": "\n".join(messages)})
                    )
                    self._mark_success()
                    return data
                self.last_error = "GraphQLQueryError"
                raise GraphQLQueryError("Stratz API query failed and returned invalid data", messages)
            self._mark_success()
            return data

        return await self._queue.run(task)

    def _mark_success(self) -> None:
        self.last_success = time.time()
        self.last_error = None
