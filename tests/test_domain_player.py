"""domain.player: rank estimate, profile extension, hero recommendation, roster."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from dota2tracker.domain.player import (
    build_guild_members,
    estimate_weighted_rank,
    extend_player_data,
    hero_recommendation,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2025, 10, 26, 20, 0, tzinfo=TZ)


def _ranked(*ranks):
    return {"steamAccount": {}, "matches": [{"rank": r} for r in ranks]}


# ---------------------------------------------------------- estimate rank
def test_estimate_prefers_season_rank():
    assert estimate_weighted_rank({"steamAccount": {"seasonRank": 63}, "matches": [{"rank": 11}]}) == 63


def test_estimate_empty_and_invalid():
    assert estimate_weighted_rank({"steamAccount": {}}) == 0
    assert estimate_weighted_rank(_ranked(None, 0, 7, 96, 90)) == 0


def test_estimate_weights():
    assert estimate_weighted_rank(_ranked(54)) == 54
    # values 30 (rank 50) weight 1.0 and 18 (rank 30) weight 0.5 → 26 → tier 4, stars 2
    assert estimate_weighted_rank(_ranked(50, 30)) == 42
    assert estimate_weighted_rank(_ranked(80, 80)) == 80


def test_estimate_clamps():
    # (48 * 1.0 + 47 * 0.5) / 1.5 = 47.67 → tier 7, Math.round(5.67) = 6 stars clamped to 5
    assert estimate_weighted_rank(_ranked(80, 75)) == 75
    assert estimate_weighted_rank(_ranked(10)) == 10
    assert estimate_weighted_rank(_ranked(15, 15)) == 15


# ------------------------------------------------------------- profile
def _query(account, matches=None):
    return {"player": {"steamAccount": account, "matches": matches or []}}


def test_anonymous_fake_matches_deterministic(constants):
    account = {"id": 123, "isAnonymous": True}
    first = extend_player_data(_query(dict(account)), None, constants)
    second = extend_player_data(_query(dict(account)), None, constants)
    assert len(first["matches"]) == 25
    assert first["matches"] == second["matches"]
    assert "isEstimatedRank" not in first
    other = extend_player_data(_query({"id": 124, "isAnonymous": True}), None, constants)
    assert other["matches"] != first["matches"]


def test_dota_plus_merge_and_sort(constants):
    extra = {
        "player": {
            "dotaPlus": [
                {"heroId": 5, "level": 10},
                {"heroId": 5, "level": 25},
                {"heroId": 2, "level": 25},
                {"heroId": 9, "level": 3},
            ],
            "heroesPerformance": [{"hero": {"id": 5, "shortName": "cm"}, "winCount": 4, "matchCount": 7}],
        }
    }
    player = extend_player_data(_query({"id": 1, "seasonRank": 54}), extra, constants)
    assert [(h["heroId"], h["level"]) for h in player["dotaPlus"]] == [(2, 25), (5, 25), (9, 3)]
    assert player["dotaPlus"][1]["shortName"] == "cm" and player["dotaPlus"][1]["winCount"] == 4
    assert player["heroesPerformanceTop10"] == extra["player"]["heroesPerformance"]


def test_estimated_rank_flag(constants):
    player = extend_player_data(_query({"id": 1}, [{"rank": 54, "durationSeconds": 60}]), None, constants)
    assert player["isEstimatedRank"] is True and player["steamAccount"]["seasonRank"] == 54
    assert player["matches"][0]["durationTime"]


def test_gen_hero(constants):
    extra = {
        "player": {
            "dotaPlus": [{"heroId": 5, "level": 3}, {"heroId": 2, "level": 9}],
            "heroesPerformance": [{"hero": {"id": 5, "shortName": "cm"}, "winCount": 4, "matchCount": 7, "imp": 12}],
        }
    }
    player = extend_player_data(_query({"id": 1, "seasonRank": 54}), extra, constants, gen_hero={"heroId": 5})
    assert player["matchCount"] == 7 and player["winCount"] == 4 and player["performance"]["imp"] == 12
    assert [h["heroId"] for h in player["dotaPlus"]] == [5]
    with pytest.raises(ValueError):
        extend_player_data(_query({"id": 1, "seasonRank": 54}), {"player": {}}, constants, gen_hero={"heroId": 5})


# --------------------------------------------------------- recommendation
def _perf(hero_id, wins, last=None, imp=None):
    entry = {"heroId": hero_id, "winCount": wins, "lastPlayedDateTime": last or (NOW - timedelta(days=10)).timestamp()}
    if imp is not None:
        entry["imp"] = imp
    return entry


def test_recommendation_scores():
    player = {
        "steamAccount": {},
        "lifetimePerformance": [_perf(1, 9), _perf(2, 0)],
        "recentPerformance": [_perf(1, 3, (NOW - timedelta(days=1)).timestamp(), imp=20)],
    }
    result = hero_recommendation(1, player, NOW)
    assert result["recommendationType"] == "PERSONALIZED"
    top, other = result["recommendationPool"]
    assert top["heroId"] == 1 and top["isHotStreak"] is True
    base = 3 + math.log(10) * 5 + 2.0
    assert top["baseScore"] == pytest.approx(base)
    assert top["totalScore"] == pytest.approx(base * 1.2)
    assert top["hotStreakBonus"] == pytest.approx(base * 0.2)
    assert other["totalScore"] == 0
    # zero-weight hero can never be picked; recommendation stops instead of pushing NaN
    assert result["recommendedHeroes"] == [1]


def test_recommendation_pool_and_determinism():
    lifetime = [_perf(i, i * 3) for i in range(1, 15)]
    player = {"steamAccount": {}, "lifetimePerformance": lifetime, "recentPerformance": []}
    first = hero_recommendation(7, player, NOW)
    assert first["recommendationType"] == "LIFETIME_ONLY"
    assert len(first["recommendationPool"]) == 10
    assert [h["heroId"] for h in first["recommendationPool"]] == list(range(14, 4, -1))
    assert len(first["recommendedHeroes"]) == 3 and len(set(first["recommendedHeroes"])) == 3
    assert set(first["recommendedHeroes"]) <= set(range(5, 15))
    assert hero_recommendation(7, player, NOW) == first


def test_recommendation_types():
    assert (
        hero_recommendation(1, {"steamAccount": {"isAnonymous": True}}, NOW)["recommendationType"] == "PLAYER_ANONYMOUS"
    )
    empty = hero_recommendation(1, {"steamAccount": {}}, NOW)
    assert empty == {"recommendedHeroes": [], "recommendationPool": [], "recommendationType": "LIFETIME_NO_RECORD"}


def test_hot_streak_boundary():
    player = {
        "steamAccount": {},
        "lifetimePerformance": [_perf(1, 1), _perf(2, 1)],
        "recentPerformance": [
            _perf(1, 1, (NOW - timedelta(days=3)).timestamp()),
            _perf(2, 1, (NOW - timedelta(days=3)).timestamp() + 1),
        ],
    }
    pool = {h["heroId"]: h for h in hero_recommendation(1, player, NOW)["recommendationPool"]}
    assert pool[1]["isHotStreak"] is False and pool[2]["isHotStreak"] is True


# ----------------------------------------------------------------- roster
def test_build_guild_members(fixture_loader):
    players = fixture_loader("members.json")["players"]
    bindings = [SimpleNamespace(steam_id=p["steamAccount"]["id"], nickname="") for p in players]
    bindings[1] = SimpleNamespace(steam_id=players[1]["steamAccount"]["id"], nickname="Nick")
    members = build_guild_members(players, bindings)
    assert members[0]["name"] == "李火旺" and members[1]["name"] == "Nick"
    assert members[0]["winRate"] == pytest.approx(0.6)
    assert members[0]["lastMatchTime"] == players[0]["matches"][0]["startDateTime"]
    no_matches = members[4]
    assert math.isnan(no_matches["winRate"]) and no_matches["lastMatchTime"] is None


def test_extend_player_data_tolerates_null_account_and_matches(constants):
    from dota2tracker.domain.player import extend_player_data

    player = extend_player_data({"player": {"steamAccount": None, "matches": None}}, None, constants)
    assert isinstance(player["steamAccount"], dict) and player["matches"] == []
    player = extend_player_data({"player": {"matches": [None, {"durationSeconds": 60}]}}, {"player": None}, constants)
    assert [m["durationTime"] for m in player["matches"]] == ["1:00"]
