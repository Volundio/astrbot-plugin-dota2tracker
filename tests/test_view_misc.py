"""help / members / hero_of_the_day view models and templates."""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dota2tracker.presentation.previews import build_preview
from dota2tracker.presentation.viewmodels.misc import (
    build_help_view,
    build_hero_of_the_day_view,
    build_members_view,
)

TZ = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("template", ["help", "members", "hero_of_the_day"])
def test_misc_renders(strict_renderer, preview_context, locale, template):
    view = build_preview(template, preview_context(locale))
    html = strict_renderer.render_html(template, view, locale)
    assert "width: max-content" in html or "width:max-content" in html
    assert "width: fit-content" not in html and "width:fit-content" not in html


def test_help_view_escaping():
    view = build_help_view([["a", "x\ny", "<arg>", "", None, "e"]], "<arg>\n[arg]")
    assert str(view["header"]) == "&lt;arg&gt;<br/>[arg]"
    assert view["columnCount"] == 6
    assert [str(c) for c in view["rows"][0]] == ["a", "x<br/>y", "&lt;arg&gt;", "", "", "e"]
    assert build_help_view([["1"] * 8], "")["columnCount"] == 8


def test_help_preview_uses_raw_header(strict_renderer, preview_context):
    view = build_preview("help", preview_context("zh-CN"))
    html = strict_renderer.render_html("help", view, "zh-CN")
    assert "&lt;arg&gt; 是必须参数" in html
    assert "</arg>" not in html


def test_members_view(i18n):
    now = datetime(2025, 10, 26, 20, 0, tzinfo=TZ)
    members = [
        {"name": "<i>n</i>", "steamId": 1, "winRate": 0.6, "lastMatchTime": now.timestamp() - 86400},
        {"name": "b", "steamId": 2, "winRate": math.nan, "lastMatchTime": None},
        {"name": "c", "steamId": 3, "winRate": 1 / 3, "lastMatchTime": 0},
    ]
    view = build_members_view(
        members,
        "zh-CN",
        now,
        TZ,
        lambda k, p=None: i18n.t("zh-CN", k, p),
        lambda k, p=None: i18n.text("zh-CN", k, p),
    )
    assert view["title"] == "本群 DOTA2 玩家名册 (共 3 人)"
    rows = view["members"]
    assert [r["winRate"] for r in rows] == ["60.0%", "-----", "33.3%"]
    assert rows[0]["lastMatch"] == "1天前"
    assert rows[1]["lastMatch"] == rows[2]["lastMatch"] == "----------"


def test_members_name_is_escaped(strict_renderer, preview_context):
    view = build_preview("members", preview_context("zh-CN"))
    html = strict_renderer.render_html("members", view, "zh-CN")
    assert "&lt;b&gt;o.O&lt;/b&gt;" in html
    assert view["members"][4]["winRate"] == "-----"


def _meta():
    return {
        "recommendation": {"pos1": [{"heroId": 1, "pickRate": 0.04981, "winRate": 0.5}], "pos2": []},
        "targetTiers": [4, 5, 6],
    }


def test_hero_of_the_day_personalized(i18n):
    personal = {
        "recommendedHeroes": [2],
        "recommendationType": "LIFETIME_ONLY",
        "recommendationPool": [
            {
                "heroId": 2,
                "recentWinScore": 0,
                "lifetimeWinScore": 20.5541,
                "impBonus": 0.15,
                "isHotStreak": True,
                "totalScore": 24.665,
            },
            {
                "heroId": 1,
                "recentWinScore": 3,
                "lifetimeWinScore": 1.0,
                "impBonus": -0.4,
                "isHotStreak": False,
                "totalScore": 3.6,
            },
        ],
    }
    view = build_hero_of_the_day_view(personal, _meta(), "zh-CN", i18n)
    assert view["personalized"] and view["lifetimeOnly"]
    first, second = view["pool"]
    assert first["name"].endswith("*") and not second["name"].endswith("*")
    assert first["lifetimeWinScore"] == "20.55" and first["impBonus"] == "0.15"
    assert first["hotStreak"] == "√" and second["hotStreak"] == "/"
    assert first["totalScore"] == "24.66"  # toFixed on the binary value (24.664999…)
    assert second["lifetimeWinScore"] == "1" and second["totalScore"] == "3.60"
    assert "<br>" in str(view["tableHeaders"][2])
    assert str(view["positions"][0]["label"]) == "1号位："
    assert view["positions"][0]["heroes"].endswith("(4.98% 50%)")
    assert view["positions"][1]["heroes"] == ""
    assert "统帅, 传奇, 万古流芳" in str(view["metaIntro"]) and "<b>" in str(view["metaIntro"])


@pytest.mark.parametrize(
    ("kind", "key"),
    [("LIFETIME_NO_RECORD", "no_record"), ("PLAYER_ANONYMOUS", "anonymous")],
)
def test_hero_of_the_day_no_data(strict_renderer, i18n, kind, key):
    personal = {"recommendedHeroes": [], "recommendationPool": [], "recommendationType": kind}
    view = build_hero_of_the_day_view(personal, _meta(), "en-US", i18n)
    assert not view["personalized"]
    expected = i18n.t("en-US", f"commands.dota2tracker.hero-of-the-day.messages.recommendation_type_{key}")
    assert str(view["notice"]) == expected
    html = strict_renderer.render_html("hero_of_the_day", view, "en-US")
    assert expected in html


@pytest.mark.parametrize("variant", ["", "lifetime", "anonymous"])
def test_hero_of_the_day_preview_variants(strict_renderer, preview_context, variant):
    view = build_preview("hero_of_the_day", preview_context("zh-CN", variant))
    strict_renderer.render_html("hero_of_the_day", view, "zh-CN")
    assert len(view["positions"]) == 5
    if variant == "":
        assert view["type"] == "PERSONALIZED" and len(view["pool"]) == 10
        assert sum(hero["name"].endswith("*") for hero in view["pool"]) == 3


def test_user_text_cannot_forge_asset_placeholders(strict_renderer, preview_context, assets):
    """A nickname that looks like an asset reference stays plain text (no image data spliced in)."""
    from dota2tracker.presentation.previews import build_preview

    baseline = strict_renderer.render_html("members", build_preview("members", preview_context()), "zh-CN")
    data = build_preview("members", preview_context())
    forged = "d2t-asset://xi.jpg"  # the scheme used before 1.1.1
    data["members"][0]["name"] = forged
    html = strict_renderer.render_html("members", data, "zh-CN")
    assert forged in html
    assert assets.scheme not in html
    assert html.count("data:image/") == baseline.count("data:image/")
