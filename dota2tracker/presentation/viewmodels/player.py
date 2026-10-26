"""View model for the ``player_1`` template (port of ``player/player_1.ejs``).

Every computation of the archived EJS template (streak, near-25 win stats,
lane outcome counts, kill participation, bar scales, position performance and
colours) is evaluated here with JavaScript number semantics.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ...utils.jsnum import js_div, js_round, js_str, js_to_fixed, js_truthy
from ...utils.timefmt import from_seconds, luxon_format
from ..assets import ImageAssets

__all__ = ["build_player_view", "get_guild_class", "get_scale", "get_win_rate_color", "preview_player_1"]

Translate = Callable[..., Any]
NEAR_COUNT = 25
POS_ICONS = ["damage", "nuke", "armor", "speed", "healing"]
LANE_MAP = {
    "RADIANT_VICTORY": ("victory", "fail"),
    "RADIANT_STOMP": ("stomp", "stomped"),
    "DIRE_VICTORY": ("fail", "victory"),
    "DIRE_STOMP": ("stomped", "stomp"),
}


def _cat(value: Any) -> str:
    """String concatenation of a JS value (``"" + value``; ``null`` → ``"null"``)."""
    return "null" if value is None else js_str(value)


def _num(value: Any) -> float:
    """``Number(value)`` for arithmetic (``null`` → 0, ``undefined`` → NaN is approximated as 0)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return 0


def _hex(component: float) -> str:
    if isinstance(component, float) and math.isnan(component):
        return "NAN"
    return format(int(component), "x").rjust(2, "0").upper()


def get_win_rate_color(value: float) -> str:
    """``getWinRateColor``: red (0%) → white (50%) → green (100%)."""
    if isinstance(value, float) and math.isnan(value):
        val = math.nan
    else:
        val = max(0.0, min(100.0, value * 100))
    if not math.isnan(val) and val <= 50:
        s = js_round(255 * (val / 50))
        r, g, b = 255, s, s
    else:
        s = js_round(255 * ((val - 50) / 50))
        r, g, b = 255 - s, 255, 255 - s
    return f"#{_hex(r)}{_hex(g)}{_hex(b)}"


def get_guild_class(percentile: Any) -> str:
    """``getGuildClass`` (``undefined`` comparisons are false → ``Diamond``)."""
    if not isinstance(percentile, (int, float)):
        return "Diamond"
    if percentile <= 25:
        return "Copper"
    if percentile <= 50:
        return "Silver"
    if percentile <= 75:
        return "Gold"
    return "Diamond"


def get_scale(items: list[dict[str, Any]], is_near: bool) -> float:
    """``getScale``: pixels per match for the win/lose bars (600px available)."""
    if not items:
        return 0
    max_win = max(_num(i.get("winCount")) for i in items)
    max_lose = max(_num(i.get("matchCount")) - _num(i.get("winCount")) for i in items)
    total = max_win + max_lose
    base = 600 / (total or 1)
    if not is_near:
        return base
    return base * min(js_div(max_win, total), js_div(max_lose, total))


def _signed(value: Any) -> str:
    """``(value > 0 ? "+" : "") + value``."""
    return ("+" if isinstance(value, (int, float)) and value > 0 else "") + _cat(value)


def _bar_style(count: float, scale: float) -> str:
    hidden = "visibility:hidden;" if count == 0 else ""
    return f"{hidden}width: {js_str(count * scale)}px"


def _inner(match: dict[str, Any], steam_id: Any) -> dict[str, Any]:
    for player in match.get("players") or []:
        if (player.get("steamAccount") or {}).get("id") == steam_id:
            return player
    return {}


