"""Background task tests: scheduler, parse polling, discovery, ranks, cancellation."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dota2tracker.application.binding import BindingService
from dota2tracker.application.match import MatchResult
from dota2tracker.application.models import RenderedImage, Subscriber
from dota2tracker.config import PluginConfig
from dota2tracker.data.cache import CacheService
from dota2tracker.data.database import Database
from dota2tracker.errors import ErrorReporter
from dota2tracker.tasks.polling import ParsePollingTask
from dota2tracker.tasks.scheduler import TaskScheduler, cron_weekday
from dota2tracker.tasks.watcher import MatchWatcherTask

TZ = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger("dota2tracker.tests.tasks")


class FakeSender:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.sent: list[tuple[str, object]] = []
        self.fail = fail or set()

    async def send(self, umo, message):
        if umo in self.fail:
            raise RuntimeError("send failed")
        self.sent.append((umo, message))
        return True


class FakeImages:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def match(self, match, locale, template):
        self.calls.append(("match", locale, template))
        return RenderedImage(b"img", template=template)

    async def rank_fun(self, *args):
        self.calls.append(("rank_fun", *args))
        return RenderedImage(b"rank", template="rank_fun")


class FakeMessages:
    def match_message(self, locale, match, steam_ids):
        return f"{locale}:{sorted(steam_ids)}"

    def rank_changed_message(self, locale, name, previous, current):
        return f"rank:{name}:{previous['medal']}{previous['star']}->{current['medal']}{current['star']}"


class FakeI18n:
    default_locale = "zh-CN"

    def gt(self, key, params=None):
        return key

    def text(self, locale, key, params=None):
        return key

    def resolve_locale(self, locale=None):
        return locale or "zh-CN"


class FakeMatches:
    def __init__(self, results):
        self.results = results
        self.calls: list[tuple[int, bool]] = []

    async def get_match_result(self, match_id, *, wait_for_parse, allow_fallback):
        self.calls.append((match_id, wait_for_parse))
        result = self.results.get(match_id)
        if isinstance(result, Exception):
            raise result
        return result or MatchResult("PENDING", match_id)

    async def generate_match_data(self, query):
        return dict(query["match"])


class FakeStratz:
    def __init__(self, players=None):
        self.players = players or []
        self.parse_requests: list[int] = []

    async def request_parse_match(self, match_id):
        self.parse_requests.append(match_id)
        return True

    async def query_players_last_match_rank_info(self, steam_ids):
        return {"players": [p for p in self.players if p["steamAccount"]["id"] in steam_ids]}


@pytest.fixture
async def db(tmp_db_path):
    database = Database(tmp_db_path, LOG)
    await database.open()
    yield database
    await database.close()


def subscriber(umo="aiocqhttp:GroupMessage:1", locale="zh-CN", template=None, source="AUTOMATIC"):
    return Subscriber(source, "CHANNEL", umo, "bot", umo.rsplit(":", 1)[1], locale, template_name=template)


def make_polling(db, matches, *, sender=None, images=None, clock=time.time, config=None, stratz=None):
    reporter = ErrorReporter(LOG)
    return ParsePollingTask(
        config=config or PluginConfig(),
        db=db,
        cache=CacheService(db, "test"),
        matches=matches,
        stratz=stratz or FakeStratz(),
        opendota=None,
        images=images or FakeImages(),
        messages=FakeMessages(),
        sender=sender or FakeSender(),
        i18n=FakeI18n(),
        reporter=reporter,
        logger=LOG,
        clock=clock,
    )


# ---------------------------------------------------------------- scheduler
def test_cron_weekday():
    assert cron_weekday(datetime(2025, 10, 26, tzinfo=TZ)) == 0  # Sunday
    assert cron_weekday(datetime(2025, 10, 27, tzinfo=TZ)) == 1  # Monday


async def test_scheduler_dispatches_jobs():
    calls: list[str] = []

    def job(name):
        async def run():
            calls.append(name)

        return run

    config = PluginConfig(daily_report_switch=True, daily_report_hours=6, weekly_report_switch=True)
    scheduler = TaskScheduler(
        config, every_minute=[job("discovery"), job("polling")], daily=job("daily"), weekly=job("weekly"), logger=LOG
    )
    scheduler.tick(datetime(2025, 10, 27, 6, 0, tzinfo=TZ))  # Monday 06:00
    await asyncio.sleep(0)
    await asyncio.gather(*scheduler._jobs)
    assert calls[:2] == ["discovery", "polling"] and "daily" in calls and "weekly" not in calls
    calls.clear()
    scheduler.tick(datetime(2025, 10, 27, 10, 0, tzinfo=TZ))  # Monday 10:00 -> weekly default [1, 10]
    await asyncio.gather(*scheduler._jobs)
    assert "weekly" in calls and "daily" not in calls


async def test_scheduler_job_failure_and_stop():
    async def boom():
        raise RuntimeError("x")

    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(3600)

    scheduler = TaskScheduler(PluginConfig(), every_minute=[boom, slow], daily=None, weekly=None, logger=LOG)
    scheduler.start()
    scheduler.tick(datetime(2025, 10, 27, 6, 1, tzinfo=TZ))
    await asyncio.wait_for(started.wait(), 1)
    assert scheduler.running
    await scheduler.stop()
    assert not scheduler.running and not scheduler._jobs


# ------------------------------------------------------------------ polling
async def test_polling_dedup_publish_and_persist(db):
    match = {"match": {"id": 42, "parsedDateTime": 1, "players": []}}
    matches = FakeMatches({})
    sender = FakeSender()
    images = FakeImages()
    stratz = FakeStratz()
    task = make_polling(db, matches, sender=sender, images=images, stratz=stratz)
    await task.add(42, [subscriber("bot:GroupMessage:1"), subscriber("bot:GroupMessage:2")])
    await task.add(42, [subscriber("bot:GroupMessage:3", template="match_2", source="COMMAND")])
    await asyncio.sleep(0)
    assert stratz.parse_requests == [42]  # one parse request per match
    assert [r.match_id for r in await db.list_pending_matches()] == [42]

    # A reloaded task restores the persisted entry.
    restored = make_polling(db, matches)
    await restored.restore()
    assert restored.is_pending(42) and len(restored._pending[42].subscribers) == 3

    await task.polling()
    assert not sender.sent  # still pending
    matches.results[42] = MatchResult("READY", 42, match)
    await task.polling()
    assert [umo for umo, _ in sender.sent] == ["bot:GroupMessage:1", "bot:GroupMessage:2", "bot:GroupMessage:3"]
    # Default template rendered once for both automatic subscribers, custom once.
    assert images.calls == [("match", "zh-CN", "match_1"), ("match", "zh-CN", "match_2")]
    assert not task.is_pending(42)
    assert await db.list_pending_matches() == []
    assert 42 in await CacheService(db, "test").sent_match_ids()
    await task.stop()


async def test_polling_timeout_and_send_isolation(db):
    now = [1000.0]
    matches = FakeMatches({})
    sender = FakeSender(fail={"bot:GroupMessage:1"})
    config = PluginConfig(data_parsing_timeout_minutes=10)
    task = make_polling(db, matches, sender=sender, clock=lambda: now[0], config=config)
    await task.add(7, [subscriber("bot:GroupMessage:1"), subscriber("bot:GroupMessage:2")])
    await task.polling()
    assert matches.calls[-1] == (7, True)
    now[0] += 11 * 60
    matches.results[7] = MatchResult("READY", 7, {"match": {"id": 7, "players": []}})
    await task.polling()
    assert matches.calls[-1] == (7, False)  # timed out: publish unparsed
    assert [umo for umo, _ in sender.sent] == ["bot:GroupMessage:2"]
    assert not task.is_pending(7)
    await task.stop()


async def test_polling_survives_errors(db):
    matches = FakeMatches({1: RuntimeError("api down")})
    task = make_polling(db, matches)
    await task.add(1, [subscriber()])
    await task.polling()
    assert task.is_pending(1)
    await task.stop()


# ------------------------------------------------------------------ watcher
async def _bound(db, *, rank=None):
    await db.subscribe_channel("bot:GroupMessage:100", "bot", "aiocqhttp", "100")
    player = await db.bind_player("bot:GroupMessage:100", "bot", "100", "u1", 123, "Alice")
    if rank is not None:
        await db.set_player_rank(player.id, rank)
    return player


def make_watcher(db, stratz, polling, *, sender=None, images=None, config=None):
    return MatchWatcherTask(
        config=config or PluginConfig(),
        db=db,
        cache=CacheService(db, "test"),
        bindings=BindingService(db, None, LOG),
        stratz=stratz,
        polling=polling,
        images=images or FakeImages(),
        messages=FakeMessages(),
        sender=sender or FakeSender(),
        profiles=None,
        i18n=FakeI18n(),
        reporter=ErrorReporter(LOG),
        logger=LOG,
        now=lambda: datetime(2025, 10, 27, 12, 0, tzinfo=TZ),
    )


def stratz_player(match_id, start, rank=None, leader=None):
    return {
        "steamAccount": {
            "id": 123,
            "name": "steam",
            "seasonRank": rank,
            "seasonLeaderboardRank": leader,
            "avatar": "a",
        },
        "matches": [{"id": match_id, "startDateTime": start, "players": [{"steamAccount": {"id": 123}}]}],
    }


async def test_discovery_adds_recent_unsent_matches(db):
    await _bound(db)
    polling = make_polling(db, FakeMatches({}))
    stratz = FakeStratz([stratz_player(55, int(time.time()) - 600)])
    watcher = make_watcher(db, stratz, polling)
    await watcher.discovery()
    assert polling.is_pending(55)
    sub = polling._pending[55].subscribers[0]
    assert sub.umo == "bot:GroupMessage:100" and sub.relevant_players[0].nickname == "Alice"
    await watcher.discovery()  # already pending -> not duplicated
    assert len(polling._pending[55].subscribers) == 1

    old = make_polling(db, FakeMatches({}))
    stale = make_watcher(db, FakeStratz([stratz_player(56, int(time.time()) - 2 * 86400)]), old)
    await stale.discovery()
    assert not old.is_pending(56)
    await polling.stop()


async def test_rank_first_fill_then_broadcast(db):
    await _bound(db)
    polling = make_polling(db, FakeMatches({}))
    sender = FakeSender()
    config = PluginConfig(rank_broad_switch=True)
    old = int(time.time()) - 5 * 86400
    watcher = make_watcher(db, FakeStratz([stratz_player(1, old, rank=53)]), polling, sender=sender, config=config)
    await watcher.discovery()
    assert sender.sent == []  # first fill is silent
    assert (await db.list_players())[0].rank == {"rank": 53, "leader": None}

    watcher = make_watcher(db, FakeStratz([stratz_player(1, old, rank=61)]), polling, sender=sender, config=config)
    await watcher.discovery()
    assert sender.sent[0][0] == "bot:GroupMessage:100"
    assert sender.sent[0][1].text == "rank:Alice:53->61"
    assert (await db.list_players())[0].rank["rank"] == 61


async def test_rank_fun_broadcast(db):
    await _bound(db, rank={"rank": 61, "leader": None})
    images = FakeImages()
    sender = FakeSender()
    config = PluginConfig(rank_broad_switch=True, rank_broad_fun=True)
    stratz = FakeStratz([stratz_player(1, 0, rank=53)])
    watcher = make_watcher(db, stratz, make_polling(db, FakeMatches({})), sender=sender, images=images, config=config)
    await watcher.discovery()
    call = images.calls[0]
    assert call[0] == "rank_fun" and call[1] == "Alice" and call[3] is False  # falling
    assert call[6] == "2025/10/27 12时00分"
    assert sender.sent[0][1].images[0].template == "rank_fun"
