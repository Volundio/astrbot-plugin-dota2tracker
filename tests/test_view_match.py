"""match_1 / match_2 / match_2+ view models and templates (strict Jinja rendering)."""

from __future__ import annotations

import pytest

from dota2tracker.presentation.previews import build_preview
from dota2tracker.presentation.viewmodels.match import darken, parse_badge


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("variant", ["", "opendota"])
def test_match_1_renders(strict_renderer, preview_context, locale, variant):
    ctx = preview_context(locale, variant)
    view = build_preview("match_1", ctx)
    html = strict_renderer.render_html("match_1", view, locale)
    assert "8000000001" in html
    assert html.count('class="box h-[320px]') == 10
    assert "d2t-asset-" not in html  # every local image is embedded
    won = "获胜" if locale == "zh-CN" else "Won"
    assert won in html


def test_match_view_values(preview_context):
    view = build_preview("match_1", preview_context("zh-CN"))
    luna = view["players"][0]
    assert luna["gridStyle"] == "grid-column: 1; grid-row: 1"
    assert luna["titles"][0]["shortText"] == "MVP"
    assert luna["kcVal"] == "61" and luna["score"] == "2.39"
    assert view["gameModeStr"] == "天梯比赛 / 全英雄选择"
    assert view["hasExtra"] is True


def test_badge_and_darken():
    t = {"k.Rich": "Rich-#FFD700", "k.MVP": "MVP-#FFA500", "k.Soul": "魂-#66CCFF"}.get
    assert parse_badge("k.Rich", t) == {"fullText": "Rich", "shortText": "R", "color": "#FFD700"}
    assert parse_badge("k.MVP", t)["shortText"] == "MVP"
    assert parse_badge("k.Soul", t)["shortText"] == "魂"
    assert darken("#FFD700", 25) == "#bf9700"  # Math.round(2.55 * 25) == 64
    assert darken("#fff", 25) == "#bfbfbf"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("variant", ["", "opendota"])
@pytest.mark.parametrize("template", ["match_2", "match_2+"])
def test_match_2_renders(strict_renderer, preview_context, template, locale, variant):
    ctx = preview_context(locale, variant)
    view = build_preview(template, ctx)
    html = strict_renderer.render_html(template, view, locale)
    assert "d2t-asset-" not in html
    assert "{{" not in html and "<%" not in html
    assert html.count('<div class="player ') == 10
    assert "Radiant<br>天辉" in html and "Dire<br>夜魇" in html
    status = "opendota" if variant else "success"
    assert f'<p class="{status}"></p>' in html
    match_label = "比赛编号" if locale == "zh-CN" else "Match ID"
    assert match_label in html
    if template == "match_2":
        assert 'id="extra"' not in html
        return
    assert view["hasExtra"] is True
    assert 'id="charts"' in html and 'id="map"' in html and 'class="lane_outcome"' in html
    assert '<symbol id="tower"' in html
    unavailable = ctx.t("dota2tracker.template.opendota.networth_unavailable")
    if variant:
        assert unavailable in html
        assert ">\n                  ~" in html or "~" in html.split('class="lane_outcome"')[1]
        assert ctx.t("dota2tracker.template.opendota.lane_outcome_tip") in html
    else:
        assert unavailable not in html
        assert "~" not in html.split('class="lane_outcome"')[1]


def test_match_2_view_values(preview_context):
    view = build_preview("match_2", preview_context("en-US"))
    assert view["navLabels"]["matchId"] == "Match ID:"  # $t("…match_id_").slice(0, -1)
    luna = view["players"][0]
    assert luna["orderStyle"] == "order: 1"
    assert luna["scepterImg"] and luna["shardImg"]
    assert all(title["darkColor"].startswith("#") for title in luna["titles"])


def test_match_2_plus_without_extra(strict_renderer, preview_context):
    view = build_preview("match_2+", preview_context("zh-CN"))
    view["hasExtra"] = False
    html = strict_renderer.render_html("match_2+", view, "zh-CN")
    assert "比赛未解析或信息缺失" in html and 'id="charts"' not in html
