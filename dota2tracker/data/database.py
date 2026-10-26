"""SQLite persistence (``aiosqlite``) with versioned migrations and repositories.

The database lives in ``data/plugin_data/<plugin>/dota2tracker.sqlite3``. All
public operations are idempotent where the original commands require it
(subscribe twice, bind twice, unbind a missing user…), and multi statement
updates run inside explicit transactions guarded by an ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

__all__ = [
    "SCHEMA_VERSION",
    "AlreadyBoundError",
    "AuditEntry",
    "ChannelRecord",
    "Database",
    "PendingMatchRecord",
    "PlayerRecord",
]

SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            umo TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            platform_type TEXT NOT NULL DEFAULT '',
            channel_id TEXT NOT NULL,
            subscribed INTEGER NOT NULL DEFAULT 0,
            locale TEXT,
            name TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(platform, channel_id)
        )""",
        """CREATE TABLE IF NOT EXISTS players(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            umo TEXT NOT NULL,
            platform TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            steam_id INTEGER NOT NULL,
            nickname TEXT NOT NULL DEFAULT '',
            rank_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(platform, channel_id, user_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_players_channel ON players(platform, channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_players_steam ON players(steam_id)",
        """CREATE TABLE IF NOT EXISTS match_extensions(
            match_id INTEGER PRIMARY KEY,
            start_time INTEGER NOT NULL,
            data_json TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_match_extensions_start ON match_extensions(start_time)",
        """CREATE TABLE IF NOT EXISTS sent_matches(
            match_id INTEGER PRIMARY KEY,
            expires_at INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS pending_matches(
            match_id INTEGER PRIMARY KEY,
            request_time INTEGER NOT NULL,
            subscribers_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS cache_entries(
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            expires_at INTEGER,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(namespace, key)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at)",
        """CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        )""",
    ],
}


class AlreadyBoundError(Exception):
    """The user already has a binding in this channel."""


def _now() -> int:
    return int(time.time())


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class ChannelRecord:
    id: int
    umo: str
    platform: str
    platform_type: str
    channel_id: str
    subscribed: bool
    locale: str | None
    name: str
    created_at: int
    updated_at: int

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.channel_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "umo": self.umo,
            "platform": self.platform,
            "platform_type": self.platform_type,
            "channel_id": self.channel_id,
            "subscribed": self.subscribed,
            "locale": self.locale,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PlayerRecord:
    id: int
    umo: str
    platform: str
    channel_id: str
    user_id: str
    steam_id: int
    nickname: str
    rank: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0

    @property
    def channel_key(self) -> str:
        return f"{self.platform}:{self.channel_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "umo": self.umo,
            "platform": self.platform,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "steam_id": self.steam_id,
            "nickname": self.nickname,
            "rank": self.rank,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PendingMatchRecord:
    match_id: int
    request_time: int
    subscribers: list[dict[str, Any]]
    updated_at: int


@dataclass
class AuditEntry:
    id: int
    created_at: int
    actor: str
    action: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "actor": self.actor,
            "action": self.action,
            "detail": self.detail,
        }


def _channel(row: aiosqlite.Row) -> ChannelRecord:
    return ChannelRecord(
        id=row["id"],
        umo=row["umo"],
        platform=row["platform"],
        platform_type=row["platform_type"],
        channel_id=row["channel_id"],
        subscribed=bool(row["subscribed"]),
        locale=row["locale"],
        name=row["name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _player(row: aiosqlite.Row) -> PlayerRecord:
    try:
        rank = json.loads(row["rank_json"] or "{}")
    except json.JSONDecodeError:
        rank = {}
    return PlayerRecord(
        id=row["id"],
        umo=row["umo"],
        platform=row["platform"],
        channel_id=row["channel_id"],
        user_id=row["user_id"],
        steam_id=int(row["steam_id"]),
        nickname=row["nickname"],
        rank=rank if isinstance(rank, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Database:
    """Owns the aiosqlite connection; repositories are exposed as methods."""

    def __init__(self, path: Path, logger: logging.Logger | None = None) -> None:
        self.path = path
        self._logger = logger or logging.getLogger(__name__)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- lifecycle
    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        await self._migrate()

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn

    async def _migrate(self) -> None:
        async with self.transaction():
            await self.conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = await self._fetchone("SELECT value FROM meta WHERE key='schema_version'")
            current = int(row["value"]) if row else 0
            for version in sorted(_MIGRATIONS):
                if version <= current:
                    continue
                for statement in _MIGRATIONS[version]:
                    await self.conn.execute(statement)
                await self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(version),),
                )
                self._logger.info("dota2tracker database migrated to schema v%s", version)

    async def schema_version(self) -> int:
        row = await self._fetchone("SELECT value FROM meta WHERE key='schema_version'")
        return int(row["value"]) if row else 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            else:
                await self.conn.execute("COMMIT")

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            try:
                return cursor.rowcount
            finally:
                await cursor.close()

    # ------------------------------------------------------------------ channels
    async def get_channel(self, platform: str, channel_id: str) -> ChannelRecord | None:
        row = await self._fetchone("SELECT * FROM channels WHERE platform=? AND channel_id=?", (platform, channel_id))
        return _channel(row) if row else None

    async def get_channel_by_id(self, channel_pk: int) -> ChannelRecord | None:
        row = await self._fetchone("SELECT * FROM channels WHERE id=?", (channel_pk,))
        return _channel(row) if row else None

    async def list_channels(self, *, subscribed_only: bool = False) -> list[ChannelRecord]:
        sql = "SELECT * FROM channels"
        if subscribed_only:
            sql += " WHERE subscribed=1"
        rows = await self._fetchall(sql + " ORDER BY id")
        return [_channel(row) for row in rows]

    async def subscribe_channel(
        self, umo: str, platform: str, platform_type: str, channel_id: str, name: str = ""
    ) -> bool:
        """Subscribe a channel. Returns ``False`` when it was already subscribed."""
        now = _now()
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT id, subscribed FROM channels WHERE platform=? AND channel_id=?", (platform, channel_id)
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None and row["subscribed"]:
                return False
            if row is not None:
                await conn.execute(
                    "UPDATE channels SET subscribed=1, umo=?, platform_type=?,"
                    " name=CASE WHEN ?='' THEN name ELSE ? END,"
                    " updated_at=? WHERE id=?",
                    (umo, platform_type, name, name, now, row["id"]),
                )
            else:
                await conn.execute(
                    "INSERT INTO channels(umo, platform, platform_type, channel_id, subscribed, name,"
                    " created_at, updated_at)"
                    " VALUES(?, ?, ?, ?, 1, ?, ?, ?)"
                    " ON CONFLICT(umo) DO UPDATE SET platform=excluded.platform, channel_id=excluded.channel_id,"
                    " subscribed=1, updated_at=excluded.updated_at",
                    (umo, platform, platform_type, channel_id, name, now, now),
                )
            return True

    async def unsubscribe_channel(self, platform: str, channel_id: str) -> bool:
        """Unsubscribe; returns ``False`` when the channel was not subscribed."""
        changed = await self._execute(
            "UPDATE channels SET subscribed=0, updated_at=? WHERE platform=? AND channel_id=? AND subscribed=1",
            (_now(), platform, channel_id),
        )
        return changed > 0

    async def is_channel_subscribed(self, platform: str, channel_id: str) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM channels WHERE platform=? AND channel_id=? AND subscribed=1", (platform, channel_id)
        )
        return row is not None

    async def set_channel_locale(self, channel_pk: int, locale: str | None) -> bool:
        changed = await self._execute(
            "UPDATE channels SET locale=?, updated_at=? WHERE id=?", (locale or None, _now(), channel_pk)
        )
        return changed > 0

    async def set_channel_name(self, platform: str, channel_id: str, name: str) -> None:
        await self._execute(
            "UPDATE channels SET name=?, updated_at=? WHERE platform=? AND channel_id=? AND name<>?",
            (name, _now(), platform, channel_id, name),
        )

    async def channel_locale(self, platform: str, channel_id: str) -> str | None:
        row = await self._fetchone(
            "SELECT locale FROM channels WHERE platform=? AND channel_id=?", (platform, channel_id)
        )
        return row["locale"] if row else None

    async def delete_channel(self, channel_pk: int) -> bool:
        return await self._execute("DELETE FROM channels WHERE id=?", (channel_pk,)) > 0

    # ------------------------------------------------------------------- players
    async def get_player(self, platform: str, channel_id: str, user_id: str) -> PlayerRecord | None:
        row = await self._fetchone(
            "SELECT * FROM players WHERE platform=? AND channel_id=? AND user_id=?", (platform, channel_id, user_id)
        )
        return _player(row) if row else None

    async def get_player_by_id(self, player_pk: int) -> PlayerRecord | None:
        row = await self._fetchone("SELECT * FROM players WHERE id=?", (player_pk,))
        return _player(row) if row else None

    async def find_player_by_nickname(self, platform: str, channel_id: str, nickname: str) -> PlayerRecord | None:
        row = await self._fetchone(
            "SELECT * FROM players WHERE platform=? AND channel_id=? AND nickname=? ORDER BY id LIMIT 1",
            (platform, channel_id, nickname),
        )
        return _player(row) if row else None

    async def list_players(self, platform: str | None = None, channel_id: str | None = None) -> list[PlayerRecord]:
        if platform is None:
            rows = await self._fetchall("SELECT * FROM players ORDER BY id")
        else:
            rows = await self._fetchall(
                "SELECT * FROM players WHERE platform=? AND channel_id=? ORDER BY id", (platform, channel_id)
            )
        return [_player(row) for row in rows]

    async def list_players_in_subscribed_channels(self) -> list[PlayerRecord]:
        rows = await self._fetchall(
            "SELECT p.* FROM players p JOIN channels c ON c.platform=p.platform AND c.channel_id=p.channel_id"
            " WHERE c.subscribed=1 ORDER BY p.id"
        )
        return [_player(row) for row in rows]

    async def bind_player(
        self, umo: str, platform: str, channel_id: str, user_id: str, steam_id: int, nickname: str = ""
    ) -> PlayerRecord:
        now = _now()
        try:
            async with self._lock:
                cursor = await self.conn.execute(
                    "INSERT INTO players(umo, platform, channel_id, user_id, steam_id, nickname, rank_json,"
                    " created_at, updated_at)"
                    " VALUES(?, ?, ?, ?, ?, ?, '{}', ?, ?)",
                    (umo, platform, channel_id, user_id, int(steam_id), nickname or "", now, now),
                )
                player_pk = cursor.lastrowid
                await cursor.close()
        except aiosqlite.IntegrityError as error:
            raise AlreadyBoundError(user_id) from error
        record = await self.get_player_by_id(int(player_pk or 0))
        assert record is not None
        return record

    async def unbind_player(self, platform: str, channel_id: str, user_id: str) -> bool:
        return (
            await self._execute(
                "DELETE FROM players WHERE platform=? AND channel_id=? AND user_id=?", (platform, channel_id, user_id)
            )
            > 0
        )

    async def delete_player(self, player_pk: int) -> bool:
        return await self._execute("DELETE FROM players WHERE id=?", (player_pk,)) > 0

    async def rename_player(self, player_pk: int, nickname: str) -> bool:
        return (
            await self._execute("UPDATE players SET nickname=?, updated_at=? WHERE id=?", (nickname, _now(), player_pk))
            > 0
        )

    async def set_player_rank(self, player_pk: int, rank: dict[str, Any]) -> None:
        await self._execute(
            "UPDATE players SET rank_json=?, updated_at=? WHERE id=?", (_dumps(rank), _now(), player_pk)
        )

    # ---------------------------------------------------------- match extensions
    async def upsert_match_extension(self, match_id: int, start_time: int, data: dict[str, Any]) -> None:
        await self._execute(
            "INSERT INTO match_extensions(match_id, start_time, data_json) VALUES(?, ?, ?)"
            " ON CONFLICT(match_id) DO UPDATE SET start_time=excluded.start_time, data_json=excluded.data_json",
            (int(match_id), int(start_time), _dumps(data)),
        )

    async def get_match_extensions(self, match_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = sorted({int(i) for i in match_ids})
        result: list[dict[str, Any]] = []
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = await self._fetchall(
                f"SELECT match_id, start_time, data_json FROM match_extensions WHERE match_id IN ({placeholders})",
                chunk,
            )
            for row in rows:
                result.append(
                    {
                        "matchId": int(row["match_id"]),
                        "startTime": int(row["start_time"]),
                        "data": json.loads(row["data_json"]),
                    }
                )
        return result

    async def count_match_extensions(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) AS n FROM match_extensions")
        return int(row["n"]) if row else 0

    # -------------------------------------------------------------- sent matches
    async def mark_match_sent(self, match_id: int, ttl_seconds: int) -> None:
        await self._execute(
            "INSERT INTO sent_matches(match_id, expires_at) VALUES(?, ?)"
            " ON CONFLICT(match_id) DO UPDATE SET expires_at=excluded.expires_at",
            (int(match_id), _now() + ttl_seconds),
        )

    async def sent_match_ids(self) -> set[int]:
        rows = await self._fetchall("SELECT match_id FROM sent_matches WHERE expires_at>?", (_now(),))
        return {int(row["match_id"]) for row in rows}

    async def is_match_sent(self, match_id: int) -> bool:
        row = await self._fetchone(
            "SELECT 1 FROM sent_matches WHERE match_id=? AND expires_at>?", (int(match_id), _now())
        )
        return row is not None

    # ----------------------------------------------------------- pending matches
    async def save_pending_match(self, match_id: int, request_time: int, subscribers: list[dict[str, Any]]) -> None:
        await self._execute(
            "INSERT INTO pending_matches(match_id, request_time, subscribers_json, updated_at) VALUES(?, ?, ?, ?)"
            " ON CONFLICT(match_id) DO UPDATE SET subscribers_json=excluded.subscribers_json,"
            " updated_at=excluded.updated_at",
            (int(match_id), int(request_time), _dumps(subscribers), _now()),
        )

    async def delete_pending_match(self, match_id: int) -> None:
        await self._execute("DELETE FROM pending_matches WHERE match_id=?", (int(match_id),))

    async def list_pending_matches(self) -> list[PendingMatchRecord]:
        rows = await self._fetchall("SELECT * FROM pending_matches ORDER BY request_time, match_id")
        records = []
        for row in rows:
            try:
                subscribers = json.loads(row["subscribers_json"])
            except json.JSONDecodeError:
                subscribers = []
            records.append(
                PendingMatchRecord(
                    match_id=int(row["match_id"]),
                    request_time=int(row["request_time"]),
                    subscribers=subscribers if isinstance(subscribers, list) else [],
                    updated_at=int(row["updated_at"]),
                )
            )
        return records

    # --------------------------------------------------------------------- cache
    async def cache_get(self, namespace: str, key: str) -> Any:
        row = await self._fetchone(
            "SELECT value_json, expires_at FROM cache_entries WHERE namespace=? AND key=?", (namespace, key)
        )
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] <= _now():
            return None
        return json.loads(row["value_json"])

    async def cache_set(self, namespace: str, key: str, value: Any, expires_at: int | None) -> None:
        await self._execute(
            "INSERT INTO cache_entries(namespace, key, value_json, expires_at, updated_at) VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(namespace, key) DO UPDATE SET value_json=excluded.value_json, expires_at=excluded.expires_at,"
            " updated_at=excluded.updated_at",
            (namespace, key, _dumps(value), expires_at, _now()),
        )

    async def cache_delete(self, namespace: str, key: str | None = None) -> int:
        if key is None:
            return await self._execute("DELETE FROM cache_entries WHERE namespace=?", (namespace,))
        return await self._execute("DELETE FROM cache_entries WHERE namespace=? AND key=?", (namespace, key))

    async def cache_values(self, namespace: str) -> list[Any]:
        rows = await self._fetchall(
            "SELECT value_json FROM cache_entries WHERE namespace=? AND (expires_at IS NULL OR expires_at>?)",
            (namespace, _now()),
        )
        return [json.loads(row["value_json"]) for row in rows]

    async def cache_stats(self) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT namespace, COUNT(*) AS entries, SUM(LENGTH(value_json)) AS bytes,"
            " SUM(CASE WHEN expires_at IS NOT NULL AND expires_at<=? THEN 1 ELSE 0 END) AS expired"
            " FROM cache_entries GROUP BY namespace ORDER BY namespace",
            (_now(),),
        )
        return [
            {
                "namespace": row["namespace"],
                "entries": row["entries"],
                "bytes": row["bytes"] or 0,
                "expired": row["expired"],
            }
            for row in rows
        ]

    async def purge_expired(self) -> dict[str, int]:
        now = _now()
        cache = await self._execute("DELETE FROM cache_entries WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
        sent = await self._execute("DELETE FROM sent_matches WHERE expires_at<=?", (now,))
        return {"cache_entries": cache, "sent_matches": sent}

    # --------------------------------------------------------------------- audit
    async def add_audit(self, actor: str, action: str, detail: dict[str, Any] | None = None, keep: int = 500) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO audit_log(created_at, actor, action, detail_json) VALUES(?, ?, ?, ?)",
                (_now(), actor or "unknown", action, _dumps(detail or {})),
            )
            await conn.execute(
                "DELETE FROM audit_log WHERE id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)", (keep,)
            )

    async def list_audit(self, limit: int = 50) -> list[AuditEntry]:
        rows = await self._fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [
            AuditEntry(
                id=row["id"],
                created_at=row["created_at"],
                actor=row["actor"],
                action=row["action"],
                detail=json.loads(row["detail_json"] or "{}"),
            )
            for row in rows
        ]

    # ----------------------------------------------------------------- summary
    async def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for name, sql in (
            ("channels", "SELECT COUNT(*) AS n FROM channels"),
            ("subscribed_channels", "SELECT COUNT(*) AS n FROM channels WHERE subscribed=1"),
            ("players", "SELECT COUNT(*) AS n FROM players"),
            ("match_extensions", "SELECT COUNT(*) AS n FROM match_extensions"),
            ("sent_matches", "SELECT COUNT(*) AS n FROM sent_matches WHERE expires_at>strftime('%s','now')"),
            ("pending_matches", "SELECT COUNT(*) AS n FROM pending_matches"),
        ):
            row = await self._fetchone(sql)
            result[name] = int(row["n"]) if row else 0
        return result
