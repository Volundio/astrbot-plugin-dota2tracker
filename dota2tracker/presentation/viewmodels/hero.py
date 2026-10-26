"""View model for the ``hero_1`` template (port of ``hero/hero_1.ejs``).

Input is the output of :func:`domain.hero.format_hero_details`. All EJS
computations (universal attribute damage, ability tags from dotaconstants,
special value rows, cooldown/mana, patch dates) run here with JS semantics.
HTML fragments from Valve's official datafeed (``hype_loc``, ``bio_loc``,
``desc_loc``, notes, scepter/shard text, patch notes) are marked safe because
the original printed them raw with ``<%-``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from markupsafe import Markup

from ...data.static import DotaConstants
from ...utils.jsnum import js_str, js_to_fixed, js_truthy
from ...utils.timefmt import iso_date
from ..assets import ImageAssets

__all__ = ["build_hero_view", "preview_hero_1"]

Translate = Callable[..., Any]
PRIMARY_ATTRS = {3: "hero_universal", 0: "hero_strength", 1: "hero_agility", 2: "hero_intelligence"}
PLUS = '<span class="plus">+</span>'
#: (left talent index, level, right talent index) from top to bottom
TALENT_ROWS = [(7, 25, 6), (5, 20, 4), (3, 15, 2), (1, 10, 0)]
PREVIEW_NOW = datetime(2025, 10, 27, 12, 0)


def _raw(value: Any) -> Markup:
    """``<%- value %>``: unescaped output (``null``/``undefined`` print nothing)."""
    return Markup("" if value is None else js_str(value))


def _cat(value: Any) -> str:
    return "null" if value is None else js_str(value)


def _concat(value: Any) -> list[Any]:
    """``[].concat(value)``."""
    return list(value) if isinstance(value, list) else [value]


def _patches(patches: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "number": _cat(patch.get("patch_number")),
            "date": iso_date(patch.get("patch_timestamp") or 0),
            "notes": [
                {"indent": _cat(n.get("indent_level")), "html": _raw(n.get("note"))} for n in patch.get("notes") or []
            ],
        }
        for patch in patches or []
    ]


def _alt_values(values: list[Any], suffix: str) -> Markup:
    return Markup(
        " / ".join((PLUS if isinstance(v, (int, float)) and v > 0 else "") + js_str(v) + suffix for v in values)
    )


def _special_values(ability: dict[str, Any], assets: ImageAssets) -> list[dict[str, Any]]:
    rows = []
    for sv in ability.get("special_values") or []:
        if not js_truthy(sv.get("heading_loc")):
            continue
        values = sv.get("values_float") or []
        all_zero = all(v == 0 for v in values) or len(values) == 0
        scepter = sv.get("values_scepter") or []
        shard = sv.get("values_shard") or []
        bonuses = sv.get("bonuses") or []
        if all_zero and not scepter and not shard and not bonuses:
            continue
        suffix = "%" if sv.get("is_percentage") else ""
        row: dict[str, Any] = {"heading": _raw(sv.get("heading_loc")), "primary": None, "scepter": None, "shard": None}
        if not all_zero or scepter or shard:
            row["primary"] = {
                "empty": all_zero,
                "text": "" if all_zero else " / ".join(js_str(v) + suffix for v in values),
            }
        if scepter:
            row["scepter"] = _alt_values(scepter, suffix)
        if shard:
            row["shard"] = _alt_values(shard, suffix)
        row["talents"] = [
            Markup(
                (PLUS if isinstance(b.get("value"), (int, float)) and b["value"] > 0 else "")
                + _cat(b.get("value"))
                + suffix
            )
            for b in bonuses
        ]
        rows.append(row)
    return rows


def _cost(ability: dict[str, Any], name: str) -> str | None:
    stats = next((sv for sv in ability.get("special_values") or [] if sv.get("name") == name), None)
    values = (stats or {}).get("values_float") or []
    if not values or (len(values) == 1 and values[0] == 0):
        return None
    return " / ".join(js_str(v) for v in values)


def _ability_view(
    ability: dict[str, Any], constants: DotaConstants, t: Translate, assets: ImageAssets
) -> dict[str, Any]:
    dc = constants.abilities.get(ability.get("name") or "") or {}
    granted = bool(ability.get("ability_is_granted_by_shard") or ability.get("ability_is_granted_by_scepter"))
    behaviors = [b for b in _concat(dc.get("behavior")) if b != "Hidden" or not granted]
    behavior = "/".join(
        t("dota2tracker.template.behavior." + js_str(b)) or "" if b is not None else "" for b in behaviors
    )
    target_team = None
    if js_truthy(dc.get("target_team")):
        target_team = "/".join(
            t("dota2tracker.template.target_team." + js_str(x)) or "" for x in _concat(dc.get("target_team"))
        )
    dmg = dc.get("dmg_type")
    damage = None
    if js_truthy(dmg) and not isinstance(dmg, list):
        damage = {"cls": js_str(dmg), "text": t("dota2tracker.template.damage_type_" + js_str(dmg)) or ""}
    disp = dc.get("dispellable")
    dispellable = None
    if js_truthy(disp):
        strong = disp == "Strong Dispels Only"
        dispellable = {
            "cls": "Strong" if strong else js_str(disp),
            "text": t("dota2tracker.template." + ("dispellable_Strong" if strong else js_str(disp))) or "",
        }
    bkb = dc.get("bkbpierce")
    bkbpierce = None
    if js_truthy(bkb) and not isinstance(bkb, list):
        bkbpierce = {"cls": js_str(bkb), "text": t("dota2tracker.template." + js_str(bkb)) or ""}

    scepter_desc = None
    if (
        ability.get("ability_has_scepter")
        and not ability.get("ability_is_granted_by_scepter")
        and js_truthy(ability.get("scepter_loc"))
    ):
        scepter_desc = _raw(ability.get("scepter_loc"))
    shard_desc = None
    if (
        ability.get("ability_has_shard")
        and not ability.get("ability_is_granted_by_shard")
        and js_truthy(ability.get("shard_loc"))
    ):
        shard_desc = _raw(ability.get("shard_loc"))

    return {
        "innate": "true" if ability.get("ability_is_innate") else "false",
        "isInnate": bool(ability.get("ability_is_innate")),
        "name": ability.get("name_loc") or "",
        "grantedByScepter": bool(ability.get("ability_is_granted_by_scepter")),
        "grantedByShard": bool(ability.get("ability_is_granted_by_shard")),
        "img": assets.url(ability.get("name"), "abilities"),
        "behavior": behavior,
        "targetTeam": target_team,
        "damage": damage,
        "dispellable": dispellable,
        "bkbpierce": bkbpierce,
        "description": _raw(ability.get("desc_loc")),
        "scepterDesc": scepter_desc,
        "shardDesc": shard_desc,
        "notes": [_raw(n) for n in ability.get("notes_loc") or []],
        "attributes": _special_values(ability, assets),
        "cooldown": _cost(ability, "AbilityCooldown"),
        "mana": _cost(ability, "AbilityManaCost"),
        "lore": ability.get("lore_loc") or None,
        "patches": _patches(ability.get("patch_notes")),
    }


def build_hero_view(
    hero: dict[str, Any], constants: DotaConstants, t: Translate, assets: ImageAssets, locale: str
) -> dict[str, Any]:
    damage_min = hero.get("damage_min") or 0
    damage_max = hero.get("damage_max") or 0
    if hero.get("primary_attr") == 3:
        base = math.floor(
            ((hero.get("str_base") or 0) + (hero.get("agi_base") or 0) + (hero.get("int_base") or 0)) * 0.45
        )
        damage_min += base
        damage_max += base
    hero_dc = constants.heroes.get(int(hero.get("id") or 0)) or {}
    short = re.match(r"^npc_dota_hero_(.+)$", hero.get("name") or "")
    talents = hero.get("talents") or []

    def talent(index: int) -> str:
        return (talents[index].get("name_loc") or "") if index < len(talents) else ""

    roles = [
        {"level": js_str(level), "name": t(f"dota2tracker.template.roles.{index}") or ""}
        for index, level in enumerate(hero.get("role_levels") or [])
        if level > 0
    ]

    def stat(icon: str, value: str) -> dict[str, str]:
        return {"img": assets.url(icon, "heroes/stats"), "value": value}

    attack = [
        stat("icon_damage", f"{_cat(damage_min)}~{_cat(damage_max)}"),
        stat("icon_attack_time", js_to_fixed(hero.get("attack_rate"), 1)),
        stat("icon_attack_range", _cat(hero.get("attack_range"))),
    ]
    if hero_dc.get("attack_type") != "Melee":
        attack.append(stat("icon_projectile_speed", _cat(hero.get("projectile_speed"))))
    columns = [
        {"title": t("dota2tracker.template.attack") or "", "stats": attack},
        {
            "title": t("dota2tracker.template.defense") or "",
            "stats": [
                stat("icon_armor", js_to_fixed(hero.get("armor"), 1)),
                stat("icon_magic_resist", f"{_cat(hero.get('magic_resistance'))}%"),
            ],
        },
        {
            "title": t("dota2tracker.template.mobility") or "",
            "stats": [
                stat("icon_movement_speed", _cat(hero.get("movement_speed"))),
                stat("icon_turn_rate", js_to_fixed(hero.get("turn_rate"), 1)),
                stat("icon_vision", f"{_cat(hero.get('sight_range_day'))} / {_cat(hero.get('sight_range_night'))}"),
            ],
        },
    ]

    return {
        "languageTag": locale,
        "id": js_str(hero.get("id")),
        "heroImg": assets.url(short.group(1) if short else "undefined", "heroes"),
        "primaryAttrImg": assets.url(PRIMARY_ATTRS.get(hero.get("primary_attr"), "undefined"), "icons"),
        "name": hero.get("name_loc") or "",
        "roles": roles,
        "attrs": [
            {"cls": key, "base": _cat(hero.get(f"{key}_base")), "gain": js_to_fixed(hero.get(f"{key}_gain"), 1)}
            for key in ("str", "agi", "int")
        ],
        "npe": hero.get("npe_desc_loc") or "",
        "hype": _raw(hero.get("hype_loc")),
        "talents": [
            {"left": talent(left), "level": level, "right": talent(right)} for left, level, right in TALENT_ROWS
        ],
        "health": {"number": _cat(hero.get("max_health")), "regen": js_to_fixed(hero.get("health_regen"), 1)},
        "mana": {
            "zero": not js_truthy(hero.get("max_mana")),
            "number": _cat(hero.get("max_mana")),
            "regen": js_to_fixed(hero.get("mana_regen"), 1),
        },
        "columns": columns,
        "patches": _patches(hero.get("patch_notes")),
        "abilities": [_ability_view(a, constants, t, assets) for a in hero.get("abilities") or []],
        "innateIcon": assets.url("innate_icon", "icons"),
        "scepterImg": assets.url("scepter"),
        "shardImg": assets.url("shard"),
        "talentImg": assets.url("talents", "icons", "svg"),
        "bio": _raw(hero.get("bio_loc")),
        "gameVersion": _cat(hero.get("game_version")),
    }


def preview_hero_1(ctx) -> dict[str, Any]:
    from ...domain.hero import format_hero_details
    from ..previews import load_fixture

    locale = ctx.locale if ctx.locale in ("zh-CN", "en-US") else "zh-CN"
    raw = load_fixture(f"hero_details.{locale}.json")
    patch_notes = load_fixture(f"patch_notes.{locale}.json")
    hero = format_hero_details(raw, patch_notes, ctx.now or PREVIEW_NOW)
    return build_hero_view(hero, ctx.constants, ctx.t, ctx.assets, ctx.locale)
