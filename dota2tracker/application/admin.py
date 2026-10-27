"""Management use cases behind the WebUI views (status, subscriptions, previews, diagnostics).

Every method returns plain, redacted DTOs. Mutations go through the same
database operations and validation rules as the chat commands and are written
to the audit log with the dashboard user name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..commands.handlers import _valid_nickname
from ..constants import PLUGIN_NAME, PLUGIN_VERSION, SUPPORTED_LOCALES, UPSTREAM_REPOSITORY, VERIFIED_PLATFORM_TYPES
from ..data.cache import CLEARABLE_NAMESPACES
from ..presentation.previews import PREVIEWS, PreviewContext, build_preview
from ..presentation.renderer import TEMPLATES
from .binding import STEAM_ID_PATTERN

__all__ = ["EXPORT_FORMAT", "AdminError", "AdminService"]

EXPORT_FORMAT = "astrbot_plugin_dota2tracker/bindings@1"
MAX_IMPORT_BYTES = 2 * 1024 * 1024
PREVIEW_VARIANTS: dict[str, tuple[str, ...]] = {
    "match_1": ("", "opendota"),
    "match_2": ("", "opendota"),
    "match_2+": ("", "opendota"),
    "player_1": ("", "anonymous", "hero"),
    "item": ("", "recipe"),
    "rank_fun": ("", "down"),
    "hero_of_the_day": ("", "lifetime", "anonymous"),
    "daily_legacy": ("", "hideCombi"),
}


def _text(value: Any) -> str | None:
    """Non-empty string field (numbers from hand-edited files are accepted)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _clean_channel(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    fields = {key: _text(row.get(key)) for key in ("umo", "platform", "platform_type", "channel_id")}
    if None in fields.values():
        return None
    locale = row.get("locale")
    name = row.get("name")
    return {
        **fields,
        "subscribed": bool(row.get("subscribed")),
        "locale": locale if locale in SUPPORTED_LOCALES else None,
        "name": name if isinstance(name, str) else "",
    }


def _clean_player(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    fields = {key: _text(row.get(key)) for key in ("umo", "platform", "channel_id", "user_id")}
    steam_id = row.get("steam_id")
    if None in fields.values() or isinstance(steam_id, bool) or not isinstance(steam_id, (str, int)):
        return None
    if not STEAM_ID_PATTERN.match(str(steam_id).strip()):
        return None
    nickname = row.get("nickname")
    nickname = nickname if isinstance(nickname, str) and _valid_nickname(nickname) else ""
    rank = row.get("rank")
    return {
        **fields,
        "steam_id": int(str(steam_id).strip()),
        "nickname": nickname,
        "rank": rank if isinstance(rank, dict) else {},
    }


class AdminError(Exception):
    """User-facing error (message is a WebUI i18n key or a short English text)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> int:
    return int(time.time())


class AdminService:
    def __init__(self, services: Any, api_checks: dict[str, Callable[[], Awaitable[Any]]] | None = None) -> None:
        self.s = services
        self._api_checks = api_checks or {}

    # ------------------------------------------------------------- overview
    async def status(self) -> dict[str, Any]:
        s = self.s
        counts = await s.db.counts()
        return {
            "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION, "upstream": UPSTREAM_REPOSITORY},
            "config": {
                "stratzToken": s.config.has_stratz_token,
                "opendota": s.config.enable_opendota_fallback,
                "rankBroadcast": s.config.rank_broad_switch,
                "dailyReport": s.config.daily_report_switch,
                "weeklyReport": s.config.weekly_report_switch,
                "issues": [i.to_dict() for i in s.issues],
            },
            "platforms": {
                "onebot": bool(s.platform.onebot_available()),
                "running": list(s.platform.running_platforms()),
            },
            "scheduler": s.scheduler.status(),
            "lastDiscovery": s.watcher.last_discovery,
            "lastPoll": s.polling.last_poll,
            "lastDaily": s.reports.last_daily,
            "lastWeekly": s.reports.last_weekly,
            "pending": s.polling.snapshot(),
            "openDotaCallsToday": await s.cache.today_opendota_request_count()
            if s.config.enable_opendota_fallback
            else None,
            "counts": counts,
            "members": s.bindings.member_cache_summary(),
            "renderer": s.renderer.status(),
            "constants": s.constants.summary(),
            "recentErrors": s.reporter.recent()[:10],
        }

    async def refresh(self, actor: str) -> dict[str, Any]:
        await self.s.bindings.refresh_all_member_caches()
        await self.s.db.add_audit(actor, "refresh")
        return await self.status()

    # --------------------------------------------------------- subscriptions
    async def subscriptions(self) -> dict[str, Any]:
        channels = await self.s.db.list_channels()
        players = await self.s.db.list_players()
        by_channel: dict[str, list[dict[str, Any]]] = {}
        for player in players:
            entry = player.to_dict()
            entry["inGroup"] = self.s.bindings.is_player_in_guild(player.platform, player.channel_id, player.user_id)
            by_channel.setdefault(player.channel_key, []).append(entry)
        result = []
        for channel in channels:
            data = channel.to_dict()
            data["supported"] = self.s.bindings.platform_supported(channel.platform_type)
            data["verified"] = channel.platform_type in VERIFIED_PLATFORM_TYPES
            data["players"] = by_channel.pop(channel.key, [])
            result.append(data)
        orphans = [p for group in by_channel.values() for p in group]
        return {"channels": result, "orphanPlayers": orphans, "locales": list(SUPPORTED_LOCALES)}

    async def set_subscribed(self, actor: str, channel_pk: int, subscribed: bool) -> dict[str, Any]:
        channel = await self.s.db.get_channel_by_id(channel_pk)
        if channel is None:
            raise AdminError("channel_not_found", 404)
        if subscribed:
            await self.s.db.subscribe_channel(channel.umo, channel.platform, channel.platform_type, channel.channel_id)
        else:
            await self.s.db.unsubscribe_channel(channel.platform, channel.channel_id)
        await self.s.db.add_audit(actor, "subscribe" if subscribed else "unsubscribe", {"channel": channel.key})
        return await self.subscriptions()

    async def set_locale(self, actor: str, channel_pk: int, locale: str | None) -> dict[str, Any]:
        if locale and locale not in SUPPORTED_LOCALES:
            raise AdminError("invalid_locale")
        if not await self.s.db.set_channel_locale(channel_pk, locale or None):
            raise AdminError("channel_not_found", 404)
        await self.s.db.add_audit(actor, "set_locale", {"channel": channel_pk, "locale": locale})
        return await self.subscriptions()

    async def rename(self, actor: str, player_pk: int, nickname: str) -> dict[str, Any]:
        nickname = nickname.strip()
        if not _valid_nickname(nickname):
            raise AdminError("invalid_nickname")
        player = await self.s.db.get_player_by_id(player_pk)
        if player is None:
            raise AdminError("player_not_found", 404)
        await self.s.bindings.rename(player, nickname)
        await self.s.db.add_audit(actor, "rename", {"player": player_pk, "steamId": player.steam_id})
        return await self.subscriptions()

    async def unbind(self, actor: str, player_pk: int) -> dict[str, Any]:
        player = await self.s.db.get_player_by_id(player_pk)
        if player is None:
            raise AdminError("player_not_found", 404)
        await self.s.db.delete_player(player_pk)
        await self.s.db.add_audit(actor, "unbind", {"player": player_pk, "steamId": player.steam_id})
        return await self.subscriptions()

    # ------------------------------------------------------- import / export
    async def export_data(self) -> dict[str, Any]:
        channels = await self.s.db.list_channels()
        players = await self.s.db.list_players()
        return {
            "format": EXPORT_FORMAT,
            "exportedAt": _now(),
            "pluginVersion": PLUGIN_VERSION,
            "channels": [
                {
                    k: c.to_dict()[k]
                    for k in ("umo", "platform", "platform_type", "channel_id", "subscribed", "locale", "name")
                }
                for c in channels
            ],
            "players": [
                {
                    k: p.to_dict()[k]
                    for k in ("umo", "platform", "channel_id", "user_id", "steam_id", "nickname", "rank")
                }
                for p in players
            ],
        }

    @staticmethod
    def parse_import(raw: bytes) -> dict[str, Any]:
        """Validate a file produced by :meth:`export_data`.

        Returns ``{channels, players, invalid}``: rows that pass the same
        rules as the chat commands (non-empty UMO/ids, SteamID ``^[0-9]{1,11}$``,
        20-unit nickname) in normalised form, and the number of rejected rows.
        Everything is validated before anything is written.
        """
        if len(raw) > MAX_IMPORT_BYTES:
            raise AdminError("import_too_large", 413)
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AdminError("import_invalid_json") from error
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
            raise AdminError("import_invalid_format")
        channels, players = data.get("channels"), data.get("players")
        if not isinstance(channels, list) or not isinstance(players, list):
            raise AdminError("import_invalid_format")
        clean_channels = [c for c in map(_clean_channel, channels) if c is not None]
        clean_players = [p for p in map(_clean_player, players) if p is not None]
        invalid = len(channels) - len(clean_channels) + len(players) - len(clean_players)
        return {"channels": clean_channels, "players": clean_players, "invalid": invalid}

    async def import_data(self, actor: str, raw: bytes, *, dry_run: bool) -> dict[str, Any]:
        """Restore an export in one transaction: all rows or none, existing data never overwritten."""
        payload = self.parse_import(raw)
        report: dict[str, Any] = await self.s.db.restore_bindings(
            payload["channels"], payload["players"], dry_run=dry_run
        )
        report["invalid"] = payload["invalid"]
        if not dry_run:
            await self.s.db.add_audit(actor, "import", dict(report))
        report["dryRun"] = dry_run
        return report

    # -------------------------------------------------------------- previews
    def templates(self) -> dict[str, Any]:
        return {
            "templates": [
                {
                    "name": name,
                    "kind": TEMPLATES[name].kind if name in TEMPLATES else "",
                    "variants": list(PREVIEW_VARIANTS.get(name, ("",))),
                }
                for name in PREVIEWS
            ],
            "locales": list(SUPPORTED_LOCALES),
            "renderer": self.s.renderer.status(),
        }

    async def preview(self, template: str, locale: str, variant: str) -> dict[str, Any]:
        if template not in PREVIEWS:
            raise AdminError("template_not_found", 404)
        if locale not in SUPPORTED_LOCALES:
            raise AdminError("invalid_locale")
        if variant not in PREVIEW_VARIANTS.get(template, ("",)):
            raise AdminError("invalid_variant")
        ctx = PreviewContext(self.s.constants, self.s.i18n, self.s.assets, locale, variant=variant)
        data = build_preview(template, ctx)
        image = await self.s.renderer.render_image(template, data, locale)
        return {"image": image.data_url(), "bytes": len(image.data), "mime": image.mime}

    def preview_html(self, template: str, locale: str, variant: str) -> str:
        if template not in PREVIEWS or locale not in SUPPORTED_LOCALES:
            raise AdminError("template_not_found", 404)
        ctx = PreviewContext(self.s.constants, self.s.i18n, self.s.assets, locale, variant=variant)
        return self.s.renderer.render_html(template, build_preview(template, ctx), locale)

    # ----------------------------------------------------------- diagnostics
    async def diagnostics(self) -> dict[str, Any]:
        s = self.s
        return {
            "config": s.config.redacted(),
            "issues": [i.to_dict() for i in s.issues],
            "cache": await self.cache_stats(),
            "counts": await s.db.counts(),
            "schemaVersion": await s.db.schema_version(),
            "recentErrors": s.reporter.recent(),
            "audit": [a.to_dict() for a in await s.db.list_audit(50)],
            "scheduler": s.scheduler.status(),
            "queues": {
                "stratz": s.stratz.queue.pending,
                "valve": s.valve.queue.pending,
                "opendota": s.opendota.queue.pending if s.opendota is not None else None,
            },
        }

    async def api_check(self, actor: str, target: str) -> dict[str, Any]:
        check = self._api_checks.get(target)
        if check is None:
            raise AdminError("unknown_target", 404)
        started = time.monotonic()
        try:
            await check()
            ok, message = True, ""
        except Exception as error:
            ok, message = False, f"{type(error).__name__}: {error}"
            self.s.reporter.report(error, f"api-check/{target}")
        await self.s.db.add_audit(actor, "api_check", {"target": target, "ok": ok})
        return {
            "target": target,
            "ok": ok,
            "ms": int((time.monotonic() - started) * 1000),
            "message": self.s.reporter.redact(message),
        }

    async def cache_stats(self) -> list[dict[str, Any]]:
        return [
            {**entry, "clearable": entry["namespace"] in CLEARABLE_NAMESPACES} for entry in await self.s.cache.stats()
        ]

    async def clear_cache(self, actor: str, namespace: str) -> dict[str, Any]:
        # Only re-fetchable data; bookkeeping such as the OpenDota daily call counter is not clearable.
        if namespace not in CLEARABLE_NAMESPACES and namespace != "*expired":
            raise AdminError("unknown_namespace", 404)
        if namespace == "*expired":
            removed = await self.s.cache.purge_expired()
        else:
            removed = {namespace: await self.s.cache.clear(namespace)}
        await self.s.db.add_audit(actor, "clear_cache", {"namespace": namespace, "removed": removed})
        return {"removed": removed, "cache": await self.cache_stats()}

    async def clear_errors(self, actor: str) -> None:
        self.s.reporter.clear()
        await self.s.db.add_audit(actor, "clear_errors")

    async def diagnostic_bundle(self) -> dict[str, Any]:
        return {
            "generatedAt": _now(),
            "status": await self.status(),
            "diagnostics": await self.diagnostics(),
        }
