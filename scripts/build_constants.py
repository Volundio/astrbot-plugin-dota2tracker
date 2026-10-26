"""Build the trimmed ``dotaconstants`` snapshot bundled with the plugin.

The Koishi plugin imported the npm package ``dotaconstants`` at runtime. The
AstrBot port ships a trimmed JSON snapshot that only keeps the fields the
original code reads (hero names / attack type, item ids / costs / behaviour,
ability behaviour flags, XP table, player colours, lobby & game modes).

Usage::

    npm pack dotaconstants@<version> && tar xzf dotaconstants-*.tgz
    python scripts/build_constants.py path/to/package [output.json]

The default output is ``dota2tracker/data/dotaconstants.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dota2tracker" / "data" / "dotaconstants.json"

HERO_FIELDS = ("id", "name", "localized_name", "attack_type")
ITEM_FIELDS = ("id", "cost", "behavior", "target_team", "dmg_type", "dispellable", "bkbpierce")
ABILITY_FIELDS = ("behavior", "target_team", "dmg_type", "dispellable", "bkbpierce")


def _load(build_dir: Path, name: str):
    return json.loads((build_dir / f"{name}.json").read_text(encoding="utf-8"))


def build(package_dir: Path) -> dict:
    build_dir = package_dir / "build"
    version = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))["version"]

    heroes = {
        hero_id: {field: hero[field] for field in HERO_FIELDS if field in hero}
        for hero_id, hero in _load(build_dir, "heroes").items()
    }

    items = {}
    for name, item in _load(build_dir, "items").items():
        trimmed = {field: item[field] for field in ITEM_FIELDS if field in item}
        abilities = item.get("abilities")
        if isinstance(abilities, list):
            trimmed["abilities"] = [{"type": ability.get("type")} for ability in abilities if isinstance(ability, dict)]
        items[name] = trimmed

    abilities = {}
    for name, ability in _load(build_dir, "abilities").items():
        trimmed = {field: ability[field] for field in ABILITY_FIELDS if field in ability}
        if trimmed:
            abilities[name] = trimmed

    return {
        "version": version,
        "heroes": heroes,
        "item_ids": _load(build_dir, "item_ids"),
        "items": items,
        "ability_ids": _load(build_dir, "ability_ids"),
        "abilities": abilities,
        "xp_level": _load(build_dir, "xp_level"),
        "player_colors": _load(build_dir, "player_colors"),
        "lobby_type": {key: {"name": value.get("name")} for key, value in _load(build_dir, "lobby_type").items()},
        "game_mode": {key: {"name": value.get("name")} for key, value in _load(build_dir, "game_mode").items()},
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    package_dir = Path(argv[1])
    output = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUTPUT
    snapshot = build(package_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"dotaconstants {snapshot['version']} -> {output} ({output.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
