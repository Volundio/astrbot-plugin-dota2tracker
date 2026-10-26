"""OpenDota → Stratz ``MatchInfo`` adapter (port of ``core/opendota.adapter.ts``).

OpenDota lacks several Stratz fields, so the adapter keeps the original
approximations: positions inferred from lanes/last hits, lane outcomes from
10-minute gold (stomp > 2500, victory > 800), cumulative gold curves as net
worth, stun duration ×100 and hero damage taken as physical damage.
"""

from __future__ import annotations

import math
from typing import Any

from ..data.static import DotaConstants
from ..utils.jsnum import js_round

__all__ = ["determine_player_positions", "transform_opendota_match"]

_LEAVER = {
    0: "NONE",
    1: "DISCONNECTED_TOO_LONG",
    2: "ABANDONED",
    3: "AFK",
    4: "NEVER_CONNECTED",
    5: "NEVER_CONNECTED_TOO_LONG",
    6: "FAILED_TO_READY_UP",
}
_LANE = {1: "SAFE_LANE", 2: "MID_LANE", 3: "OFF_LANE", 4: "JUNGLE", 5: "ROAMING"}
_POSITION = {1: "POSITION_1", 2: "POSITION_2", 3: "POSITION_3", 4: "POSITION_4", 5: "POSITION_5"}
_LOBBY = {
    "lobby_type_normal": "UNRANKED",
    "lobby_type_practice": "PRACTICE",
    "lobby_type_tournament": "TOURNAMENT",
    "lobby_type_tutorial": "TUTORIAL",
    "lobby_type_coop_bots": "COOP_VS_BOTS",
    "lobby_type_ranked_team_mm": "TEAM_MATCH",
    "lobby_type_ranked_solo_mm": "SOLO_QUEUE",
    "lobby_type_ranked": "RANKED",
    "lobby_type_1v1_mid": "SOLO_MID",
    "lobby_type_battle_cup": "BATTLE_CUP",
    "lobby_type_event": "EVENT",
    "lobby_type_gauntlet": "EVENT",
    "lobby_type_new_player": "COOP_VS_BOTS",
    "lobby_type_featured": "EVENT",
}
_GAME_MODE = {
    "game_mode_all_pick": "ALL_PICK",
    "game_mode_captains_mode": "CAPTAINS_MODE",
    "game_mode_random_draft": "RANDOM_DRAFT",
    "game_mode_single_draft": "SINGLE_DRAFT",
    "game_mode_all_random": "ALL_RANDOM",
    "game_mode_intro": "INTRO",
    "game_mode_diretide": "THE_DIRETIDE",
    "game_mode_reverse_captains_mode": "REVERSE_CAPTAINS_MODE",
    "game_mode_greeviling": "THE_GREEVILING",
    "game_mode_tutorial": "TUTORIAL",
    "game_mode_mid_only": "MID_ONLY",
    "game_mode_least_played": "LEAST_PLAYED",
    "game_mode_compendium_matchmaking": "COMPENDIUM_MATCHMAKING",
    "game_mode_custom": "CUSTOM",
    "game_mode_captains_draft": "CAPTAINS_DRAFT",
    "game_mode_balanced_draft": "BALANCED_DRAFT",
    "game_mode_ability_draft": "ABILITY_DRAFT",
    "game_mode_event": "EVENT",
    "game_mode_all_random_death_match": "ALL_RANDOM_DEATH_MATCH",
    "game_mode_1v1_mid": "SOLO_MID",
    "game_mode_all_draft": "ALL_PICK_RANKED",
    "game_mode_turbo": "TURBO",
    "game_mode_mutation": "MUTATION",
}
_BUILDINGS = {
    "npc_dota_goodguys_tower1_top": 16,
    "npc_dota_goodguys_tower2_top": 19,
    "npc_dota_goodguys_tower3_top": 22,
    "npc_dota_goodguys_tower1_mid": 17,
    "npc_dota_goodguys_tower2_mid": 20,
    "npc_dota_goodguys_tower3_mid": 23,
    "npc_dota_goodguys_tower1_bot": 18,
    "npc_dota_goodguys_tower2_bot": 21,
    "npc_dota_goodguys_tower3_bot": 24,
    "npc_dota_goodguys_tower4": 25,
    "npc_dota_goodguys_melee_rax_top": 38,
    "npc_dota_goodguys_range_rax_top": 41,
    "npc_dota_goodguys_melee_rax_mid": 39,
    "npc_dota_goodguys_range_rax_mid": 42,
    "npc_dota_goodguys_melee_rax_bot": 40,
    "npc_dota_goodguys_range_rax_bot": 43,
    "npc_dota_goodguys_fort": 50,
    "npc_dota_badguys_tower1_top": 26,
    "npc_dota_badguys_tower2_top": 29,
    "npc_dota_badguys_tower3_top": 32,
    "npc_dota_badguys_tower1_mid": 27,
    "npc_dota_badguys_tower2_mid": 30,
    "npc_dota_badguys_tower3_mid": 33,
    "npc_dota_badguys_tower1_bot": 28,
    "npc_dota_badguys_tower2_bot": 31,
    "npc_dota_badguys_tower3_bot": 34,
    "npc_dota_badguys_tower4": 35,
    "npc_dota_badguys_melee_rax_top": 44,
    "npc_dota_badguys_range_rax_top": 47,
    "npc_dota_badguys_melee_rax_mid": 45,
    "npc_dota_badguys_range_rax_mid": 48,
    "npc_dota_badguys_melee_rax_bot": 46,
    "npc_dota_badguys_range_rax_bot": 49,
    "npc_dota_badguys_fort": 51,
}
STOMP_THRESHOLD = 2500
VICTORY_THRESHOLD = 800


