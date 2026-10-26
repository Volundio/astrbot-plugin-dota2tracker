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
from ..presentation.previews import PREVIEWS, PreviewContext, build_preview
from ..presentation.renderer import TEMPLATES

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
        """Validate and normalise a file produced by :meth:`export_data` into ``{channels, players}``."""
        if len(raw) > MAX_IMPORT_BYTES:
            raise AdminError("import_too_large", 413)
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AdminError("import_invalid_json") from error
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
            raise AdminError("import_invalid_format")
        channels, players = data.get("channels") or [], data.get("players") or []
        clean_channels = [c for c in channels if isinstance(c, dict) and c.get("platform") and c.get("channel_id")]
        clean_players = []
        for p in players:
            if not isinstance(p, dict) or not p.get("platform") or not p.get("channel_id") or not p.get("user_id"):
                continue
            try:
                steam_id = int(p.get("steam_id"))
            except (TypeError, ValueError):
                continue
            nickname = str(p.get("nickname") or "")
            clean_players.append({**p, "steam_id": steam_id, "nickname": nickname if _valid_nickname(nickname) else ""})
        return {"channels": clean_channels, "players": clean_players}

    async def import_data(self, actor: str, raw: bytes, *, dry_run: bool) -> dict[str, Any]:
        """Idempotent restore of an export: existing bindings are kept, never overwritten."""
        payload = self.parse_import(raw)
        db = self.s.db
        report = {"channelsAdded": 0, "channelsSkipped": 0, "playersAdded": 0, "playersSkipped": 0, "dryRun": dry_run}
        for channel in payload["channels"]:
            existing = await db.get_channel(channel["platform"], channel["channel_id"])
            if existing is not None and (existing.subscribed or not channel.get("subscribed")):
                report["channelsSkipped"] += 1
                continue
            report["channelsAdded"] += 1
            if dry_run:
                continue
            if channel.get("subscribed", True):
                await db.subscribe_channel(
                    channel.get("umo") or (existing.umo if existing else ""),
                    channel["platform"],
                    channel.get("platform_type") or "aiocqhttp",
                    channel["channel_id"],
                    channel.get("name") or "",
                )
            stored = await db.get_channel(channel["platform"], channel["channel_id"])
            if stored is not None and channel.get("locale") in SUPPORTED_LOCALES:
                await db.set_channel_locale(stored.id, channel["locale"])
        for player in payload["players"]:
            if await db.get_player(player["platform"], player["channel_id"], player["user_id"]) is not None:
                report["playersSkipped"] += 1
                continue
            report["playersAdded"] += 1
            if dry_run:
                continue
            record = await db.bind_player(
                player.get("umo") or "",
                player["platform"],
                player["channel_id"],
                player["user_id"],
                player["steam_id"],
                player["nickname"],
            )
            if player.get("rank"):
                await db.set_player_rank(record.id, player["rank"])
        if not dry_run:
            await db.add_audit(actor, "import", {k: v for k, v in report.items() if k != "dryRun"})
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
            "cache": await s.cache.stats(),
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

    async def clear_cache(self, actor: str, namespace: str) -> dict[str, Any]:
        known = {entry["namespace"] for entry in await self.s.cache.stats()}
        if namespace not in known and namespace != "*expired":
            raise AdminError("unknown_namespace", 404)
        if namespace == "*expired":
            removed = await self.s.cache.purge_expired()
        else:
            removed = {namespace: await self.s.cache.clear(namespace)}
        await self.s.db.add_audit(actor, "clear_cache", {"namespace": namespace, "removed": removed})
        return {"removed": removed, "cache": await self.s.cache.stats()}

    async def clear_errors(self, actor: str) -> None:
        self.s.reporter.clear()
        await self.s.db.add_audit(actor, "clear_errors")

    async def diagnostic_bundle(self) -> dict[str, Any]:
        return {
            "generatedAt": _now(),
            "status": await self.status(),
            "diagnostics": await self.diagnostics(),
        }
