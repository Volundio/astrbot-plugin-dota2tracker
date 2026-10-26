"""BindingService against a real temporary SQLite database."""

from __future__ import annotations

import asyncio
import logging

import pytest

from dota2tracker.application.binding import BindingService, ResolveResult
from dota2tracker.application.models import ChatContext
from dota2tracker.data.database import Database

LOG = logging.getLogger("dota2tracker.tests.binding")


def chat(user="10001", channel="20001", platform="qq", ptype="aiocqhttp", private=False) -> ChatContext:
    kind = "FriendMessage" if private else "GroupMessage"
    return ChatContext(
        umo=f"{platform}:{kind}:{user if private else channel}",
        platform_id=platform,
        platform_type=ptype,
        channel_id=user if private else channel,
        user_id=user,
        is_private=private,
    )


class FakeMembers:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def group_member_ids(self, platform_id, group_id):
        self.calls.append((platform_id, group_id))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
async def db(tmp_db_path):
    database = Database(tmp_db_path, LOG)
    await database.open()
    yield database
    await database.close()


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_subscribe_twice_and_unsubscribe(db):
    service = BindingService(db, None, LOG)
    ctx = chat()
    assert not await service.is_subscribed(ctx)
    assert await service.subscribe(ctx) is True
    assert await service.subscribe(ctx) is False
    assert await service.is_subscribed(ctx)
    assert len(await db.list_channels()) == 1
    assert await service.unsubscribe(ctx) is True
    assert await service.unsubscribe(ctx) is False
    assert not await service.is_subscribed(ctx)
    # resubscribe reuses the same channel row
    assert await service.subscribe(ctx) is True
    assert len(await db.list_channels()) == 1
    await _settle()


async def test_subscribe_refreshes_member_cache(db):
    members = FakeMembers({"10001"})
    service = BindingService(db, members, LOG)
    await service.subscribe(chat())
    await _settle()
    assert members.calls == [("qq", "20001")]
    assert service.member_cache_summary() == {"qq:20001": 1}


async def test_bind_twice_rename_unbind(db):
    service = BindingService(db, None, LOG)
    ctx = chat()
    record = await service.bind(ctx, 123456, "小明")
    assert record is not None and record.steam_id == 123456 and record.nickname == "小明"
    assert await service.bind(ctx, 999, "other") is None
    players = await service.players_in_channel(ctx)
    assert [(p.user_id, p.steam_id) for p in players] == [("10001", 123456)]
    # another user in the same channel may bind
    assert await service.bind(chat(user="10002"), 5, "") is not None
    # same user in another channel too
    assert await service.bind(chat(channel="30001"), 6, "") is not None

    assert await service.rename(record, "大明") is True
    assert (await service.get_binding(ctx)).nickname == "大明"

    assert await service.unbind(ctx) is True
    assert await service.unbind(ctx) is False
    assert await service.get_binding(ctx) is None
    await _settle()


async def test_bind_in_private_chat_is_keyed_by_user(db):
    service = BindingService(db, None, LOG)
    ctx = chat(private=True)
    record = await service.bind(ctx, 1, "")
    assert record is not None and record.channel_id == "10001"
    assert await service.get_binding(chat()) is None
    await _settle()


async def test_unbind_discards_member_cache_entry(db):
    members = FakeMembers({"10001", "10002"})
    service = BindingService(db, members, LOG)
    await service.refresh_member_cache("qq", "20001")
    await service.unbind(chat())
    assert not service.is_player_in_guild("qq", "20001", "10001")
    assert service.is_player_in_guild("qq", "20001", "10002")