def _n(value: Any) -> Any:
    return 0 if value is None else value


def _is_radiant(player: dict[str, Any]) -> bool:
    if "isRadiant" in player and player["isRadiant"] is not None:
        return bool(player["isRadiant"])
    return _n(player.get("player_slot")) < 128


def _assign_side_lanes(side: list[dict[str, Any]]) -> None:
    side.sort(key=lambda p: -_n(p.get("last_hits")))
    pos1, pos3, sup_a, sup_b = side[0], side[1], side[2], side[3]
    pos1["calculatedPosition"] = 1
    pos3["calculatedPosition"] = 3
    pos4, pos5 = sup_a, sup_b
    if sup_a.get("lane") == pos1.get("lane"):
        pos5, pos4 = sup_a, sup_b
    pos4["calculatedPosition"] = 4
    pos5["calculatedPosition"] = 5


def determine_player_positions(players: list[dict[str, Any]]) -> None:
    """Infer 1-5 positions in place (``calculatedPosition``)."""
    radiant = [p for p in players if _is_radiant(p)]
    dire = [p for p in players if not _is_radiant(p)]
    for team in (radiant, dire):
        if len(team) != 5:
            continue
        mids = sorted((p for p in team if p.get("lane") == 2), key=lambda p: -_n(p.get("last_hits")))
        pos2 = mids[0] if mids else None
        if pos2 is not None:
            pos2["calculatedPosition"] = 2
        side = [p for p in team if p is not pos2]
        if len(side) == 4:
            _assign_side_lanes(side)
        elif len(side) == 5:
            side.sort(key=lambda p: -_n(p.get("net_worth")))
            pos1, second, pos3, sup_a, sup_b = side
            pos1["calculatedPosition"] = 1
            second["calculatedPosition"] = 2
            pos3["calculatedPosition"] = 3
            pos4, pos5 = sup_a, sup_b
            if sup_a.get("lane") == pos1.get("lane"):
                pos5, pos4 = sup_a, sup_b
            pos4["calculatedPosition"] = 4
            pos5["calculatedPosition"] = 5


def _judge(gold_advantage: float) -> str:
    if gold_advantage > STOMP_THRESHOLD:
        return "RADIANT_STOMP"
    if gold_advantage > VICTORY_THRESHOLD:
        return "RADIANT_VICTORY"
    if gold_advantage < -STOMP_THRESHOLD:
        return "DIRE_STOMP"
    if gold_advantage < -VICTORY_THRESHOLD:
        return "DIRE_VICTORY"
    return "TIE"


