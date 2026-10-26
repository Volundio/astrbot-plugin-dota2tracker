"""Channel subscriptions, SteamID bindings and group member filtering."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..constants import UNSUPPORTED_PLATFORM_TYPES
from ..data.database import AlreadyBoundError, ChannelRecord, Database, PlayerRecord
from .models import ChatContext

__all__ = ["STEAM_ID_PATTERN", "BindingService", "MemberProvider", "ResolveResult"]

STEAM_ID_PATTERN = re.compile(r"^[0-9]{1,11}$")


class MemberProvider(Protocol):
    """Platform adapter capable of listing group members (OneBot first)."""

    async def group_member_ids(self, platform_id: str, group_id: str) -> set[str] | None: ...


@dataclass(frozen=True)
class ResolveResult:
    success: bool
    steam_id: int | None = None
    reason: Literal["NOT_BINDED", "INVALID_INPUT", ""] = ""


class BindingService:
    def __init__(self, db: Database, members: MemberProvider | None, logger: logging.Logger) -> None:
        self._db = db
        self._members = members
        self._logger = logger
        self._member_cache: dict[str, set[str]] = {}

    # ------------------------------------------------------------ platform
    @staticmethod
    def platform_supported(platform_type: str) -> bool:
        return bool(platform_type) and platform_type not in UNSUPPORTED_PLATFORM_TYPES

    # --------------------------------------------------------- member cache
    async def refresh_member_cache(self, platform: str, channel_id: str) -> None:
        key = f"{platform}:{channel_id}"
        if self._members is None:
            self._member_cache.pop(key, None)
            return
        try:
            ids = await self._members.group_member_ids(platform, channel_id)
        except Exception as error:
            self._logger.warning("获取频道 %s 成员列表失败，已清除对应缓存：%s", key, error)
            ids = None
        if ids is None:
            self._member_cache.pop(key, None)
        else:
            self._member_cache[key] = ids

    async def refresh_all_member_caches(self) -> None:
        channels = await self._db.list_channels(subscribed_only=True)
        await asyncio.gather(
            *(
                self.refresh_member_cache(c.platform, c.channel_id)
                for c in channels
                if self.platform_supported(c.platform_type)
            ),
            return_exceptions=True,
        )

    def is_player_in_guild(self, platform: str, channel_id: str, user_id: str) -> bool:
        cached = self._member_cache.get(f"{platform}:{channel_id}")
        if cached is None:
            return True  # cold start / failed refresh: let the player pass
        return user_id in cached

    def member_cache_summary(self) -> dict[str, int]:
        return {key: len(ids) for key, ids in self._member_cache.items()}

    # -------------------------------------------------------------- channels
    async def is_subscribed(self, ctx: ChatContext) -> bool:
        return await self._db.is_channel_subscribed(ctx.platform_id, ctx.channel_id)

    async def subscribe(self, ctx: ChatContext) -> bool:
        created = await self._db.subscribe_channel(
            ctx.umo, ctx.platform_id, ctx.platform_type, ctx.channel_id, ctx.group_name
        )
        if created:
            asyncio.get_running_loop().create_task(self.refresh_member_cache(ctx.platform_id, ctx.channel_id))
        return created

    async def unsubscribe(self, ctx: ChatContext) -> bool:
        self._member_cache.pop(ctx.channel_key, None)
        return await self._db.unsubscribe_channel(ctx.platform_id, ctx.channel_id)

    async def channel_locale(self, platform: str, channel_id: str) -> str | None:
        return await self._db.channel_locale(platform, channel_id)

    # --------------------------------------------------------------- players
    async def get_binding(self, ctx: ChatContext) -> PlayerRecord | None:
        return await self._db.get_player(ctx.platform_id, ctx.channel_id, ctx.user_id)

    async def bind(self, ctx: ChatContext, steam_id: int, nickname: str) -> PlayerRecord | None:
        """Create a binding; returns ``None`` if the user was bound concurrently."""
        try:
            record = await self._db.bind_player(
                ctx.umo, ctx.platform_id, ctx.channel_id, ctx.user_id, steam_id, nickname
            )
        except AlreadyBoundError:
            return None
        asyncio.get_running_loop().create_task(self.refresh_member_cache(ctx.platform_id, ctx.channel_id))
        return record

    async def unbind(self, ctx: ChatContext) -> bool:
        cached = self._member_cache.get(ctx.channel_key)
        if cached is not None:
            cached.discard(ctx.user_id)
        return await self._db.unbind_player(ctx.platform_id, ctx.channel_id, ctx.user_id)

    async def rename(self, player: PlayerRecord, nickname: str) -> bool:
        return await self._db.rename_player(player.id, nickname)

    async def players_in_channel(self, ctx: ChatContext) -> list[PlayerRecord]:
        return await self._db.list_players(ctx.platform_id, ctx.channel_id)

    async def resolve_steam_id(self, ctx: ChatContext, value: str | None) -> ResolveResult:
        """SteamID (1–11 digits) → channel nickname → caller's own binding."""
        if value:
            if STEAM_ID_PATTERN.match(value):
                return ResolveResult(True, int(value))
            target = await self._db.find_player_by_nickname(ctx.platform_id, ctx.channel_id, value)
            if target is not None:
                return ResolveResult(True, target.steam_id)
            return ResolveResult(False, reason="INVALID_INPUT")
        own = await self._db.get_player(ctx.platform_id, ctx.channel_id, ctx.user_id)
        if own is not None:
            return ResolveResult(True, own.steam_id)
        return ResolveResult(False, reason="NOT_BINDED")

    async def active_players(self) -> list[PlayerRecord]:
        """Bindings in subscribed channels on supported platforms that are still group members."""
        channels = {c.key: c for c in await self._db.list_channels(subscribed_only=True)}
        result = []
        for player in await self._db.list_players_in_subscribed_channels():
            channel: ChannelRecord | None = channels.get(player.channel_key)
            if channel is None or not self.platform_supported(channel.platform_type):
                continue
            if self.is_player_in_guild(player.platform, player.channel_id, player.user_id):
                result.append(player)
        return result

    async def subscribed_channels(self) -> list[ChannelRecord]:
        return [
            c for c in await self._db.list_channels(subscribed_only=True) if self.platform_supported(c.platform_type)
        ]

    async def channel_for(self, platform: str, channel_id: str) -> ChannelRecord | None:
        return await self._db.get_channel(platform, channel_id)


def binding_summary(player: PlayerRecord) -> dict[str, Any]:
    return {"userId": player.user_id, "nickName": player.nickname, "steamId": player.steam_id}
