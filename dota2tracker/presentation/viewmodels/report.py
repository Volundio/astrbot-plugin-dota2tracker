"""View models for ``daily`` and ``daily_legacy`` (ports of ``templates/report/daily.ejs``
+ ``daily/spotlight_card.ejs`` and ``templates/report/daily_legacy.ejs``)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from ...domain.report import ReportTranslator, build_legacy_report, format_daily_report_bundles
from ...utils.jsnum import js_div, js_round, js_str
from ..assets import ImageAssets

__all__ = [
    "badge_style",
    "build_daily_legacy_view",
    "build_daily_view",
    "imp_info",
    "preview_daily",
    "preview_daily_legacy",
    "spotlight_theme",
]

PREVIEW_STEAM_IDS = [100000001, 100000002, 100000003, 100000007]
PREVIEW_NICKNAMES = {100000002: "白小罗"}
FOOTER_ID = "astrbot_plugin_dota2tracker"

_THEME_BY_TYPE = {
    "MVP": "gold",
    "LVP": "red",
    "UTILITY": "cyan",
    "HEALER": "green",
    "DEMOLISHER": "orange",
    "DEFAULT": "gray",
}

#: (theme, type) → (borderStyle, badgeStyle, accentTextColor), checked in EJS order
_STYLES = [
    (
        "red",
        "LVP",
        "border-dota-red shadow-[0_0_30px_rgba(255,60,60,0.15)]",
        "bg-dota-red text-white shadow-dota-red/20",
        "text-dota-red",
    ),
    (
        "cyan",
        "UTILITY",
        "border-[#00e5ff] shadow-[0_0_30px_rgba(0,229,255,0.18)]",
        "bg-[#00e5ff] text-black shadow-[0_0_15px_rgba(0,229,255,0.3)]",
        "text-[#00e5ff]",
    ),
    (
        "green",
        "HEALER",
        "border-[#0bda5b] shadow-[0_0_30px_rgba(11,218,91,0.18)]",
        "bg-[#0bda5b] text-black shadow-[0_0_15px_rgba(11,218,91,0.3)]",
        "text-[#0bda5b]",
    ),
    (
        "orange",
        "DEMOLISHER",
        "border-[#f97316] shadow-[0_0_30px_rgba(249,115,22,0.18)]",
        "bg-[#f97316] text-black shadow-[0_0_15px_rgba(249,115,22,0.3)]",
        "text-[#f97316]",
    ),
    ("gray", "DEFAULT", "border-white/10 shadow-none", "bg-white/10 text-slate-300", "text-slate-400"),
]

#: icon order of the EJS ``if/else if`` chain
_ICONS = [("MVP", "gold", "trophy"), ("LVP", "red", "warning"), ("UTILITY", "cyan", "shield"),
          ("HEALER", "green", "heart"), ("DEMOLISHER", "orange", "hammer")]  # fmt: skip


def spotlight_theme(card: dict[str, Any]) -> dict[str, str]:
    card_type = card.get("type")
    theme = card.get("themeColor") or _THEME_BY_TYPE.get(card_type, "gold")
    border = "border-dota-gold shadow-[0_0_30px_rgba(232,188,86,0.15)]"
    badge = "bg-dota-gold text-black shadow-dota-gold/20"
    accent = "text-dota-gold"
    for style_theme, style_type, style_border, style_badge, style_accent in _STYLES:
        if theme == style_theme or card_type == style_type:
            border, badge, accent = style_border, style_badge, style_accent
            break
    icon = "flat"
    for icon_type, icon_theme, icon_name in _ICONS:
        if card_type == icon_type or theme == icon_theme:
            icon = icon_name
            break
    return {"theme": theme, "borderStyle": border, "badgeStyle": badge, "accentTextColor": accent, "icon": icon}


def _parse_hex(text: str) -> float | int:
    """``parseInt(text, 16)``: leading hex digits or ``NaN``."""
    digits = ""
    for char in text.strip():
        if char in "0123456789abcdefABCDEF":
            digits += char
        else:
            break
    return int(digits, 16) if digits else math.nan


def badge_style(hex_color: str) -> str:
    r, g, b = (_parse_hex(hex_color[i : i + 2]) for i in (1, 3, 5))
    r, g, b = (js_str(v) for v in (r, g, b))
    return f"background-color: rgba({r}, {g}, {b}, 0.2); border-color: rgba({r}, {g}, {b}, 0.3); color: {hex_color};"


def _card_view(card: dict[str, Any]) -> dict[str, Any]:
    return {
        **card,
        **spotlight_theme(card),
        "badges": [{**badge, "style": badge_style(str(badge.get("hexColor")))} for badge in card.get("badges") or []],
    }


def build_daily_view(report: dict[str, Any]) -> dict[str, Any]:
    """Context of ``report/daily.html.j2`` from a ``format_daily_report_bundles`` report."""
    spotlights = report.get("spotlights") or {}
    cards = spotlights.get("cards")
    if cards is None:
        cards = [c for c in (spotlights.get("mvp"), spotlights.get("secondary") or spotlights.get("lvp")) if c]
    squad = report.get("squad") or []
    rows = []
    for row in squad:
        rank = row.get("rank")
        if rank == 1:
            rank_class = "text-dota-gold"
        elif rank == len(squad):
            rank_class = "text-dota-red"
        else:
            rank_class = "text-slate-400"
        rows.append({**row, "rankClass": rank_class})
    win_rate = report["headerStats"]["winRate"]
    if win_rate.get("hasComparison"):
        subtext_class = "text-dota-green" if win_rate.get("isPositive") else "text-dota-red"
    else:
        subtext_class = "text-slate-500"
    return {
        "meta": report.get("meta") or {},
        "headerStats": report.get("headerStats") or {},
        "winRateBarClass": "bg-dota-green/50" if win_rate.get("isWinRateAbove50") else "bg-dota-red/50",
        "winRateClass": "text-dota-green" if win_rate.get("isWinRateAbove50") else "text-dota-red",
        "winRateSubtextClass": subtext_class,
        "cards": [_card_view(card) for card in cards],
        "squad": rows,
    }


# ------------------------------------------------------------------ legacy
def imp_info(imp: Any) -> dict[str, str]:
    """``getImpInfo`` of ``daily_legacy.ejs``."""
    value = 0 if imp is None else imp
    absolute = abs(value)
    positive = value > 0
    over = absolute > 25
    left = right = 0
    if over:
        if positive:
            right = absolute
        else:
            left = absolute
    else:
        left = right = absolute
    return {
        "valStr": ("+" if positive else "") + js_str(value),
        "barClass": f"score_bar {'pos' if positive else 'neg'}{' over' if over else ''}",
        "leftStyle": f"width: {js_str(left)}px",
        "rightStyle": f"width: {js_str(right)}px",
    }


def _rate(wins: Any, total: int) -> str:
    """``Math.round((wins / total) * 100)`` (``NaN`` without matches)."""
    return js_str(js_round(js_div(wins, total) * 100)) if total else "NaN"


def build_daily_legacy_view(
    title: Any,
    players: list[dict[str, Any]],
    combinations: list[dict[str, Any]],
    show_combi: bool,
    assets: ImageAssets | None = None,
) -> dict[str, Any]:
    """Context of ``report/daily_legacy.html.j2`` (entries of ``build_legacy_report``)."""
    player_views = []
    for player in players:
        matches = player.get("matches") or []
        player_views.append(
            {
                "avatar": (player.get("steamAccount") or {}).get("avatar"),
                "name": player.get("name"),
                "winCount": js_str(player.get("winCount")),
                "loseCount": js_str(player.get("loseCount")),
                "winRate": _rate(player.get("winCount") or 0, len(matches)),
                "imp": imp_info(player.get("avgImp")),
                "kda": f"{js_str(player.get('avgKills'))}/{js_str(player.get('avgDeaths'))}/"
                f"{js_str(player.get('avgAssists'))} ({js_str(player.get('avgKDA'))})",
            }
        )
    combi_views = []
    for combi in combinations:
        matches = combi.get("matches") or []
        wins = combi.get("winCount") or 0
        combi_views.append(
            {
                "avatars": [(p.get("steamAccount") or {}).get("avatar") for p in combi.get("players") or []],
                "winCount": js_str(wins),
                "loseCount": js_str(len(matches) - wins),
                "winRate": _rate(wins, len(matches)),
            }
        )
    return {"title": title, "players": player_views, "combinations": combi_views, "showCombi": bool(show_combi)}


# ------------------------------------------------------------------ previews
def _preview_users() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=index + 1,
            platform="aiocqhttp",
            channel_id="123456789",
            steam_id=steam_id,
            nickname=PREVIEW_NICKNAMES.get(steam_id),
        )
        for index, steam_id in enumerate(PREVIEW_STEAM_IDS)
    ]


def _target_date(fixture: dict[str, Any], ctx) -> datetime:
    moment = datetime.fromtimestamp(fixture["targetDate"], UTC)
    return moment.astimezone(ctx.tz) if ctx.tz is not None else moment


def preview_daily(ctx) -> dict[str, Any]:
    from ..previews import load_fixture

    fixture = load_fixture("daily_report.json")
    users = _preview_users()
    translator = ReportTranslator(
        t=lambda key, params=None: ctx.text(key, params),
        locale=ctx.locale,
        hero_name=lambda hero_id: ctx.i18n.hero_name(hero_id, ctx.locale) or "",
    )
    key = f"{users[0].platform}:{users[0].channel_id}"
    bundles = format_daily_report_bundles(
        fixture["data"],
        users,
        fixture.get("extensions") or [],
        ctx.constants,
        _target_date(fixture, ctx),
        {key: translator},
        lambda name: ctx.assets.url(name, "heroes"),
        lambda name: ctx.assets.url(name, "heroes", "png"),
        FOOTER_ID,
    )
    return build_daily_view(bundles[0]["report"])


def preview_daily_legacy(ctx) -> dict[str, Any]:
    """Weekly summary built from the daily fixture; ``variant="hideCombi"`` hides combinations."""
    from ..previews import load_fixture

    fixture = load_fixture("daily_report.json")
    users = _preview_users()
    channel = SimpleNamespace(platform=users[0].platform, channel_id=users[0].channel_id)
    names = {u.id: u.nickname for u in users if u.nickname}
    entry = build_legacy_report([channel], users, fixture["data"].get("players") or [], names)[0]
    return build_daily_legacy_view(
        ctx.text("dota2tracker.template.last_weeks_summary"),
        entry["players"],
        entry["combinations"],
        ctx.variant != "hideCombi",
        ctx.assets,
    )
