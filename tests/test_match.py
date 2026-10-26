"""Tests for match extension (domain) and :class:`MatchService` (application)."""

from __future__ import annotations

import copy
import logging

import httpx
import pytest

from dota2tracker.application.match import MatchService
from dota2tracker.data.cache import CacheService
from dota2tracker.data.database import Database
from dota2tracker.domain.match import (
    build_match_extension,
    extend_match_data,
    is_match_parsed,
    parse_rank,
    sec2time,
)
from dota2tracker.errors import ErrorReporter, FetchMatchDataFailError, GraphQLQueryError, wrap_fetch_error

PREFIX = "dota2tracker.template.titles."


@pytest.fixture
def stratz_query(fixture_loader):
    return fixture_loader("stratz_match.json")


@pytest.fixture
def od_raw(fixture_loader):
    return fixture_loader("opendota_match.json")


def unparsed(query):
    query = copy.deepcopy(query)
    query["match"]["parsedDateTime"] = None
    return query


# ------------------------------------------------------------------ domain
def test_is_match_parsed(stratz_query):
    assert is_match_parsed(stratz_query)
    assert not is_match_parsed(unparsed(stratz_query))
    assert not is_match_parsed(None)
    assert not is_match_parsed({"match": None})
    no_damage = copy.deepcopy(stratz_query)
    for player in no_damage["match"]["players"]:
        player["stats"]["heroDamageReport"] = None
    assert not is_match_parsed(no_damage)


def test_sec2time_and_parse_rank():
    assert sec2time(0) == "--:--" and sec2time(None) == "--:--"
    assert sec2time(65) == "1:05" and sec2time(-5) == "-0:05" and sec2time(1916) == "31:56"
    assert parse_rank(54, None) == {"medal": 5, "star": 4, "leaderboard": None, "inTop100": None}
    assert parse_rank(80, 7)["inTop100"] == "8c"
    assert parse_rank(80, 100)["inTop100"] == "8b"
    assert parse_rank(80, 101)["inTop100"] is None
    assert parse_rank(None, None)["medal"] == 0


def test_extend_match_data(stratz_query, constants):
    snapshot = copy.deepcopy(stratz_query)
    match = extend_match_data(stratz_query, constants)
    assert stratz_query == snapshot  # deep copy
    players = match["players"]
    assert match["radiant"]["killsCount"] == sum(snapshot["match"]["radiantKills"]) == 55
    assert match["dire"]["killsCount"] == 31
    assert match["durationTime"] == "31:56"
    p0 = players[0]
    assert p0["team"] == "radiant"
    assert p0["killContribution"] == pytest.approx((30 + 4) / 55)
    assert p0["deathContribution"] == pytest.approx(3 / 31)
    assert match["radiant"]["networth"] == sum(p["networth"] for p in players[:5])
    assert match["radiant"]["heroDamage"] == sum(p["heroDamage"] for p in players[:5])
    # lanes: RADIANT_STOMP bottom, DIRE_VICTORY top, TIE mid
    lanes = [(p["isRadiant"], p["lane"], p["laneResult"]) for p in players]
    assert lanes == [
        (True, "SAFE_LANE", "stomp"),
        (True, "OFF_LANE", "disadvantage"),
        (True, "SAFE_LANE", "stomp"),
        (True, "MID_LANE", "tie"),
        (True, "OFF_LANE", "disadvantage"),
        (False, "SAFE_LANE", "advantage"),
        (False, "MID_LANE", "tie"),
        (False, "OFF_LANE", "stomped"),
        (False, "SAFE_LANE", "advantage"),
        (False, "OFF_LANE", "stomped"),
    ]
    # party marks: first four party ids get I..IV, rest None
    assert match["party"] == {0: "I", 1: "II", 2: "III", 3: "IV", 4: None, 5: None}
    # items
    item = p0["items"][0]
    assert item["id"] == 116 and item["name"] == constants.item_name(116)
    assert len(p0["items"]) == 6 and len(p0["backpacks"]) == 3
    assert all(i is None or {"id", "name", "seconds", "time", "isRecipe"} <= set(i) for i in p0["items"])
    # buffs: max stackCount per ability/item
    for player in players:
        keys = [(b["type"], b["id"]) for b in player["buffs"]]
        assert len(keys) == len(set(keys))
    # titles
    titles = {t.removeprefix(PREFIX): i for i, p in enumerate(players) for t in p["titles"]}
    mvp = max(range(5), key=lambda i: players[i]["mvpScore"])
    assert titles["MVP"] == mvp
    assert titles["Soul"] == max(range(5, 10), key=lambda i: players[i]["mvpScore"])
    assert titles["Rich"] == max(range(10), key=lambda i: players[i]["networth"])
    assert titles["Nuker"] == max(range(10), key=lambda i: players[i]["heroDamage"])
    assert {"Controller", "Tank", "Utility", "Idle", "Ghost", "Wise"} <= set(titles)
    assert isinstance(p0["formattedNetworth"], str)


