"""Cancellable minute scheduler replacing Koishi ``ctx.cron``.

* every minute: match discovery, then parse polling (same order as the original)
* ``0 {daily_report_hours} * * *``: daily report
* ``0 {hour} * * {weekday}``: legacy weekly report (cron weekday, 0 = Sunday)

A report fires when its scheduled time lies between the previous tick and the
current one, at most once per local date. A clock stepping forward over the
scheduled time or a stalled loop therefore still fires the report, while a
clock stepping back or the repeated hour at the end of DST never fires it
twice (target fix over a plain "hour == H" check).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from ..config import PluginConfig
from ..utils.timefmt import local_now

__all__ = ["TaskScheduler"]

Job = Callable[[], Awaitable[None]]


def cron_weekday(moment: datetime) -> int:
    """Python ``weekday()`` (Mon=0) → cron (Sun=0)."""
    return (moment.weekday() + 1) % 7


class TaskScheduler:
    def __init__(
        self,
        config: PluginConfig,
        *,
        every_minute: list[Job],
        daily: Job | None,
        weekly: Job | None,
        logger: logging.Logger,
        now: Callable[[], datetime] = local_now,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._every_minute = every_minute
        self._daily = daily
        self._weekly = weekly
        self._logger = logger
        self._now = now
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._jobs: set[asyncio.Task[Any]] = set()
        self._minute_running = False
        self._last_tick: datetime | None = None
        self._fired: dict[str, date] = {}
        self.ticks = 0
        self.started_at: float | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self.started_at = self._now().timestamp()
        self._task = asyncio.create_task(self._loop(), name="dota2tracker-scheduler")

    async def stop(self) -> None:
        tasks = [t for t in [self._task, *self._jobs] if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        self._jobs.clear()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ticks": self.ticks,
            "startedAt": self.started_at,
            "activeJobs": len(self._jobs),
            "daily": self._config.daily_report_switch,
            "weekly": self._config.weekly_report_switch,
        }

    async def _loop(self) -> None:
        while True:
            now = self._now()
            # A small margin keeps an early wake-up from landing in the previous minute.
            await self._sleep(60.05 - now.second - now.microsecond / 1_000_000)
            self.tick(self._now())

    def tick(self, moment: datetime) -> None:
        """Run the minute jobs and fire the reports scheduled in ``(previous tick, moment]``."""
        self.ticks += 1
        previous = self._last_tick or moment - timedelta(minutes=1)
        self._last_tick = moment
        if not self._minute_running:
            self._spawn(self._run_minute())
        else:
            self._logger.debug("dota2tracker: previous minute job still running, tick skipped")
        if self._daily is not None and self._config.daily_report_switch:
            self._fire_if_due("daily", self._daily, previous, moment, self._config.daily_report_hours, None)
        if self._weekly is not None and self._config.weekly_report_switch:
            day, hour = self._config.weekly_report_day_hours
            self._fire_if_due("weekly", self._weekly, previous, moment, hour, day % 7)

    def _fire_if_due(
        self, name: str, job: Job, previous: datetime, moment: datetime, hour: int, weekday: int | None
    ) -> None:
        # Today and yesterday cover a jump across midnight; larger jumps are not caught up.
        for day in (moment.date() - timedelta(days=1), moment.date()):
            if weekday is not None and (day.weekday() + 1) % 7 != weekday:
                continue
            scheduled = datetime.combine(day, time(hour), tzinfo=moment.tzinfo)
            if previous < scheduled <= moment and self._fired.get(name) != day:
                self._fired[name] = day
                self._spawn(self._guard(job, name))

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run_minute(self) -> None:
        self._minute_running = True
        try:
            for job in self._every_minute:
                await self._guard(job, "minute")
        finally:
            self._minute_running = False

    async def _guard(self, job: Job, name: str) -> None:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("dota2tracker: scheduled job %s failed", name)
