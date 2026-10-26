"""View models for ``item`` and ``itemlist`` (ports of ``templates/item/item.ejs``,
``item/recipe.ejs`` and ``item/itemlist.ejs``).

Every EJS computation (placeholder replacement, ``<h1>`` ability split, attribute
headings, ``toArray`` behaviour lists, recipe graph, list font sizes) is done here
with JavaScript semantics; the templates only print values.

Documented target fix: the ``$token`` of an attribute heading is replaced by the
``item_token`` constant rendered as HTML. The original printed it with ``<%= %>``
so tokens such as ``attack_range`` showed their ``<font>`` markup as visible text.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from markupsafe import Markup, escape

from ...data.static import DotaConstants
from ...domain.item import format_item_details, format_item_list, search_items
from ...utils.jsnum import js_str, js_truthy
from ...utils.timefmt import iso_date
from ..assets import ImageAssets

__all__ = [
    "build_item_view",
    "build_itemlist_view",
    "calculate_font_size",
    "parse_ability_desc",
    "preview_item",
    "preview_itemlist",
    "process_text",
    "value_map",
]

Translate = Callable[..., Any]

#: JS ``.`` (no ``s`` flag) does not match line terminators.
_DOT = r"[^\n\r\u2028\u2029]"
_PLACEHOLDER = re.compile(r"%(\w+)%", re.ASCII)
_ABILITY = re.compile(rf"<h1>({_DOT}*?)</h1>((?:(?!<h1>){_DOT}|\n)*)")
_HEADING = re.compile(rf"^([+-]?)({_DOT}*)")
_TOKEN = re.compile(r"\$(\w+)", re.ASCII)
_CHINESE = re.compile("[\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef]")
_JS_WS = (
    " \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
PREVIEW_NOW = "2025-01-01T12:00:00+08:00"


def _t(t: Translate, key: str) -> str:
    value = t(key)
    return "" if value is None else str(value)


def _js_join(values: Any, sep: str = "/") -> str:
    """``Array.prototype.join``: ``null``/``undefined`` become empty strings."""
    return sep.join("" if v is None else js_str(v) for v in values or [])


def value_map(special_values: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in special_values or []:
        joined = _js_join(value.get("values_float"))
        result[str(value.get("name"))] = f"{joined}%" if value.get("is_percentage") else joined
    return result


def process_text(text: Any, values: dict[str, str]) -> str:
    """``processText``: ``%key%`` → value span, ``%%`` → ``%`` span, then ``trim()`` (raw HTML)."""
    if not text:
        return ""

    def placeholder(match: re.Match[str]) -> str:
        key = match.group(1)
        value = values.get(key)
        return f'<span class="value">{value if value is not None else f"[{key}]"}</span>'

    text = _PLACEHOLDER.sub(placeholder, str(text))
    text = text.replace("%%", '<span class="value">%</span>')
    return text.strip(_JS_WS)


def parse_ability_desc(desc: Any, values: dict[str, str]) -> list[dict[str, str]]:
    if not desc:
        return []
    return [
        {"name": match.group(1).strip(_JS_WS), "desc": process_text(match.group(2), values)}
        for match in _ABILITY.finditer(str(desc))
    ]


def _to_array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _capitalize(text: Any) -> str:
    text = "" if text is None else str(text)
    return text[:1].upper() + text[1:].lower()


def _item_image(name: Any, assets: ImageAssets) -> str:
    name = "" if name is None else str(name)
    return assets.url("recipe" if name.startswith("recipe_") else name, "items")


def _recipe_entry(entry: dict[str, Any], assets: ImageAssets) -> dict[str, Any]:
    return {"img": _item_image(entry.get("name"), assets), "name_loc": entry.get("name_loc")}


def _stats(citem: dict[str, Any] | None, t: Translate) -> dict[str, Any] | None:
    if not citem or not citem.get("abilities"):
        return None
    prefix = "dota2tracker.template."
    if any(_capitalize((a or {}).get("type")) == "Active" for a in citem["abilities"]):
        behavior = "/".join(_t(t, f"{prefix}behavior.{js_str(b)}") for b in _to_array(citem.get("behavior")))
    else:
        behavior = _t(t, f"{prefix}behavior.Passive")
    target_team = citem.get("target_team")
    team = None
    if target_team and len(target_team):
        team = "/".join(_t(t, f"{prefix}target_team.{js_str(tt)}") for tt in _to_array(target_team))
    dmg_type = citem.get("dmg_type")
    dispellable = citem.get("dispellable")
    bkb = citem.get("bkbpierce")
    return {
        "behavior": behavior,
        "targetTeam": team,
        "dmgType": {"cls": js_str(dmg_type), "text": _t(t, f"{prefix}damage_type_{js_str(dmg_type)}")}
        if js_truthy(dmg_type)
        else None,
        "dispellable": {
            "cls": js_str(dispellable),
            "text": _t(
                t, prefix + ("dispellable_Strong" if dispellable == "Strong Dispels Only" else js_str(dispellable))
            ),
        }
        if js_truthy(dispellable)
        else None,
        "bkbpierce": {"cls": js_str(bkb), "text": _t(t, prefix + js_str(bkb))} if js_truthy(bkb) else None,
    }


def _attrs(special_values: list[dict[str, Any]], t: Translate) -> list[dict[str, Any]]:
    result = []
    for value in special_values or []:
        heading = value.get("heading_loc")
        if not heading:
            continue
        match = _HEADING.match(str(heading))
        sign = (match.group(1) if match else "") or "+"
        raw = match.group(2) if match else ""
        token = _TOKEN.search(raw)
        if token:
            # Target fix: the constant may contain <font> markup, rendered as HTML.
            replacement = _t(t, f"dota2tracker.template.item_token.{token.group(1)}")
            desc = Markup(escape(raw[: token.start()]) + Markup(replacement) + escape(raw[token.end() :]))
        else:
            desc = escape(raw)
        floats = value.get("values_float") or []
        suffix = "%" if value.get("is_percentage") else ""
        first = floats[0] if floats else None
        result.append(
            {
                "sign": sign,
                "positive": isinstance(first, (int, float)) and first > 0,
                "value": "/".join(js_str(v) + suffix for v in floats),
                "desc": desc,
            }
        )
    return result


def _recipe_blocks(item: dict[str, Any], constants: DotaConstants, t: Translate, assets: ImageAssets) -> list[dict]:
    recipes = item.get("recipes") or []
    builds_into = item.get("builds_into") or []
    if not recipes and not builds_into:
        return []
    upper = [_recipe_entry(target, assets) for target in builds_into]
    blocks = []
    for recipe in recipes if recipes else [None]:
        lower = None
        lower_multiple = False
        if recipes and recipe:
            ingredients = [_recipe_entry(entry, assets) for entry in recipe.get("items") or []]
            if f"recipe_{js_str(item.get('name'))}" in constants.items:
                ingredients.append(
                    {"img": assets.url("recipe", "items"), "name_loc": _t(t, "dota2tracker.template.recipe")}
                )
            lower = ingredients
            lower_multiple = len(ingredients) > 1
        blocks.append(
            {
                "upper": upper,
                "upperMultiple": len(upper) > 1,
                "lower": lower,
                "lowerMultiple": lower_multiple,
            }
        )
    return blocks


def build_item_view(
    item: dict[str, Any],
    constants: DotaConstants,
    t: Translate,
    assets: ImageAssets,
    locale: str,
) -> dict[str, Any]:
    """Context of ``item/item.html.j2``.

    ``item`` is ``{**format_item_details(...), **item_list_entry}`` (the list entry
    wins, like ``{ ...itemDetails, ...basicItemInfo }``).
    """
    values = value_map(item.get("special_values") or [])
    abilities = []
    passive = _t(t, "dota2tracker.template.behavior.Passive")
    mana_costs = item.get("mana_costs") or []
    cooldowns = item.get("cooldowns") or []
    for ability in parse_ability_desc(item.get("desc_loc"), values):
        kind = "passive" if ability["name"].startswith(passive) else "active"
        abilities.append(
            {
                "type": kind,
                "name": ability["name"],
                "desc": Markup(ability["desc"]),
                "mana": _js_join(mana_costs) if kind == "active" and mana_costs and js_truthy(mana_costs[0]) else None,
                "cooldown": _js_join(cooldowns) if kind == "active" and cooldowns and js_truthy(cooldowns[0]) else None,
            }
        )
    patches = [
        {
            "number": patch.get("patch_number"),
            "date": iso_date(patch.get("patch_timestamp") or 0),
            "notes": [
                {"indent": js_str(note.get("indent_level")), "note": Markup(js_str(note.get("note")))}
                for note in patch.get("notes") or []
            ],
        }
        for patch in item.get("patch_notes") or []
    ]
    name = item.get("name")
    cost = item.get("item_cost")
    return {
        "languageTag": locale,
        "name": name,
        "nameLoc": item.get("name_loc"),
        "img": assets.url(name, "items"),
        "cost": js_str(cost) if js_truthy(cost) else None,
        "goldImg": assets.url("gold", "icons"),
        "cooldownImg": assets.url("cooldown", "icons"),
        "stats": _stats(constants.items.get(js_str(name)), t),
        "attrs": _attrs(item.get("special_values") or [], t),
        "abilities": abilities,
        "notes": [Markup(process_text(note, values)) for note in item.get("notes_loc") or []],
        "lore": item.get("lore_loc") or None,
        "recipeBlocks": _recipe_blocks(item, constants, t, assets),
        "middle": {"img": _item_image(name, assets), "name_loc": item.get("name_loc")},
        "patches": patches,
    }


# ------------------------------------------------------------------ itemlist
def calculate_font_size(text: Any) -> int:
    """``calculateFontSize`` including the global-regex ``lastIndex`` quirk.

    ``chineseRegex`` has the ``g`` flag, so after a successful ``test`` on a
    one-character string ``lastIndex`` is 1 and the next ``test`` fails (and
    resets): consecutive CJK characters alternate between 2 and 1 units.
    """
    text = "" if text is None else str(text)
    units = 0
    last_index = 0
    for unit in _utf16_units(text):
        if last_index == 0 and _CHINESE.fullmatch(unit):
            units += 2
            last_index = 1
        else:
            units += 1
            last_index = 0
    if units == 0:
        return 12
    size = math.floor((44 / units) * 1.8)
    return min(12, max(6, size))


def _utf16_units(text: str) -> list[str]:
    units: list[str] = []
    for char in text:
        if ord(char) > 0xFFFF:
            units.extend(["\ud800", "\udc00"])  # surrogates never match the CJK ranges
        else:
            units.append(char)
    return units


def build_itemlist_view(items: list[dict[str, Any]], assets: ImageAssets) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": item.get("id"),
                "img": assets.url(item.get("name"), "items"),
                "nameLoc": item.get("name_loc"),
                "fontSize": calculate_font_size(item.get("name_loc")),
            }
            for item in items
        ]
    }


# ------------------------------------------------------------------ previews
def _preview_list(ctx) -> list[dict[str, Any]]:
    from ..previews import load_fixture

    return format_item_list(load_fixture(f"item_list.{ctx.locale}.json"))


def preview_item(ctx) -> dict[str, Any]:
    """Blink Dagger (id 1); ``variant="recipe"`` shows Battle Fury (id 145)."""
    from ..previews import load_fixture

    item_id = 145 if ctx.variant == "recipe" else 1
    details = load_fixture(f"item_details.{ctx.locale}.json")[str(item_id)]
    patch_notes = load_fixture(f"patch_notes.{ctx.locale}.json")
    entry = next(i for i in _preview_list(ctx) if i.get("id") == item_id)
    now = datetime.fromisoformat(PREVIEW_NOW)
    if ctx.tz is not None:
        now = now.astimezone(ctx.tz)
    merged = {**format_item_details(details, patch_notes, now), **entry}
    return build_item_view(merged, ctx.constants, ctx.t, ctx.assets, ctx.locale)


def preview_itemlist(ctx) -> dict[str, Any]:
    """All items; ``variant`` other than empty/``all`` is used as a search keyword."""
    items = _preview_list(ctx)
    if ctx.variant and ctx.variant != "all":
        aliases = (ctx.i18n.constant_locale(ctx.locale).get("dota2tracker") or {}).get("items_alias")
        items = search_items(items, ctx.variant, aliases, [])
    return build_itemlist_view(items, ctx.assets)
