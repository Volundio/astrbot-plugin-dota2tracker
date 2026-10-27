"""Parse polling (port of ``tasks/parse-polling.task.ts``).

Target fixes over the original (DEVELOPMENT.md §9.5):

* pending matches are persisted in SQLite and every destination is marked
  ``done`` as soon as it has been handled, so a reload only resumes the
  destinations that are still waiting (at most the in-flight message can be
  repeated);
* a destination whose image could not be formatted/rendered stays pending and
  is retried on the next round, up to ``MAX_PUBLISH_ATTEMPTS`` rounds;
* the entry is finished (pending row deleted + sent marker written) in one
  transaction and only when no destination is left, so subscribers added
  while a publication is running are not lost;
* entries that still have no data ``PENDING_GRACE_SECONDS`` after the parse
  timeout are dropped instead of being polled forever.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..application.match import MatchResult, MatchService
from ..application.models import OutgoingMessage, Subscriber
from ..config import PluginConfig
from ..data.cache import CacheService
from ..data.database import Database
from ..errors import ErrorReporter
from ..i18n.service import I18nService
from ..presentation.images import ImageFactory
from ..presentation.messages import MessageBuilder
from .ports import MessageSender

__all__ = ["MAX_PUBLISH_ATTEMPTS", "PENDING_GRACE_SECONDS", "ParsePollingTask", "PendingEntry"]

#: Rounds a destination may fail to format/render before it is given up.
MAX_PUBLISH_ATTEMPTS = 3
#: How long past the parse timeout an entry without any data is kept.
PENDING_GRACE_SECONDS = 24 * 60 * 60


@dataclass
class PendingEntry:
    match_id: int
    request_time: float
    subscribers: list[Subscriber] = field(default_factory=list)
    #: failed publication rounds (in memory only; reset by a reload)
    attempts: int = 0
    #: last "still waiting" notice / OpenDota parse request (clock seconds)
    last_notice: float | None = None


class ParsePollingTask:
    def __init__(
        self,
        *,
        config: PluginConfig,
        db: Database,
        cache: CacheService,
        matches: MatchService,
        stratz,
        opendota,
        images: ImageFactory,
        messages: MessageBuilder,
        sender: MessageSender,
        i18n: I18nService,
        reporter: ErrorReporter,
        logger: logging.Logger,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._db = db
        self._cache = cache
        self._matches = matches
        self._stratz = stratz
        self._opendota = opendota
        self._images = images
        self._messages = messages
        self._sender = sender
        self._i18n = i18n
        self._reporter = reporter
        self._logger = logger
        self._clock = clock
        self._pending: dict[int, PendingEntry] = {}
        self._index = 0
        self._lock = asyncio.Lock()
        self._background: set[asyncio.Task[Any]] = set()
        self.last_poll: float | None = None

    # ---------------------------------------------------------------- state
    async def restore(self) -> None:
        """Reload persisted pending matches (called once on startup)."""
        for record in await self._db.list_pending_matches():
            self._pending[record.match_id] = PendingEntry(
                record.match_id,
                float(record.request_time),
                [Subscriber.from_dict(s) for s in record.subscribers if isinstance(s, dict)],
            )

    def is_pending(self, match_id: int) -> bool:
        return match_id in self._pending

    def snapshot(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [
            {
                "matchId": entry.match_id,
                "requestTime": int(entry.request_time),
                "waitingMinutes": math.floor((now - entry.request_time) / 60),
                "subscribers": [
                    {"source": s.source, "type": s.type, "platform": s.platform, "channelId": s.channel_id}
                    for s in entry.subscribers
                    if not s.done
                ],
            }
            for entry in self._pending.values()
        ]

    async def _persist(self, entry: PendingEntry) -> None:
        await self._db.save_pending_match(
            entry.match_id, int(entry.request_time), [s.to_dict() for s in entry.subscribers]
        )

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def stop(self) -> None:
        for task in list(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    # ------------------------------------------------------------------ add
    def automatic_targets(self, match_id: int) -> set[str]:
        """UMOs that already have an automatic broadcast for a pending match (published or not)."""
        entry = self._pending.get(match_id)
        if entry is None:
            return set()
        return {s.umo for s in entry.subscribers if s.source == "AUTOMATIC"}

    async def add(self, match_id: int, subscribers: list[Subscriber]) -> None:
        async with self._lock:
            is_new = match_id not in self._pending
            entry = self._pending.get(match_id) or PendingEntry(match_id, self._clock())
            known = {s.umo for s in entry.subscribers if s.source == "AUTOMATIC"}
            fresh = [s for s in subscribers if s.source != "AUTOMATIC" or s.umo not in known]
            if not fresh and not is_new:
                return
            entry.subscribers.extend(fresh)
            self._pending[match_id] = entry
            await self._persist(entry)
        if is_new:
            self._spawn(self._request_parse(match_id))

    async def _request_parse(self, match_id: int) -> None:
        try:
            value = await self._stratz.request_parse_match(match_id)
        except Exception as error:
            self._reporter.report(error, "parse-request")
            value = False
        key = "sent" if value else "failed"
        self._logger.info(self._i18n.gt(f"dota2tracker.logger.parse_request_{key}", {"matchId": match_id}))

    async def _request_opendota_parse(self, match_id: int) -> None:
        try:
            value = await self._opendota.request_parse_match(match_id)
        except Exception as error:
            self._reporter.report(error, "opendota-parse-request")
            value = False
        key = "sent" if value else "failed"
        self._logger.info(self._i18n.gt(f"dota2tracker.logger.opendota_parse_request_{key}", {"matchId": match_id}))

    # -------------------------------------------------------------- polling
    async def polling(self) -> None:
        """Check one pending match per tick (round robin, like the original)."""
        self.last_poll = self._clock()
        try:
            if not self._pending:
                return
            entries = list(self._pending.values())
            if self._index >= len(entries):
                self._index = 0
            entry = entries[self._index]
            self._index += 1
            await self._poll_entry(entry)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._reporter.report(error, "parse-polling")

    async def _poll_entry(self, entry: PendingEntry) -> None:
        timeout_seconds = self._config.data_parsing_timeout_minutes * 60
        age = self._clock() - entry.request_time
        need_to_wait = age < timeout_seconds
        result: MatchResult | None = None
        try:
            result = await self._matches.get_match_result(
                entry.match_id, wait_for_parse=need_to_wait, allow_fallback=self._config.enable_opendota_fallback
            )
        except Exception as error:
            self._reporter.report(error, "parse-polling")

        if result is not None and result.ready and result.match_data:
            await self._publish(entry, result.match_data)
            return
        if age >= timeout_seconds + PENDING_GRACE_SECONDS:
            self._logger.warning(
                self._i18n.gt(
                    "dota2tracker.logger.pending_expired",
                    {"matchId": entry.match_id, "hours": math.floor(age / 3600)},
                )
            )
            await self._finish(entry, force=True)
            return
        if result is None or result.status == "PENDING":
            # Every 5 minutes of waiting, measured by time rather than by "polled when the
            # waiting minutes are a multiple of 5": with N pending matches each one is only
            # polled every N minutes, so the modulo could never match (target fix).
            now = self._clock()
            waiting = math.floor(age / 60)
            if waiting >= 5 and (entry.last_notice is None or now - entry.last_notice >= 5 * 60 - 30):
                entry.last_notice = now
                self._logger.info(
                    self._i18n.gt("dota2tracker.logger.waiting_for_parse", {"matchId": entry.match_id, "time": waiting})
                )
                if self._config.enable_opendota_fallback and self._opendota is not None:
                    self._spawn(self._request_opendota_parse(entry.match_id))
        # NOT_FOUND keeps the entry until the timeout turns it into an unparsed
        # report (or the grace period above gives up on it).

    async def _mark_done(self, entry: PendingEntry, handled: list[Subscriber]) -> None:
        """Mark handled destinations and persist the progress."""
        async with self._lock:
            for subscriber in handled:
                subscriber.done = True
            if self._pending.get(entry.match_id) is entry:
                await self._persist(entry)

    async def _finish(self, entry: PendingEntry, *, force: bool = False) -> bool:
        """Finish the entry when every destination is done (or unconditionally with ``force``)."""
        async with self._lock:
            if not force and any(not s.done for s in entry.subscribers):
                return False
            if self._pending.get(entry.match_id) is entry:
                del self._pending[entry.match_id]
            await self._cache.finish_pending_match(entry.match_id)
            return True

    async def _publish(self, entry: PendingEntry, match_query: dict[str, Any]) -> None:
        targets = [s for s in entry.subscribers if not s.done]
        by_locale: dict[str, list[Subscriber]] = {}
        for subscriber in targets:
            by_locale.setdefault(subscriber.language_tag, []).append(subscriber)

        delivered: list[dict[str, str]] = []
        failed: list[Subscriber] = []
        for locale, subscribers in by_locale.items():
            try:
                match = await self._matches.generate_match_data(match_query)
            except Exception as error:
                self._reporter.report(error, "parse-polling/format")
                failed.extend(subscribers)
                continue
            default_image = None
            for subscriber in subscribers:
                template = (
                    subscriber.template_name if subscriber.source == "COMMAND" and subscriber.template_name else None
                )
                try:
                    if template is None:
                        if default_image is None:
                            default_image = await self._images.match(match, locale, self._config.template_match)
                        image = default_image
                    else:
                        image = await self._images.match(match, locale, template)
                except Exception as error:
                    self._reporter.report(error, "parse-polling/render")
                    failed.append(subscriber)
                    continue
                text = self._messages.match_message(locale, match, [p.steam_id for p in subscriber.relevant_players])
                try:
                    ok = await self._sender.send(subscriber.umo, OutgoingMessage.of(text, image))
                except Exception as error:
                    self._reporter.report(error, f"send/{subscriber.umo}")
                    ok = False
                # Delivered or refused by the platform (bot left the group, …): handled either way.
                await self._mark_done(entry, [subscriber])
                if ok:
                    delivered.append(
                        {"platform": subscriber.platform, "guildId": subscriber.channel_id, "languageTag": locale}
                    )

        if delivered:
            match_info = match_query.get("match") or {}
            parsed = "parsed" if match_info.get("parsedDateTime") else "unparsed"
            self._logger.info(
                self._i18n.gt(
                    f"dota2tracker.logger.match_{parsed}",
                    {
                        "matchId": match_info.get("id", entry.match_id),
                        "timeout": self._config.data_parsing_timeout_minutes,
                        "guilds": delivered,
                        "odParsed": match_info.get("odParsed"),
                    },
                )
            )
        if failed:
            entry.attempts += 1
            if entry.attempts >= MAX_PUBLISH_ATTEMPTS:
                self._logger.error(
                    self._i18n.gt(
                        "dota2tracker.logger.publish_gave_up",
                        {"matchId": entry.match_id, "count": len(failed), "attempts": entry.attempts},
                    )
                )
                await self._mark_done(entry, failed)
        await self._finish(entry)