async def test_resolve_steam_id(db):
    service = BindingService(db, None, LOG)
    ctx = chat()
    assert await service.resolve_steam_id(ctx, "123") == ResolveResult(True, 123)
    assert await service.resolve_steam_id(ctx, "12345678901") == ResolveResult(True, 12345678901)
    # 12 digits is not a SteamID and no such nickname
    assert await service.resolve_steam_id(ctx, "123456789012") == ResolveResult(False, reason="INVALID_INPUT")
    assert await service.resolve_steam_id(ctx, "unknown") == ResolveResult(False, reason="INVALID_INPUT")
    assert await service.resolve_steam_id(ctx, None) == ResolveResult(False, reason="NOT_BINDED")
    assert await service.resolve_steam_id(ctx, "") == ResolveResult(False, reason="NOT_BINDED")

    await service.bind(chat(user="10002"), 777, "老王")
    await service.bind(ctx, 555, "")
    assert await service.resolve_steam_id(ctx, "老王") == ResolveResult(True, 777)
    assert await service.resolve_steam_id(ctx, None) == ResolveResult(True, 555)
    # nickname lookup is channel scoped
    assert await service.resolve_steam_id(chat(channel="other"), "老王") == ResolveResult(False, reason="INVALID_INPUT")
    await _settle()


async def test_member_cache_pass_through(db):
    # No provider: everyone passes
    service = BindingService(db, None, LOG)
    await service.refresh_member_cache("qq", "1")
    assert service.is_player_in_guild("qq", "1", "x")

    # Provider failure clears a previous cache and passes everyone
    members = FakeMembers({"a"})
    service = BindingService(db, members, LOG)
    await service.refresh_member_cache("qq", "1")
    assert not service.is_player_in_guild("qq", "1", "b")
    members.error = RuntimeError("boom")
    await service.refresh_member_cache("qq", "1")
    assert service.is_player_in_guild("qq", "1", "b")
    assert service.member_cache_summary() == {}

    # Provider returns None: also pass-through
    members.error = None
    members.result = None
    await service.refresh_member_cache("qq", "1")
    assert service.is_player_in_guild("qq", "1", "b")


async def test_active_players_filtering(db):
    members = FakeMembers({"u1", "u3"})
    service = BindingService(db, members, LOG)
    g1 = chat(channel="g1")
    await service.subscribe(g1)
    await _settle()
    for user in ("u1", "u2", "u3"):
        await service.bind(chat(user=user, channel="g1"), 100, "")
    # Unsubscribed channel
    await service.bind(chat(user="u1", channel="g2"), 200, "")
    # Telegram channel, subscribed: generic platform, no member list -> passes
    other = chat(channel="g3", platform="tg", ptype="telegram")
    await db.subscribe_channel(other.umo, other.platform_id, other.platform_type, other.channel_id)
    await service.bind(chat(user="u1", channel="g3", platform="tg", ptype="telegram"), 300, "")
    # Subscribed channel without member cache (cold start): passes
    g4 = chat(channel="g4")
    await db.subscribe_channel(g4.umo, g4.platform_id, g4.platform_type, g4.channel_id)
    await service.bind(chat(user="u9", channel="g4"), 400, "")
    await _settle()
    # refresh member caches explicitly: g4 returns None (pass-through)
    members.result = {"u1", "u3"}
    await service.refresh_member_cache("qq", "g1")
    service._member_cache.pop("qq:g4", None)

    active = await service.active_players()
    assert sorted((p.channel_id, p.user_id) for p in active) == [
        ("g1", "u1"),
        ("g1", "u3"),
        ("g3", "u1"),
        ("g4", "u9"),
    ]
    channels = await service.subscribed_channels()
    assert sorted(c.channel_id for c in channels) == ["g1", "g3", "g4"]


async def test_refresh_all_member_caches_all_platforms(db):
    members = FakeMembers({"x"})
    service = BindingService(db, members, LOG)
    await db.subscribe_channel("qq:GroupMessage:1", "qq", "aiocqhttp", "1")
    await db.subscribe_channel("tg:GroupMessage:2", "tg", "telegram", "2")
    await service.refresh_all_member_caches()
    # The provider itself decides (OneBotGateway returns None for non-OneBot platforms).
    assert sorted(members.calls) == [("qq", "1"), ("tg", "2")]


def test_platform_supported():
    for platform_type in ("aiocqhttp", "telegram", "discord", "qq_official"):
        assert BindingService.platform_supported(platform_type)
    assert not BindingService.platform_supported("")


def test_platform_denylist(monkeypatch):
    import dota2tracker.application.binding as binding

    monkeypatch.setattr(binding, "UNSUPPORTED_PLATFORM_TYPES", ("webchat",))
    assert not BindingService.platform_supported("webchat")
    assert BindingService.platform_supported("discord")
