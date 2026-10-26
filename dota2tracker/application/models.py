"""Value objects shared by the application, presentation and command layers.

Nothing here imports AstrBot: commands receive a :class:`ChatContext` built
from ``AstrMessageEvent`` in ``main.py`` and yield :class:`OutgoingMessage`
objects that the platform layer converts into ``MessageChain``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ChatContext", "OutgoingMessage", "PlayerContext", "RenderedImage", "Subscriber"]


@dataclass(frozen=True)
class ChatContext:
    """Abstract view of the incoming event (UMO, platform, channel, sender)."""

    umo: str
    platform_id: str
    platform_type: str
    channel_id: str
    user_id: str
    sender_name: str = ""
    is_private: bool = False
    message_id: str = ""
    self_id: str = ""
    group_name: str = ""

    @property
    def channel_key(self) -> str:
        return f"{self.platform_id}:{self.channel_id}"


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    mime: str = "image/png"
    template: str = ""

    def data_url(self) -> str:
        import base64

        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass
class OutgoingMessage:
    """One platform message made of ordered text/image parts (``text + image``)."""

    parts: list[str | RenderedImage] = field(default_factory=list)

    @classmethod
    def of(cls, *parts: str | RenderedImage | None) -> OutgoingMessage:
        return cls([part for part in parts if part is not None and part != ""])

    @property
    def text(self) -> str:
        return "".join(part for part in self.parts if isinstance(part, str))

    @property
    def images(self) -> list[RenderedImage]:
        return [part for part in self.parts if isinstance(part, RenderedImage)]

    @property
    def empty(self) -> bool:
        return not self.text.strip() and not self.images


@dataclass
class PlayerContext:
    steam_id: int
    nickname: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"steamId": self.steam_id, "nickname": self.nickname}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerContext:
        return cls(steam_id=int(data.get("steamId") or 0), nickname=str(data.get("nickname") or ""))


@dataclass
class Subscriber:
    """A destination waiting for a parsed match (``PendingMatchSubscriber``)."""

    source: str  # "COMMAND" | "AUTOMATIC"
    type: str  # "CHANNEL" | "PRIVATE"
    umo: str
    platform: str
    channel_id: str
    language_tag: str
    relevant_players: list[PlayerContext] = field(default_factory=list)
    user_id: str = ""
    template_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "type": self.type,
            "umo": self.umo,
            "platform": self.platform,
            "channelId": self.channel_id,
            "languageTag": self.language_tag,
            "relevantPlayers": [p.to_dict() for p in self.relevant_players],
            "userId": self.user_id,
            "templateName": self.template_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subscriber:
        return cls(
            source=str(data.get("source") or "AUTOMATIC"),
            type=str(data.get("type") or "CHANNEL"),
            umo=str(data.get("umo") or ""),
            platform=str(data.get("platform") or ""),
            channel_id=str(data.get("channelId") or ""),
            language_tag=str(data.get("languageTag") or "zh-CN"),
            relevant_players=[PlayerContext.from_dict(p) for p in data.get("relevantPlayers") or []],
            user_id=str(data.get("userId") or ""),
            template_name=data.get("templateName") or None,
        )
