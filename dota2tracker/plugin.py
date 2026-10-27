"""Service container: builds every service for one plugin lifetime.

Nothing here imports AstrBot. ``main.py`` supplies the platform adapters
(message sender, OneBot member access, ``html_render``) and owns the
lifecycle: :meth:`Services.start` on ``on_astrbot_loaded`` and
:meth:`Services.close` in ``Star.terminate``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .application.binding import BindingService, MemberProvider
from .application.library import HeroService, ItemService, RecommendationService
from .application.match import MatchService
from .application.players import PlayerService
from .commands.handlers import CommandHandlers
from .config import ConfigIssue, PluginConfig
from .constants import PLUGIN_VERSION
from .data.apis import OpenDotaAPI, ValveAPI
from .data.cache import CacheService
from .data.database import Database
from .data.static import DotaConstants, FileService, StaticDataService
from .data.stratz import StratzAPI
from .errors import ErrorReporter
from .i18n.service import I18nService
from .presentation.assets import ImageAssets
from .presentation.images import ImageFactory
from .presentation.messages import MessageBuilder
from .presentation.renderer import Renderer
from .tasks.polling import ParsePollingTask
from .tasks.ports import MemberProfiles, MessageSender
from .tasks.reports import ReportTask
from .tasks.scheduler import TaskScheduler
from .tasks.watcher import MatchWatcherTask

__all__ = ["Platform", "Services"]

HtmlRender = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[str]]


@dataclass
class Platform:
    """Adapters provided by the AstrBot layer."""

    html_render: HtmlRender | None
    sender: MessageSender
    members: MemberProvider | None = None
    profiles: MemberProfiles | None = None
    #: ``True`` when at least one supported (OneBot) platform instance is running
    onebot_available: Callable[[], bool] = lambda: False
    #: platform adapter types currently running (e.g. ``["aiocqhttp", "telegram"]``)
    running_platforms: Callable[[], list[str]] = list


@dataclass
class Services:
    config: PluginConfig
    issues: list[ConfigIssue]
    data_dir: Path
    logger: logging.Logger
    platform: Platform
    db: Database = field(init=False)
    started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        config, logger = self.config, self.logger
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.constants = DotaConstants.load(self.data_dir / "dotaconstants.json", logger)
        self.i18n = I18nService(
            default_locale=config.default_locale,
            use_hero_nicknames=config.use_hero_nicknames,
            hero_ids=self.constants.hero_ids,
            logger=logger,
        )
        secrets = tuple(s for s in (config.stratz_api_token, config.opendota_api_key, config.proxy_address) if s)
        self.reporter = ErrorReporter(
            logger,
            translate=self.i18n.gt,
            suppress_network_errors=config.suppress_api_network_errors,
            secrets=secrets,
        )
        self.db = Database(self.data_dir / "dota2tracker.sqlite3", logger)
        self.cache = CacheService(self.db, PLUGIN_VERSION)
        self.files = FileService(self.data_dir, logger)
        try:
            self._build_clients(config.proxy_address or None)
        except ValueError:
            # httpx rejected the proxy: keep the plugin (and its WebUI) usable without it.
            self.issues.append(
                ConfigIssue("error", "proxy_address", "rejected", "代理地址无法使用，已忽略代理，直接连接。")
            )
            self._build_clients(None)
        self.static_data = StaticDataService(
            self.valve, self.cache, self.files, lambda: self.config.patch_notes_retrieval_depth
        )
        self.assets = ImageAssets()
        self.renderer = Renderer(
            i18n=self.i18n,
            assets=self.assets,
            html_render=self.platform.html_render,
            options=self._render_options,
            logger=logger,
        )
        self.images = ImageFactory(self.renderer, self.i18n, self.constants, self.assets)
        self.messages = MessageBuilder(self.i18n, config.url_in_message_type)

        self.bindings = BindingService(self.db, self.platform.members, logger)
        self.matches = MatchService(
            stratz=self.stratz,
            opendota=self.opendota,
            cache=self.cache,
            db=self.db,
            constants=self.constants,
            reporter=self.reporter,
            logger=logger,
        )
        self.players = PlayerService(
            stratz=self.stratz,
            constants=self.constants,
            i18n=self.i18n,
            logger=logger,
            estimate_rank=config.player_rank_estimate,
        )
        self.heroes = HeroService(
            valve=self.valve,
            stratz=self.stratz,
            static_data=self.static_data,
            cache=self.cache,
            constants=self.constants,
            i18n=self.i18n,
        )
        self.items = ItemService(
            valve=self.valve,
            static_data=self.static_data,
            cache=self.cache,
            i18n=self.i18n,
            custom_aliases=config.custom_item_alias,
        )
        self.recommendations = RecommendationService(stratz=self.stratz, heroes=self.heroes)

        self.polling = ParsePollingTask(
            config=config,
            db=self.db,
            cache=self.cache,
            matches=self.matches,
            stratz=self.stratz,
            opendota=self.opendota,
            images=self.images,
            messages=self.messages,
            sender=self.platform.sender,
            i18n=self.i18n,
            reporter=self.reporter,
            logger=logger,
        )
        self.watcher = MatchWatcherTask(
            config=config,
            db=self.db,
            cache=self.cache,
            bindings=self.bindings,
            stratz=self.stratz,
            polling=self.polling,
            images=self.images,
            messages=self.messages,
            sender=self.platform.sender,
            profiles=self.platform.profiles,
            i18n=self.i18n,
            reporter=self.reporter,
            logger=logger,
        )
        self.reports = ReportTask(
            config=config,
            db=self.db,
            bindings=self.bindings,
            stratz=self.stratz,
            constants=self.constants,
            images=self.images,
            assets=self.assets,
            sender=self.platform.sender,
            profiles=self.platform.profiles,
            i18n=self.i18n,
            reporter=self.reporter,
            logger=logger,
        )
        self.scheduler = TaskScheduler(
            config,
            every_minute=[self._refresh_members, self._purge_expired, self.watcher.discovery, self.polling.polling],
            daily=self.reports.run_daily,
            weekly=self.reports.run_weekly,
            logger=logger,
        )
        self.commands = CommandHandlers(
            config=config,
            i18n=self.i18n,
            bindings=self.bindings,
            matches=self.matches,
            players=self.players,
            heroes=self.heroes,
            items=self.items,
            recommendations=self.recommendations,
            images=self.images,
            messages=self.messages,
            pending=self.polling,
            reporter=self.reporter,
        )
        self._member_refresh_at = 0.0
        self._purged_at = 0.0

    def _build_clients(self, proxy: str | None) -> None:
        config, logger = self.config, self.logger
        self.stratz = StratzAPI(config.stratz_api_token, proxy=proxy, logger=logger, translate=self.i18n.gt)
        self.valve = ValveAPI(proxy=proxy, language_of=I18nService.valve_language)
        self.opendota: OpenDotaAPI | None = None
        if config.enable_opendota_fallback:
            self.opendota = OpenDotaAPI(
                api_key=config.opendota_api_key,
                proxy=proxy,
                force_ipv4=config.opendota_ip_stack == "ipv4",
                record_usage=self.cache.add_opendota_request_log,
                logger=logger,
            )

    def _render_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "type": self.config.render_image_type,
            "device_scale_factor_level": self.config.render_scale,
        }
        if self.config.render_image_type == "jpeg":
            options["quality"] = self.config.render_jpeg_quality
        return options

    async def _refresh_members(self) -> None:
        """Refresh OneBot member lists on the first tick and then hourly (original: hourly cron)."""
        loop = asyncio.get_running_loop()
        if self._member_refresh_at and loop.time() - self._member_refresh_at < 3600:
            return
        self._member_refresh_at = loop.time()
        await self.bindings.refresh_all_member_caches()

    async def _purge_expired(self) -> None:
        """Delete expired cache rows and sent markers hourly (reads already ignore them)."""
        loop = asyncio.get_running_loop()
        if loop.time() - self._purged_at < 3600:
            return
        self._purged_at = loop.time()
        removed = await self.cache.purge_expired()
        if any(removed.values()):
            self.logger.debug("dota2tracker: purged expired rows %s", removed)

    @property
    def can_track(self) -> bool:
        return self.config.has_stratz_token

    async def open(self) -> None:
        """Open storage; safe to call before the platform is ready."""
        if not self.db.is_open:
            await self.db.open()
            await self.cache.purge_expired()
            self._purged_at = asyncio.get_running_loop().time()
            await self.polling.restore()

    async def start(self) -> None:
        """Start background work (``on_astrbot_loaded``)."""
        await self.open()
        if self.started:
            return
        self.started = True
        if self.can_track:
            self.scheduler.start()
        else:
            self.logger.warning("dota2tracker: STRATZ token missing, background tracking disabled")

    async def close(self) -> None:
        """Cancel tasks and close clients/storage (``Star.terminate``)."""
        await self.scheduler.stop()
        await self.polling.stop()
        await self.bindings.stop()
        for client in (self.stratz, self.valve, self.opendota):
            if client is not None:
                try:
                    await client.aclose()
                except Exception as error:
                    self.logger.debug("dota2tracker: closing client failed: %s", error)
        await self.db.close()
        self.started = False