def _lane_outcomes(players: list[dict[str, Any]]) -> dict[str, str]:
    gold = {"radiant": {"top": 0, "mid": 0, "bottom": 0}, "dire": {"top": 0, "mid": 0, "bottom": 0}}
    for player in players:
        radiant = _is_radiant(player)
        team = "radiant" if radiant else "dire"
        gold_t = player.get("gold_t") or []
        gold_at_10 = (gold_t[10] if len(gold_t) > 10 else None) or 0
        position = player.get("calculatedPosition")
        if position in (1, 5):
            gold[team]["bottom" if radiant else "top"] += gold_at_10
        elif position == 2:
            gold[team]["mid"] += gold_at_10
        elif position in (3, 4):
            gold[team]["top" if radiant else "bottom"] += gold_at_10
    return {
        "topLaneOutcome": _judge(gold["radiant"]["top"] - gold["dire"]["top"]),
        "midLaneOutcome": _judge(gold["radiant"]["mid"] - gold["dire"]["mid"]),
        "bottomLaneOutcome": _judge(gold["radiant"]["bottom"] - gold["dire"]["bottom"]),
    }


def _imp(player: dict[str, Any]) -> int:
    values = [v for v in (player.get("benchmarks") or {}).values() if isinstance(v, dict) and _n(v.get("raw")) > 0]
    if not values:
        return 0
    total = sum(_n(v.get("pct")) for v in values)
    return int(js_round((total / len(values)) * 100 - 50))