def _match_rows(
    player: dict[str, Any], t: Translate, assets: ImageAssets, tz
) -> tuple[list[dict[str, Any]], dict[str, int], int, int | None]:
    steam_id = player["steamAccount"].get("id")
    outcomes = {"victory": 0, "stomp": 0, "fail": 0, "stomped": 0, "tie": 0}
    near_win = 0
    streak = 0
    final_streak: int | None = None
    rows = []
    for match in player.get("matches") or []:
        inner = _inner(match, steam_id)
        is_rad = inner.get("isRadiant")
        did_win = match.get("didRadiantWin") == is_rad

        # p.streak is only assigned once the streak breaks.
        if not final_streak:
            if streak != 0:
                if did_win and streak > 0:
                    streak += 1
                elif not did_win and streak < 0:
                    streak -= 1
                else:
                    final_streak = streak
            else:
                streak = 1 if did_win else -1

        near_win += 1 if did_win else 0

        lane = inner.get("lane")
        if lane == "JUNGLE":
            lane_res = "jungle"
        else:
            if lane == "MID_LANE":
                lane_key = "mid"
            elif lane == "SAFE_LANE":
                lane_key = "bottom" if is_rad else "top"
            else:
                lane_key = "top" if is_rad else "bottom"
            outcome = match.get(lane_key + "LaneOutcome") if lane_key != "mid" else match.get("midLaneOutcome")
            pair = LANE_MAP.get(outcome) if isinstance(outcome, str) else None
            lane_res = (pair or ("tie", "tie"))[0 if is_rad else 1]
        if lane_res in outcomes:
            outcomes[lane_res] += 1

        kills = _num(inner.get("kills"))
        deaths = _num(inner.get("deaths"))
        assists = _num(inner.get("assists"))
        parsed = js_truthy(match.get("parsedDateTime"))
        if parsed:
            side = match.get(("radiant" if is_rad else "dire") + "Kills")
            team_kills = sum(_num(k) for k in side) if isinstance(side, list) else 0
        else:
            team_kills = sum(_num(p.get("kills")) for p in match.get("players") or [] if p.get("isRadiant") == is_rad)
        kp = js_to_fixed((kills + assists) / (team_kills or 1) * 100, 0)
        kda = js_to_fixed((kills + assists) / max(1, deaths), 2)

        lobby = match.get("lobbyType")
        mode = match.get("gameMode")
        imp = inner.get("imp")
        rank = match.get("rank")
        start = match.get("startDateTime")
        rows.append(
            {
                "id": js_str(match.get("id")),
                "parsed": parsed,
                "didWin": did_win,
                "lobby": t("dota2tracker.template.lobby_types." + js_str(lobby)) or js_str(lobby),
                "mode": t("dota2tracker.template.game_modes." + js_str(mode)) or js_str(mode),
                "heroImg": assets.url((inner.get("hero") or {}).get("shortName"), "heroes/icons"),
                "kda": kda,
                "kp": ("" if parsed else "≈") + kp,
                "kills": _cat(inner.get("kills")),
                "deaths": _cat(inner.get("deaths")),
                "assists": _cat(inner.get("assists")),
                "laneRes": lane_res,
                "laneImg": assets.url("lane_" + lane_res, None, "svg"),
                "timeStr": luxon_format(from_seconds(0 if start is None else start, tz), "yyyy-MM-dd HH:mm:ss")[2:],
                "durationTime": match.get("durationTime") or "",
                "imp": "?" if imp is None else ("+" if imp >= 0 else "") + js_str(imp),
                "rankImg": assets.url("medal_" + (js_str(rank)[0] if rank is not None else "undefined")),
            }
        )
    return rows, outcomes, near_win, final_streak


def _position_performance(player: dict[str, Any], assets: ImageAssets) -> list[dict[str, Any]]:
    steam_id = player["steamAccount"].get("id")
    result = []
    for index in range(5):
        matches = [
            m for m in player.get("matches") or [] if _inner(m, steam_id).get("position") == f"POSITION_{index + 1}"
        ]
        wins = len([m for m in matches if m.get("didRadiantWin") == _inner(m, steam_id).get("isRadiant")])
        if matches:
            imp: Any = js_round(sum(_num(_inner(m, steam_id).get("imp")) for m in matches) / len(matches))
        else:
            imp = "-"
        result.append(
            {
                "pos": index + 1,
                "icon": POS_ICONS[index],
                "img": assets.url(POS_ICONS[index], "icons/facets"),
                "matchCount": len(matches),
                "winCount": wins,
                "imp": imp,
            }
        )
    return result


