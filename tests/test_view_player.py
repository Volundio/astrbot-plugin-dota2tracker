"""player_1 view model and template (strict Jinja rendering)."""

from __future__ import annotations

import pytest

from dota2tracker.presentation.previews import build_preview
from dota2tracker.presentation.viewmodels.player import get_guild_class, get_scale, get_win_rate_color


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("variant", ["", "anonymous", "hero"])
def test_player_1_renders(strict_renderer, preview_context, locale, variant):
    view = build_preview("player_1", preview_context(locale, variant))
    html = strict_renderer.render_html("player_1", view, locale)
    assert html.count('<tr class="match ') == 25
    assert "d2t-asset-" not in html
    assert html.count('class="blur-overlay"') == (3 if variant == "anonymous" else 0)
    if variant == "hero":
        assert 'class="hero_name"' in html


def test_player_view_values(preview_context):
    view = build_preview("player_1", preview_context("zh-CN"))
    assert view["guild"] == {"cls": "Diamond", "tag": "D2T"}
    assert view["impText"] == "+12"
    assert view["streak"]["text"].startswith("2")
    assert len(view["positions"]) == 5
    assert view["matches"][0]["timeStr"] == "25-10-26 20:00:00"


def test_helpers():
    assert get_win_rate_color(0) == "#FF0000"
    assert get_win_rate_color(0.5) == "#FFFFFF"
    assert get_win_rate_color(1) == "#00FF00"
    assert get_guild_class(None) == "Diamond" and get_guild_class(30) == "Silver"
    assert get_scale([], True) == 0
    assert get_scale([{"winCount": 3, "matchCount": 4}], False) == 150
    assert get_scale([{"winCount": 3, "matchCount": 4}], True) == 37.5