def test_extend_unparsed_counts_player_kills(stratz_query, constants):
    query = unparsed(stratz_query)
    query["match"]["radiantKills"] = None
    match = extend_match_data(query, constants)
    assert match["radiant"]["killsCount"] == sum(p["kills"] for p in query["match"]["players"][:5])
    titles = {t.removeprefix(PREFIX) for p in match["players"] for t in p["titles"]}
    assert not {"Controller", "Tank", "Utility"} & titles


def test_build_match_extension(stratz_query, constants):
    match = extend_match_data(stratz_query, constants)
    ext = build_match_extension(match)
    assert ext["matchId"] == match["id"]
    assert len(ext["players"]) == 10
    first = ext["players"][0]
    assert set(first) == {
        "steamAccountId",
        "rankSnapshot",
        "mvpScore",
        "titles",
        "utilityScore",
        "laneResult",
        "partyId",
    }
    assert first["steamAccountId"] == 100000001 and first["laneResult"] == "stomp"


# ------------------------------------------------------------- application
class FakeStratz:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def query_match_info(self, match_id):
        self.calls += 1
        if self.error:
            raise self.error
        return copy.deepcopy(self.result)


class FakeOpenDota(FakeStratz):
    pass


class Reporter(ErrorReporter):
    def __init__(self):
        super().__init__(logging.getLogger("t"))
        self.reported = []

    def report(self, error, source=""):
        self.reported.append((error, source))


@pytest.fixture
async def env(tmp_db_path, constants):
    db = Database(tmp_db_path)
    await db.open()
    cache = CacheService(db, "1.0.0")

    def make(stratz, opendota=None):
        reporter = Reporter()
        service = MatchService(
            stratz=stratz,
            opendota=opendota,
            cache=cache,
            db=db,
            constants=constants,
            reporter=reporter,
            logger=logging.getLogger("t"),
        )
        return service, reporter

    yield make, cache, db
    await db.close()


MID = 8000000001


async def test_parsed_ready_and_cached(env, stratz_query):
    make, cache, _ = env
    stratz = FakeStratz(stratz_query)
    service, _ = make(stratz)
    result = await service.get_match_result(MID, wait_for_parse=True, allow_fallback=False)
    assert result.status == "READY" and result.ready and result.match_data == stratz_query
    assert (await cache.get_match(MID))["pluginVersion"] == "1.0.0"
    await service.get_match_result(MID, wait_for_parse=True, allow_fallback=False)
    assert stratz.calls == 1  # served from cache


async def test_cache_with_other_plugin_version_is_refreshed(env, stratz_query):
    make, cache, db = env
    await db.cache_set("match", str(MID), {"data": {"match": {"id": 0}}, "pluginVersion": "0.9"}, None)
    stratz = FakeStratz(stratz_query)
    service, _ = make(stratz)
    result = await service.get_match_result(MID, wait_for_parse=False, allow_fallback=False)
    assert stratz.calls == 1 and result.match_data == stratz_query
    assert (await cache.get_match(MID))["data"] == stratz_query


async def test_unparsed_wait_without_fallback_pending(env, stratz_query):
    make, cache, _ = env
    opendota = FakeOpenDota({})
    service, _ = make(FakeStratz(unparsed(stratz_query)), opendota)
    result = await service.get_match_result(MID, wait_for_parse=True, allow_fallback=False)
    assert result.status == "PENDING" and result.match_data is None
    assert opendota.calls == 0
    assert await cache.get_match(MID) is None  # unparsed results are not cached


async def test_unparsed_wait_fallback_unavailable_pending(env, stratz_query):
    make, _, _ = env
    service, _ = make(FakeStratz(unparsed(stratz_query)), None)
    assert not service.opendota_available
    result = await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)
    assert result.status == "PENDING"


async def test_unparsed_no_wait_ready(env, stratz_query):
    make, _, _ = env
    query = unparsed(stratz_query)
    service, _ = make(FakeStratz(query), FakeOpenDota({}))
    result = await service.get_match_result(MID, wait_for_parse=False, allow_fallback=True)
    assert result.status == "READY" and result.match_data == query


async def test_unparsed_wait_opendota_fallback_ready(env, stratz_query, od_raw):
    make, cache, _ = env
    service, reporter = make(FakeStratz(unparsed(stratz_query)), FakeOpenDota(od_raw))
    result = await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)
    assert result.status == "READY"
    assert result.match_data["match"]["odParsed"] is True
    assert reporter.reported == []
    assert (await cache.get_match(MID))["data"]["match"]["odParsed"] is True


