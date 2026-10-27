"""Tests for :mod:`dota2tracker.data.database`."""

from __future__ import annotations

import time

import pytest

from dota2tracker.data import database as db_module
from dota2tracker.data.database import SCHEMA_VERSION, AlreadyBoundError, Database

UMO = "aiocqhttp:GroupMessage:1001"


@pytest.fixture
async def db(tmp_db_path):
    database = Database(tmp_db_path)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def clock(monkeypatch):
    """Controllable ``_now`` for TTL tests."""
    state = {"now": int(time.time())}
    monkeypatch.setattr(db_module, "_now", lambda: state["now"])
    return state


async def test_migration_sets_schema_version_and_is_repeatable(tmp_db_path):
    database = Database(tmp_db_path)
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    assert database.is_open
    await database.close()
    assert not database.is_open
    with pytest.raises(RuntimeError):
        _ = database.conn
    await database.open()  # second migration run is a no-op
    assert await database.schema_version() == SCHEMA_VERSION
    await database.close()


async def test_creates_parent_directory(tmp_path):
    database = Database(tmp_path / "nested" / "dir" / "x.sqlite3")
    await database.open()
    assert (tmp_path / "nested" / "dir" / "x.sqlite3").exists()
    await database.close()


async def test_subscribe_unsubscribe_idempotent(db):
    assert await db.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001", "群") is True
    assert await db.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001") is False
    assert await db.is_channel_subscribed("aiocqhttp", "1001")
    channel = await db.get_channel("aiocqhttp", "1001")
    assert channel.subscribed and channel.name == "群" and channel.key == "aiocqhttp:1001"
    assert channel.to_dict()["umo"] == UMO

    assert await db.unsubscribe_channel("aiocqhttp", "1001") is True
    assert await db.unsubscribe_channel("aiocqhttp", "1001") is False
    assert await db.unsubscribe_channel("aiocqhttp", "missing") is False
    assert not await db.is_channel_subscribed("aiocqhttp", "1001")

    # resubscribe keeps the same row and name when no new name is given
    assert await db.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001") is True
    channels = await db.list_channels()
    assert len(channels) == 1 and channels[0].id == channel.id and channels[0].name == "群"
    assert [c.id for c in await db.list_channels(subscribed_only=True)] == [channel.id]


async def test_channel_locale_name_delete(db):
    await db.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001")
    channel = await db.get_channel("aiocqhttp", "1001")
    assert await db.set_channel_locale(channel.id, "en-US")
    assert await db.channel_locale("aiocqhttp", "1001") == "en-US"
    assert await db.set_channel_locale(channel.id, "")
    assert await db.channel_locale("aiocqhttp", "1001") is None
    assert await db.channel_locale("aiocqhttp", "nope") is None
    await db.set_channel_name("aiocqhttp", "1001", "新名字")
    assert (await db.get_channel_by_id(channel.id)).name == "新名字"
    assert await db.delete_channel(channel.id)
    assert not await db.delete_channel(channel.id)
    assert await db.get_channel("aiocqhttp", "1001") is None


async def test_bind_uniqueness_unbind_rename(db):
    player = await db.bind_player(UMO, "aiocqhttp", "1001", "u1", 123456, "小明")
    assert (player.steam_id, player.nickname, player.rank) == (123456, "小明", {})
    assert player.channel_key == "aiocqhttp:1001"
    with pytest.raises(AlreadyBoundError):
        await db.bind_player(UMO, "aiocqhttp", "1001", "u1", 999)
    # same user in another channel is allowed
    other = await db.bind_player("aiocqhttp:GroupMessage:1002", "aiocqhttp", "1002", "u1", 999)
    assert other.id != player.id

    assert await db.rename_player(player.id, "大明")
    assert (await db.get_player("aiocqhttp", "1001", "u1")).nickname == "大明"
    assert (await db.find_player_by_nickname("aiocqhttp", "1001", "大明")).id == player.id
    assert await db.find_player_by_nickname("aiocqhttp", "1002", "大明") is None

    assert await db.unbind_player("aiocqhttp", "1001", "u1") is True
    assert await db.unbind_player("aiocqhttp", "1001", "u1") is False
    assert await db.get_player("aiocqhttp", "1001", "u1") is None
    # rebinding after unbind works
    await db.bind_player(UMO, "aiocqhttp", "1001", "u1", 5)
    assert await db.delete_player(other.id)
    assert not await db.rename_player(other.id, "x")


