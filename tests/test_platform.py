"""AstrBot adapter layer (``dota2tracker/platform.py``): events, message chains, sending, tips."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from dota2tracker.application.models import OutgoingMessage, RenderedImage

pytest.importorskip("astrbot.api.event")

LOG = logging.getLogger("dota2tracker.tests.platform")


def test_to_chain_and_chat_context():
    from dota2tracker.platform import chat_context, to_chain

    chain = to_chain(OutgoingMessage.of("hi", RenderedImage(b"png")))
    assert [type(c).__name__ for c in chain.chain] == ["Plain", "Image"]

    event = SimpleNamespace(
        unified_msg_origin="napcat:GroupMessage:100",
        is_private_chat=lambda: False,
        get_sender_id=lambda: "42",
        get_group_id=lambda: "100",
        get_platform_id=lambda: "napcat",
        get_platform_name=lambda: "aiocqhttp",
        get_sender_name=lambda: "Alice",
        get_self_id=lambda: "1",
        message_obj=SimpleNamespace(group=SimpleNamespace(group_name="G"), message_id=5),
    )
    chat = chat_context(event)
    assert (chat.umo, chat.platform_id, chat.channel_id, chat.user_id, chat.is_private) == (
        "napcat:GroupMessage:100",
        "napcat",
        "100",
        "42",
        False,
    )
    event.is_private_chat = lambda: True
    assert chat_context(event).channel_id == "private:42"


async def test_sender_and_tips_recall():
    from dota2tracker.platform import AstrBotSender, EventTips

    class Ctx:
        def __init__(self, fail=False):
            self.fail = fail
            self.sent = []

        async def send_message(self, umo, chain):
            if self.fail:
                raise RuntimeError("down")
            self.sent.append((umo, chain))
            return True

    ok = AstrBotSender(Ctx(), LOG)
    assert await ok.send("u", OutgoingMessage.of("x")) is True
    assert await ok.send("u", OutgoingMessage.of()) is False
    assert await AstrBotSender(Ctx(fail=True), LOG).send("u", OutgoingMessage.of("x")) is False

    actions = []

    class Bot:
        async def call_action(self, action, **kwargs):
            actions.append((action, kwargs))
            return {"message_id": 77} if action.startswith("send") else None

    event = SimpleNamespace(
        bot=Bot(),
        get_platform_name=lambda: "aiocqhttp",
        is_private_chat=lambda: False,
        get_group_id=lambda: "100",
        get_sender_id=lambda: "42",
    )
    tips = EventTips(event, recall=True, logger=LOG)
    await tips.send("wait")
    await tips.finish()
    assert actions == [("send_group_msg", {"group_id": 100, "message": "wait"}), ("delete_msg", {"message_id": 77})]

    plain = []

    async def send(chain):
        plain.append(chain)

    event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp", send=send)
    tips = EventTips(event, recall=False, logger=LOG)
    await tips.send("wait")
    await tips.finish()
    assert len(plain) == 1


async def test_failed_tip_does_not_fail_the_command():
    from dota2tracker.platform import EventTips

    async def broken_send(chain):
        raise RuntimeError("rate limited")

    event = SimpleNamespace(get_platform_name=lambda: "telegram", send=broken_send)
    tips = EventTips(event, recall=True, logger=LOG)
    await tips.send("wait")  # must not raise
    await tips.finish()
