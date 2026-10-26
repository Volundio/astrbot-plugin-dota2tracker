"""domain.item: item list graph, search (aliases/exact/fuzzy) and item details."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from dota2tracker.domain.item import format_item_details, format_item_list, search_items

RAW = [
    {"id": 1, "name": "item_blink", "name_loc": "Blink Dagger", "recipes": []},
    {"id": 2, "name": "item_broadsword", "name_loc": "Broadsword", "recipes": []},
    {"id": 3, "name": "item_quelling_blade", "name_loc": "Quelling Blade", "recipes": []},
    {"id": 145, "name": "item_bfury", "name_loc": "Battle Fury", "recipes": [{"items": [99]}]},
    {"id": 144, "name": "item_recipe_bfury", "name_loc": "Recipe ", "recipes": [{"items": [2, 2, 3, 404]}]},
    {"id": 600, "name": "item_overwhelming_blink", "name_loc": "Overwhelming Blink", "recipes": [{"items": [1]}]},
    {"id": 50, "name": "item_recipe_empty", "name_loc": "  ", "recipes": []},
    {"id": 51, "name": "item_empty", "name_loc": "水剑之刃", "recipes": []},
]


def by_id(items):
    return {item["id"]: item for item in items}


def test_format_item_list_merges_recipes_and_builds_into():
    items = by_id(format_item_list(RAW))
    # recipe entries are merged into their base item, not listed separately
    assert set(items) == {1, 2, 3, 145, 600, 51}
    bfury = items[145]
    assert bfury["name"] == "bfury"
    assert [i["id"] for i in bfury["recipes"][0]["items"]] == [2, 2, 3]  # unknown id 404 dropped
    assert bfury["required_recipe"] is True
    assert items[51]["required_recipe"] is False  # blank recipe name
    assert items[1]["required_recipe"] is False
    assert items[2]["builds_into"] == [{"id": 145, "name": "bfury", "name_loc": "Battle Fury"}] * 2
    assert [b["id"] for b in items[1]["builds_into"]] == [600]
    assert items[145]["builds_into"] == []


ALIASES = {"bf": ["Battle Fury"], "bkb": ["Black King Bar"]}


def custom(alias, keyword):
    return SimpleNamespace(alias=alias, keyword=keyword)


def test_search_exact_alias_and_id():
    items = format_item_list(RAW)
    assert [i["id"] for i in search_items(items, "bf", ALIASES, [])] == [145]
    assert [i["id"] for i in search_items(items, "  broadSWORD ", ALIASES, [])] == [2]
    assert [i["id"] for i in search_items(items, "600", ALIASES, [])] == [600]
    assert search_items(items, "", ALIASES, []) == []


def test_search_custom_alias_only_without_builtin():
    items = format_item_list(RAW)
    assert [i["id"] for i in search_items(items, "dagger", ALIASES, [custom("dagger", "Blink Dagger")])] == [1]
    # builtin alias wins over custom aliases with the same name
    assert [i["id"] for i in search_items(items, "bf", ALIASES, [custom("bf", "Broadsword")])] == [145]


def test_search_fuzzy():
    items = format_item_list(RAW)
    assert {i["id"] for i in search_items(items, "blink", None, [])} == {1, 600}
    # every character present (not contiguous)
    assert [i["id"] for i in search_items(items, "水刃", None, [])] == [51]
    assert search_items(items, "zzz", None, []) == []
    # alias with no exact hit falls back to fuzzy over the alias keywords
    assert search_items(items, "bkb", ALIASES, []) == []


def test_format_item_details_patches():
    patches = [
        {"patch_number": "7.40", "patch_timestamp": 1, "items": [{"ability_id": 1, "ability_notes": ["a"]}]},
        {"patch_number": "7.41", "patch_timestamp": 2, "items": [{"ability_id": 2, "ability_notes": ["b"]}]},
        {"patch_number": "7.42", "patch_timestamp": 3, "items": [{"ability_id": 1, "ability_notes": ["c"]}]},
    ]
    item = format_item_details({"id": 1, "name": "item_blink"}, patches, datetime(2025, 1, 1, 12, 30))
    assert item["game_version"] == "7.42 (2025-01-01 12:30)"
    assert [p["patch_number"] for p in item["patch_notes"]] == ["7.42", "7.40"]
    assert item["patch_notes"][0]["notes"] == ["c"]
    assert format_item_details({"id": 1}, [], datetime(2025, 1, 1))["game_version"].startswith("Unknown (")