async def test_list_players_and_subscribed_channels(db):
    await db.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001")
    await db.subscribe_channel("aiocqhttp:GroupMessage:1002", "aiocqhttp", "aiocqhttp", "1002")
    await db.unsubscribe_channel("aiocqhttp", "1002")
    a = await db.bind_player(UMO, "aiocqhttp", "1001", "u1", 1)
    b = await db.bind_player(UMO, "aiocqhttp", "1001", "u2", 2)
    await db.bind_player("aiocqhttp:GroupMessage:1002", "aiocqhttp", "1002", "u3", 3)
    await db.bind_player("x", "aiocqhttp", "1003", "u4", 4)  # channel never subscribed
    assert len(await db.list_players()) == 4
    assert [p.id for p in await db.list_players("aiocqhttp", "1001")] == [a.id, b.id]
    assert [p.id for p in await db.list_players_in_subscribed_channels()] == [a.id, b.id]


async def test_rank_json_roundtrip_and_corrupt(db):
    player = await db.bind_player(UMO, "aiocqhttp", "1001", "u1", 1)
    await db.set_player_rank(player.id, {"rank": 54, "leader": None, "名": "x"})
    assert (await db.get_player_by_id(player.id)).rank == {"rank": 54, "leader": None, "名": "x"}
    await db.conn.execute("UPDATE players SET rank_json='not json' WHERE id=?", (player.id,))
    assert (await db.get_player_by_id(player.id)).rank == {}
    await db.conn.execute("UPDATE players SET rank_json='[1]' WHERE id=?", (player.id,))
    assert (await db.get_player_by_id(player.id)).rank == {}


async def test_match_extensions(db):
    await db.upsert_match_extension(1, 100, {"a": 1})
    await db.upsert_match_extension(2, 200, {"b": 2})
    await db.upsert_match_extension(1, 150, {"a": 3})
    result = sorted(await db.get_match_extensions([1, 2, 2, 3]), key=lambda r: r["matchId"])
    assert result == [
        {"matchId": 1, "startTime": 150, "data": {"a": 3}},
        {"matchId": 2, "startTime": 200, "data": {"b": 2}},
    ]
    assert await db.get_match_extensions([]) == []
    assert await db.count_match_extensions() == 2


async def test_match_extensions_chunked(db):
    for i in range(1, 1201):
        await db.upsert_match_extension(i, i, {})
    assert len(await db.get_match_extensions(range(1, 1201))) == 1200


async def test_sent_matches_ttl(db, clock):
    await db.mark_match_sent(10, 100)
    await db.mark_match_sent(11, 10)
    assert await db.sent_match_ids() == {10, 11}
    assert await db.is_match_sent(10)
    clock["now"] += 50
    assert await db.sent_match_ids() == {10}
    assert not await db.is_match_sent(11)
    await db.mark_match_sent(11, 100)  # re-mark extends
    assert await db.is_match_sent(11)
    clock["now"] += 100
    assert await db.purge_expired() == {"cache_entries": 0, "sent_matches": 2}


async def test_pending_matches(db):
    await db.save_pending_match(5, 1000, [{"umo": "a"}])
    await db.save_pending_match(4, 2000, [])
    await db.save_pending_match(5, 9999, [{"umo": "a"}, {"umo": "b"}])  # request_time preserved
    records = await db.list_pending_matches()
    assert [(r.match_id, r.request_time) for r in records] == [(5, 1000), (4, 2000)]
    assert records[0].subscribers == [{"umo": "a"}, {"umo": "b"}]
    await db.conn.execute("UPDATE pending_matches SET subscribers_json='{' WHERE match_id=4")
    assert (await db.list_pending_matches())[1].subscribers == []
    await db.delete_pending_match(5)
    assert [r.match_id for r in await db.list_pending_matches()] == [4]


