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


# ----------------------------------------------------------------- 1.1.1 fixes
class FlakyImages(FakeImages):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def match(self, match, locale, template):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("t2i timeout")
        return await super().match(match, locale, template)


READY = {"match": {"id": 42, "parsedDateTime": 1, "players": []}}


async def test_render_failure_keeps_the_match_pending_and_retries(db):
    sender = FakeSender()
    task = make_polling(db, FakeMatches({42: MatchResult("READY", 42, READY)}), sender=sender, images=FlakyImages(1))
    await task.add(42, [subscriber("bot:GroupMessage:1")])
    await task.polling()
    assert sender.sent == [] and task.is_pending(42)
    assert 42 not in await CacheService(db, "test").sent_match_ids()  # not marked as sent
    await task.polling()
    assert [umo for umo, _ in sender.sent] == ["bot:GroupMessage:1"]
    assert not task.is_pending(42) and await db.list_pending_matches() == []
    await task.stop()


async def test_render_failures_give_up_after_max_attempts(db):
    from dota2tracker.tasks.polling import MAX_PUBLISH_ATTEMPTS

    sender = FakeSender()
    task = make_polling(db, FakeMatches({42: MatchResult("READY", 42, READY)}), sender=sender, images=FlakyImages(99))
    await task.add(42, [subscriber("bot:GroupMessage:1")])
    for _ in range(MAX_PUBLISH_ATTEMPTS):
        await task.polling()
    assert sender.sent == [] and not task.is_pending(42)
    assert 42 in await CacheService(db, "test").sent_match_ids()
    await task.stop()


async def test_subscriber_added_during_publication_is_not_lost(db):
    gate = asyncio.Event()
    release = asyncio.Event()

    class SlowSender(FakeSender):
        async def send(self, umo, message):
            gate.set()
            await release.wait()
            return await super().send(umo, message)

    sender = SlowSender()
    task = make_polling(db, FakeMatches({42: MatchResult("READY", 42, READY)}), sender=sender)
    await task.add(42, [subscriber("bot:GroupMessage:1")])
    publishing = asyncio.create_task(task.polling())
    await gate.wait()
    # A user runs "查询比赛 42 -p" while the report is being sent.
    await task.add(42, [subscriber("bot:FriendMessage:7", source="COMMAND")])
    release.set()
    await publishing
    assert task.is_pending(42)
    assert [s.umo for s in task._pending[42].subscribers if not s.done] == ["bot:FriendMessage:7"]
    await task.polling()
    assert [umo for umo, _ in sender.sent] == ["bot:GroupMessage:1", "bot:FriendMessage:7"]
    assert not task.is_pending(42)
    await task.stop()


async def test_reload_during_publication_only_resumes_remaining_destinations(db):
    class CancelOnSecond(FakeSender):
        async def send(self, umo, message):
            if self.sent:
                raise asyncio.CancelledError  # plugin terminated while sending
            return await super().send(umo, message)

    sender = CancelOnSecond()
    matches = FakeMatches({42: MatchResult("READY", 42, READY)})
    task = make_polling(db, matches, sender=sender)
    await task.add(42, [subscriber("bot:GroupMessage:1"), subscriber("bot:GroupMessage:2")])
    with pytest.raises(asyncio.CancelledError):
        await task.polling()
    await task.stop()

    resumed_sender = FakeSender()
    resumed = make_polling(db, matches, sender=resumed_sender)
    await resumed.restore()
    assert [s.umo for s in resumed._pending[42].subscribers if not s.done] == ["bot:GroupMessage:2"]
    await resumed.polling()
    assert [umo for umo, _ in resumed_sender.sent] == ["bot:GroupMessage:2"]  # group 1 is not sent twice
    await resumed.stop()


async def test_entries_without_data_expire(db):
    from dota2tracker.tasks.polling import PENDING_GRACE_SECONDS

    now = [1000.0]
    matches = FakeMatches({7: MatchResult("NOT_FOUND", 7)})
    config = PluginConfig(data_parsing_timeout_minutes=10)
    task = make_polling(db, matches, clock=lambda: now[0], config=config)
    await task.add(7, [subscriber()])
    now[0] += 600 + PENDING_GRACE_SECONDS - 60
    await task.polling()
    assert task.is_pending(7)
    now[0] += 120
    await task.polling()
    assert not task.is_pending(7) and await db.list_pending_matches() == []
    assert 7 in await CacheService(db, "test").sent_match_ids()  # not rediscovered by the watcher
    await task.stop()


# --------------------------------------------------------- 1.1.2 (low priority)
def _scheduler(config=None, calls=None):
    calls = [] if calls is None else calls

    def job(name):
        async def run():
            calls.append(name)

        return run

    config = config or PluginConfig(daily_report_switch=True, daily_report_hours=8, weekly_report_switch=True)
    return TaskScheduler(config, every_minute=[], daily=job("daily"), weekly=job("weekly"), logger=LOG), calls


async def _drain(scheduler):
    await asyncio.sleep(0)
    if scheduler._jobs:
        await asyncio.gather(*scheduler._jobs)