async def test_unparsed_wait_opendota_not_parsed_pending(env, stratz_query, od_raw):
    make, _, _ = env
    od_raw["od_data"]["has_parsed"] = False
    service, _ = make(FakeStratz(unparsed(stratz_query)), FakeOpenDota(od_raw))
    result = await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)
    assert result.status == "PENDING"


async def test_stratz_error_without_fallback_reraised(env):
    make, _, db = env
    error = GraphQLQueryError("bad", ["x"])
    await db.cache_set("match", str(MID), {"data": None, "pluginVersion": "1.0.0"}, None)
    service, _ = make(FakeStratz(error=error))
    with pytest.raises(GraphQLQueryError) as info:
        await service.get_match_result(MID, wait_for_parse=True, allow_fallback=False)
    assert info.value is error
    assert await db.cache_get("match", str(MID)) is None  # broken cache entry dropped


async def test_stratz_error_opendota_success_reports(env, od_raw):
    make, _, _ = env
    error = wrap_fetch_error(httpx.ReadTimeout("t"), "Stratz", "MatchInfo")
    service, reporter = make(FakeStratz(error=error), FakeOpenDota(od_raw))
    result = await service.get_match_result(MID, wait_for_parse=False, allow_fallback=True)
    assert result.status == "READY" and result.match_data["match"]["odParsed"]
    assert reporter.reported == [(error, "match")]


async def test_both_fail(env):
    make, _, _ = env
    stratz_error = wrap_fetch_error(httpx.ConnectError("c"), "Stratz", "MatchInfo")
    od_error = wrap_fetch_error(httpx.ReadTimeout("t"), "OpenDota", "/matches/1")
    service, _ = make(FakeStratz(error=stratz_error), FakeOpenDota(error=od_error))
    with pytest.raises(FetchMatchDataFailError) as info:
        await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)
    assert info.value.stratz_error is stratz_error and info.value.opendota_error is od_error


async def test_stratz_error_opendota_unparsed_fails(env, od_raw):
    make, _, _ = env
    od_raw["od_data"]["has_parsed"] = False
    stratz_error = GraphQLQueryError("bad", ["x"])
    service, _ = make(FakeStratz(error=stratz_error), FakeOpenDota(od_raw))
    with pytest.raises(FetchMatchDataFailError) as info:
        await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)
    assert info.value.stratz_error is stratz_error and info.value.opendota_error is None


async def test_unparsed_wait_opendota_error_fails(env, stratz_query):
    make, _, _ = env
    od_error = wrap_fetch_error(httpx.ReadTimeout("t"), "OpenDota", "/matches/1")
    service, _ = make(FakeStratz(unparsed(stratz_query)), FakeOpenDota(error=od_error))
    with pytest.raises(FetchMatchDataFailError) as info:
        await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)
    assert info.value.stratz_error is None and info.value.opendota_error is od_error


@pytest.mark.parametrize("response", [None, {"match": None}, {}])
async def test_not_found_without_fallback(env, response):
    make, _, _ = env
    service, _ = make(FakeStratz(response), FakeOpenDota({}))
    result = await service.get_match_result(MID, wait_for_parse=True, allow_fallback=False)
    assert result.status == "NOT_FOUND"


async def test_not_found_with_fallback(env, od_raw):
    make, _, _ = env
    od_raw["od_data"]["has_parsed"] = False
    service, _ = make(FakeStratz({"match": None}), FakeOpenDota(od_raw))
    assert (await service.get_match_result(MID, wait_for_parse=False, allow_fallback=True)).status == "NOT_FOUND"
    assert (await service.get_match_result(MID, wait_for_parse=True, allow_fallback=True)).status == "PENDING"
    od_raw["od_data"]["has_parsed"] = True
    service, _ = make(FakeStratz({"match": None}), FakeOpenDota(od_raw))
    assert (await service.get_match_result(MID, wait_for_parse=False, allow_fallback=True)).status == "READY"


async def test_generate_match_data_persists_extension(env, stratz_query):
    make, _, db = env
    service, _ = make(FakeStratz())
    match = await service.generate_match_data(stratz_query)
    stored = await db.get_match_extensions([MID])
    assert stored[0]["startTime"] == stratz_query["match"]["startDateTime"]
    assert stored[0]["data"]["matchId"] == MID
    assert stored[0]["data"]["players"][0]["titles"] == match["players"][0]["titles"]


async def test_generate_match_data_tolerates_db_errors(env, stratz_query, monkeypatch, caplog):
    make, _, db = env
    service, _ = make(FakeStratz())

    async def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "upsert_match_extension", boom)
    with caplog.at_level(logging.WARNING, logger="t"):
        match = await service.generate_match_data(stratz_query)
    assert match["id"] == MID
    assert "disk full" in caplog.text