async def test_cache_entries_and_purge(db, clock):
    now = clock["now"]
    await db.cache_set("ns", "a", {"v": 1}, now + 10)
    await db.cache_set("ns", "b", [1, 2], None)
    await db.cache_set("other", "a", "x", now + 1)
    assert await db.cache_get("ns", "a") == {"v": 1}
    assert await db.cache_get("ns", "missing") is None
    assert sorted(map(str, await db.cache_values("ns"))) == sorted(map(str, [{"v": 1}, [1, 2]]))
    clock["now"] = now + 10  # expires_at <= now means expired
    assert await db.cache_get("ns", "a") is None
    assert await db.cache_values("ns") == [[1, 2]]
    stats = {s["namespace"]: s for s in await db.cache_stats()}
    assert stats["ns"]["entries"] == 2 and stats["ns"]["expired"] == 1 and stats["ns"]["bytes"] > 0
    assert await db.purge_expired() == {"cache_entries": 2, "sent_matches": 0}
    assert await db.cache_get("ns", "b") == [1, 2]
    await db.cache_set("ns", "b", "new", None)
    assert await db.cache_get("ns", "b") == "new"
    assert await db.cache_delete("ns", "b") == 1
    await db.cache_set("ns", "c", 1, None)
    await db.cache_set("ns", "d", 1, None)
    assert await db.cache_delete("ns") == 2


async def test_audit_trimming(db):
    for i in range(7):
        await db.add_audit("admin", f"act{i}", {"i": i}, keep=5)
    await db.add_audit("", "anon", keep=5)
    entries = await db.list_audit(limit=10)
    assert len(entries) == 5
    assert entries[0].action == "anon" and entries[0].actor == "unknown" and entries[0].detail == {}
    assert [e.action for e in entries[1:]] == ["act6", "act5", "act4", "act3"]
    assert entries[1].to_dict()["detail"] == {"i": 6}
    assert len(await db.list_audit(limit=2)) == 2


async def test_transaction_rollback(db):
    with pytest.raises(ValueError):
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO audit_log(created_at, actor, action) VALUES(1, 'a', 'b')",
            )
            raise ValueError
    assert await db.list_audit() == []


async def test_counts(db):
    await db.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001")
    await db.subscribe_channel("u2", "aiocqhttp", "aiocqhttp", "1002")
    await db.unsubscribe_channel("aiocqhttp", "1002")
    await db.bind_player(UMO, "aiocqhttp", "1001", "u1", 1)
    await db.upsert_match_extension(1, 1, {})
    await db.mark_match_sent(1, 1000)
    await db.mark_match_sent(2, -10)  # already expired
    await db.save_pending_match(3, 1, [])
    assert await db.counts() == {
        "channels": 2,
        "subscribed_channels": 1,
        "players": 1,
        "match_extensions": 1,
        "sent_matches": 1,
        "pending_matches": 1,
    }


async def test_reopen_persists(tmp_db_path):
    database = Database(tmp_db_path)
    await database.open()
    await database.subscribe_channel(UMO, "aiocqhttp", "aiocqhttp", "1001")
    await database.bind_player(UMO, "aiocqhttp", "1001", "u1", 42, "nick")
    await database.cache_set("ns", "k", {"x": 1}, None)
    await database.close()

    reopened = Database(tmp_db_path)
    await reopened.open()
    assert await reopened.is_channel_subscribed("aiocqhttp", "1001")
    assert (await reopened.get_player("aiocqhttp", "1001", "u1")).nickname == "nick"
    assert await reopened.cache_get("ns", "k") == {"x": 1}
    await reopened.close()


# ----------------------------------------------------------------- 1.1.1 fixes
async def test_transaction_cancelled_during_begin_leaves_connection_usable(tmp_db_path):
    import asyncio

    database = Database(tmp_db_path)
    await database.open()
    # Keep aiosqlite's worker busy so BEGIN is still queued when the caller is cancelled.
    blocker = asyncio.create_task(database.conn._execute(time.sleep, 0.3))
    await asyncio.sleep(0.05)

    async def subscribe():
        await database.subscribe_channel("u:GroupMessage:1", "u", "aiocqhttp", "1")

    task = asyncio.create_task(subscribe())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await blocker
    assert not database.conn.in_transaction
    # Later transactions work and are committed.
    assert await database.subscribe_channel("u:GroupMessage:2", "u", "aiocqhttp", "2")
    await database.close()
    await database.open()
    assert [c.channel_id for c in await database.list_channels()] == ["2"]
    await database.close()


async def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO channels(umo, platform, channel_id, created_at, updated_at) VALUES('x', 'p', 'c', 0, 0)"
            )
            raise RuntimeError("boom")
    assert await db.list_channels() == []
    assert not db.conn.in_transaction


async def test_finish_pending_match_is_atomic(db):
    await db.save_pending_match(5, 100, [{"umo": "x"}])
    await db.finish_pending_match(5, 60)
    assert await db.list_pending_matches() == []
    assert await db.is_match_sent(5)
