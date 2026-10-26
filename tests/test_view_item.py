"""item / itemlist view models and templates."""

from __future__ import annotations

import pytest

from dota2tracker.presentation.previews import build_preview
from dota2tracker.presentation.viewmodels.item import (
    build_item_view,
    calculate_font_size,
    parse_ability_desc,
    process_text,
    value_map,
)


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("variant", ["", "recipe"])
def test_item_renders(strict_renderer, preview_context, locale, variant):
    view = build_preview("item", preview_context(locale, variant))
    html = strict_renderer.render_html("item", view, locale)
    assert "d2t-asset://" not in html
    assert 'class="recipes"' in html
    assert "%" not in view["abilities"][0]["desc"].replace('<span class="value">%</span>', "")


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_itemlist_renders(strict_renderer, preview_context, locale):
    view = build_preview("itemlist", preview_context(locale))
    html = strict_renderer.render_html("itemlist", view, locale)
    assert html.count('class="item"') == len(view["items"]) > 100


def test_itemlist_search_variant(preview_context):
    view = build_preview("itemlist", preview_context("zh-CN", "闪烁"))
    assert {item["nameLoc"] for item in view["items"]} >= {"闪烁匕首"}


def test_battle_fury_view(preview_context):
    view = build_preview("item", preview_context("zh-CN", "recipe"))
    assert view["name"] == "bfury" and view["cost"] == "3900"
    assert view["stats"]["behavior"] == "单位目标"
    assert [a["value"] for a in view["attrs"]] == ["50", "7.5", "2.75"]
    assert [a["sign"] for a in view["attrs"]] == ["+", "+", "+"]
    assert str(view["attrs"][0]["desc"]) == "攻击力"
    assert [a["type"] for a in view["abilities"]] == ["active", "passive", "passive"]
    active = view["abilities"][0]
    assert active["cooldown"] == "3" and active["mana"] is None
    assert '<span class="value">60</span><span class="value">%</span>' in view["abilities"][2]["desc"]
    (block,) = view["recipeBlocks"]
    assert [entry["name_loc"] for entry in block["lower"]] == ["坚韧球", "阔剑", "阔剑", "压制之刃", "图纸"]
    assert block["lower"][-1]["img"].endswith("/items/recipe.png")
    assert block["lowerMultiple"] is True and block["upper"] == []
    assert view["patches"][0]["date"] == "2026-09-15"


def test_blink_builds_into_only(preview_context):
    view = build_preview("item", preview_context("zh-CN"))
    (block,) = view["recipeBlocks"]
    assert block["lower"] is None
    assert len(block["upper"]) == 3 and block["upperMultiple"] is True


def test_process_text_and_abilities():
    values = value_map(
        [
            {"name": "a", "values_float": [1, 2.5], "is_percentage": False},
            {"name": "p", "values_float": [10], "is_percentage": True},
        ]
    )
    assert values == {"a": "1/2.5", "p": "10%"}
    assert process_text(" %a% %p% %missing% 5%%%x ", values) == (
        '<span class="value">1/2.5</span> <span class="value">10%</span> '
        '<span class="value">[missing]</span> 5<span class="value">%</span>%x'
    )
    parsed = parse_ability_desc("<h1> Active: X </h1>line1\nline2<h1>Passive: Y</h1>%a%", values)
    assert parsed == [
        {"name": "Active: X", "desc": "line1\nline2"},
        {"name": "Passive: Y", "desc": '<span class="value">1/2.5</span>'},
    ]
    # JS "." stops at \r: the rest of the description is dropped like the original regex
    assert parse_ability_desc("<h1>A</h1>x\ry", values) == [{"name": "A", "desc": "x"}]


def test_attr_token_rendered_as_html(constants, assets):
    def t(key, params=None):
        return {
            "dota2tracker.template.item_token.attack_range": "Range<font color='#7d7d7d'>(ranged)</font>",
        }.get(key)

    item = {
        "name": "blink",
        "special_values": [
            {"name": "r", "values_float": [-100, 50], "is_percentage": True, "heading_loc": "-$attack_range <b>"},
            {"name": "s", "values_float": [0], "is_percentage": False, "heading_loc": "$unknown"},
        ],
        "recipes": [],
        "builds_into": [],
    }
    view = build_item_view(item, constants, t, assets, "en-US")
    first, second = view["attrs"]
    assert first["sign"] == "-" and first["value"] == "-100%/50%" and first["positive"] is False
    assert str(first["desc"]) == "Range<font color='#7d7d7d'>(ranged)</font> &lt;b&gt;"
    assert second["sign"] == "+" and str(second["desc"]) == "" and second["positive"] is False
    assert view["recipeBlocks"] == []


def test_calculate_font_size_regex_quirk():
    # consecutive CJK characters alternate 2/1 units because of the /g lastIndex
    assert calculate_font_size("闪烁匕首") == 12  # 2+1+2+1 = 6 → 13 → 12
    assert calculate_font_size("食人魔之斧") == 9  # 2+1+2+1+2 = 8 → floor(9.9)
    assert calculate_font_size("Overwhelming Blink") == 6  # 18 → floor(4.4) → clamp 6
    assert calculate_font_size("Quelling Blade") == 6  # 14 → floor(5.65)
    assert calculate_font_size("Blink Dagger") == 6  # 12 → 6.6
    assert calculate_font_size("Broadsword") == 7  # 10 → 7.92
    assert calculate_font_size("") == 12
