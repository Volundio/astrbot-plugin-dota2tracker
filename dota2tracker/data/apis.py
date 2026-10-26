"""Valve datafeed and OpenDota clients (ports of ``valve.api.ts`` / ``opendota.api.ts``)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..errors import wrap_fetch_error
from .http import RequestQueue, build_client

__all__ = ["OpenDotaAPI", "ValveAPI"]


class _BaseClient:
    service_name = "API"

    def __init__(self, client: httpx.AsyncClient, *, interval: float = 0.2) -> None:
        self._client = client
        self._queue = RequestQueue(interval)
        self.request_count = 0
        self.last_success: float | None = None
        self.last_error: str | None = None

    @property
    def queue(self) -> RequestQueue:
        return self._queue

    async def aclose(self) -> None:
        self._queue.dispose()
        await self._client.aclose()

    async def _request(self, label: str, send: Callable[[], Awaitable[httpx.Response]]) -> Any:
        async def task() -> Any:
            self.request_count += 1
            try:
                response = await send()
                response.raise_for_status()
                data = response.json()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                wrapped = wrap_fetch_error(error, self.service_name, label)
                self.last_error = f"{type(wrapped).__name__}: {wrapped}"
                raise wrapped from error
            self.last_success = time.time()
            self.last_error = None
            return data

        return await self._queue.run(task)


class ValveAPI(_BaseClient):
    """``https://www.dota2.com/datafeed`` – hero/item details and patch notes."""

    service_name = "ValveAPI"
    BASE_URL = "https://www.dota2.com/datafeed"

    def __init__(
        self,
        *,
        proxy: str | None = None,
        language_of: Callable[[str], str] = lambda locale: "schinese",
        transport: httpx.AsyncBaseTransport | None = None,
        interval: float = 0.2,
    ) -> None:
        super().__init__(build_client(proxy=proxy, base_url=self.BASE_URL, transport=transport), interval=interval)
        self._language_of = language_of

    async def fetch(self, path: str, params: dict[str, Any] | None = None, locale: str | None = None) -> Any:
        query = dict(params or {})
        if locale:
            query["language"] = self._language_of(locale)
        return await self._request(path, lambda: self._client.get(path, params=query))

    async def query_hero_details(self, hero_id: int, locale: str = "zh-CN") -> dict[str, Any]:
        data = await self.fetch("/herodata", {"hero_id": hero_id}, locale)
        return data["result"]["data"]["heroes"][0]

    async def query_item_list(self, locale: str = "zh-CN") -> list[dict[str, Any]]:
        data = await self.fetch("/itemlist", None, locale)
        return data["result"]["data"]["itemabilities"]

    async def query_item_details(self, item_id: int, locale: str = "zh-CN") -> dict[str, Any]:
        data = await self.fetch("/itemdata", {"item_id": item_id}, locale)
        return data["result"]["data"]["items"][0]

    async def query_patch_list(self) -> list[dict[str, Any]]:
        data = await self.fetch("/patchnoteslist")
        return data["patches"]

    async def query_patch_notes(self, version: str, locale: str = "zh-CN") -> dict[str, Any]:
        return await self.fetch("/patchnotes", {"version": version}, locale)

    async def query_last_patch_number(self) -> str:
        patches = await self.query_patch_list()
        return patches[-1]["patch_number"]


class OpenDotaAPI(_BaseClient):
    """OpenDota fallback source; every call is added to the daily usage log."""

    service_name = "OpenDota"
    BASE_URL = "https://api.opendota.com/api"

    def __init__(
        self,
        *,
        api_key: str = "",
        proxy: str | None = None,
        force_ipv4: bool = False,
        record_usage: Callable[[str, int], Awaitable[None]] | None = None,
        logger: logging.Logger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        interval: float = 0.2,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        client = build_client(
            proxy=proxy,
            force_ipv4=force_ipv4 and not proxy,
            base_url=self.BASE_URL,
            headers=headers,
            transport=transport,
        )
        super().__init__(client, interval=interval)
        self._record_usage = record_usage
        self._logger = logger or logging.getLogger(__name__)

    async def _record(self, path: str, count: int) -> None:
        if self._record_usage is None:
            return
        try:
            await self._record_usage(path, count)
        except Exception as error:
            self._logger.debug("Failed to record OpenDota usage: %s", error)

    async def query_match_info(self, match_id: int) -> dict[str, Any]:
        path = f"/matches/{match_id}"
        data = await self._request(path, lambda: self._client.get(path))
        await self._record(path, 1)
        return data

    async def request_parse_match(self, match_id: int) -> Any:
        path = f"/request/{match_id}"
        job = await self._request(path, lambda: self._client.post(path))
        await self._record(path, 10)
        return job
