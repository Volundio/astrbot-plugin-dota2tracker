"""hero_1 view model and template (strict Jinja rendering)."""

from __future__ import annotations

import pytest

from dota2tracker.presentation.previews import build_preview


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_hero_1_renders(strict_renderer, preview_context, locale):
    view = build_preview("hero_1", preview_context(locale))
    html = strict_renderer.render_html("hero_1", view, locale)
    assert html.count('class="skill"') == len(view["abilities"])
    assert "d2t-asset://" not in html
    assert "<%" not in html
    assert '<span class="plus">+</span>' in html or "alternative" not in html


def test_hero_view_values(preview_context):
    view = build_preview("hero_1", preview_context("zh-CN"))
    assert [row["level"] for row in view["talents"]] == [25, 20, 15, 10]
    assert view["columns"][0]["stats"][0]["value"] == "50~56"
    assert len(view["columns"][0]["stats"]) == 4  # Luna is ranged
    beam = view["abilities"][0]
    assert beam["dispellable"] == {"cls": "Strong", "text": "仅强驱散"}
    assert beam["cooldown"] == "9 / 8 / 7 / 6"
    assert str(beam["attributes"][0]["talents"][0]) == '<span class="plus">+</span>80'
    assert view["gameVersion"].endswith("(2025-10-27 12:00)")
