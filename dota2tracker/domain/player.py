"""Player domain logic (ports from ``core/player.service.ts``)."""

from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta
from typing import Any

from ..data.static import DotaConstants
from ..utils.jsnum import js_round, js_str, js_truthy
from ..utils.koishi_random import seeded_random
from ..utils.timefmt import from_seconds, luxon_format
from .match import parse_rank, sec2time

__all__ = [
    "build_guild_members",
    "estimate_weighted_rank",
    "extend_player_data",
    "hero_recommendation",
]

RECENT_IMP_WEIGHT = 0.1
LIFETIME_WIN_LOG_WEIGHT = 5.0
HOT_STREAK_MULTIPLIER = 1.2
HOT_STREAK_DAYS = 3
RECOMMENDATION_COUNT = 3
RECOMMENDATION_POOL_SIZE = 10


def _n(value: Any) -> Any:
    return 0 if value is None else value


# --------------------------------------------------------------- rank estimate
def _validate_rank(rank: Any) -> bool:
    if rank is None or (not js_truthy(rank) and rank != 0):
        return False
    if rank == 80:
        return True
    tier = math.floor(rank / 10)
    stars = math.fmod(rank, 10)
    return 1 <= tier <= 8 and 0 <= stars <= 5


def _rank_to_value(rank: float) -> float:
    return math.floor(rank / 10) * 6 + math.fmod(rank, 10)


def _weight(index: int, total: int) -> float:
    if total <= 1:
        return 1.0
    return 1.0 - (0.5 / (total - 1)) * index


def estimate_weighted_rank(player: dict[str, Any]) -> int:
    """``PlayerService.estimateWeightedRank``: newest match weight 1.0, oldest 0.5."""
    account = player.get("steamAccount") or {}
    if js_truthy(account.get("seasonRank")):
        return account["seasonRank"]
    matches = player.get("matches") or []
    if not matches:
        return 0
    ranks = [m.get("rank") for m in matches if _validate_rank(m.get("rank"))]
    if not ranks:
        return 0
    weighted_sum = 0.0
    total_weight = 0.0
    for index, rank in enumerate(ranks):
        weight = _weight(index, len(ranks))
        weighted_sum += _rank_to_value(rank) * weight
        total_weight += weight
    if total_weight == 0:
        return 0
    average = weighted_sum / total_weight
    tier = math.floor(average / 6)
    stars = js_round(math.fmod(average, 6))
    return int(max(1, min(tier, 8)) * 10 + max(0, min(stars, 5)))


# ------------------------------------------------------------- player profile
def _anonymous_matches(steam_id: Any, constants: DotaConstants) -> list[dict[str, Any]]:
    hero_ids = [str(h) for h in constants.hero_ids()]  # Object.keys order: ascending numeric strings
    matches = []
    for index in range(25):
        random = seeded_random(f"{steam_id}-{index}")
        hero_id = random.pick(hero_ids)
        short_name = constants.hero_short_name(hero_id) or ""
        matches.append(
            {
                "id": 1000000000 + index,
                "gameMode": "UNKNOWN",
                "lobbyType": "UNRANKED",
                "didRadiantWin": random.bool(0.5),
                "rank": random.int(0, 8) * 10,
                "radiantKills": [random.int(0, 30)],
                "direKills": [random.int(0, 30)],
                "parsedDateTime": 1,
                "players": [
                    {
                        "steamAccount": {"id": steam_id},
                        "isRadiant": True,
                        "kills": random.int(0, 20),
                        "deaths": random.int(0, 20),
                        "assists": random.int(0, 20),
                        "hero": {"id": hero_id, "shortName": short_name},
                    }
                ],
            }
        )
    return matches


def extend_player_data(
    player_query: dict[str, Any],
    player_extra_query: dict[str, Any] | None,
    constants: DotaConstants,
    *,
    gen_hero: dict[str, Any] | None = None,
    estimate_rank: bool = True,
) -> dict[str, Any]:
    """``PlayerService.extendPlayerData`` returning a new player dict."""
    player = copy.deepcopy(player_query["player"])
    extra = (player_extra_query or {}).get("player") or {}
    account = player.setdefault("steamAccount", {})
    player.setdefault("matches", [])
    if account.get("isAnonymous"):
        player["matches"].extend(_anonymous_matches(account.get("id"), constants))

    dota_plus: dict[Any, dict[str, Any]] = {}
    for entry in extra.get("dotaPlus") or []:
        hero_id = entry.get("heroId")
        if hero_id not in dota_plus or _n(dota_plus[hero_id]["level"]) < _n(entry.get("level")):
            dota_plus[hero_id] = {"heroId": hero_id, "level": entry.get("level")}
    for hero in extra.get("heroesPerformance") or []:
        hero_id = (hero.get("hero") or {}).get("id")
        if hero_id in dota_plus:
            dota_plus[hero_id]["shortName"] = (hero.get("hero") or {}).get("shortName")
            dota_plus[hero_id]["winCount"] = hero.get("winCount")
            dota_plus[hero_id]["matchCount"] = hero.get("matchCount")

    if not js_truthy(account.get("seasonRank")) and estimate_rank and not account.get("isAnonymous"):
        player["isEstimatedRank"] = True
        account["seasonRank"] = estimate_weighted_rank(player)

    player["rank"] = parse_rank(account.get("seasonRank"), account.get("seasonLeaderboardRank"))

    # ``Object.values`` orders integer keys ascending before the explicit sort.
    plus_list = [dota_plus[key] for key in sorted(dota_plus, key=lambda k: (not isinstance(k, int), k))]
    plus_list.sort(key=lambda h: (-_n(h.get("level")), _n(h.get("heroId"))))
    player["dotaPlus"] = plus_list

    performance = extra.get("heroesPerformance")
    player["heroesPerformanceTop10"] = list(performance[:10]) if isinstance(performance, list) else []

    if gen_hero:
        if not player["heroesPerformanceTop10"]:
            # The original destructures ``heroesPerformanceTop10[0]`` and fails → "query_failed".
            raise ValueError("player has no matches with the requested hero")
        top = player["heroesPerformanceTop10"][0]
        player["matchCount"] = top.get("matchCount")
        player["winCount"] = top.get("winCount")
        player.setdefault("performance", {})
        if player["performance"] is None:
            player["performance"] = {}
        player["performance"]["imp"] = top.get("imp")
        player["dotaPlus"] = [hero for hero in player["dotaPlus"] if hero.get("heroId") == gen_hero.get("heroId")]
        player["genHero"] = gen_hero

    for match in player["matches"]:
        match["durationTime"] = sec2time(match.get("durationSeconds"))
    return player


