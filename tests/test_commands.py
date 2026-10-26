"""Command handlers (ports of ``app/commands/*.command.ts``) with fake services."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from dota2tracker.application.binding import BindingService
from dota2tracker.application.models import ChatContext, OutgoingMessage, RenderedImage, Subscriber
from dota2tracker.application.players import LastMatch, SteamValidation
from dota2tracker.commands.handlers import CommandHandlers
from dota2tracker.config import PluginConfig
from dota2tracker.data.database import Database
from dota2tracker.i18n.service import I18nService
from dota2tracker.presentation.messages import MessageBuilder
from dota2tracker.presentation.renderer import RenderError

LOG = logging.getLogger("dota2tracker.tests.commands")
NOW = datetime(2026, 10, 27, 12, 0, 0)
P = "commands.dota2tracker."


def img(name: str) -> RenderedImage:
    return RenderedImage(name.encode(), template=name)


# ------------------------------------------------------------------ fakes
class Tips:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.finished = 0

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def finish(self) -> None:
        self.finished += 1


class Pending:
    def __init__(self) -> None:
        self.added: list[tuple[int, list[Subscriber]]] = []

    async def add(self, match_id, subscribers):
        self.added.append((match_id, subscribers))


class Reporter:
    def __init__(self) -> None:
        self.reports: list[tuple[BaseException, str]] = []

    def report(self, error, source=""):
        self.reports.append((error, source))


class Matches:
    def __init__(self) -> None:
        self.status = "READY"
        self.error: Exception | None = None
        self.calls: list[tuple[int, bool, bool]] = []

    async def get_match_result(self, match_id, *, wait_for_parse, allow_fallback):
        self.calls.append((match_id, wait_for_parse, allow_fallback))
        if self.error:
            raise self.error
        return SimpleNamespace(status=self.status, match_data={"id": match_id} if self.status == "READY" else None)

    async def generate_match_data(self, data):
        return {"id": data["id"], "players": []}


class Players:
    def __init__(self) -> None:
        self.validation = SteamValidation("VALID")
        self.last = LastMatch(id=8000000001)
        self.error: Exception | None = None
        self.player_calls: list[tuple[int, Any, str]] = []
        self.members: list[dict] = [{"nickname": "x"}]

    async def validate_steam_id(self, steam_id):
        return self.validation

    async def last_match(self, steam_id):
        if self.error:
            raise self.error
        return self.last

    async def formatted_player_data(self, steam_id, hero_id, locale):
        self.player_calls.append((steam_id, hero_id, locale))
        if self.error:
            raise self.error
        return {"steamId": steam_id}

    async def guild_members(self, bindings):
        if self.error:
            raise self.error
        return self.members if bindings else []


class Heroes:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.stale = False

    def resolve_hero_id(self, value, *, random=False):
        if random:
            return 1
        return {"敌法": 1, "am": 1}.get(value)

    async def hero_details(self, hero_id, locale, on_stale=None):
        if self.stale and on_stale:
            await on_stale()
        if self.error:
            raise self.error
        return {"id": hero_id, "name": "npc_dota_hero_antimage"}


class Items:
    def __init__(self) -> None:
        self.items = [{"id": i, "name_loc": f"物品{i}", "name": f"item_{i}"} for i in range(1, 11)]
        self.list_error: Exception | None = None
        self.detail_fail: set[int] = set()
        self.cache_miss = False

    async def item_list(self, locale, on_cache_miss=None):
        if self.cache_miss and on_cache_miss:
            await on_cache_miss()
        if self.list_error:
            raise self.list_error
        return self.items

    def search(self, items, keyword, locale):
        if keyword == "many":
            return items
        if keyword == "two":
            return items[:2]
        return []

    async def item_details(self, item_id, locale, on_stale=None):
        if item_id in self.detail_fail:
            raise RuntimeError("detail failed")
        return {"id": item_id, "cost": 100}


class Recs:
    def __init__(self) -> None:
        self.days: list[float] = []
        self.error: Exception | None = None

    async def recommend(self, steam_id, days):
        self.days.append(days)
        if self.error:
            raise self.error
        return {"steamId": steam_id}, {}


class Images:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.help_error: Exception | None = None

    async def help(self, locale):
        if self.help_error:
            raise self.help_error
        return img("help")

    async def match(self, match, locale, template):
        self.calls.append(("match", template, locale))
        return img("match")

    async def player(self, player, locale, template):
        self.calls.append(("player", template))
        return img("player")

    async def hero(self, hero, locale, template):
        return img("hero")

    async def item(self, item, locale):
        self.calls.append(("item", item["id"], item["name_loc"], item["cost"]))
        return img(f"item{item['id']}")

    async def itemlist(self, items, locale):
        self.calls.append(("itemlist", len(items)))
        return img("itemlist")

    async def members(self, members, locale, now):
        self.calls.append(("members", now))
        return img("members")

    async def hero_of_the_day(self, personal, meta, locale):
        return img("hotd")


# ----------------------------------------------------------------- harness
class Harness:
    def __init__(self, db: Database, config: PluginConfig, i18n: I18nService) -> None:
        self.db = db
        self.i18n = i18n
        self.bindings = BindingService(db, None, LOG)
        self.matches = Matches()
        self.players = Players()
        self.heroes = Heroes()
        self.items = Items()
        self.recs = Recs()
        self.images = Images()
        self.pending = Pending()
        self.reporter = Reporter()
        self.config = config
        self.tips = Tips()
        self.build()

    def build(self) -> None:
        self.handlers = CommandHandlers(
            config=self.config,
            i18n=self.i18n,
            bindings=self.bindings,
            matches=self.matches,
            players=self.players,
            heroes=self.heroes,
            items=self.items,
            recommendations=self.recs,
            images=self.images,
            messages=MessageBuilder(self.i18n, self.config.url_in_message_type),
            pending=self.pending,
            reporter=self.reporter,
            clock=lambda: NOW,
        )

    async def run(self, key: str, raw: str = "", ctx: ChatContext | None = None) -> list[OutgoingMessage]:
        self.tips = Tips()
        return [m async for m in self.handlers.run(key, ctx or GROUP, raw, self.tips)]

    async def texts(self, key: str, raw: str = "", ctx: ChatContext | None = None) -> list[str]:
        return [m.text for m in await self.run(key, raw, ctx)]

    def t(self, key: str, params: Any = None, locale: str = "zh-CN") -> str:
        return self.i18n.text(locale, P + key, params)


GROUP = ChatContext(
    umo="qq:GroupMessage:20001",
    platform_id="qq",
    platform_type="aiocqhttp",
    channel_id="20001",
    user_id="10001",
)
OTHER = ChatContext(
    umo="qq:GroupMessage:20001",
    platform_id="qq",
    platform_type="aiocqhttp",
    channel_id="20001",
    user_id="10002",
)
PRIVATE = ChatContext(
    umo="qq:FriendMessage:10001",
    platform_id="qq",
    platform_type="aiocqhttp",
    channel_id="10001",
    user_id="10001",
    is_private=True,
)
UNSUPPORTED = ChatContext(
    umo="tg:GroupMessage:1",
    platform_id="tg",
    platform_type="telegram",
    channel_id="1",
    user_id="1",
)


@pytest.fixture
async def db(tmp_db_path):
    database = Database(tmp_db_path, LOG)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def make(db, constants):
    def factory(**config: Any) -> Harness:
        i18n = I18nService(default_locale="zh-CN", hero_ids=constants.hero_ids)
        return Harness(db, PluginConfig(**config), i18n)

    return factory


@pytest.fixture
def h(make) -> Harness:
    return make()


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def set_english(h: Harness, ctx: ChatContext = GROUP) -> None:
    await h.bindings.subscribe(ctx)
    channel = await h.db.get_channel(ctx.platform_id, ctx.channel_id)
    await h.db.set_channel_locale(channel.id, "en-US")
    await settle()


# ------------------------------------------------------------------- help
async def test_help(h):
    [message] = await h.run("help")
    assert message.images == [img("help")]
    assert message.text == h.t("help.messages.footer")
    assert message.text.startswith("插件配置")


async def test_help_render_failure(h):
    h.images.help_error = RenderError("x")
    [message] = await h.run("help")
    assert message.images == [] and message.text == h.t("help.messages.footer")
    assert h.reporter.reports[0][1] == "command/help"


async def test_help_english(h):
    await set_english(h)
    [message] = await h.run("help")
    assert message.text.startswith("For more information")


# ------------------------------------------------------------- subscribe
async def test_subscribe_idempotent(h):
    assert await h.texts("subscribe") == ["订阅成功。"]
    assert await h.texts("subscribe") == ["本群已订阅，无需重复订阅。"]
    assert await h.texts("unsubscribe") == ["取消订阅成功。"]
    assert await h.texts("unsubscribe") == ["本群尚未订阅，无需取消订阅。"]
    await settle()


async def test_subscribe_english(h):
    await set_english(h)
    assert await h.texts("subscribe") == ["This Channel has been subscribed, no need to subscribe again."]
    assert await h.texts("unsubscribe") == ["Unsubscription successful."]


async def test_subscribe_private_is_silent(h):
    assert await h.texts("subscribe", ctx=PRIVATE) == []
    assert await h.texts("unsubscribe", ctx=PRIVATE) == []
    assert await h.db.list_channels() == []


async def test_subscribe_on_telegram(h):
    [text] = await h.texts("subscribe", ctx=UNSUPPORTED)
    [channel] = await h.db.list_channels()
    assert channel.platform_type == "telegram" and channel.umo == "tg:GroupMessage:1"
    assert "telegram" not in text


async def test_subscribe_denylisted_platform(h, monkeypatch):
    import dota2tracker.application.binding as binding

    monkeypatch.setattr(binding, "UNSUPPORTED_PLATFORM_TYPES", ("telegram",))
    [text] = await h.texts("subscribe", ctx=UNSUPPORTED)
    assert await h.db.list_channels() == []
    assert "telegram" in text


# ------------------------------------------------------------------ bind
async def test_bind_success_and_idempotent(h):
    [text] = await h.texts("bind", "123456 小明")
    assert text == "绑定成功，\nID：10001\n别名：小明\nSteamID：123456"
    [text] = await h.texts("bind", "999")
    assert text == "你已绑定，无需重复绑定。\n以下是你的个人信息：\nID：10001\n别名：小明\nSteamID：123456"
    assert len(await h.db.list_players()) == 1
    await settle()


async def test_bind_without_nickname(h):
    [text] = await h.texts("bind", "123456")
    assert text == "绑定成功，\nID：10001\n别名：\nSteamID：123456"
    assert (await h.bindings.get_binding(GROUP)).nickname == ""
    await settle()


@pytest.mark.parametrize("raw", ["", "abc", "123456789012", "-1", "12.5", "１２３"])
async def test_bind_invalid_steam_id(h, raw):
    assert await h.texts("bind", raw) == ["SteamID无效。"]
    assert await h.db.list_players() == []


@pytest.mark.parametrize(
    ("nickname", "ok"),
    [
        ("一" * 20, True),
        ("一" * 21, False),
        ("😀" * 10, True),  # 20 UTF-16 code units
        ("😀" * 10 + "a", False),
        ('"a\nb"', False),
        ('"a b"', False),
        ('"a b"', True),
    ],
)
async def test_bind_nickname_validation(h, nickname, ok):
    [text] = await h.texts("bind", f"123 {nickname}")
    if ok:
        assert text.startswith("绑定成功")
    else:
        assert text == "别名过长，请限制在20个字符以内。（也可以留空）"
    await settle()


async def test_bind_empty_quoted_nickname_allowed(h):
    [text] = await h.texts("bind", '123 ""')
    assert text.startswith("绑定成功")
    await settle()


@pytest.mark.parametrize(
    ("validation", "reason"),
    [
        (SteamValidation("NO_DOTA_PROFILE", reason=".reason_without_match"), "SteamID无效或无任何场次。"),
        (
            SteamValidation("API_ERROR", reason=".reason_fetch_failed"),
            "网络状况不佳或其他原因无法验证SteamID，请稍后重试。",
        ),
    ],
)
async def test_bind_failed(h, validation, reason):
    h.players.validation = validation
    assert await h.texts("bind", "123") == ["绑定失败，" + reason]
    assert await h.db.list_players() == []


async def test_bind_anonymous_player_warned(h):
    h.players.validation = SteamValidation("VALID", is_anonymous=True)
    [text] = await h.texts("bind", "123 a")
    assert text == h.t(
        "bind.messages.bind_success", {"userId": "10001", "nickName": "a", "steamId": "123"}
    ) + "\n" + h.t("bind.messages.is_anonymous")
    assert "并未公开" in text
    assert (await h.bindings.get_binding(GROUP)) is not None
    await settle()


async def test_bind_english(h):
    await set_english(h)
    assert await h.texts("bind", "x") == ["Invalid SteamID."]
    [text] = await h.texts("bind", "42 Bob")
    assert text == "Binding successful,\nID: 10001\nNickname: Bob\nSteamID: 42"
    await settle()


async def test_bind_private_is_silent(h):
    assert await h.texts("bind", "123", ctx=PRIVATE) == []
    assert await h.texts("unbind", ctx=PRIVATE) == []
    assert await h.texts("rename", "x", ctx=PRIVATE) == []
    assert await h.texts("query-members", ctx=PRIVATE) == []
    assert await h.db.list_players() == []


async def test_bind_concurrent_race_returns_already_binded(h):
    await h.db.bind_player(GROUP.umo, "qq", "20001", "10001", 9, "先到")
    real_get = h.bindings.get_binding
    calls = 0

    async def first_none(ctx):
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_get(ctx)

    h.bindings.get_binding = first_none
    [text] = await h.texts("bind", "123")
    assert text == h.t("bind.messages.already_binded", {"userId": "10001", "nickName": "先到", "steamId": 9})


# ---------------------------------------------------------- unbind/rename
async def test_unbind(h):
    assert await h.texts("unbind") == ["尚未绑定，无需取消绑定。"]
    await h.texts("bind", "1 a")
    assert await h.texts("unbind") == ["取消绑定成功。"]
    assert await h.texts("unbind") == ["尚未绑定，无需取消绑定。"]
    await settle()


async def test_rename(h):
    assert await h.texts("rename", "abc") == ["请先绑定，绑定时即可设定别名。"]
    await h.texts("bind", "1 old")
    # Target fix: the original used the misspelled ".emtpy_input" key.
    assert await h.texts("rename") == ["请输入你的别名。"]
    assert await h.texts("rename", '""') == ["请输入你的别名。"]
    assert await h.texts("rename", "一" * 21) == ["别名过长，请限制在20个字符以内。"]
    assert await h.texts("rename", "old") == ["目标别名与原始别名相同，无需改名。"]
    assert await h.texts("rename", "“new name”") == ["改名成功，现在你叫new name了。"]
    assert (await h.bindings.get_binding(GROUP)).nickname == "new name"
    await settle()


# --------------------------------------------------------- query-members
async def test_query_members(h):
    assert await h.texts("query-members") == ["本群尚无绑定玩家。"]
    await h.texts("bind", "1 a")
    [message] = await h.run("query-members")
    assert message.images == [img("members")]
    assert ("members", NOW) in h.images.calls
    h.players.error = RuntimeError("api")
    assert await h.texts("query-members") == ["查询群友失败。"]
    assert h.reporter.reports[-1][1] == "command/query-members"
    await settle()


# ----------------------------------------------------------- query-match
async def test_query_match_validation(h):
    assert await h.texts("query-match") == ["请输入比赛ID。"]
    assert await h.texts("query-match", "abc") == ["比赛ID无效。"]
    assert await h.texts("query-match", "123456789012") == ["比赛ID无效。"]
    assert h.matches.calls == []


async def test_query_match_success(make):
    h = make(url_in_message_type=("match",), template_match="match_2")
    [message] = await h.run("query-match", "8000000001")
    assert message.text == "https://stratz.com/matches/8000000001"
    assert message.images == [img("match")]
    assert h.images.calls == [("match", "match_2", "zh-CN")]
    assert h.matches.calls == [(8000000001, False, False)]
    assert h.tips.sent == ["正在搜索对局详情，请稍后……"]
    assert h.tips.finished >= 1


async def test_query_match_template_option(make):
    h = make(enable_opendota_fallback=True)
    await h.run("query-match", "1 -t match_2+")
    assert h.images.calls[-1][1] == "match_2+"
    assert h.matches.calls == [(1, False, True)]


async def test_query_match_unknown_template_fails(h):
    assert await h.texts("query-match", "1 -t nope") == ["获取比赛数据失败。"]
    assert h.reporter.reports and h.reporter.reports[-1][1] == "command/query-match"
    assert h.tips.finished >= 1


async def test_query_match_parse_waiting(h):
    h.matches.status = "PENDING"
    assert await h.texts("query-match", "777 -p -t match_2") == [h.t("query-match.messages.waiting_for_parse")]
    assert h.matches.calls == [(777, True, False)]
    [(match_id, [subscriber])] = h.pending.added
    assert match_id == 777
    assert subscriber.source == "COMMAND"
    assert subscriber.type == "CHANNEL"
    assert subscriber.umo == GROUP.umo
    assert subscriber.template_name == "match_2"
    assert subscriber.platform == "qq" and subscriber.channel_id == "20001"
    assert subscriber.user_id == "10001" and subscriber.language_tag == "zh-CN"
    assert h.tips.finished >= 1


async def test_query_match_parse_waiting_private(h):
    h.matches.status = "PENDING"
    await h.run("query-match", "777 -p", ctx=PRIVATE)
    [(_, [subscriber])] = h.pending.added
    assert subscriber.type == "PRIVATE" and subscriber.umo == PRIVATE.umo and subscriber.template_name is None


async def test_query_match_not_found(h):
    h.matches.status = "NOT_FOUND"
    assert await h.texts("query-match", "777") == ["获取比赛数据失败。"]
    assert h.pending.added == []


async def test_query_match_api_error(h):
    h.matches.error = RuntimeError("stratz down")
    assert await h.texts("query-match", "777") == ["获取比赛数据失败。"]
    assert isinstance(h.reporter.reports[0][0], RuntimeError)
    assert h.tips.sent and h.tips.finished >= 1


async def test_query_match_english(h):
    await set_english(h)
    assert await h.texts("query-match", "x") == ["Invalid match ID."]


# ---------------------------------------------------- query-recent-match
async def test_recent_match_not_binded(h):
    assert await h.texts("query-recent-match") == [h.t("common.messages.user_not_binded_in_channel")]
    assert await h.texts("query-recent-match", "nobody") == ["SteamID无效并且未在本群根据输入信息找到玩家。"]
    assert await h.texts("query-recent-match", ctx=PRIVATE) == [h.t("common.messages.user_not_in_group")]
    assert h.tips.sent == []


async def test_recent_match_own_binding(h):
    await h.texts("bind", "123 me")
    [message] = await h.run("query-recent-match")
    assert message.images == [img("match")]
    assert h.matches.calls == [(8000000001, False, False)]
    assert h.tips.finished >= 1
    await settle()


async def test_recent_match_by_nickname_and_steam_id(h):
    await h.texts("bind", "123 小明", ctx=OTHER)
    await h.run("query-recent-match", "小明 -p")
    assert h.matches.calls[-1] == (8000000001, True, False)
    await h.run("query-recent-match", "456", ctx=PRIVATE)
    assert len(h.matches.calls) == 2
    await settle()


async def test_recent_match_anonymous(h):
    # Target fix: the original checked "!id" first so is_anonymous was unreachable.
    h.players.last = LastMatch(id=0, is_anonymous=True)
    [text] = await h.texts("query-recent-match", "123")
    assert text == h.t("query-recent-match.messages.is_anonymous")
    assert h.tips.finished >= 1


async def test_recent_match_no_id_and_error(h):
    h.players.last = LastMatch(id=0)
    assert await h.texts("query-recent-match", "123") == ["获取玩家最近比赛失败。"]
    h.players.error = RuntimeError("boom")
    assert await h.texts("query-recent-match", "123") == ["获取玩家最近比赛失败。"]
    assert h.reporter.reports[-1][1] == "command/query-recent-match"
    assert h.tips.finished >= 1


async def test_recent_match_pending(h):
    h.matches.status = "PENDING"
    assert await h.texts("query-recent-match", "123 -p") == [h.t("query-match.messages.waiting_for_parse")]
    assert h.pending.added[0][0] == 8000000001


# ---------------------------------------------------------- query-player
async def test_query_player(make):
    h = make(url_in_message_type=("player",))
    [message] = await h.run("query-player", "123 -o 敌法")
    assert message.text == "https://stratz.com/players/123"
    assert message.images == [img("player")]
    assert h.players.player_calls[0][0] == 123
    assert h.players.player_calls[0][1] == h.i18n.find_hero_id("敌法")
    assert h.tips.sent == ["正在获取玩家数据，请稍后……"] and h.tips.finished >= 1


async def test_query_player_resolution_errors(h):
    assert await h.texts("query-player", ctx=PRIVATE) == [h.t("common.messages.user_not_in_group")]
    assert await h.texts("query-player") == [h.t("common.messages.user_not_binded_in_channel")]
    assert await h.texts("query-player", "???") == [h.t("common.messages.invalid_input_include_steam_id")]
    assert h.players.player_calls == []


async def test_query_player_failure(h):
    h.players.error = RuntimeError("x")
    assert await h.texts("query-player", "123") == ["获取玩家信息失败。"]
    assert h.reporter.reports[-1][1] == "command/query-player"
    assert h.tips.sent and h.tips.finished >= 1


async def test_query_player_english_private_error(h):
    h.i18n.default_locale = "en-US"
    [text] = await h.texts("query-player", ctx=PRIVATE)
    assert text.startswith("Command failed.")


# ------------------------------------------------------------ query-hero
async def test_query_hero(make):
    h = make(url_in_message_type=("hero",))
    h.heroes.stale = True
    [message] = await h.run("query-hero", "敌法")
    assert message.text == "https://wiki.dota2.com.cn/hero/antimage.html"
    assert message.images == [img("hero")]
    assert h.tips.sent == ["正在获取英雄数据，请稍后……", "正在获取英雄历史改动建立缓存，请稍候……"]
    assert h.tips.finished >= 1


async def test_query_hero_random_and_errors(h):
    assert len(await h.run("query-hero", "-r")) == 1
    # Target fix: the original stayed silent without input.
    assert await h.texts("query-hero") == ["请输入参数。"]
    assert await h.texts("query-hero", "nobody") == ["未找到输入的英雄，请确认后重新输入。"]
    assert h.tips.finished >= 1
    h.heroes.error = RuntimeError("x")
    assert await h.texts("query-hero", "am") == ["获取英雄数据失败。"]
    assert h.reporter.reports[-1][1] == "command/query-hero"
    assert h.tips.finished >= 1


# ------------------------------------------------------------ query-item
async def test_query_item_empty_input(make):
    h = make(show_item_list_at_too_much_items=False)
    assert await h.texts("query-item") == ["未输入关键字参数。根据当前配置无内容可发送。"]
    h = make()
    [message] = await h.run("query-item")
    assert message.images == [img("itemlist")]
    assert ("itemlist", 10) in h.images.calls


async def test_query_item_found(h):
    h.items.cache_miss = True
    messages = await h.run("query-item", "two")
    assert [m.text for m in messages] == ["找到以下物品：物品1、物品2", "", ""]
    assert [m.images for m in messages[1:]] == [[img("item1")], [img("item2")]]
    assert ("item", 1, "物品1", 100) in h.images.calls
    assert h.tips.sent == ["正在查询物品数据，请稍候…", h.t("query-item.messages.cache_building")]
    assert h.tips.finished >= 1


async def test_query_item_not_found(h):
    assert await h.texts("query-item", "zzz") == ["未找到与关键字匹配的物品，请确认后重试。"]


async def test_query_item_too_many(make):
    h = make(max_send_item_count=3)
    messages = await h.run("query-item", "many")
    assert messages[0].text == "找到10个物品，超过最大发送限制（3个），将发送物品列表。"
    assert messages[1].images == [img("itemlist")]
    assert len(messages) == 2
    h = make(max_send_item_count=3, show_item_list_at_too_much_items=False)
    assert await h.texts("query-item", "many") == ["找到10个物品，超过最大发送限制（3个）。"]


async def test_query_item_count_at_limit_sends_all(make):
    h = make(max_send_item_count=10)
    assert len(await h.run("query-item", "many")) == 11


async def test_query_item_list_failure(h):
    h.items.list_error = RuntimeError("x")
    assert await h.texts("query-item", "two") == ["获取物品列表数据失败。"]
    assert h.reporter.reports[-1][1] == "command/query-item"
    assert h.tips.finished >= 1


async def test_query_item_single_detail_failure(h):
    h.items.detail_fail = {1}
    texts = await h.texts("query-item", "two")
    # Target fix: the "{0}" placeholder is filled positionally.
    assert texts == ["找到以下物品：物品1、物品2", "获取物品「物品1」数据失败", ""]
    assert len(h.reporter.reports) == 1


async def test_query_item_english(h):
    await set_english(h)
    assert await h.texts("query-item", "zzz") == ["No items found matching the keywords, please verify and retry"]


# -------------------------------------------------------- hero-of-the-day
@pytest.mark.parametrize(
    ("raw", "days"),
    [("123", 30), ("123 -d 7", 7), ("123 -d 0", 1), ("123 -d 999", 180), ("123 -d abc", 30), ("123 -d -5", 1)],
)
async def test_hero_of_the_day_days(h, raw, days):
    [message] = await h.run("hero-of-the-day", raw)
    assert message.images == [img("hotd")]
    assert h.recs.days == [days]
    assert not math.isnan(h.recs.days[0])
    assert h.tips.sent == ["正在查询玩家数据、英雄胜率等数据，请稍候……"]
    assert h.tips.finished >= 1


async def test_hero_of_the_day_errors(h):
    # Target fix: the tip is recalled on resolution failure too.
    assert await h.texts("hero-of-the-day") == [h.t("common.messages.user_not_binded_in_channel")]
    assert h.tips.sent and h.tips.finished >= 1
    assert await h.texts("hero-of-the-day", ctx=PRIVATE) == [h.t("common.messages.user_not_in_group")]
    assert h.tips.finished >= 1
    h.recs.error = RuntimeError("x")
    assert await h.texts("hero-of-the-day", "1") == ["指令调用失败。"]
    assert h.reporter.reports[-1][1] == "command/hero-of-the-day"
    assert h.tips.finished >= 1
