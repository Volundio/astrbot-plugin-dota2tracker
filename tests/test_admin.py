"""WebUI management service and AstrBot adapter tests."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from dota2tracker.application.admin import EXPORT_FORMAT, AdminError, AdminService
from dota2tracker.application.models import OutgoingMessage, RenderedImage
from dota2tracker.config import load_config
from dota2tracker.plugin import Platform, Services

LOG = logging.getLogger("dota2tracker.tests.admin")


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, OutgoingMessage]] = []

    async def send(self, umo, message):
        self.sent.append((umo, message))
        return True


@pytest.fixture
async def services(tmp_path):
    config, issues = load_config({"stratz_api_token": "secret-token-123", "proxy_address": "http://u:p@h:1"})
    s = Services(config, issues, tmp_path / "data" / "plugin_data" / "p", LOG, Platform(None, Sender()))
    await s.open()
    yield s
    await s.close()


@pytest.fixture
def admin(services):
    async def failing():
        raise RuntimeError("boom secret-token-123")

    async def fine():
        return None

    return AdminService(services, {"stratz": failing, "valve": fine})


async def _seed(services):
    db = services.db
    await db.subscribe_channel("bot:GroupMessage:1", "bot", "aiocqhttp", "1")
    player = await db.bind_player("bot:GroupMessage:1", "bot", "1", "u1", 111, "Alice")
    await db.bind_player("bot:FriendMessage:9", "bot", "9", "u9", 999, "")
    return player


async def test_status_and_diagnostics_are_redacted(services, admin):
    status = await admin.status()
    assert status["config"]["stratzToken"] is True
    assert status["counts"]["players"] == 0
    diagnostics = await admin.diagnostics()
    dumped = json.dumps(diagnostics, default=str) + json.dumps(status, default=str)
    assert "secret-token-123" not in dumped and "u:p@h" not in dumped
    assert diagnostics["schemaVersion"] >= 1


async def test_subscription_mutations_and_audit(services, admin):
    player = await _seed(services)
    data = await admin.subscriptions()
    channel = data["channels"][0]
    assert channel["supported"] and channel["players"][0]["nickname"] == "Alice"
    assert [p["steam_id"] for p in data["orphanPlayers"]] == [999]

    data = await admin.rename("webui:admin", player.id, "  Bob  ")
    assert data["channels"][0]["players"][0]["nickname"] == "Bob"
    with pytest.raises(AdminError, match="invalid_nickname"):
        await admin.rename("webui:admin", player.id, "x" * 21)
    with pytest.raises(AdminError, match="player_not_found"):
        await admin.unbind("webui:admin", 12345)

    data = await admin.set_subscribed("webui:admin", channel["id"], False)
    assert data["channels"][0]["subscribed"] is False
    data = await admin.set_locale("webui:admin", channel["id"], "en-US")
    assert data["channels"][0]["locale"] == "en-US"
    with pytest.raises(AdminError):
        await admin.set_locale("webui:admin", channel["id"], "fr-FR")

    await admin.unbind("webui:admin", player.id)
    assert (await admin.subscriptions())["channels"][0]["players"] == []
    actions = [a.action for a in await services.db.list_audit()]
    assert actions[:2] == ["unbind", "set_locale"] and "rename" in actions


async def test_export_import_roundtrip_is_idempotent(services, admin, tmp_path):
    await _seed(services)
    exported = await admin.export_data()
    assert exported["format"] == EXPORT_FORMAT
    raw = json.dumps(exported).encode()
    assert "secret" not in raw.decode()

    other_config, issues = load_config({})
    other = Services(other_config, issues, tmp_path / "other" / "a" / "b", LOG, Platform(None, Sender()))
    await other.open()
    try:
        other_admin = AdminService(other)
        preview = await other_admin.import_data("t", raw, dry_run=True)
        assert preview == {
            "channelsAdded": 1,
            "channelsSkipped": 0,
            "playersAdded": 2,
            "playersSkipped": 0,
            "dryRun": True,
        }
        assert await other.db.list_players() == []
        await other_admin.import_data("t", raw, dry_run=False)
        again = await other_admin.import_data("t", raw, dry_run=False)
        assert again["playersAdded"] == 0 and again["playersSkipped"] == 2 and again["channelsSkipped"] == 1
        assert {p.nickname for p in await other.db.list_players()} == {"Alice", ""}
    finally:
        await other.close()


def test_parse_import_rejects_foreign_formats():
    koishi = {"dt_subscribed_guilds": [], "dt_subscribed_players": []}
    for bad in (json.dumps(koishi).encode(), b"not json", b"[]", b'{"x": 1}', b"x" * (2 * 1024 * 1024 + 1)):
        with pytest.raises(AdminError):
            AdminService.parse_import(bad)


async def test_api_check_and_cache_clear(services, admin):
    result = await admin.api_check("webui:a", "stratz")
    assert result["ok"] is False and "secret-token-123" not in result["message"]
    assert (await admin.api_check("webui:a", "valve"))["ok"] is True
    with pytest.raises(AdminError):
        await admin.api_check("webui:a", "nope")
    await services.cache.set_weekly_meta("k", {"a": 1})
    cleared = await admin.clear_cache("webui:a", "weekly_meta")
    assert cleared["removed"] == {"weekly_meta": 1}
    with pytest.raises(AdminError):
        await admin.clear_cache("webui:a", "../etc")
    await admin.clear_errors("webui:a")
    assert services.reporter.recent() == []


async def test_template_list_and_preview_validation(services, admin):
    listing = admin.templates()
    names = {t["name"] for t in listing["templates"]}
    assert {"match_1", "match_2+", "player_1", "hero_1", "item", "daily", "rank_fun", "help"} <= names
    with pytest.raises(AdminError):
        await admin.preview("../../etc/passwd", "zh-CN", "")
    with pytest.raises(AdminError):
        await admin.preview("match_1", "zh-CN", "evil")
    html = admin.preview_html("rank_fun", "zh-CN", "")
    assert "<html" in html.lower()


async def test_preview_uses_html_render(tmp_path):
    calls = []

    async def html_render(tmpl, data, options):
        calls.append(options)
        out = tmp_path / "shot.png"
        out.write_bytes(b"\x89PNG")
        return str(out)

    config, issues = load_config({"render_image_type": "jpeg", "render_jpeg_quality": 70})
    s = Services(config, issues, tmp_path / "d" / "p" / "x", LOG, Platform(html_render, Sender()))
    await s.open()
    try:
        result = await AdminService(s).preview("rank_fun", "en-US", "down")
        assert result["image"].startswith("data:image/jpeg;base64,")
        assert calls[0]["type"] == "jpeg" and calls[0]["quality"] == 70
    finally:
        await s.close()


async def test_services_lifecycle_without_token(tmp_path):
    config, issues = load_config({})
    s = Services(config, issues, tmp_path / "d" / "p" / "x", LOG, Platform(None, Sender()))
    await s.start()
    assert not s.scheduler.running  # no token -> no background tracking
    await s.close()
    config, issues = load_config({"stratz_api_token": "t"})
    s = Services(config, issues, tmp_path / "d" / "p" / "y", LOG, Platform(None, Sender()))
    await s.start()
    await s.start()  # idempotent
    assert s.scheduler.running
    await s.close()
    assert not s.scheduler.running and not s.db.is_open


# ------------------------------------------------------------------ platform
astrbot = pytest.importorskip("astrbot.api.event")


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
    assert chat_context(event).channel_id == "42"


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
