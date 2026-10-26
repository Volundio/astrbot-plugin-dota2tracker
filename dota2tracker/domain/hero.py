"""Hero domain logic (port of ``core/hero.service.ts``)."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from ..constants import RANK_BRACKETS
from ..utils.jsnum import js_str
from ..utils.timefmt import luxon_format

__all__ = [
    "format_hero_desc",
    "format_hero_details",
    "process_weekly_meta",
    "recommend_weekly_meta",
    "weekly_meta_brackets",
]

MINIMUM_PICK_RATE = 0.02
META_RECOMMENDATION_COUNT = 3

_DESC_PATTERN = re.compile(r"%%|%([^%]+)%|\{([^}]+)\}")
_TALENT_PATTERN = re.compile(r"\{s:(.*?)\}")


def _join(values: Any, separator: str) -> str:
    return separator.join(js_str(v) for v in values or [])


def format_hero_desc(template: Any, special_values: list[dict[str, Any]], kind: str = "normal") -> Any:
    """Replace ``%value%`` / ``{s:value}`` placeholders with highlighted values."""
    if not template:
        return template

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "%%":
            return "%"
        field = match.group(1) or match.group(2)
        field_name = re.sub(r"^shard_", "", re.sub(r"^s:", "", field)).lower()
        stripped_field = re.sub(r"_tooltip$", "", field_name)

        def matches(sv: dict[str, Any]) -> bool:
            name = str(sv.get("name", "")).lower()
            stripped_name = re.sub(r"_tooltip$", "", name)
            return (
                name == field_name
                or name == f"bonus_{field_name}"
                or name == f"shard_{field_name}"
                or f"bonus_{name}" == field_name
                or f"shard_{name}" == field_name
                or stripped_name == stripped_field
                or name == f"ability{stripped_field}"
            )

        special = next((sv for sv in special_values or [] if matches(sv)), None)
        if special is None:
            return match.group(0)
        if kind == "scepter":
            values = special.get("values_scepter") or special.get("values_float")
        elif kind == "shard":
            values = special.get("values_shard") or special.get("values_float")
        else:
            values = special.get("values_float")
        return f'<span class="value">{_join(values, " / ")}</span>'

    return _DESC_PATTERN.sub(replace, template)


def format_hero_details(raw_hero: dict[str, Any], patch_notes: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """``HeroService.formatHeroDetails`` (newest patches first)."""
    newest_first = list(reversed(patch_notes or []))
    hero_id = raw_hero.get("id")
    patches_of_hero = []
    for patch in newest_first:
        data = next((h for h in patch.get("heroes") or [] if h.get("hero_id") == hero_id), None)
        if data:
            patches_of_hero.append(
                {
                    "patch_number": patch.get("patch_number"),
                    "patch_timestamp": patch.get("patch_timestamp"),
                    "notes": data,
                }
            )

    hero = dict(raw_hero)
    version = newest_first[0].get("patch_number") if newest_first else "Unknown"
    hero["game_version"] = f"{version} ({luxon_format(now, 'yyyy-MM-dd HH:mm')})"
    hero["patch_notes"] = []
    for patch in patches_of_hero:
        combined = [*(patch["notes"].get("hero_notes") or []), *(patch["notes"].get("talent_notes") or [])]
        if combined:
            hero["patch_notes"].append(
                {"patch_number": patch["patch_number"], "patch_timestamp": patch["patch_timestamp"], "notes": combined}
            )

    abilities = [dict(ab) for ab in raw_hero.get("abilities") or []]
    for ability in abilities:
        values = ability.get("special_values") or []
        ability["desc_loc"] = format_hero_desc(ability.get("desc_loc"), values)
        ability["notes_loc"] = [format_hero_desc(note, values) for note in ability.get("notes_loc") or []]
        if ability.get("ability_has_scepter"):
            ability["scepter_loc"] = format_hero_desc(ability.get("scepter_loc"), values, "scepter")
        if ability.get("ability_has_shard"):
            ability["shard_loc"] = format_hero_desc(ability.get("shard_loc"), values, "shard")
        ability["patch_notes"] = []
        for patch in patches_of_hero:
            notes = patch["notes"].get("abilities")
            if not notes:
                continue
            found = next((n for n in notes if n.get("ability_id") == ability.get("id")), None)
            if found and found.get("ability_notes"):
                ability["patch_notes"].append(
                    {
                        "patch_number": patch["patch_number"],
                        "patch_timestamp": patch["patch_timestamp"],
                        "notes": found["ability_notes"],
                    }
                )
    hero["abilities"] = abilities

    talents = [dict(t) for t in raw_hero.get("talents") or []]
    for talent in talents:

        def replace(match: re.Match[str], talent: dict[str, Any] = talent) -> str:
            special_name = match.group(1)
            target = next((sv for sv in talent.get("special_values") or [] if sv.get("name") == special_name), None)
            if target is not None:
                return _join(target.get("values_float"), "/")
            clean = re.sub(r"^bonus_", "", special_name)
            for ability in abilities:
                for sv in ability.get("special_values") or []:
                    if sv.get("name") not in (clean, special_name):
                        continue
                    bonuses = sv.get("bonuses") or []
                    bonus = next((b for b in bonuses if b.get("name") == talent.get("name")), None)
                    if bonus is not None:
                        if "value" in bonus:
                            # ``bonusObj.value !== undefined``: JSON null is returned as "null" in JS.
                            return "null" if bonus["value"] is None else js_str(bonus["value"])
                        break
            return "?"

        talent["name_loc"] = _TALENT_PATTERN.sub(replace, talent.get("name_loc") or "")
    hero["talents"] = talents
    return hero


# -------------------------------------------------------------- weekly meta
def weekly_meta_brackets(rank: int) -> tuple[list[int], list[str | None]]:
    """Target tiers (±1) and the queried brackets (``tier-1 .. tier+3`` as in the original)."""
    tier = min(max(math.floor((rank or 0) / 10), 2), 7)
    target_tiers = [tier - 1, tier, tier + 1]
    brackets = [RANK_BRACKETS[i] if 0 <= i < len(RANK_BRACKETS) else None for i in range(tier - 1, tier + 4)]
    return target_tiers, brackets


def weekly_meta_cache_key(date_text: str, brackets: list[str | None]) -> str:
    return f"{date_text}_{'-'.join(b or '' for b in brackets)}"


def process_weekly_meta(hero_stats: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Add ``pickRate``/``winRate`` to every hero of every position."""
    result: dict[str, list[dict[str, Any]]] = {}
    for position, heroes in (hero_stats or {}).items():
        entries = [dict(hero) for hero in heroes or []]
        total = sum((hero.get("matchCount") or 0) for hero in entries) / 2
        for hero in entries:
            match_count = hero.get("matchCount") or 0
            hero["pickRate"] = match_count / total if total > 0 else 0
            hero["winRate"] = (hero.get("winCount") or 0) / match_count if match_count > 0 else 0
        result[position] = entries
    return result


def recommend_weekly_meta(meta: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        position: sorted(
            (hero for hero in heroes if hero.get("pickRate", 0) > MINIMUM_PICK_RATE),
            key=lambda hero: -hero.get("winRate", 0),
        )[:META_RECOMMENDATION_COUNT]
        for position, heroes in meta.items()
    }
