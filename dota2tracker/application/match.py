"""Match retrieval use case (port of ``core/match.service.ts``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from ..data.cache import CacheService
from ..data.database import Database
from ..data.static import DotaConstants
from ..domain.match import build_match_extension, extend_match_data, is_match_parsed
from ..domain.opendota import transform_opendota_match
from ..errors import ErrorReporter, FetchMatchDataFailError

__all__ = ["MatchResult", "MatchService"]


@dataclass(frozen=True)
class MatchResult:
    status: Literal["READY", "PENDING", "NOT_FOUND"]
    match_id: int
    match_data: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status == "READY"


class MatchService:
    """Stratz first, optional OpenDota fallback, cached parsed results."""

    def __init__(
        self,
        *,
        stratz,
        opendota,
        cache: CacheService,
        db: Database,
        constants: DotaConstants,
        reporter: ErrorReporter,
        logger: logging.Logger,
    ) -> None:
        self._stratz = stratz
        self._opendota = opendota
        self._cache = cache
        self._db = db
        self._constants = constants
        self._reporter = reporter
        self._logger = logger

    @property
    def opendota_available(self) -> bool:
        return self._opendota is not None

    async def get_match_result(self, match_id: int, *, wait_for_parse: bool, allow_fallback: bool) -> MatchResult:
        """State machine documented in DEVELOPMENT.md §2.4."""
        allow_fallback = allow_fallback and self._opendota is not None
        stratz_data: dict[str, Any] | None = None
        stratz_error: BaseException | None = None
        try:
            stratz_data = await self._get_stratz_match(match_id)
        except Exception as error:
            stratz_error = error

        if stratz_data:
            if is_match_parsed(stratz_data) or not wait_for_parse:
                return MatchResult("READY", match_id, stratz_data)
            if not allow_fallback:
                return MatchResult("PENDING", match_id)
        elif stratz_error is not None:
            if not allow_fallback:
                raise stratz_error
        elif not allow_fallback:
            return MatchResult("NOT_FOUND", match_id)

        opendota_data: dict[str, Any] | None = None
        opendota_error: BaseException | None = None
        try:
            opendota_data = await self._get_opendota_match(match_id)
        except Exception as error:
            opendota_error = error

        if opendota_data:
            if stratz_error is not None:
                self._reporter.report(stratz_error, "match")
            return MatchResult("READY", match_id, opendota_data)
        if stratz_error is not None or opendota_error is not None:
            raise FetchMatchDataFailError(
                "Failed to fetch match data from all sources", stratz_error=stratz_error, opendota_error=opendota_error
            )
        if wait_for_parse:
            return MatchResult("PENDING", match_id)
        return MatchResult("NOT_FOUND", match_id)

    async def _get_stratz_match(self, match_id: int) -> dict[str, Any] | None:
        try:
            cached = await self._cache.get_match(match_id)
            if cached and cached.get("data") and cached.get("pluginVersion") == self._cache.plugin_version:
                query = cached["data"]
                await self._cache.set_match(match_id, query)  # refresh TTL like the original
            else:
                query = await self._stratz.query_match_info(match_id)
                if is_match_parsed(query):
                    await self._cache.set_match(match_id, query)
        except Exception:
            await self._cache.delete_match(match_id)
            raise
        # A response without ``match`` is treated as "not found" (see DEVELOPMENT.md §9).
        if not query or not query.get("match"):
            return None
        return query

    async def _get_opendota_match(self, match_id: int) -> dict[str, Any] | None:
        od_match = await self._opendota.query_match_info(match_id)
        if not ((od_match or {}).get("od_data") or {}).get("has_parsed"):
            return None
        query = {"match": transform_opendota_match(od_match, self._constants)}
        await self._cache.set_match(match_id, query)
        return query

    async def generate_match_data(self, match_query: dict[str, Any]) -> dict[str, Any]:
        """Extend the match for templates and persist the report snapshot."""
        match = extend_match_data(match_query, self._constants)
        try:
            await self._db.upsert_match_extension(
                int(match.get("id")), int(match.get("startDateTime") or 0), build_match_extension(match)
            )
        except Exception as error:
            self._logger.warning("Failed to record match extension %s: %s", match.get("id"), error)
        return match
