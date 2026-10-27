"""AstrBot adapter layer: the only module (besides ``main.py``) importing AstrBot.

OneBot v11 specifics (``delete_msg``, ``get_group_member_list``,
``get_group_member_info``) are confined to :class:`OneBotGateway`; the rest of
the plugin only sees UMOs, :class:`ChatContext` and :class:`OutgoingMessage`.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context

from .application.models import ChatContext, OutgoingMessage
from .constants import ONEBOT_PLATFORM_TYPES

__all__ = ["AstrBotSender", "EventTips", "OneBotGateway", "chat_context", "to_chain"]


def chat_context(event: AstrMessageEvent) -> ChatContext:
    is_private = bool(event.is_private_chat())
    sender_id = str(event.get_sender_id() or "")
    group_id = str(event.get_group_id() or "")
    group = getattr(event.message_obj, "group", None)
    return ChatContext(
        umo=event.unified_msg_origin,
        platform_id=str(event.get_platform_id() or ""),
        platform_type=str(event.get_platform_name() or ""),
        # Like Koishi's "private:<id>": QQ user and group numbers share one number space,
        # so a bare sender id could match a group's bindings.
        channel_id=f"private:{sender_id}" if is_private else group_id,
        user_id=sender_id,
        sender_name=str(event.get_sender_name() or ""),
        is_private=is_private,
        message_id=str(getattr(event.message_obj, "message_id", "") or ""),
        self_id=str(event.get_self_id() or ""),
        group_name=str(getattr(group, "group_name", "") or ""),
    )


def to_chain(message: OutgoingMessage) -> MessageChain:
    chain = MessageChain()
    for part in message.parts:
        if isinstance(part, str):
            chain.chain.append(Plain(part))
        else:
            chain.chain.append(Image.fromBase64(base64.b64encode(part.data).decode("ascii")))
    return chain


class AstrBotSender:
    """Proactive delivery through ``context.send_message(umo, MessageChain)``."""

    def __init__(self, context: Context, logger: logging.Logger) -> None:
        self._context = context
        self._logger = logger

    async def send(self, umo: str, message: OutgoingMessage) -> bool:
        if message.empty:
            return False
        try:
            ok = await self._context.send_message(umo, to_chain(message))
        except Exception as error:
            self._logger.warning("dota2tracker: sending to %s failed: %s", umo, error)
            return False
        if not ok:
            self._logger.warning("dota2tracker: no platform accepted the message for %s", umo)
        return bool(ok)


class OneBotGateway:
    """Group member access for OneBot v11 platforms (``MemberProvider``/``MemberProfiles``)."""

    def __init__(self, context: Context, logger: logging.Logger) -> None:
        self._context = context
        self._logger = logger

    def _client(self, platform_id: str) -> Any | None:
        platform = self._context.get_platform_inst(platform_id)
        if platform is None or platform.meta().name not in ONEBOT_PLATFORM_TYPES:
            return None
        get_client = getattr(platform, "get_client", None)
        return get_client() if callable(get_client) else None

    async def group_member_ids(self, platform_id: str, group_id: str) -> set[str] | None:
        client = self._client(platform_id)
        if client is None or not str(group_id).isdigit():
            return None
        members = await client.call_action("get_group_member_list", group_id=int(group_id))
        if not isinstance(members, list):
            return None
        return {str(m.get("user_id")) for m in members if isinstance(m, dict)}

    async def member_profile(self, platform_id: str, group_id: str, user_id: str) -> dict[str, Any] | None:
        client = self._client(platform_id)
        if client is None or not str(group_id).isdigit() or not str(user_id).isdigit():
            return None
        info = await client.call_action("get_group_member_info", group_id=int(group_id), user_id=int(user_id))
        if not isinstance(info, dict):
            return None
        return {
            "nick": info.get("card") or info.get("nickname") or "",
            "avatar": f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640",
        }


class EventTips:
    """``TipMessenger``: sends "please wait" tips and recalls them when possible.

    On OneBot the tip is sent through the protocol API so its message id is
    known and ``delete_msg`` can recall it; other platforms (or failures) fall
    back to a plain ``event.send`` without recall.
    """

    def __init__(self, event: AstrMessageEvent, *, recall: bool, logger: logging.Logger) -> None:
        self._event = event
        self._recall = recall
        self._logger = logger
        self._message_ids: list[Any] = []

    async def send(self, text: str) -> None:
        bot = getattr(self._event, "bot", None)
        if self._recall and self._event.get_platform_name() in ONEBOT_PLATFORM_TYPES and bot is not None:
            try:
                if self._event.is_private_chat():
                    result = await bot.call_action(
                        "send_private_msg", user_id=int(self._event.get_sender_id()), message=text
                    )
                else:
                    result = await bot.call_action(
                        "send_group_msg", group_id=int(self._event.get_group_id()), message=text
                    )
                if isinstance(result, dict) and result.get("message_id") is not None:
                    self._message_ids.append(result["message_id"])
                return
            except Exception as error:
                self._logger.debug("dota2tracker: OneBot tip send failed, falling back: %s", error)
        try:
            await self._event.send(MessageChain().message(text))
        except Exception as error:
            # Tips are a courtesy: a failed tip (rate limit, …) must not fail the command.
            self._logger.debug("dota2tracker: sending a tip failed: %s", error)

    async def finish(self) -> None:
        if not self._message_ids:
            return
        bot = getattr(self._event, "bot", None)
        ids, self._message_ids = self._message_ids, []
        for message_id in ids:
            try:
                await bot.call_action("delete_msg", message_id=message_id)
            except Exception as error:
                self._logger.debug("dota2tracker: recalling tip %s failed: %s", message_id, error)