def build_player_view(
    player: dict[str, Any], t: Translate, assets: ImageAssets, locale: str, tz=None
) -> dict[str, Any]:
    """All values needed by ``player_1``; ``player`` is ``extend_player_data`` output."""
    account = player.get("steamAccount") or {}
    is_anon = bool(account.get("isAnonymous"))
    rows, outcomes, near_win, streak = _match_rows(player, t, assets, tz)

    gen_hero = player.get("genHero")
    if gen_hero:
        hero_list = player.get("heroesPerformanceTop10") or []
    else:
        hero_list = [h for h in player.get("heroesPerformance") or [] if _num(h.get("matchCount")) > 1]
    hero_scale = get_scale(hero_list, not gen_hero)
    heroes = []
    for hero in hero_list:
        wins = _num(hero.get("winCount"))
        loses = _num(hero.get("matchCount")) - wins
        heroes.append(
            {
                "img": assets.url((hero.get("hero") or {}).get("shortName"), "heroes/icons"),
                "matchCount": _cat(hero.get("matchCount")),
                "winRate": js_to_fixed(js_div(wins, _num(hero.get("matchCount"))) * 100, 0),
                "imp": _signed(hero.get("imp")),
                "winCount": js_str(wins),
                "loseCount": js_str(loses),
                "winStyle": _bar_style(wins, hero_scale),
                "loseStyle": _bar_style(loses, hero_scale),
            }
        )

    positions_raw = _position_performance(player, assets)
    pos_scale = get_scale([p for p in positions_raw if p["matchCount"] > 1], True)
    positions = []
    for pos in positions_raw:
        loses = pos["matchCount"] - pos["winCount"]
        positions.append(
            {
                "img": pos["img"],
                "matchCount": js_str(pos["matchCount"]),
                "winRate": js_to_fixed(pos["winCount"] / pos["matchCount"] * 100, 0) if pos["matchCount"] > 0 else "-",
                "imp": _signed(pos["imp"]),
                "winCount": js_str(pos["winCount"]),
                "loseCount": js_str(loses),
                "winStyle": _bar_style(pos["winCount"], pos_scale),
                "loseStyle": _bar_style(loses, pos_scale),
            }
        )

    match_count = _num(player.get("matchCount"))
    win_count = _num(player.get("winCount"))
    win_rate = js_div(win_count, match_count)
    lane_total = sum(outcomes.values())
    lane_score = js_div(outcomes["victory"] + outcomes["stomp"] + outcomes["tie"] / 2, lane_total)
    performance = player.get("performance")
    if is_anon:
        imp_text = "?"
    elif performance is None:
        imp_text = "undefined"
    else:
        imp_text = _signed(performance.get("imp")) if "imp" in performance else "undefined"

    guild_member = player.get("guildMember")
    guild = None
    if guild_member:
        guild_data = guild_member.get("guild") or {}
        guild = {"cls": get_guild_class(guild_data.get("currentPercentile")), "tag": guild_data.get("tag") or ""}

    rank = player.get("rank") or {}
    medal = rank.get("inTop100") if rank.get("inTop100") is not None else rank.get("medal")
    leaderboard = account.get("seasonLeaderboardRank")

    show_streak = streak is not None and abs(streak) > 1 and not is_anon
    streak_view = None
    if show_streak:
        assert streak is not None
        key = "winning_streak" if streak > 0 else "losing_streak"
        streak_view = {
            "style": f"box-shadow:none;color:{get_win_rate_color((streak + 10) / 20)};",
            "text": js_str(abs(streak)) + (t("dota2tracker.template." + key) or ""),
        }

    dota_plus = []
    for hero in player.get("dotaPlus") or []:
        level = hero.get("level")
        badge = math.ceil((_num(level) + 1) / 6)
        win = hero.get("winCount")
        total = hero.get("matchCount")
        rate = js_div(_num(win), _num(total)) if win is not None and total is not None else math.nan
        dota_plus.append(
            {
                "img": assets.url(hero.get("shortName"), "heroes"),
                "badge": assets.url("hero_badge_" + js_str(badge)),
                "level": _cat(level),
                "winRate": js_to_fixed(rate * 100, 2),
                "matchCount": js_str(total),
            }
        )

    return {
        "languageTag": locale,
        "isAnon": is_anon,
        "playerName": account.get("name") or "",
        "avatar": account.get("avatar") or "",
        "guild": guild,
        "genHeroName": gen_hero.get("name") if gen_hero else None,
        "matchCount": _cat(player.get("matchCount")),
        "winCount": _cat(player.get("winCount")),
        "loseCount": js_str(match_count - win_count),
        "winRateColor": get_win_rate_color(win_rate),
        "winRate": js_to_fixed(win_rate * 100, 2),
        "nearWin": js_str(near_win),
        "nearLose": js_str(NEAR_COUNT - near_win),
        "nearRateColor": get_win_rate_color(near_win / NEAR_COUNT),
        "nearRate": js_to_fixed(near_win / NEAR_COUNT * 100, 2),
        "impText": imp_text,
        "outcomes": {
            "win": js_str(outcomes["victory"] + outcomes["stomp"]),
            "stomp": js_str(outcomes["stomp"]),
            "tie": js_str(outcomes["tie"]),
            "fail": js_str(outcomes["fail"] + outcomes["stomped"]),
            "stomped": js_str(outcomes["stomped"]),
        },
        "laneColor": get_win_rate_color(lane_score),
        "laneRate": "?" if is_anon else js_to_fixed(lane_score * 100, 2),
        "estimated": bool(player.get("isEstimatedRank")),
        "medalImg": assets.url("medal_" + _cat(medal)),
        "starImg": assets.url("star_" + _cat(rank.get("star"))),
        "leaderboard": "" if leaderboard is None else js_str(leaderboard),
        "isGenHero": bool(gen_hero),
        "heroes": heroes,
        "positions": positions,
        "streak": streak_view,
        "matches": rows,
        "dotaPlus": dota_plus,
    }


def preview_player_1(ctx) -> dict[str, Any]:
    from ...domain.player import extend_player_data
    from ..previews import load_fixture

    name = "player_profile_anonymous.json" if ctx.variant == "anonymous" else "player_profile.json"
    data = load_fixture(name)
    gen_hero = None
    if ctx.variant == "hero":
        performance = (data.get("playerExtraQuery") or {}).get("player", {}).get("heroesPerformance") or []
        hero_id = performance[0]["hero"]["id"]
        # The hero filtered query returns only this hero in heroesPerformance.
        data["playerExtraQuery"]["player"]["heroesPerformance"] = performance[:1]
        gen_hero = {"heroId": hero_id, "name": ctx.t(f"dota2tracker.template.hero_names.{hero_id}") or ""}
    player = extend_player_data(
        data["playerQuery"], data.get("playerExtraQuery"), ctx.constants, gen_hero=gen_hero, estimate_rank=True
    )
    return build_player_view(player, ctx.t, ctx.assets, ctx.locale, ctx.tz)
