"""Convert the archived Koishi locale files into the runtime JSON locales.

The archived project (``old/src/locales``) stores runtime texts in three YAML
files per language plus a constants JSON file. The AstrBot port keeps the same
nested key paths (``dota2tracker.*`` / ``commands.*``) so that translation keys
stored in historical data (e.g. match titles) stay valid.

Usage::

    python scripts/convert_locales.py

The script is idempotent: it rewrites ``dota2tracker/locales/*.json`` from the
archived sources every time it runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "old" / "src" / "locales"
TARGET_DIR = ROOT / "dota2tracker" / "locales"
LANGUAGES = ("zh-CN", "en-US")
MESSAGE_FILES = ("{lang}.yml", "{lang}.command.yml", "{lang}.template.yml")


def deep_merge(target: dict, source: dict) -> dict:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def normalize(value):
    """Koishi stores every leaf as text; YAML may yield ints/bools."""
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if not isinstance(value, str) else value


def main() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for lang in LANGUAGES:
        messages: dict = {}
        for pattern in MESSAGE_FILES:
            path = SOURCE_DIR / pattern.format(lang=lang)
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            deep_merge(messages, normalize(data))
        (TARGET_DIR / f"{lang}.json").write_text(
            json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        constants = json.loads((SOURCE_DIR / f"{lang}.constants.json").read_text(encoding="utf-8"))
        (TARGET_DIR / f"{lang}.constants.json").write_text(
            json.dumps(constants, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"converted {lang}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
