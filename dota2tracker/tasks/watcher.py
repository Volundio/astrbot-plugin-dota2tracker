"""Match discovery and rank broadcasts (port of ``tasks/match-watcher.task.ts``)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..application.binding import BindingService
from ..application.models import OutgoingMessage, PlayerContext, Subscriber
from ..config import PluginConfig
from ..data.cache import CacheService
from ..data.database import Database, PlayerRecord
from ..domain.rank import decide_rank_change
from ..errors import ErrorReporter
from ..i18n.service import I18nService
from ..presentation.images import ImageFactory
from ..presentation.messages import MessageBuilder
from ..utils.timefmt import local_now, luxon_format
from .polling import ParsePollingTask
from .ports import MemberProfiles, MessageSender

__all__ = ["MatchWatcherTask"]

ONE_DAY = 24 * 60 * 60


def _account_id(player: dict[str, Any]) -> Any:
    return (player.get("steamAccount") or {}).get("id")


class MatchWatcherTask:
    def __init__(
        self,
        *,
        config: PluginConfig,
        db: Database,
        cache: CacheService,
        bindings: BindingService,
        stratz,
        polling: ParsePollingTask,
        images: ImageFactory,
        messages: MessageBuilder,
        sender: MessageSender,
        profiles: MemberProfiles | None,
        i18n: I18nService,
        reporter: ErrorReporter,
        logger: logging.Logger,
        clock: Callable[[], float] = time.time,
        now: Callable[[], datetime] = local_now,
    ) -> None:
        self._config = config
        self._db = db
        self._cache = cache
        self._bindings = bindings
        self._stratz = stratz
        self._polling = polling
        self._images = images
        self._messages = messages
        self._sender = sender
        self._profiles = profiles
        self._i18n = i18n
        self._reporter = reporter
        self._logger = logger
        self._clock = clock
        self._now = now
        self.last_discovery: float | None = None

    async def discovery(self) -> None:
        self.last_discovery = self._clock()
        try:
            active = await self._bindings.active_players()
            if not active:
                return
            steam_ids = list(dict.fromkeys(p.steam_id for p in active))
            data = await self._stratz.query_players_last_match_rank_info(steam_ids)
            players_data = (data or {}).get("players") or []
            await self._discover_new_matches(players_data, active)
            if self._config.rank_broad_switch:
                await self._detect_rank_changes(players_data, active)
        except Exception as error:
            self._reporter.report(error, "match-watcher")

    async def _locale(self, platform: str, channel_id: str) -> str:
        return self._i18n.resolve_locale(await self._bindings.channel_locale(platform, channel_id))

    # ------------------------------------------------------------ discovery
    async def _discover_new_matches(self, players_data: list[dict[str, Any]], active: list[PlayerRecord]) -> None:
        sent = await self._cache.sent_match_ids()
        now = self._clock()
        seen: set[Any] = set()
        last_matches = []
        for player in players_data:
            match = (player.get("matches") or [None])[0]
            if not match or not match.get("id") or match["id"] in seen:
                continue
            seen.add(match["id"])
            # Skip matches older than one day so long-idle players don't resurface ancient reports.
            if (match.get("startDateTime") or 0) <= now - ONE_DAY:
                continue
            if self._polling.is_pending(match["id"]) or match["id"] in sent:
                continue
            last_matches.append(match)

        channels = {c.key: c for c in await self._bindings.subscribed_channels()}
        for match in last_matches:
            ids_in_match = {_account_id(p) for p in match.get("players") or []}
            relevant = [p for p in active if p.steam_id in ids_in_match]
            if not relevant:
                continue
            by_channel: dict[str, list[PlayerRecord]] = {}
            for player in relevant:
                by_channel.setdefault(player.channel_key, []).append(player)

            subscribers: list[Subscriber] = []
            for key, players in by_channel.items():
                first = players[0]
                channel = channels.get(key)
                subscribers.append(
                    Subscriber(
                        source="AUTOMATIC",
                        type="CHANNEL",
                        umo=channel.umo if channel else first.umo,
                        platform=first.platform,
                        channel_id=first.channel_id,
                        language_tag=await self._locale(first.platform, first.channel_id),
                        relevant_players=[PlayerContext(p.steam_id, p.nickname) for p in players],
                    )
                )
            if subscribers:
                await self._polling.add(int(match["id"]), subscribers)
                self._logger.info(
                    self._i18n.gt(
                        "dota2tracker.logger.match_tracked",
                        {
                            "match": match,
                            "messageToLogger": [
                                {
                                    "platform": s.platform,
                                    "guildId": s.channel_id,
                                    "players": [p.to_dict() for p in s.relevant_players],
                                }
                                for s in subscribers
                            ],
                        },
                    )
                )

    # ---------------------------------------------------------------- ranks
    async def _profile(self, player: PlayerRecord) -> dict[str, Any] | None:
        if self._profiles is None:
            return None
        try:
            return await self._profiles.member_profile(player.platform, player.channel_id, player.user_id)
        except Exception as error:
            self._logger.warning("%s %s", self._i18n.gt("dota2tracker.logger.fetch_guilds_failed"), error)
            return None

    async def _detect_rank_changes(self, players_data: list[dict[str, Any]], active: list[PlayerRecord]) -> None:
        latest: dict[Any, dict[str, Any]] = {}
        accounts: dict[Any, dict[str, Any]] = {}
        for player in players_data:
            account = player.get("steamAccount") or {}
            latest[account.get("id")] = {
                "rank": account.get("seasonRank"),
                "leader": account.get("seasonLeaderboardRank"),
            }
            accounts[account.get("id")] = account

        for player in active:
            try:
                await self._check_player_rank(player, latest.get(player.steam_id), accounts.get(player.steam_id) or {})
            except Exception as error:
                self._reporter.report(error, f"rank/{player.channel_key}")

    async def _check_player_rank(
        self, player: PlayerRecord, latest: dict[str, Any] | None, account: dict[str, Any]
    ) -> None:
        decision = decide_rank_change(
            player.rank,
            latest,
            broadcast_star=self._config.rank_broad_star,
            broadcast_leader=self._config.rank_broad_leader,
        )
        if decision.broadcast is not None and latest is not None:
            change = decision.broadcast
            profile = await self._profile(player)
            name = player.nickname or (profile or {}).get("nick") or account.get("name") or str(player.steam_id)
            locale = await self._locale(player.platform, player.channel_id)
            if self._config.rank_broad_fun:
                date_text = luxon_format(
                    self._now(), "yyyy/MM/dd HH时mm分" if locale == "zh-CN" else "yyyy/MM/dd HH:mm"
                )
                image = await self._images.rank_fun(
                    name,
                    (profile or {}).get("avatar") or account.get("avatar") or "",
                    change.rising,
                    change.previous,
                    change.current,
                    date_text,
                    locale,
                )
                message = OutgoingMessage.of(image)
            else:
                message = OutgoingMessage.of(
                    self._messages.rank_changed_message(locale, name, change.previous, change.current)
                )
            channel = await self._bindings.channel_for(player.platform, player.channel_id)
            await self._sender.send(channel.umo if channel else player.umo, message)
            self._logger.info(
                self._i18n.gt(
                    "dota2tracker.logger.rank_sent",
                    {
                        "platform": player.platform,
                        "guildId": player.channel_id,
                        "player": {"nickName": player.nickname, "steamId": player.steam_id},
                    },
                )
            )
        if decision.store and latest is not None:
            await self._db.set_player_rank(player.id, latest)
            player.rank = dict(latest)
