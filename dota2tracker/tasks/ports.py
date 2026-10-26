"""Platform-facing protocols used by the background tasks.

The implementations live in the AstrBot adapter layer (``platform.py``); tasks
only know UMOs and :class:`OutgoingMessage` objects.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..application.models import OutgoingMessage

__all__ = ["MemberProfiles", "MessageSender"]


class MessageSender(Protocol):
    async def send(self, umo: str, message: OutgoingMessage) -> bool:
        """Deliver ``message`` to ``umo``; ``False`` when the platform refused it."""
        ...


class MemberProfiles(Protocol):
    async def member_profile(self, platform_id: str, group_id: str, user_id: str) -> dict[str, Any] | None:
        """``{"nick": str, "avatar": str}`` for a group member, ``None`` when unavailable."""
        ...
