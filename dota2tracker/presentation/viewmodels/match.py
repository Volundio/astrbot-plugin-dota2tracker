"""View models for the match templates (``match_1``, ``match_2``, ``match_2+``).

Every expression of the archived EJS templates that computed something
(colours, percentages, chart coordinates, building states, lane rivals…) is
evaluated here with JavaScript number semantics; templates only print values.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from ...data.static import DotaConstants
from ...utils.jsnum import js_div, js_parse_int, js_round, js_str, js_to_fixed, js_truthy, to_locale_string
from ...utils.timefmt import from_seconds, luxon_format
from ..assets import ImageAssets

__all__ = ["build_match_view", "parse_badge"]

Translate = Callable[..., Any]
PARTY_COLORS = ["#caffe5", "#ffe484", "#e19be2", "#ccdaf4"]
LANE_SVG = {
    "stomp": "lane_stomp",
    "advantage": "lane_victory",
    "disadvantage": "lane_fail",
    "stomped": "lane_stomped",
    "tie": "lane_tie",
    "jungle": "lane_jungle",
}
BAN_GRADIENT = (
    "background-image: linear-gradient(to bottom left, transparent calc(50% - 1px), red calc(50% - 1px), "
    "black calc(50% + 1px), transparent calc(50% + 1px));"
)


def _n(value: Any) -> Any:
    return 0 if value is None else value


def _get(data: Any, *path: str) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _js_template(value: Any) -> str:
    """``${value}`` in a JS template literal."""
    if value is None:
        return "null"
    return js_str(value)


def parse_badge(key: str, t: Translate) -> dict[str, str]:
    """``parseBadge`` from ``common/utils/match.ejs``."""
    raw = t(key) or ""
    parts = raw.split("-#")
    text = parts[0]
    color = "#" + parts[1] if len(parts) > 1 else "#9CA3AF"
    suffix = key.split(".")[-1] if key else ""
    short = text
    if suffix not in ("MVP", "Soul") and re.fullmatch(r"[A-Za-z0-9]+", text) and len(text) > 3:
        short = text[0].upper()
    return {"fullText": text, "shortText": short, "color": color}


def darken(color: str, amount: float) -> str:
    """``darken(col, amt)`` from ``match_2/original.ejs``."""
    col = color.lstrip("#")
    if len(col) == 3:
        col = "".join(c + c for c in col)
    try:
        number = int(col, 16)
    except ValueError:
        number = 0
    delta = js_round(2.55 * amount)
    r = max(0, (number >> 16) - delta)
    g = max(0, ((number >> 8) & 0x00FF) - delta)
    b = max(0, (number & 0x0000FF) - delta)
    return "#" + format(0x1000000 + (r << 16) + (g << 8) + b, "x")[1:]


def _rank_digit(rank: Any, index: int, missing: str) -> str:
    if not js_truthy(rank):
        return missing
    text = js_str(rank)
    return text[index] if index < len(text) else "undefined"


def _rank_digit_or_zero(rank: Any, index: int) -> str:
    """``match.rank?.toString().split('')[i] ?? '0'`` (match_2)."""
    if rank is None:
        return "0"
    text = js_str(rank)
    return text[index] if index < len(text) else "0"


def _kc_color(value: float) -> str:
    red = js_to_fixed(255 - (value * 255) / 100, 2)
    return f"rgb(255,{red},{red})"


def _dc_color(value: float) -> str:
    gray = js_to_fixed((50 - min(value, 50)) * (255 / 50), 2) if not math.isnan(value) else "NaN"
    return f"rgb({gray},{gray},{gray})"


def _floor(value: float) -> float:
    return math.floor(value) if math.isfinite(value) else value


def _position_digit(position: Any) -> str:
    return str(position)[-1] if position else "undefined"


def _item_view(item: dict[str, Any] | None, assets: ImageAssets) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "time": item.get("time"),
        "isRecipe": bool(item.get("isRecipe")),
        "img": assets.url(item.get("name"), "items"),
    }


def _neutral(item_id: Any, constants: DotaConstants, assets: ImageAssets) -> dict[str, Any]:
    # Target fix: OpenDota sends 0 for an empty neutral slot (would show "ability_base").
    name = constants.item_name(item_id) if item_id else None
    return {"id": item_id, "name": name, "img": assets.url(name if name else None, "items") if item_id else None}


def _player_view(
    match: dict[str, Any],
    player: dict[str, Any],
    t: Translate,
    constants: DotaConstants,
    assets: ImageAssets,
) -> dict[str, Any]:
    stats = player.get("stats") or {}
    rank = player.get("rank") or {}
    account = player.get("steamAccount") or {}
    order = player.get("order")
    is_bear = _get(player, "hero", "id") == 80
    team = player.get("team")
    position = player.get("position")

    # match_1 grid placement and party colours
    position_number = js_parse_int(str(position)[-1]) if position else 0
    grid_row = math.fmod(position_number, 5) if isinstance(position_number, int) else math.nan
    party_id = player.get("partyId")
    party_color = PARTY_COLORS[party_id] if isinstance(party_id, int) and 0 <= party_id < len(PARTY_COLORS) else None
    order_colors = ["red", "yellow", "green"]
    order_index = math.floor(order / 4) if isinstance(order, (int, float)) else None
    order_color = order_colors[order_index] if order_index is not None and 0 <= order_index < 3 else "white"

    kc = _floor(player.get("killContribution", math.nan) * 100)
    dc = _floor(player.get("deathContribution", math.nan) * 100)
    dealt = _get(stats, "heroDamageReport", "dealtTotal") or {}
    received = _get(stats, "heroDamageReport", "receivedTotal") or {}
    damage_received = (
        _n(received.get("physicalDamage")) + _n(received.get("magicalDamage")) + _n(received.get("pureDamage"))
    )

    team_damage = match[team]["heroDamage"] or 1
    team_taken = match[team]["damageReceived"] or 1
    buff_events = stats.get("matchPlayerBuffEvent") or []
    items = player.get("items") or []
    backpacks = player.get("backpacks") or []
    has_aghs = any(i and i.get("id") == 108 for i in [*items, *backpacks]) or any(
        b.get("itemId") == 108 for b in buff_events
    )
    has_shard = any(b.get("itemId") == 609 for b in buff_events)

    dota_plus = player.get("dotaPlus")
    plus_level = (dota_plus or {}).get("level")
    titles = [parse_badge(key, t) for key in player.get("titles") or []]
    for title in titles:
        title["darkColor"] = darken(title["color"], 25)

    def buff_image(buff: dict[str, Any]) -> str:
        if buff.get("type") == "ability":
            return assets.url(constants.ability_name(buff.get("id")), "abilities")
        return assets.url(constants.item_name(buff.get("id")), "items")

    pick_order = "?" if order is None else _js_template(order + 1)
    party_mark = match["party"].get(party_id) if party_id is not None else None
    leaderboard = account.get("seasonLeaderboardRank")
    view = {
        "raw": player,
        "team": team,
        "isRadiant": bool(player.get("isRadiant")),
        "isBear": is_bear,
        "gridStyle": f"grid-column: {2 if not player.get('isRadiant') else 1}; grid-row: {_js_template(grid_row)}",
        "partyLineStyle": f"background-color: {party_color if party_color else 'undefined'};",
        "partyMarkStyle": f"color: {party_color if party_color else 'undefined'};",
        "partyMark": party_mark or "",
        "partyClass": f" party_{party_mark if party_mark else 'undefined'}" if party_id is not None else "",
        "orderColor": order_color,
        "orderText": t("dota2tracker.template.random")
        if player.get("isRandom")
        else t("dota2tracker.template.pick_order", [pick_order]),
        "positionText": t(f"dota2tracker.template.position_{_position_digit(position)}") or "",
        "leaver": player.get("leaverStatus") not in ("NONE", "DISCONNECTED"),
        "level": player.get("level"),
        "heroImg": assets.url(_get(player, "hero", "shortName"), "heroes"),
        "heroIcon": assets.url(_get(player, "hero", "shortName"), "heroes/icons"),
        "name": account.get("name"),
        "kills": player.get("kills"),
        "deaths": player.get("deaths"),
        "assists": player.get("assists"),
        "kcVal": _js_template(kc),
        "dcVal": _js_template(dc),
        "kcStyle": f"color: {_kc_color(kc)}",
        "dcStyle": f"color: {_dc_color(dc)}",
        "networth": player.get("networth"),
        "formattedNetworth": player.get("formattedNetworth"),
        "score": js_to_fixed(js_div(_n(player.get("heroDamage")), _n(player.get("networth"))), 2),
        "laneText": t(f"dota2tracker.template.lane_{player.get('laneResult')}"),
        "laneImg": assets.url(LANE_SVG.get(player.get("laneResult"), "undefined"), None, "svg"),
        "laneResult": player.get("laneResult"),
        "medalImg": assets.url(
            f"medal_{_js_template(rank.get('medal') if rank.get('inTop100') is None else rank.get('inTop100'))}"
        ),
        "starImg": assets.url(f"star_{rank.get('star')}"),
        "rankText": f"[{t('dota2tracker.template.ranks.' + js_str(rank.get('medal'))) or ''}{rank.get('star') or ''}]",
        "leaderboard": "" if leaderboard is None else leaderboard,
        "dotaPlus": dota_plus,
        "plusLevel": plus_level,
        "plusImg": assets.url(f"hero_badge_{_js_template(math.ceil((_n(plus_level) + 1) / 6)) if dota_plus else 1}"),
        "titles": titles,
        "items": [_item_view(item, assets) for item in items],
        "backpacks": [_item_view(item, assets) for item in backpacks],
        "unitItems": [_item_view(item, assets) for item in player.get("unitItems") or []],
        "unitBackpacks": [_item_view(item, assets) for item in player.get("unitBackpacks") or []],
        "neutral": _neutral(player.get("neutral0Id"), constants, assets),
        "unitNeutral": _neutral((player.get("additionalUnit") or {}).get("neutral0Id"), constants, assets),
        "buffs": [{"img": buff_image(b), "count": b.get("stackCount") or ""} for b in player.get("buffs") or []],
        "supports": [
            {"img": assets.url(s.get("name"), "items"), "count": s.get("count") or ""}
            for s in player.get("supportItemsCount") or []
        ],
        "heroDamage": player.get("heroDamage"),
        "towerDamage": player.get("towerDamage"),
        "damageReceived": damage_received,
        "lastHits": player.get("numLastHits"),
        "denies": player.get("numDenies"),
        "gpm": player.get("goldPerMinute"),
        "xpm": player.get("experiencePerMinute"),
        "heal": player.get("heroHealing"),
        "ccStun": js_to_fixed(_n(dealt.get("stunDuration")) / 100, 2),
        "ccSlow": js_to_fixed(_n(dealt.get("slowDuration")) / 100, 2),
        "ccDisable": js_to_fixed(_n(dealt.get("disableDuration")) / 100, 2),
        # match_2 specific
        "orderStyle": f"order: {1 if team == 'radiant' else 100}",
        "dmgPct": js_to_fixed(_n(player.get("heroDamage")) / team_damage * 100, 2),
        "takenPct": js_to_fixed(_n(player.get("damageReceived")) / team_taken * 100, 2),
        "kdaVal": js_to_fixed((_n(player.get("kills")) + _n(player.get("assists"))) / (player.get("deaths") or 1), 2),
        "kcPct": js_to_fixed(player.get("killContribution", math.nan) * 100, 2),
        "stunVal": js_to_fixed(_n(dealt.get("stunDuration")) / 100, 2),
        "hasAghs": has_aghs,
        "hasShard": has_shard,
        "scepterImg": assets.url(f"scepter_{1 if has_aghs else 0}"),
        "shardImg": assets.url(f"shard_{1 if has_shard else 0}"),
    }
    return view


def _charts(match: dict[str, Any], t: Translate, constants: DotaConstants, assets: ImageAssets) -> dict[str, Any]:
    svg_width, svg_height, padding = 400, 225, 50
    chart_h = svg_height - padding * 2
    chart_w = svg_width - padding * 2
    main = match.get("radiantNetworthLeads") or []
    secondary = match.get("radiantExperienceLeads") or []
    magnitudes = [abs(_n(v)) for v in [*main, *secondary]]
    max_val = max(magnitudes) if magnitudes else -math.inf
    duration = _n(match.get("durationSeconds"))
    x_scale = js_div(chart_w, duration - 1)
    y_scale = js_div(chart_h, max_val * 2)

    def get_x(i: int) -> float:
        if i * 60 > duration:
            return padding + (((i - 1) * 60) + math.fmod(duration, 60)) * x_scale
        return padding + i * x_scale * 60

    def get_y(value: float, scale: float) -> float:
        return svg_height / 2 - value * scale

    def point(x: float, y: float) -> str:
        return f"{js_str(x)},{js_str(y)}"

    sec_points = " ".join(point(get_x(i), get_y(_n(v), y_scale)) for i, v in enumerate(secondary))
    half = svg_height / 2
    sec_poly = f"{sec_points} {svg_width - padding},{js_str(half)} {padding},{js_str(half)}"
    raw_points = [{"val": _n(v), "x": get_x(i), "y": get_y(_n(v), y_scale)} for i, v in enumerate(main)]
    main_points = " ".join(point(p["x"], p["y"]) for p in raw_points)
    upper: list[str] = []
    lower: list[str] = []
    for index, current in enumerate(raw_points):
        following = raw_points[index + 1] if index + 1 < len(raw_points) else None
        (upper if current["val"] >= 0 else lower).append(point(current["x"], current["y"]))
        if following and ((current["val"] >= 0 > following["val"]) or (current["val"] < 0 <= following["val"])):
            ratio = abs(current["val"]) / (abs(current["val"]) + abs(following["val"]))
            cross_x = current["x"] + ratio * (following["x"] - current["x"])
            upper.append(point(cross_x, half))
            lower.append(point(cross_x, half))
    closing = [f"{svg_width - padding},{js_str(half)}", f"{padding},{js_str(half)}"]
    upper.extend(closing)
    lower.extend(closing)

    win_rates = match.get("winRates")
    wr_points = ""
    if win_rates:
        series = [win_rates[0], *win_rates]
        wr_points = " ".join(point(get_x(i), svg_height - padding - _n(v) * chart_h) for i, v in enumerate(series))

    time_labels = []
    for i in range(0, max(0, len(main) - 1), 10):
        time_labels.append(
            {"x": js_str(get_x(i)), "text": f"{str(i).rjust(2, '0')}:00", "showText": (duration / 60 - i) >= 5}
        )

    def fmt(value: float) -> str:
        return to_locale_string(math.floor(value)) if math.isfinite(value) else js_str(value)

    val_labels = [fmt(v) for v in (max_val, max_val / 2, 0, -max_val / 2, -max_val)]
    series_nw = [[*(_get(p, "stats", "networthPerMinute") or []), p.get("networth")] for p in match["players"]]
    flat = [abs(_n(v)) for series in series_nw for v in series]
    max_nw = max(flat) if flat else -math.inf
    nw_scale = js_div(chart_h, max_nw)
    nw_labels = [fmt(v) for v in (max_nw, max_nw * 0.75, max_nw * 0.5, max_nw * 0.25, 0)]
    colors = constants.player_colors
    player_lines = []
    for index, series in enumerate(series_nw):
        color = colors.get(match["players"][index].get("playerSlot")) or "#000"
        points = " ".join(point(get_x(i), svg_height - padding - _n(v) * nw_scale) for i, v in enumerate(series))
        player_lines.append({"color": color, "points": points})
    legend = []
    for index, player in enumerate(sorted(match["players"], key=lambda p: _n(p.get("networth")))):
        legend.append(
            {
                "x": index * 30,
                "color": colors.get(player.get("playerSlot")) or "#000",
                "icon": assets.url(_get(player, "hero", "shortName"), "heroes/icons"),
            }
        )
    grid = [{"y": js_str(padding + i * (chart_h / 4)), "value": val_labels[i], "nw": nw_labels[i]} for i in range(5)]
    return {
        "svgWidth": svg_width,
        "svgHeight": svg_height,
        "padding": padding,
        "chartBottom": svg_height - padding,
        "chartRight": svg_width - padding,
        "direLogoY": js_str(svg_height - padding * 1.4),
        "direTextY": js_str(svg_height - padding * 1.4 + 10),
        "radiantTextY": padding + 10,
        "legendY": svg_height - 20,
        "secPoints": sec_points,
        "secPoly": sec_poly,
        "mainPoints": main_points,
        "upper": " ".join(upper),
        "lower": " ".join(lower),
        "wrPoints": wr_points,
        "timeLabels": time_labels,
        "grid": grid,
        "endLabel": f"{math.floor(duration / 60)}:{js_str(math.fmod(duration, 60)).rjust(2, '0')}",
        "playerLines": player_lines,
        "legend": legend,
        "legendX": js_str((svg_width - (len(match["players"]) * 30)) / 2),
        "legendTop": svg_height - 27,
        "unavailableY": js_str(svg_height / 2 + 10),
    }


def _level_info(xp: float, xp_table: list[int]) -> dict[str, Any]:
    level = next((index for index, value in enumerate(xp_table) if xp < value), -1) - 1
    if level < 0:
        level = len(xp_table) - 1
    current = xp - xp_table[level]
    following = xp_table[level + 1] - xp_table[level] if level + 1 < len(xp_table) else math.nan
    pct = js_div(current, following) if not math.isnan(following) else math.nan
    circumference = 2 * math.pi * 12
    progress = circumference * pct
    return {"lvl": level, "prog": js_str(progress), "gap": js_str(circumference - progress)}


def _lane_outcome(match: dict[str, Any], constants: DotaConstants, assets: ImageAssets) -> list[dict[str, Any]]:
    od = bool(match.get("odParsed"))

    def position_of(player: dict[str, Any]) -> float:
        position = player.get("position")
        return js_parse_int(str(position)[-1]) if position else math.nan

    def stat(player: dict[str, Any], kind: str) -> Any:
        if od:
            return "-"
        events = _get(player, "stats", f"{kind}Events")
        if events is None:
            return 0
        return len([e for e in events if _n(e.get("time")) <= 600]) or 0

    def gold10(player: dict[str, Any]) -> Any:
        series = _get(player, "stats", "networthPerMinute") or []
        if not series:
            return None
        return series[min(10, len(series) - 1)]

    def xp10(player: dict[str, Any]) -> float:
        series = _get(player, "stats", "experiencePerMinute") or []
        return sum(_n(v) for v in series[: max(0, min(11, len(series) - 1))])

    def lane_data(radiant_positions: list[int], dire_positions: list[int]) -> dict[str, Any] | None:
        rivals = [
            p
            for p in match["players"]
            if (p.get("isRadiant") and position_of(p) in radiant_positions)
            or (not p.get("isRadiant") and position_of(p) in dire_positions)
        ]
        rivals.sort(key=lambda p: -int(bool(p.get("isRadiant"))))
        if len(rivals) < 2:
            return None
        first, second = rivals[0], rivals[1]
        g1, g2 = gold10(first), gold10(second)
        total = _n(g1) + _n(g2)
        split = js_div(_n(g1), total or 1)
        width = 240

        def side(player: dict[str, Any]) -> dict[str, Any]:
            return {
                "icon": assets.url(_get(player, "hero", "shortName"), "heroes/icons"),
                "k": stat(player, "kill"),
                "d": stat(player, "death"),
                "a": stat(player, "assist"),
                "lvl": _level_info(xp10(player), constants.xp_level),
            }

        return {
            "p1": side(first),
            "p2": side(second),
            "gold": {
                "g1": _js_template(g1) if g1 is not None else "undefined",
                "g2": _js_template(g2) if g2 is not None else "undefined",
                "splitWidth": js_str(width * split),
                "diff": js_str(abs(_n(g2) - _n(g1))),
                "diffColor": "#3c9028" if _n(g1) > _n(g2) else "#9c3628",
                "isOd": od,
            },
        }

    lanes = [
        ("lane_top", match.get("topLaneOutcome"), [([3], [1]), ([4], [5])]),
        ("lane_mid", match.get("midLaneOutcome"), [([2], [2])]),
        ("lane_bottom", match.get("bottomLaneOutcome"), [([1], [3]), ([5], [4])]),
    ]
    return [
        {"key": key, "outcome": outcome, "pairs": [d for d in (lane_data(r, d) for r, d in pairs) if d is not None]}
        for key, outcome, pairs in lanes
    ]


BUILDINGS = [
    {"id": 16, "name": "Rad Top T1", "type": "tower", "lane": "top", "bit": 0, "team": "radiant", "x": 35.8, "y": 96.5},
    {
        "id": 19,
        "name": "Rad Top T2",
        "type": "tower",
        "lane": "top",
        "bit": 1,
        "team": "radiant",
        "x": 33.7,
        "y": 133.8,
    },
    {
        "id": 22,
        "name": "Rad Top T3",
        "type": "tower",
        "lane": "top",
        "bit": 2,
        "team": "radiant",
        "x": 32.5,
        "y": 168.4,
    },
    {
        "id": 38,
        "name": "Rad Top Melee",
        "type": "rax",
        "lane": "top",
        "bit": 0,
        "team": "radiant",
        "x": 36.8,
        "y": 174.5,
    },
    {"id": 41, "name": "Rad Top Range", "type": "rax", "lane": "top", "bit": 1, "team": "radiant", "x": 30, "y": 174.5},
    {
        "id": 38,
        "name": "Rad Top HG Time",
        "type": "rax_time",
        "lane": "top",
        "bit": 0,
        "team": "radiant",
        "x": 38.6,
        "y": 180,
    },
    {"id": 17, "name": "Rad Mid T1", "type": "tower", "lane": "mid", "bit": 3, "team": "radiant", "x": 101.2, "y": 141},
    {
        "id": 20,
        "name": "Rad Mid T2",
        "type": "tower",
        "lane": "mid",
        "bit": 4,
        "team": "radiant",
        "x": 78.7,
        "y": 161.8,
    },
    {"id": 23, "name": "Rad Mid T3", "type": "tower", "lane": "mid", "bit": 5, "team": "radiant", "x": 59, "y": 178.3},
    {
        "id": 39,
        "name": "Rad Mid Melee",
        "type": "rax",
        "lane": "mid",
        "bit": 2,
        "team": "radiant",
        "x": 59.8,
        "y": 185.1,
    },
    {
        "id": 42,
        "name": "Rad Mid Range",
        "type": "rax",
        "lane": "mid",
        "bit": 3,
        "team": "radiant",
        "x": 54.5,
        "y": 180.5,
    },
    {
        "id": 39,
        "name": "Rad Mid HG Time",
        "type": "rax_time",
        "lane": "mid",
        "bit": 2,
        "team": "radiant",
        "x": 64,
        "y": 187,
    },
    {
        "id": 18,
        "name": "Rad Bot T1",
        "type": "tower",
        "lane": "bot",
        "bit": 6,
        "team": "radiant",
        "x": 189.3,
        "y": 206.2,
    },
    {
        "id": 21,
        "name": "Rad Bot T2",
        "type": "tower",
        "lane": "bot",
        "bit": 7,
        "team": "radiant",
        "x": 117.5,
        "y": 207.2,
    },
    {"id": 24, "name": "Rad Bot T3", "type": "tower", "lane": "bot", "bit": 8, "team": "radiant", "x": 68.7, "y": 205},
    {"id": 40, "name": "Rad Bot Melee", "type": "rax", "lane": "bot", "bit": 4, "team": "radiant", "x": 65, "y": 210},
    {"id": 43, "name": "Rad Bot Range", "type": "rax", "lane": "bot", "bit": 5, "team": "radiant", "x": 65, "y": 202.7},
    {
        "id": 40,
        "name": "Rad Bot HG Time",
        "type": "rax_time",
        "lane": "bot",
        "bit": 4,
        "team": "radiant",
        "x": 70,
        "y": 211,
    },
    {
        "id": 25,
        "name": "Rad T4 Left",
        "type": "tower",
        "lane": "mid",
        "bit": 9,
        "team": "radiant",
        "x": 44.7,
        "y": 188.7,
    },
    {
        "id": 25,
        "name": "Rad T4 Right",
        "type": "tower",
        "lane": "mid",
        "bit": 10,
        "team": "radiant",
        "x": 50,
        "y": 193.1,
    },
    {
        "id": 25,
        "name": "Rad T4 Time",
        "type": "t4_time",
        "lane": "mid",
        "bit": [9, 10],
        "team": "radiant",
        "x": 53,
        "y": 196,
    },
    {"id": 50, "name": "Rad Ancient", "type": "fort", "lane": "", "bit": -1, "team": "radiant", "x": 39.5, "y": 193.5},
    {
        "id": 50,
        "name": "Rad Ancient Time",
        "type": "fort_time",
        "lane": "",
        "bit": -1,
        "team": "radiant",
        "x": 42,
        "y": 207,
    },
    {"id": 26, "name": "Dire Top T1", "type": "tower", "lane": "top", "bit": 0, "team": "dire", "x": 50.4, "y": 41},
    {"id": 29, "name": "Dire Top T2", "type": "tower", "lane": "top", "bit": 1, "team": "dire", "x": 120.7, "y": 39.8},
    {"id": 32, "name": "Dire Top T3", "type": "tower", "lane": "top", "bit": 2, "team": "dire", "x": 170.7, "y": 42.9},
    {"id": 44, "name": "Dire Top Melee", "type": "rax", "lane": "top", "bit": 0, "team": "dire", "x": 176.5, "y": 48.5},
    {"id": 47, "name": "Dire Top Range", "type": "rax", "lane": "top", "bit": 1, "team": "dire", "x": 176.5, "y": 40.8},
    {
        "id": 44,
        "name": "Dire Top HG Time",
        "type": "rax_time",
        "lane": "top",
        "bit": 0,
        "team": "dire",
        "x": 181.5,
        "y": 49,
    },
    {"id": 27, "name": "Dire Mid T1", "type": "tower", "lane": "mid", "bit": 3, "team": "dire", "x": 129.4, "y": 112.7},
    {"id": 30, "name": "Dire Mid T2", "type": "tower", "lane": "mid", "bit": 4, "team": "dire", "x": 156.4, "y": 92.8},
    {"id": 33, "name": "Dire Mid T3", "type": "tower", "lane": "mid", "bit": 5, "team": "dire", "x": 180.6, "y": 70.5},
    {"id": 45, "name": "Dire Mid Melee", "type": "rax", "lane": "mid", "bit": 2, "team": "dire", "x": 187.5, "y": 70.5},
    {"id": 48, "name": "Dire Mid Range", "type": "rax", "lane": "mid", "bit": 3, "team": "dire", "x": 182.7, "y": 65.6},
    {
        "id": 45,
        "name": "Dire Mid HG Time",
        "type": "rax_time",
        "lane": "mid",
        "bit": 2,
        "team": "dire",
        "x": 187,
        "y": 74,
    },
    {"id": 28, "name": "Dire Bot T1", "type": "tower", "lane": "bot", "bit": 6, "team": "dire", "x": 207.8, "y": 152.2},
    {
        "id": 31,
        "name": "Dire Bot T2",
        "type": "tower",
        "lane": "bot",
        "bit": 7,
        "team": "dire",
        "x": 209.75,
        "y": 116.6,
    },
    {"id": 34, "name": "Dire Bot T3", "type": "tower", "lane": "bot", "bit": 8, "team": "dire", "x": 208.7, "y": 80.5},
    {"id": 46, "name": "Dire Bot Melee", "type": "rax", "lane": "bot", "bit": 4, "team": "dire", "x": 213.2, "y": 76.8},
    {"id": 49, "name": "Dire Bot Range", "type": "rax", "lane": "bot", "bit": 5, "team": "dire", "x": 206.2, "y": 76.8},
    {
        "id": 46,
        "name": "Dire Bot HG Time",
        "type": "rax_time",
        "lane": "bot",
        "bit": 4,
        "team": "dire",
        "x": 214.5,
        "y": 82,
    },
    {"id": 35, "name": "Dire T4 Left", "type": "tower", "lane": "mid", "bit": 9, "team": "dire", "x": 189.7, "y": 56.4},
    {
        "id": 35,
        "name": "Dire T4 Right",
        "type": "tower",
        "lane": "mid",
        "bit": 10,
        "team": "dire",
        "x": 194.4,
        "y": 60.2,
    },
    {
        "id": 35,
        "name": "Dire T4 Time",
        "type": "t4_time",
        "lane": "mid",
        "bit": [9, 10],
        "team": "dire",
        "x": 198,
        "y": 63,
    },
    {"id": 51, "name": "Dire Ancient", "type": "fort", "lane": "", "bit": -1, "team": "dire", "x": 195.7, "y": 49},
    {
        "id": 51,
        "name": "Dire Ancient Time",
        "type": "fort_time",
        "lane": "",
        "bit": -1,
        "team": "dire",
        "x": 210,
        "y": 52,
    },
]
_BUILDING_PROPS = {
    "rax": (10, 1),
    "tower": (12, 2),
    "fort": (16, 3),
    "rax_time": (0, 10),
    "t4_time": (0, 10),
    "fort_time": (0, 10),
    "tower_time": (0, 10),
}


def _map(match: dict[str, Any], assets: ImageAssets) -> dict[str, Any]:
    destruction: dict[int, Any] = {}
    for event in _get(match, "playbackData", "buildingEvents") or []:
        if _n(event.get("time")) > 0:
            destruction[event.get("npcId")] = event.get("time")

    def mask_for(building: dict[str, Any]) -> int:
        tower = "tower" in building["type"] or building["type"] == "t4_time"
        if building["team"] == "radiant":
            return _n(match.get("towerStatusRadiant" if tower else "barracksStatusRadiant"))
        return _n(match.get("towerStatusDire" if tower else "barracksStatusDire"))

    def alive(building: dict[str, Any]) -> bool:
        mask = int(mask_for(building))
        if building["type"] == "t4_time":
            return bool(mask & (1 << building["bit"][0])) or bool(mask & (1 << building["bit"][1]))
        if building["type"] in ("fort_time", "fort"):
            return bool(match.get("didRadiantWin")) if building["team"] == "radiant" else not match.get("didRadiantWin")
        return bool(mask & (1 << building["bit"]))

    def fmt(seconds: Any) -> str:
        absolute = abs(_n(seconds))
        return (
            "-" if _n(seconds) < 0 else ""
        ) + f"{math.floor(absolute / 60)}:{js_str(math.fmod(absolute, 60)).rjust(2, '0')}"

    nodes = []
    for building in sorted(BUILDINGS, key=lambda b: _BUILDING_PROPS.get(b["type"], (12, 0))[1]):
        size = _BUILDING_PROPS.get(building["type"], (12, 0))[0]
        is_alive = alive(building)
        death_time = destruction.get(building["id"])
        outer = building["type"] == "tower" and "T3" not in building["name"] and "T4" not in building["name"]
        anchor = building["type"].endswith("_time")
        nodes.append(
            {
                "icon": not anchor,
                "href": "#" + building["type"] + ("_angle" if building["lane"] == "mid" else ""),
                "cls": building["team"] if is_alive else "dead",
                "x": js_str(building["x"]),
                "y": js_str(building["y"]),
                "size": size,
                "showTime": (outer or anchor) and not is_alive and js_truthy(death_time),
                "cx": js_str(building["x"] + size / 2),
                "cy": js_str(building["y"] + size / 2),
                "time": fmt(death_time),
            }
        )
    return {"minimap": assets.url("dotamap_7.40"), "nodes": nodes}


def build_match_view(
    match: dict[str, Any],
    t: Translate,
    constants: DotaConstants,
    assets: ImageAssets,
    locale: str,
    tz=None,
) -> dict[str, Any]:
    """All values needed by the three match templates."""
    start = from_seconds(_n(match.get("startDateTime")), tz)
    end = from_seconds(_n(match.get("endDateTime")), tz)
    lobby = match.get("lobbyType")
    game_mode = match.get("gameMode")
    lobby_text = t("dota2tracker.template.lobby_types." + js_str(lobby)) or js_str(lobby)
    mode_text = t("dota2tracker.template.game_modes." + js_str(game_mode)) or js_str(game_mode)
    bans = []
    for pick in match.get("pickBans") or []:
        if pick.get("isPick"):
            continue
        short = constants.hero_short_name(pick.get("bannedHeroId")) or ""
        bans.append(assets.url(short, "heroes"))
    rank = match.get("rank")
    od = bool(match.get("odParsed"))
    players = [_player_view(match, player, t, constants, assets) for player in match["players"]]
    view = {
        "match": match,
        "players": players,
        "startTime": luxon_format(start, "yyyy-MM-dd HH:mm:ss")[2:],
        "endTime": luxon_format(end, "yyyy-MM-dd HH:mm:ss")[2:],
        "gameModeStr": f"{lobby_text} / {mode_text}",
        "modeName": f"{lobby_text}/{mode_text}",
        "regionStr": t("dota2tracker.template.regions." + js_str(match.get("regionId"))) or "",
        "rankStarImg": assets.url("star_" + _rank_digit(rank, 1, "")),
        "rankMedalImg": assets.url("medal_" + _rank_digit(rank, 0, "")),
        "rankStarImg2": assets.url("star_" + _rank_digit_or_zero(rank, 1)),
        "rankMedalImg2": assets.url("medal_" + _rank_digit_or_zero(rank, 0)),
        "rankGray": od and lobby != "RANKED",
        "navLabels": {
            name: (t(f"dota2tracker.template.{key}") or "")[:-1]
            for name, key in (
                ("matchId", "match_id_"),
                ("startTime", "start_time_"),
                ("duration", "duration_"),
                ("region", "region_"),
                ("mode", "game_mode_"),
            )
        },
        "statusClass": "opendota" if od else ("success" if js_truthy(match.get("parsedDateTime")) else "fail"),
        "bans": bans,
        "banStyle": BAN_GRADIENT,
        "radiantWon": bool(match.get("didRadiantWin")),
        "odParsed": od,
        "flagRadiant": assets.url("flag_radiant"),
        "flagDire": assets.url("flag_dire"),
        "teams": [
            {
                "key": team,
                "logo": assets.url("logo_" + team),
                "names": ["Radiant", "天辉"] if team == "radiant" else ["Dire", "夜魇"],
                "orderStyle": f"order: {0 if team == 'radiant' else 50}",
                "won": (team == "radiant") == bool(match.get("didRadiantWin")),
                "data": match[team],
            }
            for team in ("radiant", "dire")
        ],
    }
    has_extra = (
        js_truthy(match.get("parsedDateTime"))
        and match.get("radiantNetworthLeads")
        and match.get("radiantExperienceLeads")
    )
    view["hasExtra"] = bool(has_extra)
    if has_extra:
        view["charts"] = _charts(match, t, constants, assets)
        view["lanes"] = _lane_outcome(match, constants, assets)
        view["map"] = _map(match, assets)
    return view


def _preview(ctx) -> dict[str, Any]:
    from ...domain.match import extend_match_data
    from ...domain.opendota import transform_opendota_match
    from ..previews import load_fixture

    if ctx.variant == "opendota":
        query = {"match": transform_opendota_match(load_fixture("opendota_match.json"), ctx.constants)}
    else:
        query = load_fixture("stratz_match.json")
    match = extend_match_data(query, ctx.constants)
    return build_match_view(match, ctx.t, ctx.constants, ctx.assets, ctx.locale, ctx.tz)


def preview_match_1(ctx) -> dict[str, Any]:
    return _preview(ctx)


def preview_match_2(ctx) -> dict[str, Any]:
    return _preview(ctx)


def preview_match_2_plus(ctx) -> dict[str, Any]:
    return _preview(ctx)
