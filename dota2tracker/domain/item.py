"""Item domain logic (port of ``core/item.service.ts``)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from ..utils.jsnum import js_number
from ..utils.timefmt import luxon_format

__all__ = ["format_item_details", "format_item_list", "search_items"]


def _process_item_name(name: str) -> str:
    name = re.sub(r"^item_", "", name, flags=re.I)
    return re.sub(r"^recipe_", "recipe_", name, flags=re.I)


def format_item_list(raw_items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """``getFormattedItemListData``: merge recipes and build the ``builds_into`` graph."""
    recipes: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for item in raw_items:
        processed = {
            **item,
            "name": _process_item_name(str(item.get("name", ""))),
            "name_loc": item.get("name_loc"),
            "name_english_loc": item.get("name_english_loc"),
        }
        (recipes if "_recipe_" in str(item.get("name", "")) else items).append(processed)

    item_map: dict[Any, dict[str, Any]] = {}
    for item in items + recipes:
        item_map[item.get("id")] = {"id": item.get("id"), "name": item.get("name"), "name_loc": item.get("name_loc")}

    processed_items = []
    for base in items:
        recipe = next(
            (r for r in recipes if r.get("name") == f"recipe_{str(base.get('name')).replace('item_', '', 1)}"), None
        )
        source = (
            recipe.get("recipes") if recipe is not None and recipe.get("recipes") is not None else base.get("recipes")
        )
        merged_recipes = [
            {**entry, "items": [item_map[i] for i in entry.get("items") or [] if i in item_map]}
            for entry in source or []
        ]
        processed_items.append(
            {
                **base,
                "recipes": merged_recipes,
                "required_recipe": bool(str(recipe.get("name_loc") or "").strip()) if recipe is not None else False,
                "builds_into": [],
            }
        )

    builds_into: dict[Any, list[dict[str, Any]]] = {}
    for item in processed_items:
        for entry in item["recipes"]:
            for material in entry["items"]:
                builds_into.setdefault(material["id"], []).append(item_map[item.get("id")])

    return [
        {
            **item,
            "builds_into": [
                {"id": target["id"], "name": target["name"], "name_loc": target["name_loc"]}
                for target in builds_into.get(item.get("id"), [])
            ],
        }
        for item in processed_items
    ]


def _clean(text: str) -> str:
    lowered = text.lower()
    kept = "".join(ch for ch in lowered if unicodedata.category(ch)[0] in ("L", "N"))
    return kept.strip()


def _fuzzy(keywords: list[str], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not keywords:
        return []
    result: dict[Any, dict[str, Any]] = {}
    for item in items:
        clean_name = _clean(str(item.get("name_loc") or ""))
        matched_all = True
        for keyword in keywords:
            clean_keyword = _clean(str(keyword))
            if not clean_keyword:
                continue
            if not (clean_keyword in clean_name or all(char in clean_name for char in clean_keyword)):
                matched_all = False
                break
        if matched_all:
            result[item.get("id")] = item
    return list(result.values())


def search_items(
    items: list[dict[str, Any]],
    keyword: str,
    builtin_aliases: Mapping[str, list[str]] | None,
    custom_aliases: Iterable[Any],
) -> list[dict[str, Any]]:
    """``ItemService.searchItems``: aliases, exact name/ID match, then fuzzy search."""
    if not keyword:
        return []
    if builtin_aliases is not None and builtin_aliases.get(keyword) is not None:
        alias = list(builtin_aliases[keyword])
    else:
        alias = [entry.keyword for entry in custom_aliases if entry.alias == keyword]
    number = js_number(keyword)
    is_integer = isinstance(number, int) or (
        isinstance(number, float) and math.isfinite(number) and number.is_integer()
    )
    target = keyword.strip().lower()

    def exact(item: dict[str, Any]) -> bool:
        name = str(item.get("name_loc") or "").strip().lower()
        if any(name == str(a).lower() for a in alias):
            return True
        if name == target:
            return True
        return is_integer and item.get("id") == number

    exact_matches = [item for item in items if exact(item)]
    if exact_matches:
        return exact_matches
    return _fuzzy(alias if alias else [keyword], items)


def format_item_details(raw_item: dict[str, Any], patch_notes: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    newest_first = list(reversed(patch_notes or []))
    patches = []
    for patch in newest_first:
        data = next((i for i in patch.get("items") or [] if i.get("ability_id") == raw_item.get("id")), None)
        if data:
            patches.append(
                {
                    "patch_number": patch.get("patch_number"),
                    "patch_timestamp": patch.get("patch_timestamp"),
                    "notes": data.get("ability_notes"),
                }
            )
    item = dict(raw_item)
    version = newest_first[0].get("patch_number") if newest_first else "Unknown"
    item["game_version"] = f"{version} ({luxon_format(now, 'yyyy-MM-dd HH:mm')})"
    item["patch_notes"] = patches
    return item
