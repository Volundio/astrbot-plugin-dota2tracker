"""Tests for :mod:`dota2tracker.domain.opendota` (port of ``opendota.adapter.ts``)."""

from __future__ import annotations

import copy

import pytest

from dota2tracker.domain.match import extend_match_data, is_match_parsed
from dota2tracker.domain.opendota import determine_player_positions, transform_opendota_match


@pytest.fixture
def od_match(fixture_loader):
    return fixture_loader("opendota_match.json")


@pytest.fixture
def match(od_match, constants):
    return transform_opendota_match(od_match, constants)


def by_slot(match):
    return {p["playerSlot"]: p for p in match["players"]}


def test_does_not_mutate_input(od_match, constants):
    snapshot = copy.deepcopy(od_match)
    transform_opendota_match(od_match, constants)
    assert od_match == snapshot


def test_match_level_fields(match, od_match):
    assert match["id"] == od_match["match_id"]
    assert match["didRadiantWin"] is True
    assert match["odParsed"] is True
    assert match["lobbyType"] == "RANKED"  # lobby_type 7
    assert match["gameMode"] == "ALL_PICK_RANKED"  # game_mode 22 = all_draft
    assert match["startDateTime"] == od_match["start_time"]
    assert match["parsedDateTime"] == match["endDateTime"] == od_match["start_time"] + od_match["duration"]
    assert match["durationSeconds"] == od_match["duration"]
    assert match["radiantKills"] == [od_match["radiant_score"]]
    assert match["direKills"] == [od_match["dire_score"]]
    assert match["radiantNetworthLeads"] == [0, *od_match["radiant_gold_adv"]]
    assert match["radiantExperienceLeads"] == [0, *od_match["radiant_xp_adv"]]
    assert (match["actualRank"], match["averageRank"], match["winRates"]) == (0, 0, None)
    # rank_tier average of non-null tiers (504 / 9 = 56) → clamp star to 5
    assert match["rank"] == 55


def test_positions_inferred_like_original(match):
    positions = {slot: p["position"] for slot, p in by_slot(match).items()}
    assert positions == {
        0: "POSITION_1",
        3: "POSITION_2",
        4: "POSITION_3",
        1: "POSITION_4",
        2: "POSITION_5",
        132: "POSITION_1",
        129: "POSITION_2",
        128: "POSITION_3",
        131: "POSITION_4",
        130: "POSITION_5",
    }


def test_lane_outcomes_from_10_minute_gold(match):
    # radiant top 4710 vs dire top 5878 → -1168; mid -188; bottom 6512 vs 3974 → +2538
    assert match["topLaneOutcome"] == "DIRE_VICTORY"
    assert match["midLaneOutcome"] == "TIE"
    assert match["bottomLaneOutcome"] == "RADIANT_STOMP"


