"""Tests for :mod:`dota2tracker.data.cache` TTL rules (DEVELOPMENT.md §3.4)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from dota2tracker.constants import DAYS_30
from dota2tracker.data import cache as cache_module
from dota2tracker.data import database as db_module
from dota2tracker.data.cache import CLEARABLE_NAMESPACES, PATCH_NOTES_TTL, CacheService, seconds_until_utc_end_of_day
from dota2tracker.data.database import Database

NOW = 1_800_000_000


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(db_module, "_now", lambda: state["now"])
    monkeypatch.setattr(cache_module.time, "time", lambda: float(state["now"]))
    monkeypatch.setattr(
        cache_module,
        "seconds_until_utc_end_of_day",
        lambda now=None: seconds_until_utc_end_of_day(datetime.fromtimestamp(state["now"], UTC)),
    )
    return state


@pytest.fixture
async def db(tmp_db_path):
    database = Database(tmp_db_path)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def cache(db):
    return CacheService(db, "1.0.0")


async def expires_at(db, namespace, key):
    async with db.conn.execute(
        "SELECT expires_at FROM cache_entries WHERE namespace=? AND key=?", (namespace, key)
    ) as cursor:
        row = await cursor.fetchone()
    return row["expires_at"]


def test_seconds_until_utc_end_of_day():
    assert seconds_until_utc_end_of_day(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)) == 86400
    assert seconds_until_utc_end_of_day(datetime(2026, 1, 1, 23, 0, 0, tzinfo=UTC)) == 3600
    assert seconds_until_utc_end_of_day(datetime(2026, 1, 1, 23, 59, 59, 999999, tzinfo=UTC)) == 1
    assert 0 < seconds_until_utc_end_of_day() <= 86400


def utc_day_end(ts: int) -> int:
    return (ts // 86400 + 1) * 86400


async def test_match_cache_30_days_and_version(db, cache, clock):
    await cache.set_match(1, {"match": {"id": 1}})
    assert await cache.get_match(1) == {"data": {"match": {"id": 1}}, "pluginVersion": "1.0.0"}
    assert await expires_at(db, "match", "1") == NOW + DAYS_30
    assert cache.plugin_version == "1.0.0"
    clock["now"] = NOW + DAYS_30
    assert await cache.get_match(1) is None
    await cache.delete_match(1)
    assert await db.cache_get("match", "1") is None


async def test_sent_markers_30_days(cache, clock):
    await cache.mark_match_sent(7)
    assert await cache.sent_match_ids() == {7}
    clock["now"] = NOW + DAYS_30 - 1
    assert await cache.sent_match_ids() == {7}
    clock["now"] = NOW + DAYS_30
    assert await cache.sent_match_ids() == set()


async def test_weekly_meta_until_utc_day_end(db, cache, clock):
    await cache.set_weekly_meta("k", {"x": 1})
    assert await expires_at(db, "weekly_meta", "k") == utc_day_end(NOW)
    assert await cache.get_weekly_meta("k") == {"x": 1}
    clock["now"] = utc_day_end(NOW)
    assert await cache.get_weekly_meta("k") is None


async def test_patch_notes_6_hours(db, cache, clock):
    assert PATCH_NOTES_TTL == 6 * 3600
    await cache.set_patch_notes("zh-CN", [1])
    assert await expires_at(db, "patch_notes", "zh-CN") == NOW + 6 * 3600
    clock["now"] = NOW + 6 * 3600 - 1
    assert await cache.get_patch_notes("zh-CN") == [1]
    clock["now"] = NOW + 6 * 3600
    assert await cache.get_patch_notes("zh-CN") is None


async def test_item_list_never_expires(db, cache):
    await cache.set_item_list("zh-CN", [{"id": 1}], "7.37")
    assert await expires_at(db, "item_list", "zh-CN") is None
    assert await cache.get_item_list("zh-CN") == {"gameVersion": "7.37", "itemList": [{"id": 1}]}


async def test_opendota_counter_until_utc_day_end(db, cache, clock):
    assert await cache.today_opendota_request_count() == 0
    await cache.add_opendota_request_log("/matches/1")
    await cache.add_opendota_request_log("/request/1", 10)
    await cache.add_opendota_request_log("/matches/1")
    assert await cache.today_opendota_request_count() == 12
    rows = await db._fetchall("SELECT expires_at FROM cache_entries WHERE namespace='opendota_request_log'")
    assert {r["expires_at"] for r in rows} == {utc_day_end(NOW)}
    clock["now"] = utc_day_end(NOW)
    assert await cache.today_opendota_request_count() == 0
    stats = await cache.stats()
    assert stats[0]["namespace"] == "opendota_request_log" and stats[0]["expired"] == 3
    assert (await cache.purge_expired())["cache_entries"] == 3


async def test_clear_namespace(cache):
    await cache.set_patch_notes("a", 1)
    await cache.set_patch_notes("b", 1)
    assert await cache.clear("patch_notes") == 2
    assert "opendota_request_log" not in CLEARABLE_NAMESPACES
    assert set(CLEARABLE_NAMESPACES) == {"match", "item_list", "weekly_meta", "patch_notes"}


async def test_unique_log_keys_in_same_instant(db, cache, monkeypatch):
    monkeypatch.setattr(time, "time_ns", lambda: 1)
    for _ in range(3):
        await cache.add_opendota_request_log("/x")
    assert await cache.today_opendota_request_count() == 3
