"""View models for the pages the Koishi ``MessageBuilder`` assembled in code.

* ``help``            – ``buildHelpMessage`` + ``_createTableHTML``
* ``members``         – ``buildMembersMessage``
* ``hero_of_the_day`` – ``buildHeroOfTheDayMessage``

The original concatenated HTML strings; here every value is prepared in
Python and the Jinja2 templates in ``templates/misc`` only lay them out.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from markupsafe import Markup, escape

from ...utils.jsnum import js_str, js_to_fixed, round_to_decimal_places
from ...utils.timefmt import format_custom_relative_time, from_seconds

__all__ = [
    "build_help_view",
    "build_hero_of_the_day_view",
    "build_members_view",
    "preview_help",
    "preview_hero_of_the_day",
    "preview_members",
]

Translate = Callable[..., Any]

HELP_HEADER_KEYS = ("command", "alias", "arguments", "description", "options", "examples")
HOTD_PREFIX = "commands.dota2tracker.hero-of-the-day.messages."
MEMBERS_PREVIEW_NOW = "2025-10-26T20:00:00+08:00"
HOTD_PREVIEW_NOW = "2025-10-26T20:00:00+08:00"


def _js_template(value: Any) -> str:
    """``${value}`` in a JS template literal."""
    if value is None:
        return "undefined"
    return js_str(value)


def _table_cell(content: Any) -> Markup:
    """``escapeHtml`` of ``_createTableHTML``: ``(str || "")`` escaped, ``\\n`` → ``<br/>``."""
    text = content if isinstance(content, str) else ("" if not content else str(content))
    return Markup(str(escape(text)).replace("\n", "<br/>"))


# ---------------------------------------------------------------------- help
def build_help_view(rows: list[list[str]], header: Any, table_headers: list[str] | None = None) -> dict[str, Any]:
    """Context of ``misc/help.html.j2``.

    ``rows`` come from :func:`dota2tracker.commands.registry.help_rows`;
    ``header`` is the *raw* locale entry (the original read ``_data`` to keep
    ``<arg>`` literal). ``table_headers`` overrides the header row texts; when
    omitted the template translates ``help.messages.table_headers.*`` itself.
    """
    column_count = max([len(HELP_HEADER_KEYS), *(len(row) for row in rows)])
    return {
        "header": _table_cell(header) if header else None,
        "columnCount": column_count,
        "headerKeys": list(HELP_HEADER_KEYS),
        "tableHeaders": [_table_cell(cell) for cell in table_headers] if table_headers else None,
        "rows": [[_table_cell(cell) for cell in row] for row in rows],
    }


def preview_help(ctx) -> dict[str, Any]:
    from ...commands.registry import help_rows

    rows = help_rows(lambda key: ctx.i18n.raw(ctx.locale, key), ctx.t)
    header = ctx.i18n.raw(ctx.locale, "commands.dota2tracker.help.messages.header") or ""
    return build_help_view(rows, header)


# ------------------------------------------------------------------- members
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(value)


def build_members_view(
    members: list[dict[str, Any]],
    locale: str,
    now: datetime,
    tz: tzinfo | None,
    t: Translate,
    text: Translate,
) -> dict[str, Any]:
    """Context of ``misc/members.html.j2`` (empty lists are answered with text by the caller)."""
    prefix = "commands.dota2tracker.query-members.messages."
    rows = []
    for member in members:
        win_rate = member.get("winRate")
        last = member.get("lastMatchTime")
        rows.append(
            {
                # Target fix: the original interpolated the name unescaped (autoescape now).
                "name": _js_template(member.get("name")),
                "winRate": (
                    js_to_fixed(round_to_decimal_places(win_rate * 100), 1) + "%" if _is_number(win_rate) else "-----"
                ),
                "lastMatch": (
                    format_custom_relative_time(from_seconds(last, tz), now, locale, t) if last else "----------"
                ),
            }
        )
    return {
        "title": text(prefix + "title", {"count": len(members)}),
        "headers": [
            text(prefix + "table_headers.nickname"),
            text(prefix + "table_headers.winrate"),
            text(prefix + "table_headers.last_match"),
        ],
        "members": rows,
    }


class _Binding:
    def __init__(self, steam_id: int, nickname: str) -> None:
        self.steam_id = steam_id
        self.nickname = nickname


def preview_members(ctx) -> dict[str, Any]:
    from ...domain.player import build_guild_members
    from ..previews import load_fixture

    players = load_fixture("members.json")["players"]
    nicknames = {100000002: "鬼行者" if ctx.locale == "zh-CN" else "Walker", 100000006: "<b>o.O</b>"}
    bindings = [_Binding(p["steamAccount"]["id"], nicknames.get(p["steamAccount"]["id"], "")) for p in players]
    members = build_guild_members(players, bindings)
    now = datetime.fromisoformat(MEMBERS_PREVIEW_NOW)
    if ctx.tz is not None:
        now = now.astimezone(ctx.tz)
    return build_members_view(members, ctx.locale, now, ctx.tz, ctx.t, ctx.text)


# ----------------------------------------------------------- hero of the day
def _round(value: Any) -> str:
    """``${roundToDecimalPlaces(value)}``."""
    return js_str(round_to_decimal_places(value))


def build_hero_of_the_day_view(personal: dict[str, Any], meta: dict[str, Any], locale: str, i18n) -> dict[str, Any]:
    """Context of ``misc/hero_of_the_day.html.j2``.

    ``personal`` is :func:`dota2tracker.domain.player.hero_recommendation`'s
    result; ``meta`` is ``{"recommendation": {pos: [hero, …]}, "targetTiers": [...]}``.
    Texts containing ``<b>``/``<br>`` are rendered with ``t_html``.
    """

    def t_html(key: str, params: Any = None) -> Markup:
        value = i18n.t_html(locale, HOTD_PREFIX + key, params)
        return Markup("") if value is None else value

    def hero_name(hero_id: Any) -> str:
        return i18n.display_name_for_hero(hero_id, locale, force_official_name=True)

    recommended = list(personal.get("recommendedHeroes") or [])
    kind = personal.get("recommendationType")
    view: dict[str, Any] = {"type": kind, "personalized": kind not in ("LIFETIME_NO_RECORD", "PLAYER_ANONYMOUS")}
    if view["personalized"]:
        view.update(
            {
                "titleRecommendation": t_html("title_recommendation"),
                "intro": t_html("recommendation_intro"),
                "heroes": t_html("recommendation_heroes", {"heroes": [hero_name(h) for h in recommended]}),
                "lifetimeOnly": t_html("recommendation_type_lifetime_only") if kind == "LIFETIME_ONLY" else None,
                "poolDescription": t_html("details.pool_description"),
                "tableIntro": t_html("details.table_intro"),
                "tableHeaders": [
                    t_html("details.table_headers." + key)
                    for key in ("hero", "recent_wins", "lifetime_wins", "imp_bonus", "is_hot_streak", "total_score")
                ],
                "pool": [
                    {
                        "name": hero_name(hero.get("heroId")) + ("*" if hero.get("heroId") in recommended else ""),
                        "recentWinScore": _js_template(hero.get("recentWinScore")),
                        "lifetimeWinScore": _round(hero.get("lifetimeWinScore")),
                        "impBonus": _round(hero.get("impBonus")),
                        "hotStreak": "√" if hero.get("isHotStreak") else "/",
                        "totalScore": js_to_fixed(hero.get("totalScore"), 2),
                    }
                    for hero in personal.get("recommendationPool") or []
                ],
                "scoringFormula": t_html("details.scoring_formula"),
                "hotStreakDesc": t_html("details.hot_streak_desc"),
            }
        )
    elif kind == "LIFETIME_NO_RECORD":
        view["notice"] = t_html("recommendation_type_no_record")
    else:
        view["notice"] = t_html("recommendation_type_anonymous")

    tiers_text = ", ".join(
        i18n.t(locale, "dota2tracker.template.ranks." + js_str(tier)) or "" for tier in meta.get("targetTiers") or []
    )
    positions = []
    for pos, heroes in (meta.get("recommendation") or {}).items():
        heroes_text = "、".join(
            f"{hero_name(hero.get('heroId'))}"
            f"({_round(hero.get('pickRate') * 100)}% {_round(hero.get('winRate') * 100)}%)"
            for hero in heroes
        )
        positions.append({"label": t_html("meta_position", {"pos": str(pos)[-1:]}), "heroes": heroes_text})
    view.update(
        {
            "titleMeta": t_html("title_meta"),
            "metaIntro": t_html("meta_intro", {"tiers": tiers_text}),
            "metaTableHeader": t_html("meta_table_header"),
            "positions": positions,
        }
    )
    return view


def preview_hero_of_the_day(ctx) -> dict[str, Any]:
    from ...domain.hero import process_weekly_meta, recommend_weekly_meta, weekly_meta_brackets
    from ...domain.player import hero_recommendation
    from ..previews import load_fixture

    data = load_fixture("hero_recommendation.json")
    player = data["player"]
    now = datetime.fromisoformat(HOTD_PREVIEW_NOW)
    if ctx.tz is not None:
        now = now.astimezone(ctx.tz)
    if ctx.variant == "anonymous":
        player = {"steamAccount": {"isAnonymous": True}}
    elif ctx.variant == "lifetime":
        player = {**player, "recentPerformance": []}
    personal = hero_recommendation(100000001, player, now)
    target_tiers, _ = weekly_meta_brackets(54)
    meta = {
        "recommendation": recommend_weekly_meta(process_weekly_meta(data["heroStats"])),
        "targetTiers": target_tiers,
    }
    return build_hero_of_the_day_view(personal, meta, ctx.locale, ctx.i18n)