# ------------------------------------------------------------ hero of the day
def hero_recommendation(steam_id: Any, player: dict[str, Any], now: datetime) -> dict[str, Any]:
    """``PlayerService.getHeroRecommendation`` (deterministic per day and SteamID)."""
    three_days_ago = now - timedelta(days=HOT_STREAK_DAYS)
    scores: dict[Any, dict[str, Any]] = {}
    for hero in player.get("lifetimePerformance") or []:
        scores[hero.get("heroId")] = {
            "heroId": hero.get("heroId"),
            "recentWinScore": 0,
            "lifetimeWinScore": math.log(_n(hero.get("winCount")) + 1) * LIFETIME_WIN_LOG_WEIGHT,
            "impBonus": 0,
            "isHotStreak": False,
            "hotStreakBonus": 0,
            "baseScore": 0,
            "totalScore": 0,
        }
    for hero in player.get("recentPerformance") or []:
        entry = scores.get(hero.get("heroId"))
        if entry is None:
            # The original crashes here (lifetime list always contains recent heroes).
            entry = scores[hero.get("heroId")] = {
                "heroId": hero.get("heroId"),
                "recentWinScore": 0,
                "lifetimeWinScore": 0,
                "impBonus": 0,
                "isHotStreak": False,
                "hotStreakBonus": 0,
                "baseScore": 0,
                "totalScore": 0,
            }
        entry["recentWinScore"] = hero.get("winCount")
        entry["impBonus"] = (hero.get("imp") or 0) * RECENT_IMP_WEIGHT
        last_played = from_seconds(_n(hero.get("lastPlayedDateTime")), now.tzinfo)
        entry["isHotStreak"] = last_played.timestamp() > three_days_ago.timestamp()
    for entry in scores.values():
        entry["baseScore"] = _n(entry["recentWinScore"]) + entry["lifetimeWinScore"] + entry["impBonus"]
        if entry["isHotStreak"]:
            entry["totalScore"] = entry["baseScore"] * HOT_STREAK_MULTIPLIER
            entry["hotStreakBonus"] = entry["totalScore"] - entry["baseScore"]
        else:
            entry["totalScore"] = entry["baseScore"]

    recent = player.get("recentPerformance") or []
    lifetime = player.get("lifetimePerformance") or []
    account = player.get("steamAccount") or {}
    if recent:
        recommendation_type = "PERSONALIZED"
    elif lifetime:
        recommendation_type = "LIFETIME_ONLY"
    elif account.get("isAnonymous"):
        recommendation_type = "PLAYER_ANONYMOUS"
    else:
        recommendation_type = "LIFETIME_NO_RECORD"

    pool = sorted(scores.values(), key=lambda e: -e["totalScore"])[:RECOMMENDATION_POOL_SIZE]
    weights = {entry["heroId"]: entry["totalScore"] for entry in pool}
    date_text = luxon_format(now, "yyyyMMdd")
    recommended: list[int] = []
    for seed_index in range(RECOMMENDATION_COUNT):
        if not weights:
            break
        random = seeded_random(f"{date_text}-{steam_id}-{seed_index}")
        hero_id = random.weighted_pick(weights)
        if hero_id is None:
            # Target fix: the original pushed NaN and kept looping with the same weights.
            break
        recommended.append(int(hero_id))
        weights.pop(hero_id, None)
    return {"recommendedHeroes": recommended, "recommendationPool": pool, "recommendationType": recommendation_type}


# ---------------------------------------------------------------- roster
def build_guild_members(players_data: list[dict[str, Any]], bindings: list[Any]) -> list[dict[str, Any]]:
    """``PlayerService.getMembersInChannel`` – win rate over the last 10 matches."""
    result = []
    for player in players_data:
        account = player.get("steamAccount") or {}
        steam_id = account.get("id")
        matches = player.get("matches") or []
        wins = 0
        for match in matches:
            own = next(
                (p for p in match.get("players") or [] if (p.get("steamAccount") or {}).get("id") == steam_id), None
            )
            if own is not None and match.get("didRadiantWin") == own.get("isRadiant"):
                wins += 1
        win_rate = wins / len(matches) if matches else math.nan
        binding = next((b for b in bindings if str(getattr(b, "steam_id", "")) == js_str(steam_id)), None)
        nickname = getattr(binding, "nickname", "") if binding is not None else ""
        result.append(
            {
                "name": nickname or account.get("name"),
                "steamId": steam_id,
                "winRate": win_rate,
                "lastMatchTime": matches[0].get("startDateTime") if matches else None,
            }
        )
    return result
