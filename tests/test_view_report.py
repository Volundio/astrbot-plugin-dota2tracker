"""daily / daily_legacy view models and templates."""

from __future__ import annotations

import pytest

from dota2tracker.presentation.previews import build_preview
from dota2tracker.presentation.viewmodels.report import (
    badge_style,
    build_daily_legacy_view,
    build_daily_view,
    imp_info,
    spotlight_theme,
)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_daily_renders(strict_renderer, preview_context, locale):
    view = build_preview("daily", preview_context(locale))
    html = strict_renderer.render_html("daily", view, locale)
    assert "astrbot_plugin_dota2tracker" in html
    assert html.count("grid-cols-[auto_auto_1fr_auto_1fr_auto]") == len(view["cards"]) >= 1
    assert html.count("font-serif italic text-2xl") == len(view["squad"]) == 4
    assert "dota2tracker.template" not in html


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("variant", ["", "hideCombi"])
def test_daily_legacy_renders(strict_renderer, preview_context, locale, variant):
    view = build_preview("daily_legacy", preview_context(locale, variant))
    html = strict_renderer.render_html("daily_legacy", view, locale)
    assert html.count('class="player"') == 4
    assert ('style="display:none;"' in html) is (variant == "hideCombi")


def test_daily_view_classes(preview_context):
    view = build_preview("daily", preview_context("zh-CN"))
    ranks = [row["rankClass"] for row in view["squad"]]
    assert ranks == ["text-dota-gold", "text-slate-400", "text-slate-400", "text-dota-red"]
    assert view["cards"][0]["type"] == "MVP" and view["cards"][0]["icon"] == "trophy"


def test_spotlight_theme_order():
    assert spotlight_theme({"type": "MVP", "themeColor": "gold"})["accentTextColor"] == "text-dota-gold"
    assert spotlight_theme({"type": "LVP"})["icon"] == "warning"
    healer = spotlight_theme({"type": "HEALER", "themeColor": "green"})
    assert healer["badgeStyle"].startswith("bg-[#0bda5b]") and healer["icon"] == "heart"
    # theme colour checked before type, red first
    assert spotlight_theme({"type": "UTILITY", "themeColor": "red"})["accentTextColor"] == "text-dota-red"
    gray = spotlight_theme({"type": "DEFAULT", "themeColor": "gray"})
    assert gray["borderStyle"] == "border-white/10 shadow-none" and gray["icon"] == "flat"
    assert spotlight_theme({"type": "X"})["theme"] == "gold"


def test_badge_style():
    assert badge_style("#FFA500") == (
        "background-color: rgba(255, 165, 0, 0.2); border-color: rgba(255, 165, 0, 0.3); color: #FFA500;"
    )
    assert "rgba(NaN, NaN, NaN, 0.2)" in badge_style("#zz")


def test_daily_view_fallback_cards():
    mvp = {"type": "MVP", "badges": []}
    lvp = {"type": "LVP", "badges": []}
    report = {
        "meta": {},
        "headerStats": {"winRate": {"isWinRateAbove50": False, "hasComparison": False}},
        "spotlights": {"mvp": mvp, "secondary": None, "lvp": lvp},
        "squad": [{"rank": 1}],
    }
    view = build_daily_view(report)
    assert [c["type"] for c in view["cards"]] == ["MVP", "LVP"]
    assert view["squad"][0]["rankClass"] == "text-dota-gold"
    assert view["winRateSubtextClass"] == "text-slate-500"
    assert view["winRateClass"] == "text-dota-red"


def test_imp_info():
    assert imp_info(10) == {
        "valStr": "+10",
        "barClass": "score_bar pos",
        "leftStyle": "width: 10px",
        "rightStyle": "width: 10px",
    }
    assert imp_info(-30)["barClass"] == "score_bar neg over"
    assert imp_info(-30)["leftStyle"] == "width: 30px" and imp_info(-30)["rightStyle"] == "width: 0px"
    assert imp_info(26)["rightStyle"] == "width: 26px" and imp_info(26)["leftStyle"] == "width: 0px"
    assert imp_info(0) == {
        "valStr": "0",
        "barClass": "score_bar neg",
        "leftStyle": "width: 0px",
        "rightStyle": "width: 0px",
    }


def test_legacy_rates_math_round():
    players = [
        {
            "name": "A",
            "steamAccount": {"avatar": "x"},
            "winCount": 1,
            "loseCount": 7,
            "matches": [0] * 8,
            "avgImp": 3,
            "avgKills": 5.0,
            "avgDeaths": 2.5,
            "avgAssists": 1,
            "avgKDA": 2.4,
        }
    ]
    combos = [{"players": players, "winCount": 1, "matches": [0, 0]}]
    view = build_daily_legacy_view("T", players, combos, True)
    assert view["players"][0]["winRate"] == "13"  # 12.5 → 13
    assert view["players"][0]["kda"] == "5/2.5/1 (2.4)"
    assert view["combinations"][0] == {"avatars": ["x"], "winCount": "1", "loseCount": "1", "winRate": "50"}