def _clamp(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        value = 0
    return min(max(value, low), high)


def _average_rank(players: list[dict[str, Any]]) -> int:
    tiers = [p.get("rank_tier") for p in players if p.get("rank_tier") is not None]
    average = sum(tiers) / len(tiers) if tiers else 0
    number = js_round(average)
    return int(_clamp(math.floor(number / 10), 0, 8) * 10 + _clamp(math.fmod(number, 10), 0, 5))


def _building_events(objectives: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    events = []
    for objective in objectives or []:
        key = objective.get("key")
        if objective.get("type") == "building_kill" and key and _BUILDINGS.get(key):
            events.append({"time": objective.get("time"), "npcId": _BUILDINGS[key]})
    return events


def _xp_increments(xp_t: list[Any]) -> list[Any]:
    sliced = xp_t[1:]
    return [x - (_n(sliced[i - 1]) if i - 1 >= 0 else 0) for i, x in enumerate(sliced)]


def _items(source: dict[str, Any]) -> dict[str, Any]:
    result = {f"item{i}Id": source.get(f"item_{i}") for i in range(6)}
    result.update({f"backpack{i}Id": source.get(f"backpack_{i}") for i in range(3)})
    result["neutral0Id"] = source.get("item_neutral")
    return result


def transform_opendota_match(od_match: dict[str, Any], constants: DotaConstants) -> dict[str, Any]:
    """Convert ``GET /matches/{id}`` into the Stratz ``match`` structure."""
    raw_players: list[dict[str, Any]] = [dict(p) for p in od_match.get("players") or []]
    determine_player_positions(raw_players)
    players = []
    for raw in raw_players:
        hero_id = raw.get("hero_id")
        hero_name = constants.hero_name(hero_id) or f"npc_dota_hero_{hero_id}"
        damage_taken = raw.get("damage_taken") or {}
        purchases = []
        for purchase in raw.get("purchase_log") or []:
            item = constants.items.get(purchase.get("key") or "")
            # Target fix: unknown keys are skipped instead of crashing the whole fallback.
            if item and item.get("id") is not None:
                purchases.append({"time": purchase.get("time"), "itemId": item["id"]})
        stuns = _n(raw.get("stuns"))
        player = {
            "steamAccountId": raw.get("account_id"),
            "level": raw.get("level"),
            "variant": raw.get("hero_variant"),
            "leaverStatus": _LEAVER.get(raw.get("leaver_status"), "NONE"),
            "partyId": raw.get("party_id"),
            "position": _POSITION.get(raw.get("calculatedPosition"), "UNKNOWN"),
            "playerSlot": raw.get("player_slot"),
            "lane": _LANE.get(raw.get("lane"), "UNKNOWN"),
            "imp": _imp(raw),
            "kills": raw.get("kills"),
            "deaths": raw.get("deaths"),
            "assists": raw.get("assists"),
            "isRadiant": _is_radiant(raw),
            "networth": raw.get("net_worth"),
            **_items(raw),
            "heroDamage": raw.get("hero_damage"),
            "towerDamage": raw.get("tower_damage"),
            "numLastHits": raw.get("last_hits"),
            "numDenies": raw.get("denies"),
            "goldPerMinute": raw.get("gold_per_min"),
            "experiencePerMinute": raw.get("xp_per_min"),
            "heroHealing": raw.get("hero_healing"),
            "isRandom": raw.get("randomed"),
            "steamAccount": {
                "name": raw.get("personaname"),
                "seasonRank": raw.get("rank_tier"),
                "seasonLeaderboardRank": None,
            },
            "hero": {
                "id": hero_id,
                "name": hero_name,
                "shortName": hero_name.removeprefix("npc_dota_hero_"),
            },
            "dotaPlus": None,
            "stats": {
                "networthPerMinute": [_n(g) + 600 for g in raw.get("gold_t") or []],
                "experiencePerMinute": _xp_increments(raw.get("xp_t") or []),
                "campStack": [raw.get("camps_stacked")],
                "matchPlayerBuffEvent": [],
                "killEvents": [],
                "deathEvents": [],
                "assistEvents": [],
                "heroDamageReport": {
                    "receivedTotal": {
                        "physicalDamage": sum(
                            _n(v) for k, v in damage_taken.items() if str(k).startswith("npc_dota_hero_")
                        ),
                        "magicalDamage": 0,
                        "pureDamage": 0,
                    },
                    "dealtTotal": {
                        "stunDuration": stuns * 100,
                        "stunCount": 1 if stuns > 0 else 0,
                        "slowDuration": 0,
                        "slowCount": 0,
                        "disableDuration": 0,
                        "disableCount": 0,
                    },
                },
                "itemPurchases": purchases,
            },
            "additionalUnit": None,
        }
        units = raw.get("additional_units")
        if units:
            player["additionalUnit"] = _items(units[0])
        players.append(player)

    start = _n(od_match.get("start_time"))
    duration = _n(od_match.get("duration"))
    lobby_name = (constants.lobby_type.get(od_match.get("lobby_type")) or {}).get("name")
    game_mode_name = (constants.game_mode.get(od_match.get("game_mode")) or {}).get("name")
    picks = od_match.get("picks_bans")
    match = {
        "id": od_match.get("match_id"),
        "didRadiantWin": od_match.get("radiant_win"),
        "lobbyType": _LOBBY.get(lobby_name, "EVENT"),
        "gameMode": _GAME_MODE.get(game_mode_name, "UNKNOWN"),
        "regionId": od_match.get("region"),
        "parsedDateTime": start + duration,
        "startDateTime": start,
        "endDateTime": start + duration,
        "rank": _average_rank(od_match.get("players") or []),
        "actualRank": 0,
        "averageRank": 0,
        "durationSeconds": duration,
        **_lane_outcomes(raw_players),
        "radiantKills": [od_match.get("radiant_score")],
        "direKills": [od_match.get("dire_score")],
        "radiantNetworthLeads": [0, *(od_match.get("radiant_gold_adv") or [])],
        "radiantExperienceLeads": [0, *(od_match.get("radiant_xp_adv") or [])],
        "winRates": None,
        "towerStatusRadiant": od_match.get("tower_status_radiant"),
        "towerStatusDire": od_match.get("tower_status_dire"),
        "barracksStatusRadiant": od_match.get("barracks_status_radiant"),
        "barracksStatusDire": od_match.get("barracks_status_dire"),
        "playbackData": {"buildingEvents": _building_events(od_match.get("objectives"))},
        "players": players,
        "pickBans": None
        if picks is None
        else [
            {
                "isPick": pb.get("is_pick"),
                **(
                    {"heroId": pb.get("hero_id"), "bannedHeroId": None}
                    if pb.get("is_pick")
                    else {"bannedHeroId": pb.get("hero_id"), "heroId": None}
                ),
                "order": pb.get("order"),
            }
            for pb in picks
        ],
        "odParsed": True,
    }
    return match
