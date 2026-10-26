"""Match data extension (port of ``MatchService.extendMatchData``).

The extended match keeps the Stratz field names (camelCase) because the
templates, the daily report and the stored match extensions all rely on them.
Arithmetic follows JavaScript semantics (``null`` counts as ``0`` in sums,
division by zero yields ``Infinity``/``NaN``) so that titles and MVP scores
match the Koishi output bit for bit.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from typing import Any

from ..data.static import DotaConstants
from ..utils.jsnum import format_number, js_div, js_parse_int, js_str, js_truthy

__all__ = [
    "LANE_OUTCOMES",
    "UTILITY_ITEM_IDS",
    "build_match_extension",
    "extend_match_data",
    "is_match_parsed",
    "parse_rank",
    "sec2time",
]

UTILITY_ITEM_IDS = (30, 40, 42, 43, 188)  # gem, dust, observer, sentry, smoke
PARTY_MARKS = ("I", "II", "III", "IV")
TITLE_PREFIX = "dota2tracker.template.titles."


def _n(value: Any) -> float | int:
    """JavaScript ``null`` → ``0`` coercion for arithmetic."""
    return 0 if value is None else value


def _get(data: Any, *path: str) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def sec2time(sec: Any) -> str:
    """Port of ``sec2time``: ``m:ss`` or ``--:--`` for falsy values."""
    if not js_truthy(sec):
        return "--:--"
    sign = "-" if sec < 0 else ""
    absolute = abs(sec)
    return f"{sign}{math.floor(absolute / 60)}:{('00' + js_str(absolute % 60))[-2:]}"


def parse_rank(season_rank: Any, leaderboard: Any) -> dict[str, Any]:
    """Build the ``RankInfo`` object (medal, star, leaderboard, inTop100)."""
    text = js_str(season_rank) if season_rank is not None else None
    medal = js_parse_int(text[0]) if text else 0
    star = js_parse_int(text[1]) if text and len(text) > 1 else 0
    in_top100 = None
    if js_truthy(leaderboard):
        if leaderboard <= 10:
            in_top100 = "8c"
        elif leaderboard <= 100:
            in_top100 = "8b"
    return {"medal": medal, "star": star, "leaderboard": leaderboard, "inTop100": in_top100}


def is_match_parsed(match_query: dict[str, Any] | None) -> bool:
    match = (match_query or {}).get("match") or {}
    if not js_truthy(match.get("parsedDateTime")):
        return False
    return any(_get(player, "stats", "heroDamageReport", "dealtTotal") for player in match.get("players") or [])


LANE_OUTCOMES = {
    "RADIANT_VICTORY": {"radiant": "advantage", "dire": "disadvantage"},
    "RADIANT_STOMP": {"radiant": "stomp", "dire": "stomped"},
    "DIRE_VICTORY": {"radiant": "disadvantage", "dire": "advantage"},
    "DIRE_STOMP": {"radiant": "stomped", "dire": "stomp"},
}


def _lane_outcome(outcome: Any) -> dict[str, str]:
    return LANE_OUTCOMES.get(outcome, {"radiant": "tie", "dire": "tie"})


def _create_item(
    item_id: Any,
    purchase_times: dict[Any, list[Any]],
    indices: dict[Any, int],
    constants: DotaConstants,
) -> dict[str, Any] | None:
    # Target fix: OpenDota uses 0 for empty slots; dotaconstants maps 0 to "ability_base".
    if item_id is None or item_id == 0:
        return None
    name = constants.item_name(item_id)
    if not name:
        return None
    index = indices.get(item_id, 0)
    times = purchase_times.get(item_id) or []
    seconds = times[index] if index < len(times) else None
    indices[item_id] = index + 1
    is_recipe = name.startswith("recipe_")
    return {
        "id": item_id,
        "name": name[len("recipe_") :] if is_recipe else name,
        "seconds": seconds,
        "time": sec2time(seconds),
        "isRecipe": is_recipe,
    }


def _js_gt(left: Any, right: Any) -> bool:
    return _n(left) > _n(right)


def _js_lt(left: Any, right: Any) -> bool:
    return _n(left) < _n(right)


def _find_by_property(
    players: list[dict[str, Any]],
    primary: str,
    secondary: str | None = None,
    primary_max: bool = True,
    secondary_max: bool = True,
) -> dict[str, Any] | None:
    """``findMaxByProperty``: ``reduce`` without initial value, tie-break, ``> 0`` guard."""
    if not players:
        return None
    result = players[0]
    for player in players[1:]:
        primary_cmp = (_js_gt if primary_max else _js_lt)(player.get(primary), result.get(primary))
        secondary_cmp = bool(secondary) and (_js_gt if secondary_max else _js_lt)(
            player.get(secondary), result.get(secondary)
        )
        if primary_cmp or (_n(player.get(primary)) == _n(result.get(primary)) and secondary and secondary_cmp):
            result = player
    return result if _n(result.get(primary)) > 0 else None


def _reduce_max(players: list[dict[str, Any]], key: Callable[[dict[str, Any]], float]) -> dict[str, Any] | None:
    if not players:
        return None
    best = players[0]
    for player in players[1:]:
        if key(player) > key(best):
            best = player
    return best


def extend_match_data(match_query: dict[str, Any], constants: DotaConstants) -> dict[str, Any]:
    """Return a deep-copied, extended ``match`` object ready for templates."""
    match = copy.deepcopy(match_query["match"])
    parsed = is_match_parsed(match_query)
    players: list[dict[str, Any]] = match.get("players") or []
    match["players"] = players

    for team in ("radiant", "dire"):
        kills = match.get(team + "Kills")
        match[team] = {
            "killsCount": sum(_n(k) for k in kills) if isinstance(kills, list) else 0,
            "damageReceived": 0,
            "heroDamage": 0,
            "networth": 0,
            "experience": 0,
        }
    if not parsed:
        for player in players:
            match["radiant" if player.get("isRadiant") else "dire"]["killsCount"] += _n(player.get("kills"))

    match["party"] = {}
    party_index = 0
    hero_order: dict[Any, Any] = {}
    for pick in match.get("pickBans") or []:
        if pick.get("isPick"):
            hero_order[pick.get("heroId")] = pick.get("order")

    lane_result = {
        "top": _lane_outcome(match.get("topLaneOutcome")),
        "mid": _lane_outcome(match.get("midLaneOutcome")),
        "bottom": _lane_outcome(match.get("bottomLaneOutcome")),
    }
    duration = _n(match.get("durationSeconds"))

    for player in players:
        team = "radiant" if player.get("isRadiant") else "dire"
        other = "dire" if team == "radiant" else "radiant"
        account = player.get("steamAccount") or {}
        stats = player.get("stats") or {}
        player["stats"] = stats
        player["team"] = team
        player["rank"] = parse_rank(account.get("seasonRank"), account.get("seasonLeaderboardRank"))
        player["killContribution"] = js_div(
            _n(player.get("kills")) + _n(player.get("assists")), match[team]["killsCount"]
        )
        player["deathContribution"] = js_div(_n(player.get("deaths")), match[other]["killsCount"])
        received = _get(stats, "heroDamageReport", "receivedTotal") or {}
        player["damageReceived"] = (
            _n(received.get("physicalDamage")) + _n(received.get("magicalDamage")) + _n(received.get("pureDamage"))
        )
        match[team]["heroDamage"] = match[team]["heroDamage"] + _n(player.get("heroDamage"))
        match[team]["damageReceived"] = match[team]["damageReceived"] + player["damageReceived"]
        match[team]["networth"] += _n(player.get("networth"))
        match[team]["experience"] += math.floor((_n(player.get("experiencePerMinute")) / 60) * duration)
        player["order"] = hero_order.get(_get(player, "hero", "id"))
        party_id = player.get("partyId")
        if party_id is not None and not match["party"].get(party_id):
            match["party"][party_id] = PARTY_MARKS[party_index] if party_index < len(PARTY_MARKS) else None
            party_index += 1

        buff_events = stats.get("matchPlayerBuffEvent")
        buffs: dict[str, dict[str, Any]] = {}
        for event in buff_events or []:
            is_ability = event.get("abilityId") is not None
            buff_id = event.get("abilityId") if is_ability else event.get("itemId")
            buff_type = "ability" if is_ability else "item"
            composite = f"{buff_type}|{buff_id}"
            current = buffs.get(composite)
            if current is None or _n(event.get("stackCount")) > _n(current["stackCount"]):
                buffs[composite] = {"id": buff_id, "type": buff_type, "stackCount": _n(event.get("stackCount"))}
        player["buffs"] = list(buffs.values())

        lane = player.get("lane")
        if lane == "SAFE_LANE":
            player["laneResult"] = lane_result["bottom" if player.get("isRadiant") else "top"][team]
        elif lane == "OFF_LANE":
            player["laneResult"] = lane_result["bottom" if not player.get("isRadiant") else "top"][team]
        elif lane == "JUNGLE":
            player["laneResult"] = "jungle"
        else:
            player["laneResult"] = lane_result["mid"][team]

        camp_stack = stats.get("campStack") or []
        utility_score = (_n(camp_stack[-1]) if camp_stack and js_truthy(camp_stack[-1]) else 0) * 100
        support_counts = dict.fromkeys(UTILITY_ITEM_IDS, 0)
        purchase_times: dict[Any, list[Any]] = {}
        for purchase in stats.get("itemPurchases") or []:
            item_id = purchase.get("itemId")
            if item_id not in UTILITY_ITEM_IDS:
                purchase_times.setdefault(item_id, []).append(purchase.get("time"))
            else:
                name = constants.item_name(item_id)
                if name:
                    cost = (constants.items.get(name) or {}).get("cost")
                    if js_truthy(cost):
                        utility_score += cost
                support_counts[item_id] += 1
        player["utilityScore"] = utility_score
        player["supportItemsCount"] = [
            {"name": constants.item_name(item_id), "count": count} for item_id, count in support_counts.items() if count
        ]

        indices: dict[Any, int] = {}
        player["items"] = [_create_item(player.get(f"item{i}Id"), purchase_times, indices, constants) for i in range(6)]
        player["backpacks"] = [
            _create_item(player.get(f"backpack{i}Id"), purchase_times, indices, constants) for i in range(3)
        ]
        unit = player.get("additionalUnit")
        if unit:
            player["unitItems"] = [
                _create_item(unit.get(f"item{i}Id"), purchase_times, indices, constants) for i in range(6)
            ]
            player["unitBackpacks"] = [
                _create_item(unit.get(f"backpack{i}Id"), purchase_times, indices, constants) for i in range(3)
            ]

        player["formattedNetworth"] = format_number(_n(player.get("networth")))
        player["titles"] = []
        dealt = _get(stats, "heroDamageReport", "dealtTotal") or {}
        player["mvpScore"] = (
            _n(player.get("kills")) * 5
            + _n(player.get("assists")) * 3
            + (_n(dealt.get("stunDuration")) / 100) * 0.1
            + (_n(dealt.get("disableDuration")) / 100) * 0.05
            + (_n(dealt.get("slowDuration")) / 100) * 0.025
            + _n(player.get("heroDamage")) * 0.001
            + _n(player.get("towerDamage")) * 0.01
            + _n(player.get("heroHealing")) * 0.002
            + _n(player.get("imp")) * 0.25
            + player["utilityScore"] * 0.005
        )

    def award(player: dict[str, Any] | None, title: str) -> None:
        if player is not None:
            player["titles"].append(TITLE_PREFIX + title)

    did_radiant_win = match.get("didRadiantWin")
    award(_find_by_property([p for p in players if did_radiant_win == p.get("isRadiant")], "mvpScore"), "MVP")
    award(_find_by_property([p for p in players if did_radiant_win != p.get("isRadiant")], "mvpScore"), "Soul")
    award(_find_by_property(players, "networth"), "Rich")
    award(_find_by_property(players, "experiencePerMinute"), "Wise")
    if parsed:

        def dealt_total(p: dict[str, Any]) -> dict[str, Any]:
            return _get(p, "stats", "heroDamageReport", "dealtTotal") or {}

        def received_total(p: dict[str, Any]) -> dict[str, Any]:
            return _get(p, "stats", "heroDamageReport", "receivedTotal") or {}

        award(
            _reduce_max(
                players,
                lambda p: (
                    _n(dealt_total(p).get("stunDuration"))
                    + _n(dealt_total(p).get("disableDuration")) / 2
                    + _n(dealt_total(p).get("slowDuration")) / 4
                ),
            ),
            "Controller",
        )
        award(
            _reduce_max(
                players,
                lambda p: (
                    _n(received_total(p).get("physicalDamage"))
                    + _n(received_total(p).get("magicalDamage"))
                    + _n(received_total(p).get("pureDamage"))
                ),
            ),
            "Tank",
        )
    award(_find_by_property(players, "heroDamage"), "Nuker")
    award(_find_by_property(players, "kills", "heroDamage"), "Breaker")
    # ``findMaxByProperty("deaths", "networth", undefined, undefined, Min)`` – primary stays "max".
    award(_find_by_property(players, "deaths", "networth", True, False), "Ghost")
    if parsed:
        award(_find_by_property(players, "utilityScore", "networth", True, False), "Utility")
    award(_find_by_property(players, "assists", "heroDamage"), "Assister")
    award(_find_by_property(players, "towerDamage", "heroDamage"), "Demolisher")
    award(_find_by_property(players, "heroHealing"), "Healer")

    if players:

        def contribution(p: dict[str, Any]) -> float:
            return js_div(_n(p.get("kills")) + _n(p.get("assists")), match[p["team"]]["killsCount"])

        lowest = players[0]
        for player in players[1:]:
            current, low = contribution(player), contribution(lowest)
            if current < low:
                lowest = player
            elif current == low:
                current_score = _n(player.get("kills")) + _n(player.get("assists"))
                lowest_score = _n(lowest.get("kills")) + _n(lowest.get("assists"))
                if current_score < lowest_score:
                    lowest = player
                elif current_score == lowest_score:
                    lowest = player if _js_lt(player.get("heroDamage"), lowest.get("heroDamage")) else lowest
        award(lowest, "Idle")

    match["durationTime"] = sec2time(match.get("durationSeconds"))
    return match


def build_match_extension(match: dict[str, Any]) -> dict[str, Any]:
    """``MatchExtensionData`` saved for daily/weekly reports."""
    return {
        "matchId": match.get("id"),
        "players": [
            {
                "steamAccountId": player.get("steamAccountId"),
                "rankSnapshot": player.get("rank"),
                "mvpScore": player.get("mvpScore"),
                "titles": player.get("titles"),
                "utilityScore": player.get("utilityScore"),
                "laneResult": player.get("laneResult"),
                "partyId": player.get("partyId"),
            }
            for player in match.get("players") or []
        ],
    }