def test_player_fields(match, od_match, constants):
    raw = od_match["players"][0]
    player = by_slot(match)[0]
    assert player["steamAccountId"] == raw["account_id"]
    assert player["lane"] == "SAFE_LANE"
    assert player["isRadiant"] is True
    assert player["leaverStatus"] == "NONE"
    assert player["networth"] == raw["net_worth"]
    assert player["numLastHits"] == raw["last_hits"]
    assert [player[f"item{i}Id"] for i in range(6)] == [raw[f"item_{i}"] for i in range(6)]
    assert [player[f"backpack{i}Id"] for i in range(3)] == [raw[f"backpack_{i}"] for i in range(3)]
    assert player["neutral0Id"] == raw["item_neutral"]
    assert player["hero"]["id"] == raw["hero_id"]
    assert player["hero"]["name"] == constants.hero_name(raw["hero_id"])
    assert player["hero"]["shortName"] == player["hero"]["name"].removeprefix("npc_dota_hero_")
    assert player["steamAccount"] == {
        "name": raw["personaname"],
        "seasonRank": raw["rank_tier"],
        "seasonLeaderboardRank": None,
    }
    stats = player["stats"]
    assert stats["networthPerMinute"] == [g + 600 for g in raw["gold_t"]]
    xp = raw["xp_t"][1:]
    assert stats["experiencePerMinute"] == [x - (xp[i - 1] if i else 0) for i, x in enumerate(xp)]
    assert stats["campStack"] == [raw["camps_stacked"]]
    dealt = stats["heroDamageReport"]["dealtTotal"]
    assert dealt["stunDuration"] == pytest.approx(raw["stuns"] * 100)
    assert dealt["stunCount"] == 1
    received = stats["heroDamageReport"]["receivedTotal"]
    assert received["physicalDamage"] == sum(
        v for k, v in raw["damage_taken"].items() if k.startswith("npc_dota_hero_")
    )
    assert received["magicalDamage"] == received["pureDamage"] == 0
    known = [p for p in raw["purchase_log"] if constants.items.get(p["key"])]
    assert [p["itemId"] for p in stats["itemPurchases"]] == [constants.items[p["key"]]["id"] for p in known]
    benchmarks = [v for v in raw["benchmarks"].values() if v.get("raw", 0) > 0]
    expected_imp = round(sum(v["pct"] for v in benchmarks) / len(benchmarks) * 100 - 50) if benchmarks else 0
    assert player["imp"] == expected_imp
    zero_stun = by_slot(match)[1]
    assert zero_stun["stats"]["heroDamageReport"]["dealtTotal"]["stunCount"] == 0


def test_pick_bans_and_building_events(match, od_match):
    first = od_match["picks_bans"][0]
    assert match["pickBans"][0] == {
        "isPick": True,
        "heroId": first["hero_id"],
        "bannedHeroId": None,
        "order": first["order"],
    }
    bans = [pb for pb in match["pickBans"] if not pb["isPick"]]
    assert all(pb["heroId"] is None and pb["bannedHeroId"] for pb in bans)
    events = match["playbackData"]["buildingEvents"]
    assert events[0] == {"time": 795, "npcId": 28}  # npc_dota_badguys_tower1_bot
    assert len(events) == sum(1 for o in od_match["objectives"] if o.get("type") == "building_kill")


def test_unknown_purchase_and_missing_fields(constants):
    od = {
        "match_id": 1,
        "players": [
            {"player_slot": 0, "hero_id": 1, "purchase_log": [{"time": 1, "key": "no_such_item"}], "leaver_status": 3},
        ],
        "picks_bans": None,
    }
    match = transform_opendota_match(od, constants)
    player = match["players"][0]
    assert player["stats"]["itemPurchases"] == []
    assert player["leaverStatus"] == "AFK"
    assert player["position"] == "UNKNOWN" and player["lane"] == "UNKNOWN"
    assert match["pickBans"] is None
    assert match["lobbyType"] == "EVENT" and match["gameMode"] == "UNKNOWN"
    assert match["rank"] == 0


def test_determine_positions_without_mid():
    team = [
        {"player_slot": i, "lane": lane, "last_hits": lh, "net_worth": nw}
        for i, (lane, lh, nw) in enumerate([(1, 10, 500), (3, 20, 400), (1, 30, 300), (3, 40, 200), (1, 50, 100)])
    ]
    determine_player_positions(team)
    # no mid laner: sort by net worth → 1,2,3; supports by lane relative to pos1
    assert [p["calculatedPosition"] for p in team] == [1, 2, 3, 4, 5]
    team[3]["lane"] = 1  # support shares pos1's lane → becomes pos5
    for p in team:
        p.pop("calculatedPosition")
    determine_player_positions(team)
    assert [p["calculatedPosition"] for p in team] == [1, 2, 3, 5, 4]


def test_transformed_match_is_parsed_and_extendable(match, constants):
    query = {"match": match}
    assert is_match_parsed(query)
    extended = extend_match_data(query, constants)
    assert extended["radiant"]["killsCount"] == match["radiantKills"][0]
    assert any("MVP" in t for p in extended["players"] for t in p["titles"])
