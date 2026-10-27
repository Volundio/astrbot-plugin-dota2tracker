"""Asynchronous HTTP plumbing shared by the Stratz, Valve and OpenDota clients."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from ..errors import PluginStoppedError

__all__ = ["DEFAULT_TIMEOUT", "REQUEST_DEADLINE", "RequestQueue", "build_client"]

T = TypeVar("T")

DEFAULT_TIMEOUT = 15.0
#: Upper bound for one complete request (httpx's own timeout is per phase).
REQUEST_DEADLINE = DEFAULT_TIMEOUT


class RequestQueue:
    """Port of ``MiniQueue``: serialises requests and keeps ``interval`` seconds between them.

    The original waited *after* each task before starting the next one. Here
    the next task waits until ``last_finished + interval`` instead, which gives
    the same spacing without delaying the caller of the previous request.
    """

    def __init__(self, interval: float = 0.2, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.interval = interval
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._stopped = False
        self._waiting = 0

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def pending(self) -> int:
        return self._waiting

    async def run(self, task: Callable[[], Awaitable[T]]) -> T:
        if self._stopped:
            raise PluginStoppedError("Queue has been disposed.")
        self._waiting += 1
        try:
            async with self._lock:
                if self._stopped:
                    raise PluginStoppedError("Queue has been disposed.")
                delay = self._next_allowed - self._clock()
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    return await task()
                finally:
                    self._next_allowed = self._clock() + self.interval
        finally:
            self._waiting -= 1

    def dispose(self) -> None:
        self._stopped = True


def build_client(
    *,
    proxy: str | None = None,
    force_ipv4: bool = False,
    base_url: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` with the plugin's proxy / IPv4 rules.

    * A proxy has priority (same as the Koishi version).
    * ``force_ipv4`` binds the local address to ``0.0.0.0`` which makes the
      connection pool use IPv4 sockets only (``OpenDotaIPStack: ipv4``).
    """
    kwargs: dict = {
        "timeout": httpx.Timeout(timeout),
        "headers": headers or {},
        "follow_redirects": True,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if transport is not None:
        kwargs["transport"] = transport
    elif proxy:
        kwargs["proxy"] = proxy
    elif force_ipv4:
        kwargs["transport"] = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0)
    return httpx.AsyncClient(**kwargs)