async def test_scheduler_catches_up_after_a_clock_jump_and_never_repeats():
    scheduler, calls = _scheduler()
    scheduler.tick(datetime(2025, 10, 28, 7, 58, tzinfo=TZ))
    scheduler.tick(datetime(2025, 10, 28, 8, 30, tzinfo=TZ))  # NTP step / stalled loop over 08:00
    await _drain(scheduler)
    assert calls == ["daily"]
    scheduler.tick(datetime(2025, 10, 28, 7, 59, tzinfo=TZ))  # clock steps back
    scheduler.tick(datetime(2025, 10, 28, 8, 0, tzinfo=TZ))
    await _drain(scheduler)
    assert calls == ["daily"]  # not twice on the same day
    scheduler.tick(datetime(2025, 10, 29, 8, 0, tzinfo=TZ))
    await _drain(scheduler)
    assert calls == ["daily", "daily"]


async def test_scheduler_does_not_fire_reports_missed_before_start():
    scheduler, calls = _scheduler()
    scheduler.tick(datetime(2025, 10, 28, 8, 1, tzinfo=TZ))  # started at 08:00:30
    await _drain(scheduler)
    assert calls == []


async def test_scheduler_repeated_dst_hour_fires_once():
    from zoneinfo import ZoneInfo

    new_york = ZoneInfo("America/New_York")
    config = PluginConfig(daily_report_switch=True, daily_report_hours=1)
    scheduler, calls = _scheduler(config)
    # 2025-11-02: 01:00-01:59 happens twice (EDT, then EST).
    scheduler.tick(datetime(2025, 11, 2, 0, 59, tzinfo=new_york))
    scheduler.tick(datetime(2025, 11, 2, 1, 0, tzinfo=new_york))
    scheduler.tick(datetime(2025, 11, 2, 1, 59, tzinfo=new_york))
    scheduler.tick(datetime(2025, 11, 2, 1, 0, fold=1, tzinfo=new_york))
    await _drain(scheduler)
    assert calls == ["daily"]


def test_report_window_spans_a_dst_change():
    from zoneinfo import ZoneInfo

    from dota2tracker.tasks.reports import report_window

    new_york = ZoneInfo("America/New_York")
    data_start, target, today = report_window(datetime(2025, 11, 3, 6, 0, tzinfo=new_york))
    assert (target.hour, target.day, today.hour, today.day) == (0, 2, 0, 3)
    assert today.timestamp() - target.timestamp() == 25 * 3600  # the day with the extra hour
    assert target.timestamp() - data_start.timestamp() == 24 * 3600


def test_system_timezone_follows_tz(monkeypatch):
    from dota2tracker.utils import timefmt

    timefmt.system_timezone.cache_clear()
    try:
        monkeypatch.setenv("TZ", "America/New_York")
        assert str(timefmt.system_timezone()) == "America/New_York"
        assert timefmt.local_now().tzinfo is timefmt.system_timezone()
        timefmt.system_timezone.cache_clear()
        monkeypatch.setenv("TZ", "CST-8")  # POSIX string: fall back to the fixed offset
        assert timefmt.system_timezone() is None
        assert timefmt.local_now().utcoffset() is not None
    finally:
        timefmt.system_timezone.cache_clear()


async def test_opendota_parse_requests_do_not_depend_on_queue_length(db):
    class FakeOpenDota:
        def __init__(self):
            self.requests = []

        async def request_parse_match(self, match_id):
            self.requests.append(match_id)
            return True

    now = [0.0]
    opendota = FakeOpenDota()
    config = PluginConfig(enable_opendota_fallback=True, data_parsing_timeout_minutes=60)
    task = make_polling(db, FakeMatches({}), clock=lambda: now[0], config=config)
    task._opendota = opendota
    for match_id in range(1, 6):  # five pending matches: each is polled every 5 minutes
        await task.add(match_id, [subscriber(f"bot:GroupMessage:{match_id}")])
    now[0] = 60.0  # entry 1 is first polled one minute in, so its waits are 1, 6, 11, ... minutes
    for _ in range(20):
        await task.polling()
        now[0] += 60
    await asyncio.sleep(0)
    assert sorted(set(opendota.requests)) == [1, 2, 3, 4, 5]
    await task.stop()


async def test_discovery_adds_the_group_broadcast_to_a_match_queued_by_a_command(db):
    await _bound(db)
    polling = make_polling(db, FakeMatches({}))
    await polling.add(55, [subscriber("bot:FriendMessage:9", source="COMMAND")])  # "查询比赛 55 -p" first
    watcher = make_watcher(db, FakeStratz([stratz_player(55, int(time.time()) - 600)]), polling)
    await watcher.discovery()
    await watcher.discovery()  # idempotent
    umos = [s.umo for s in polling._pending[55].subscribers]
    assert umos == ["bot:FriendMessage:9", "bot:GroupMessage:100"]
    # Once the group has been served it is not queued again while the entry is still pending.
    polling._pending[55].subscribers[1].done = True
    await watcher.discovery()
    assert len(polling._pending[55].subscribers) == 2
    await polling.stop()
