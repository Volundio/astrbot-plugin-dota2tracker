"""Daily and legacy weekly report computations (ports of ``daily-report.service.ts``
and ``DailyReportTask.report_legacy``)."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..data.static import DotaConstants
from ..utils.jsnum import js_to_fixed, js_truthy, round_to_decimal_places, to_locale_string
from ..utils.timefmt import add_calendar, luxon_format

__all__ = [
    "ReportTranslator",
    "build_legacy_report",
    "build_spotlight_card",
    "format_daily_report_bundles",
    "format_duration",
]

HeroImage = Callable[[str], str]
ErrorHandler = Callable[[str, Exception], None]


@dataclass
class ReportTranslator:
    """Per-channel translation helpers (``getTranslator`` in the original)."""

    t: Callable[..., str]
    locale: str
    hero_name: Callable[[Any], str]


def _n(value: Any) -> Any:
    return 0 if value is None else value


def format_duration(seconds: float) -> str:
    minutes = math.floor(seconds / 60)
    secs = math.floor(math.fmod(seconds, 60))
    return f"{minutes}:{str(secs).rjust(2, '0')}"


def _account_id(player: dict[str, Any]) -> Any:
    return (player.get("steamAccount") or {}).get("id")


def _find_self(match: dict[str, Any], steam_id: Any) -> dict[str, Any]:
    for player in match.get("players") or []:
        if _account_id(player) == steam_id:
            return player
    raise LookupError(f"player {steam_id} missing from match {match.get('id')}")


def _group_users_by_channel(users: Iterable[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for user in users:
        groups.setdefault(f"{user.platform}:{user.channel_id}", []).append(user)
    return groups


def _squad_stats(
    squad_data: list[dict[str, Any]], squad_ids: list[Any], target_seconds: float, end_seconds: float
) -> dict[str, Any]:
    current: dict[Any, dict[str, Any]] = {}
    previous: dict[Any, dict[str, Any]] = {}
    for player in squad_data:
        for match in player.get("matches") or []:
            start = _n(match.get("startDateTime"))
            if start >= end_seconds:
                continue  # belongs to the next report (target fix, see DEVELOPMENT.md §9.5)
            (current if start >= target_seconds else previous)[match.get("id")] = match

    def calc(matches: dict[Any, dict[str, Any]]) -> dict[str, Any]:
        total = len(matches)
        wins = kills = duration = 0
        for match in matches.values():
            duration += _n(match.get("durationSeconds"))
            members = [p for p in match.get("players") or [] if _account_id(p) in squad_ids]
            if any(p.get("isRadiant") == match.get("didRadiantWin") for p in members):
                wins += 1
            kills += sum(_n(p.get("kills")) for p in members)
        return {
            "totalMatches": total,
            "totalWins": wins,
            "totalKills": kills,
            "totalDuration": duration,
            "winRate": (wins / total) * 100 if total > 0 else 0,
            "avgKills": kills / total if total > 0 else 0,
            "avgDuration": duration / total if total > 0 else 0,
        }

    current_stats = calc(current)
    previous_stats = calc(previous)
    has_previous = previous_stats["totalMatches"] > 0
    return {
        **current_stats,
        "hasPreviousMatches": has_previous,
        "winRateDiff": current_stats["winRate"] - previous_stats["winRate"] if has_previous else 0,
    }


def _extension_for(extensions: list[dict[str, Any]], match_id: Any) -> dict[str, Any] | None:
    for extension in extensions:
        if extension.get("matchId") is not None and int(extension["matchId"]) == int(match_id):
            return extension
    return None


def _player_extension(extension: dict[str, Any] | None, steam_id: Any) -> dict[str, Any] | None:
    for player in ((extension or {}).get("data") or {}).get("players") or []:
        if player.get("steamAccountId") == steam_id:
            return player
    return None


def _process_player(
    user: Any,
    player_data: dict[str, Any],
    constants: DotaConstants,
    target_seconds: float,
    end_seconds: float,
    extensions: list[dict[str, Any]],
    hero_image: HeroImage,
) -> dict[str, Any]:
    wins = kills = deaths = assists = 0
    hero_damage = tower_damage = networth = 0
    kda_sum = mvp_sum = 0.0
    # Target fix: MVP scores can be negative (IMP weighs in); starting at -1 left
    # bestMatchId unset for a day of bad games and the spotlight lookup crashed.
    best_score, worst_ratio = -math.inf, math.inf
    best_kda, worst_kda = -1.0, math.inf
    best_match = worst_match = 0
    best_utility, best_utility_match = -1, 0
    best_healing, best_healing_match = -1, 0
    best_tower, best_tower_match = -1, 0
    played: dict[Any, dict[str, int]] = {}
    count = 0
    steam_id = user.steam_id

    for match in player_data.get("matches") or []:
        start = _n(match.get("startDateTime"))
        if start < target_seconds or start >= end_seconds:
            continue
        count += 1
        own = _find_self(match, steam_id)
        won = own.get("isRadiant") == match.get("didRadiantWin")
        if won:
            wins += 1
        kills += _n(own.get("kills"))
        deaths += _n(own.get("deaths"))
        assists += _n(own.get("assists"))
        hero_damage += _n(own.get("heroDamage"))
        tower_damage += _n(own.get("towerDamage"))
        networth += _n(own.get("networth"))
        match_kda = (_n(own.get("kills")) + _n(own.get("assists"))) / max(1, _n(own.get("deaths")))
        kda_sum += match_kda

        extension = _extension_for(extensions, match.get("id"))
        own_extension = _player_extension(extension, steam_id)
        mvp_score = (own_extension or {}).get("mvpScore") or 0
        utility_score = (own_extension or {}).get("utilityScore") or 0
        healing = own.get("heroHealing") or 0
        tower = own.get("towerDamage") or 0
        mvp_sum += mvp_score

        if mvp_score > best_score or (mvp_score == best_score and match_kda > best_kda):
            best_score, best_kda, best_match = mvp_score, match_kda, match.get("id")

        lost = own.get("isRadiant") != match.get("didRadiantWin")
        match_players = ((extension or {}).get("data") or {}).get("players") or []
        average = sum((p.get("mvpScore") or 0) for p in match_players) / len(match_players) if match_players else 0
        ratio = mvp_score / average if average > 0 else 1
        if lost and ratio < 0.25 and (ratio < worst_ratio or (ratio == worst_ratio and match_kda < worst_kda)):
            worst_ratio, worst_kda, worst_match = ratio, match_kda, match.get("id")

        if utility_score > 1200 and utility_score > best_utility:
            best_utility, best_utility_match = utility_score, match.get("id")
        if healing > 10000 and healing > best_healing:
            best_healing, best_healing_match = healing, match.get("id")
        if tower > 10000 and tower > best_tower:
            best_tower, best_tower_match = tower, match.get("id")

        hero_stats = played.setdefault(own.get("heroId"), {"count": 0, "wins": 0})
        hero_stats["count"] += 1
        if won:
            hero_stats["wins"] += 1

    sorted_heroes = sorted(played.items(), key=lambda item: (-item[1]["wins"], item[1]["count"]))
    account = player_data.get("steamAccount") or {}
    row = {
        "rank": 0,
        "player": {
            "name": user.nickname or account.get("name") or "Unknown",
            "avatarUrl": account.get("avatar") or "",
            "winCount": wins,
            "loseCount": count - wins,
        },
        "heroes": [
            {
                "url": hero_image(constants.hero_short_name(hero_id)) if constants.heroes.get(_int(hero_id)) else "",
                "wins": stats["wins"],
                "losses": stats["count"] - stats["wins"],
            }
            for hero_id, stats in sorted_heroes[:3]
        ],
        "plusHeroesCount": max(0, len(sorted_heroes) - 3),
        "kda": {
            "ratio": "0.0" if kills + assists == 0 else js_to_fixed((kills + assists) / max(1, deaths), 1),
            "detail": " / ".join(js_to_fixed(value / count, 1) for value in (kills, deaths, assists))
            if count > 0
            else "0.0 / 0.0 / 0.0",
        },
        "mvpScore": js_to_fixed(mvp_sum / count, 1) if count > 0 else "0.0",
        "impact": {"damage": {"heroPercent": 0, "buildingsPercent": 0}, "networth": {"percent": 0}},
    }
    stats = {
        "steamId": steam_id,
        "avgKda": kda_sum / count if count > 0 else 0,
        "maxMvpScore": best_score,
        "bestMatchId": best_match,
        "minLvpRatio": worst_ratio,
        "worstMatchId": worst_match,
        "maxUtilityScore": best_utility,
        "bestUtilityMatchId": best_utility_match,
        "maxHealing": best_healing,
        "bestHealingMatchId": best_healing_match,
        "maxTowerDamage": best_tower,
        "bestTowerDamageMatchId": best_tower_match,
    }
    impact = {
        "heroDamage": hero_damage,
        "towerDamage": tower_damage,
        "networth": networth,
        "matchCount": count,
        "row": row,
    }
    return {"row": row, "stats": stats, "impact": impact}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _js_round(value: float) -> int:
    floor = math.floor(value)
    return int(floor + 1 if value - floor >= 0.5 else floor)


def _impact_percentages(impacts: list[dict[str, Any]]) -> None:
    max_damage = 0.0
    max_networth = 0.0
    for data in impacts:
        average_damage = (data["heroDamage"] + data["towerDamage"]) / data["matchCount"]
        average_networth = data["networth"] / data["matchCount"]
        max_damage = max(max_damage, average_damage)
        max_networth = max(max_networth, average_networth)
    for data in impacts:
        hero_pct = _js_round(data["heroDamage"] / data["matchCount"] / max_damage * 100) if max_damage > 0 else 0
        building_pct = _js_round(data["towerDamage"] / data["matchCount"] / max_damage * 100) if max_damage > 0 else 0
        impact = data["row"]["impact"]
        impact["damage"]["heroPercent"] = min(100, hero_pct)
        impact["damage"]["buildingsPercent"] = min(100 - impact["damage"]["heroPercent"], building_pct)
        impact["networth"]["percent"] = (
            _js_round(data["networth"] / data["matchCount"] / max_networth * 100) if max_networth > 0 else 0
        )


def build_spotlight_card(
    player_data: dict[str, Any],
    card_type: str,
    match_id: Any,
    extensions: list[dict[str, Any]],
    constants: DotaConstants,
    translator: ReportTranslator,
    hero_banner: HeroImage,
) -> dict[str, Any]:
    t = translator.t
    steam_id = _account_id(player_data)
    match = next(m for m in player_data.get("matches") or [] if m.get("id") == match_id)
    own = _find_self(match, steam_id)
    own_extension = _player_extension(_extension_for(extensions, match_id), steam_id)
    badge_keys = (own_extension or {}).get("titles") or []
    mvp_score = (own_extension or {}).get("mvpScore") or 0
    utility_score = (own_extension or {}).get("utilityScore") or 0
    match_kda = (_n(own.get("kills")) + _n(own.get("assists"))) / max(1, _n(own.get("deaths")))

    prefix = "dota2tracker.template.report.daily.spotlight."
    title, theme = t(prefix + "mvp_title"), "gold"
    label = t(prefix + "score_label")
    value = js_to_fixed(mvp_score, 1) if js_truthy(mvp_score) else "-"
    if card_type == "LVP":
        title, theme, label = t(prefix + "lvp_title"), "red", t(prefix + "score_label")
    elif card_type == "UTILITY":
        title, theme, label = t(prefix + "utility_title"), "cyan", t(prefix + "utility_score_label")
        value = to_locale_string(utility_score) if js_truthy(utility_score) else "-"
    elif card_type == "HEALER":
        title, theme, label = t(prefix + "healer_title"), "green", t(prefix + "healer_score_label")
        healing = own.get("heroHealing")
        value = to_locale_string(healing) if js_truthy(healing) else "-"
    elif card_type == "DEMOLISHER":
        title, theme, label = t(prefix + "demolisher_title"), "orange", t(prefix + "demolisher_score_label")
        tower = own.get("towerDamage")
        value = to_locale_string(tower) if js_truthy(tower) else "-"

    badges = []
    for key in badge_keys:
        translated = t(key)
        parts = translated.split("-#")
        text = parts[0]
        color = parts[1] if len(parts) > 1 else None
        badges.append({"text": text or key, "hexColor": f"#{color}" if color else "#FFA500"})

    account = player_data.get("steamAccount") or {}
    hero_id = own.get("heroId")
    return {
        "type": card_type,
        "title": title,
        "themeColor": theme,
        "player": {
            "name": account.get("name") or "Unknown",
            "heroName": translator.hero_name(hero_id),
            "kda": "/".join(str(own.get(key) or 0) for key in ("kills", "deaths", "assists"))
            + f" ({js_to_fixed(match_kda, 1)})",
            "heroBannerUrl": hero_banner(constants.hero_short_name(hero_id) or "")
            if constants.heroes.get(_int(hero_id))
            else "",
            "avatarUrl": account.get("avatar") or "",
        },
        "score": {"value": value, "label": label},
        "badges": badges,
    }


def format_daily_report_bundles(
    data: dict[str, Any],
    users: list[Any],
    extensions: list[dict[str, Any]],
    constants: DotaConstants,
    target_date: datetime,
    translators: dict[str, ReportTranslator],
    hero_image: HeroImage,
    hero_banner: HeroImage,
    footer_id: str,
    *,
    end_date: datetime | None = None,
    on_error: ErrorHandler | None = None,
) -> list[dict[str, Any]]:
    """Return ``[{platform, channelId, report}]`` – one daily report per channel.

    The report covers ``target_date <= start < end_date`` (``end_date``
    defaults to one calendar day later); earlier matches form the comparison
    period. A failing channel is passed to ``on_error`` and skipped so the
    other channels still get their report (re-raised when ``on_error`` is
    ``None``).
    """
    target_seconds = target_date.timestamp()
    end_seconds = (end_date or add_calendar(target_date, days=1)).timestamp()
    bundles = []
    all_players = data.get("players") or []
    for key, squad_users in _group_users_by_channel(users).items():
        try:
            bundle = _channel_daily_report(
                key,
                squad_users,
                all_players,
                extensions,
                constants,
                target_date,
                target_seconds,
                end_seconds,
                translators[key],
                hero_image,
                hero_banner,
                footer_id,
            )
        except Exception as error:
            if on_error is None:
                raise
            on_error(key, error)
            continue
        if bundle is not None:
            bundles.append(bundle)
    return bundles


def _channel_daily_report(
    key: str,
    squad_users: list[Any],
    all_players: list[dict[str, Any]],
    extensions: list[dict[str, Any]],
    constants: DotaConstants,
    target_date: datetime,
    target_seconds: float,
    end_seconds: float,
    translator: ReportTranslator,
    hero_image: HeroImage,
    hero_banner: HeroImage,
    footer_id: str,
) -> dict[str, Any] | None:
    platform, channel_id = key.split(":", 1)
    t = translator.t
    squad_ids = [u.steam_id for u in squad_users]
    squad_data = [p for p in all_players if p and _account_id(p) in squad_ids]
    if not squad_data:
        return None
    squad = _squad_stats(squad_data, squad_ids, target_seconds, end_seconds)
    if squad["totalMatches"] == 0:
        return None

    rows: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []
    impacts: list[dict[str, Any]] = []
    for user in squad_users:
        player_data = next((p for p in squad_data if _account_id(p) == user.steam_id), None)
        if not player_data or not player_data.get("matches"):
            continue
        processed = _process_player(user, player_data, constants, target_seconds, end_seconds, extensions, hero_image)
        if processed["impact"]["matchCount"] == 0:
            continue
        rows.append(processed["row"])
        stats.append(processed["stats"])
        impacts.append(processed["impact"])
    if not rows:
        return None
    _impact_percentages(impacts)

    def card(stat: dict[str, Any], card_type: str, match_key: str) -> dict[str, Any]:
        player_data = next(p for p in squad_data if _account_id(p) == stat["steamId"])
        return build_spotlight_card(
            player_data, card_type, stat[match_key], extensions, constants, translator, hero_banner
        )

    cards = []
    stats.sort(key=lambda s: (-s["maxMvpScore"], -s["avgKda"]))
    mvp_card = card(stats[0], "MVP", "bestMatchId")
    cards.append(mvp_card)

    lvp_card = None
    lvp_candidates = [s for s in stats if s["worstMatchId"] > 0]
    if lvp_candidates:
        worst = lvp_candidates[0]
        for current in lvp_candidates[1:]:
            if current["minLvpRatio"] < worst["minLvpRatio"] or (
                current["minLvpRatio"] == worst["minLvpRatio"] and current["avgKda"] < worst["avgKda"]
            ):
                worst = current
        lvp_card = card(worst, "LVP", "worstMatchId")
        cards.append(lvp_card)

    for card_type, match_key, value_key in (
        ("UTILITY", "bestUtilityMatchId", "maxUtilityScore"),
        ("HEALER", "bestHealingMatchId", "maxHealing"),
        ("DEMOLISHER", "bestTowerDamageMatchId", "maxTowerDamage"),
    ):
        candidates = [s for s in stats if s[match_key] > 0]
        if candidates:
            candidates.sort(key=lambda s, vk=value_key: (-s[vk], -s["avgKda"]))
            cards.append(card(candidates[0], card_type, match_key))

    rows.sort(key=lambda r: (-float(r["mvpScore"]), -float(r["kda"]["ratio"])))
    for index, row in enumerate(rows):
        row["rank"] = index + 1

    prefix = "dota2tracker.template.report.daily."
    win_diff = squad["winRateDiff"]
    report = {
        "meta": {
            "date": luxon_format(target_date, t(prefix + "meta.date_format"), translator.locale),
            "summary": t(prefix + "meta.summary", [channel_id]),
            "footerId": footer_id,
        },
        "headerStats": {
            "matches": {
                "value": squad["totalMatches"],
                "subtext": t(
                    prefix + "stats.matches_subtext",
                    [squad["totalWins"], squad["totalMatches"] - squad["totalWins"]],
                ),
            },
            "winRate": {
                "value": f"{js_to_fixed(squad['winRate'], 1)}%",
                "subtext": (
                    f"{t(prefix + 'stats.vs_yesterday')} {'▲' if win_diff >= 0 else '▼'} "
                    f"{js_to_fixed(abs(win_diff), 1)}%"
                    if squad["hasPreviousMatches"]
                    else "---"
                ),
                "isPositive": win_diff >= 0,
                "isWinRateAbove50": squad["winRate"] >= 50,
                "hasComparison": squad["hasPreviousMatches"],
            },
            "kills": {
                "value": to_locale_string(squad["totalKills"]),
                "subtext": t(prefix + "stats.kills_avg", [js_to_fixed(squad["avgKills"], 1)]),
            },
            "duration": {
                "value": format_duration(squad["totalDuration"]),
                "subtext": f"{t(prefix + 'stats.avg_time')} {format_duration(squad['avgDuration'])}",
            },
        },
        "spotlights": {
            "cards": cards,
            "mvp": mvp_card,
            "secondary": cards[1] if len(cards) > 1 else None,
            "lvp": lvp_card,
        },
        "squad": rows,
    }
    return {"platform": platform, "channelId": channel_id, "report": report}


# ------------------------------------------------------------- legacy weekly
def build_legacy_report(
    channels: list[Any],
    bindings: list[Any],
    players_data: list[dict[str, Any]],
    names: dict[int, str],
    *,
    on_error: ErrorHandler | None = None,
) -> list[dict[str, Any]]:
    """Per-channel data for the legacy ``daily_legacy`` template.

    ``names`` maps binding primary keys to the display name resolved by the
    caller (nickname → group card → Steam name). Inconsistent data for one
    binding or channel is passed to ``on_error`` (keyed ``platform:channel``)
    and skipped; it is re-raised when ``on_error`` is ``None``.
    """

    def failed(key: str, error: Exception) -> None:
        if on_error is None:
            raise error
        on_error(key, error)

    players = [p for p in players_data if p and len(p.get("matches") or []) > 0]
    matches: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for player in players:
        for match in player.get("matches") or []:
            if match.get("id") not in seen:
                seen.add(match.get("id"))
                matches.append(match)

    enriched: list[dict[str, Any]] = []
    for binding in bindings:
        data = next((p for p in players if _account_id(p) == binding.steam_id), None)
        if data is None:
            continue
        try:
            enriched.append(_legacy_player(binding, data, names))
        except Exception as error:
            failed(f"{binding.platform}:{binding.channel_id}", error)

    results = []
    for channel in channels:
        current = [e for e in enriched if e["platform"] == channel.platform and e["channelId"] == channel.channel_id]
        if not current:
            continue
        try:
            combinations = _legacy_combinations(current, matches)
        except Exception as error:
            failed(f"{channel.platform}:{channel.channel_id}", error)
            continue
        ordered = sorted(current, key=lambda e: (-len(e["matches"]), _n(e["steamAccount"].get("id"))))
        results.append({"channel": channel, "players": ordered, "combinations": combinations})
    return results


def _legacy_player(binding: Any, data: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    own_matches = data.get("matches") or []
    total = len(own_matches)
    steam_id = _account_id(data)

    def own(match: dict[str, Any]) -> dict[str, Any]:
        return _find_self(match, steam_id)

    win_count = sum(1 for m in own_matches if m.get("didRadiantWin") == own(m).get("isRadiant"))
    avg_kills = round_to_decimal_places(sum(_n(own(m).get("kills")) for m in own_matches) / total, 2)
    avg_deaths = round_to_decimal_places(sum(_n(own(m).get("deaths")) for m in own_matches) / total, 2)
    avg_assists = round_to_decimal_places(sum(_n(own(m).get("assists")) for m in own_matches) / total, 2)
    return {
        "binding": binding,
        "steamId": binding.steam_id,
        "platform": binding.platform,
        "channelId": binding.channel_id,
        "steamAccount": data.get("steamAccount") or {},
        "matches": own_matches,
        "name": names.get(binding.id) or (data.get("steamAccount") or {}).get("name"),
        "winCount": win_count,
        "loseCount": total - win_count,
        "avgKills": avg_kills,
        "avgDeaths": avg_deaths,
        "avgAssists": avg_assists,
        "avgKDA": round_to_decimal_places((avg_kills + avg_assists) / (avg_deaths or 1), 2),
        "avgImp": round_to_decimal_places(sum(_n(own(m).get("imp")) for m in own_matches) / total, 0),
    }


def _legacy_combinations(current: list[dict[str, Any]], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [e["steamId"] for e in current]
    combinations: dict[str, dict[str, Any]] = {}
    for match in matches:
        sorted_ids = sorted(_account_id(p) for p in match.get("players") or [] if _account_id(p) in ids)
        key = ",".join(str(i) for i in sorted_ids)
        if key not in combinations:
            members = [e for e in current if e["steamId"] in sorted_ids]
            if members:
                first = _find_self(match, members[0]["steamId"])
                combinations[key] = {
                    "players": members,
                    "name": "/".join(str(m["name"]) for m in members),
                    "winCount": 1 if match.get("didRadiantWin") == first.get("isRadiant") else 0,
                    "matches": [match],
                }
        else:
            combo = combinations[key]
            combo["matches"].append(match)
            first = _find_self(match, combo["players"][0]["steamId"])
            combo["winCount"] += 1 if match.get("didRadiantWin") == first.get("isRadiant") else 0
    return list(combinations.values())
