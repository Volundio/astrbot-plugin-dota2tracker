"""TTL cache backed by SQLite (port of ``data/cache.ts``).

TTL rules (see DEVELOPMENT.md §3.4):

* parsed Stratz matches – 30 days, tagged with the plugin version;
* sent-match markers – 30 days (``sent_matches`` table);
* weekly hero meta – until the end of the current UTC day;
* latest patch notes – 6 hours;
* item lists – until the game version changes;
* OpenDota request log – until the end of the current UTC day.
"""

from __future__ import annotations

import itertools
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ..constants import DAYS_30
from .database import Database

__all__ = ["CacheService", "seconds_until_utc_end_of_day"]

NS_MATCH = "match"
NS_ITEM_LIST = "item_list"
NS_WEEKLY_META = "weekly_meta"
NS_PATCH_NOTES = "patch_notes"
NS_OPENDOTA_LOG = "opendota_request_log"
NS_RUNTIME = "runtime"

PATCH_NOTES_TTL = 6 * 3600


def seconds_until_utc_end_of_day(now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    end = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((end - current).total_seconds()))


class CacheService:
    """Typed accessors over the ``cache_entries`` table."""

    def __init__(self, db: Database, plugin_version: str) -> None:
        self._db = db
        self._plugin_version = plugin_version
        self._counter = itertools.count()

    @staticmethod
    def _expires(ttl: int) -> int:
        return int(time.time()) + ttl

    # ---------------------------------------------------------------- OpenDota
    async def add_opendota_request_log(self, request: str, count: int = 1) -> None:
        key = f"{time.time_ns()}-{next(self._counter)}"
        await self._db.cache_set(
            NS_OPENDOTA_LOG, key, {"count": count, "request": request}, self._expires(seconds_until_utc_end_of_day())
        )

    async def today_opendota_request_count(self) -> int:
        return sum(int(entry.get("count", 0)) for entry in await self._db.cache_values(NS_OPENDOTA_LOG))

    # ------------------------------------------------------------- weekly meta
    async def set_weekly_meta(self, key: str, value: Any) -> None:
        await self._db.cache_set(NS_WEEKLY_META, key, value, self._expires(seconds_until_utc_end_of_day()))

    async def get_weekly_meta(self, key: str) -> Any:
        return await self._db.cache_get(NS_WEEKLY_META, key)

    # ------------------------------------------------------------- patch notes
    async def set_patch_notes(self, key: str, value: Any) -> None:
        await self._db.cache_set(NS_PATCH_NOTES, key, value, self._expires(PATCH_NOTES_TTL))

    async def get_patch_notes(self, key: str) -> Any:
        return await self._db.cache_get(NS_PATCH_NOTES, key)

    # ---------------------------------------------------------------- items
    async def set_item_list(self, locale: str, item_list: list[dict[str, Any]], game_version: str) -> None:
        await self._db.cache_set(NS_ITEM_LIST, locale, {"gameVersion": game_version, "itemList": item_list}, None)

    async def get_item_list(self, locale: str) -> dict[str, Any] | None:
        return await self._db.cache_get(NS_ITEM_LIST, locale)

    # ---------------------------------------------------------------- matches
    async def get_match(self, match_id: int) -> dict[str, Any] | None:
        return await self._db.cache_get(NS_MATCH, str(match_id))

    async def set_match(self, match_id: int, match_query: dict[str, Any]) -> None:
        await self._db.cache_set(
            NS_MATCH,
            str(match_id),
            {"data": match_query, "pluginVersion": self._plugin_version},
            self._expires(DAYS_30),
        )

    async def delete_match(self, match_id: int) -> None:
        await self._db.cache_delete(NS_MATCH, str(match_id))

    @property
    def plugin_version(self) -> str:
        return self._plugin_version

    # ----------------------------------------------------------- sent markers
    async def mark_match_sent(self, match_id: int) -> None:
        await self._db.mark_match_sent(match_id, DAYS_30)

    async def finish_pending_match(self, match_id: int) -> None:
        """Remove the pending entry and write the 30-day sent marker in one transaction."""
        await self._db.finish_pending_match(match_id, DAYS_30)

    async def sent_match_ids(self) -> set[int]:
        return await self._db.sent_match_ids()

    # ------------------------------------------------------------- maintenance
    async def stats(self) -> list[dict[str, Any]]:
        return await self._db.cache_stats()

    async def clear(self, namespace: str) -> int:
        return await self._db.cache_delete(namespace)

    async def purge_expired(self) -> dict[str, int]:
        return await self._db.purge_expired()


CLEARABLE_NAMESPACES = (NS_MATCH, NS_ITEM_LIST, NS_WEEKLY_META, NS_PATCH_NOTES)
