"""Static Dota 2 data: bundled ``dotaconstants`` snapshot, patch-note files."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..constants import PACKAGE_DIR

__all__ = ["DotaConstants", "FileService", "StaticDataService"]

BUNDLED_CONSTANTS = PACKAGE_DIR / "data" / "dotaconstants.json"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version or "0")[:3]) or (0,)


class DotaConstants:
    """Read-only access to the trimmed ``dotaconstants`` data."""

    def __init__(self, data: dict[str, Any], source: str = "bundled") -> None:
        self.source = source
        self.version: str = str(data.get("version", "unknown"))
        self.heroes: dict[int, dict[str, Any]] = _int_keys(data.get("heroes"))
        self.item_ids: dict[int, str] = _int_keys(data.get("item_ids"))
        self.items: dict[str, dict[str, Any]] = dict(data.get("items") or {})
        # Some ability_ids keys are combined ("3060,1617"); JS lookups by number never hit them.
        self.ability_ids: dict[int, str] = _int_keys(data.get("ability_ids"))
        self.abilities: dict[str, dict[str, Any]] = dict(data.get("abilities") or {})
        self.xp_level: list[int] = list(data.get("xp_level") or [])
        self.player_colors: dict[int, str] = _int_keys(data.get("player_colors"))
        self.lobby_type: dict[int, dict[str, Any]] = _int_keys(data.get("lobby_type"))
        self.game_mode: dict[int, dict[str, Any]] = _int_keys(data.get("game_mode"))

    @classmethod
    def load(cls, overlay: Path | None = None, logger: logging.Logger | None = None) -> DotaConstants:
        bundled = json.loads(BUNDLED_CONSTANTS.read_text(encoding="utf-8"))
        if overlay is not None and overlay.is_file():
            try:
                data = json.loads(overlay.read_text(encoding="utf-8"))
                if _version_tuple(str(data.get("version"))) >= _version_tuple(str(bundled.get("version"))):
                    return cls(data, source="downloaded")
            except (OSError, json.JSONDecodeError) as error:
                (logger or logging.getLogger(__name__)).warning("Ignoring invalid dotaconstants overlay: %s", error)
        return cls(bundled)

    def hero_ids(self) -> list[int]:
        return sorted(self.heroes)

    def hero_name(self, hero_id: Any) -> str | None:
        hero = self.heroes.get(_int(hero_id))
        return hero.get("name") if hero else None

    def hero_short_name(self, hero_id: Any) -> str | None:
        name = self.hero_name(hero_id)
        match = re.match(r"^npc_dota_hero_(.+)$", name or "")
        return match.group(1) if match else None

    def item_name(self, item_id: Any) -> str | None:
        return self.item_ids.get(_int(item_id))

    def ability_name(self, ability_id: Any) -> str | None:
        return self.ability_ids.get(_int(ability_id))

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "heroes": len(self.heroes),
            "items": len(self.items),
            "abilities": len(self.abilities),
        }


def _int_keys(mapping: Any) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for key, value in (mapping or {}).items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue
    return result


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


class FileService:
    """JSON files inside the plugin data directory (port of ``data/file.ts``)."""

    def __init__(self, base_dir: Path, logger: logging.Logger | None = None) -> None:
        self.base_dir = base_dir
        self._logger = logger or logging.getLogger(__name__)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _target(self, sub_dir: str, filename: str) -> Path:
        parts = [p for p in sub_dir.split("/") if p]
        if not all(_SAFE_NAME.match(p) and p not in (".", "..") for p in parts) or not _SAFE_NAME.match(filename):
            raise ValueError(f"unsafe file path: {sub_dir}/{filename}")
        return self.base_dir.joinpath(*parts, filename)

    async def read_json(self, sub_dir: str, filename: str) -> Any:
        target = self._target(sub_dir, filename)
        if not target.is_file():
            return None
        try:
            text = await asyncio.to_thread(target.read_text, encoding="utf-8")
            return json.loads(text)
        except (OSError, json.JSONDecodeError) as error:
            self._logger.error("Failed to read JSON file from %s: %s", target, error)
            return None

    async def write_json(self, sub_dir: str, filename: str, data: Any) -> bool:
        target = self._target(sub_dir, filename)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            await asyncio.to_thread(target.write_text, text, encoding="utf-8")
            return True
        except OSError as error:
            self._logger.error("Failed to write JSON file to %s: %s", target, error)
            return False

    def count_files(self, sub_dir: str) -> int:
        directory = self.base_dir.joinpath(*[p for p in sub_dir.split("/") if p])
        return sum(1 for _ in directory.glob("*.json")) if directory.is_dir() else 0


class StaticDataService:
    """Patch notes for the hero/item templates (port of ``data/static-data.ts``).

    The newest patch is only cached for 6 hours; older patches are immutable and
    persisted to ``raw_data/patch_notes``. Concurrent requests for the same
    language share a single download.
    """

    def __init__(self, valve, cache, files: FileService, depth: Callable[[], int]) -> None:
        self._valve = valve
        self._cache = cache
        self._files = files
        self._depth = depth
        self._in_flight: dict[str, asyncio.Future] = {}

    async def get_patch_notes(
        self, locale: str, on_downloading_stale_patch: Callable[[], Awaitable[None] | None] | None = None
    ):
        existing = self._in_flight.get(locale)
        if existing is not None:
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._in_flight[locale] = future
        try:
            result = await self._get_patch_notes(locale, on_downloading_stale_patch)
            future.set_result(result)
            return result
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as error:
            future.set_exception(error)
            future.exception()  # mark as retrieved when nobody else is waiting
            raise
        finally:
            self._in_flight.pop(locale, None)

    async def _get_patch_notes(self, locale: str, on_downloading_stale_patch) -> list[dict[str, Any]]:
        patches = await self._valve.query_patch_list()
        if not patches:
            return []
        latest = patches[-1]
        if not re.match(r"^(\d+\.\d+)", str(latest.get("patch_name", ""))):
            return []

        targets: list[dict[str, Any]] = []
        majors: set[str] = set()
        depth = self._depth()
        for patch in reversed(patches):
            match = re.match(r"^(\d+\.\d+)", str(patch.get("patch_name", "")))
            if not match:
                continue
            majors.add(match.group(1))
            if len(majors) > depth:
                break
            targets.append(patch)
        targets.reverse()

        result: list[dict[str, Any]] = []
        latest_number = latest.get("patch_number")
        fired = False
        for patch in targets:
            number = patch.get("patch_number")
            filename = f"{number}-{locale}.json"
            if number == latest_number:
                notes = await self._cache.get_patch_notes(filename)
                if not notes:
                    notes = await self._valve.query_patch_notes(number, locale)
                    if notes and notes.get("success"):
                        await self._cache.set_patch_notes(filename, notes)
            else:
                notes = await self._files.read_json("raw_data/patch_notes", filename)
                if not notes:
                    if not fired and on_downloading_stale_patch is not None:
                        fired = True
                        outcome = on_downloading_stale_patch()
                        if asyncio.iscoroutine(outcome):
                            await outcome
                    notes = await self._valve.query_patch_notes(number, locale)
                    if notes and notes.get("success"):
                        await self._files.write_json("raw_data/patch_notes", filename, notes)
            if notes:
                result.append(notes)
        return result
