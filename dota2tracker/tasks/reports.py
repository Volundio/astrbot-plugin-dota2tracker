"""Daily and legacy weekly reports (port of ``tasks/daily-report.task.ts``)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ..application.binding import BindingService
from ..application.models import OutgoingMessage
from ..config import PluginConfig
from ..constants import PLUGIN_NAME
from ..data.database import Database
from ..data.static import DotaConstants
from ..domain.report import ReportTranslator, build_legacy_report, format_daily_report_bundles
from ..errors import ErrorReporter
from ..i18n.service import I18nService
from ..presentation.assets import ImageAssets
from ..presentation.images import ImageFactory
from ..utils.timefmt import local_now, minus_days
from .ports import MemberProfiles, MessageSender

__all__ = ["ReportTask"]


class ReportTask:
    def __init__(
        self,
        *,
        config: PluginConfig,
        db: Database,
        bindings: BindingService,
        stratz,
        constants: DotaConstants,
        images: ImageFactory,
        assets: ImageAssets,
        sender: MessageSender,
        profiles: MemberProfiles | None,
        i18n: I18nService,
        reporter: ErrorReporter,
        logger: logging.Logger,
        now: Callable[[], datetime] = local_now,
    ) -> None:
        self._config = config
        self._db = db
        self._bindings = bindings
        self._stratz = stratz
        self._constants = constants
        self._images = images
        self._assets = assets
        self._sender = sender
        self._profiles = profiles
        self._i18n = i18n
        self._reporter = reporter
        self._logger = logger
        self._now = now
        self.last_daily: float | None = None
        self.last_weekly: float | None = None

    async def _locale(self, platform: str, channel_id: str) -> str:
        return self._i18n.resolve_locale(await self._bindings.channel_locale(platform, channel_id))

    async def _umo(self, platform: str, channel_id: str) -> str | None:
        channel = await self._bindings.channel_for(platform, channel_id)
        return channel.umo if channel else None

    def _log_sent(self, title: str, platform: str, channel_id: str) -> None:
        self._logger.info(
            self._i18n.gt(
                "dota2tracker.logger.report_sent", {"title": title, "guildId": channel_id, "platform": platform}
            )
        )

    # ---------------------------------------------------------------- daily
    async def build_daily_bundles(self, days: int = 1) -> list[dict[str, Any]]:
        """``generateDailyReportBundles``: report data per subscribed channel."""
        now = self._now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target = minus_days(today, days)
        data_start = minus_days(target, days)
        users = await self._bindings.active_players()
        if not users:
            return []
        steam_ids = list(dict.fromkeys(u.steam_id for u in users))
        data = await self._stratz.query_players_matches_for_daily(steam_ids, int(data_start.timestamp()))
        match_ids = list(
            dict.fromkeys(
                int(m["id"])
                for p in (data or {}).get("players") or []
                if p
                for m in p.get("matches") or []
                if m.get("id")
            )
        )
        extensions = await self._db.get_match_extensions(match_ids)
        translators: dict[str, ReportTranslator] = {}
        for user in users:
            key = f"{user.platform}:{user.channel_id}"
            if key in translators:
                continue
            locale = await self._locale(user.platform, user.channel_id)
            translators[key] = ReportTranslator(
                t=lambda k, p=None, _l=locale: self._i18n.text(_l, k, p),
                locale=locale,
                hero_name=lambda hero_id, _l=locale: (
                    self._i18n.t(_l, f"dota2tracker.template.hero_names.{hero_id}") or ""
                ),
            )
        hero_image = lambda name: self._assets.url(name, "heroes")  # noqa: E731
        return format_daily_report_bundles(
            data or {},
            users,
            extensions,
            self._constants,
            target,
            translators,
            hero_image,
            hero_image,
            PLUGIN_NAME,
            end_date=today,
            on_error=lambda key, error: self._reporter.report(error, f"daily-report/{key}"),
        )

    async def run_daily(self) -> None:
        self.last_daily = self._now().timestamp()
        try:
            bundles = await self.build_daily_bundles()
        except Exception as error:
            self._reporter.report(error, "daily-report")
            return
        for bundle in bundles:
            platform, channel_id = bundle["platform"], bundle["channelId"]
            try:
                umo = await self._umo(platform, channel_id)
                if umo is None:
                    continue
                locale = await self._locale(platform, channel_id)
                image = await self._images.daily(bundle["report"], locale)
                await self._sender.send(umo, OutgoingMessage.of(image))
                self._log_sent("daily", platform, channel_id)
            except Exception as error:
                self._reporter.report(error, f"daily-report/{platform}:{channel_id}")

    # --------------------------------------------------------------- weekly
    async def run_weekly(self) -> None:
        self.last_weekly = self._now().timestamp()
        try:
            since = int((self._now() - timedelta(weeks=1)).timestamp())
            await self.report_legacy(
                since, "dota2tracker.template.last_weeks_summary", self._config.weekly_report_show_combi
            )
        except Exception as error:
            self._reporter.report(error, "weekly-report")

    async def report_legacy(self, since: int, title_key: str, show_combi: bool) -> None:
        channels = await self._bindings.subscribed_channels()
        channel_keys = {c.key for c in channels}
        bindings = [p for p in await self._db.list_players_in_subscribed_channels() if p.channel_key in channel_keys]
        steam_ids = list(dict.fromkeys(p.steam_id for p in bindings))
        if not steam_ids:
            return
        data = await self._stratz.query_players_matches_for_daily_legacy(steam_ids, since)
        players_data = (data or {}).get("players") or []
        active_ids = {(p.get("steamAccount") or {}).get("id") for p in players_data if p.get("matches")}
        names: dict[int, str] = {}
        for binding in bindings:
            if binding.steam_id not in active_ids:
                continue
            if binding.nickname:
                names[binding.id] = binding.nickname
                continue
            profile = None
            if self._profiles is not None:
                try:
                    profile = await self._profiles.member_profile(binding.platform, binding.channel_id, binding.user_id)
                except Exception as error:
                    self._logger.warning("%s %s", self._i18n.gt("dota2tracker.logger.fetch_guilds_failed"), error)
            if profile and profile.get("nick"):
                names[binding.id] = str(profile["nick"])

        entries = build_legacy_report(
            channels,
            bindings,
            players_data,
            names,
            on_error=lambda key, error: self._reporter.report(error, f"weekly-report/{key}"),
        )
        for entry in entries:
            channel = entry["channel"]
            try:
                locale = await self._locale(channel.platform, channel.channel_id)
                title = self._i18n.text(locale, title_key)
                image = await self._images.daily_legacy(
                    title, entry["players"], entry["combinations"], show_combi, locale
                )
                await self._sender.send(channel.umo, OutgoingMessage.of(image))
                self._log_sent(title, channel.platform, channel.channel_id)
            except Exception as error:
                self._reporter.report(error, f"weekly-report/{channel.key}")
