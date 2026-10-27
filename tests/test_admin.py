"""WebUI management service and AstrBot adapter tests."""

from __future__ import annotations

import json
import logging

import pytest

from dota2tracker.application.admin import EXPORT_FORMAT, AdminError, AdminService
from dota2tracker.application.models import OutgoingMessage
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
            "invalid": 0,
            "dryRun": True,
        }
        assert await other.db.list_players() == []
        await other_admin.import_data("t", raw, dry_run=False)
        again = await other_admin.import_data("t", raw, dry_run=False)
        assert again["playersAdded"] == 0 and again["playersSkipped"] == 2 and again["channelsSkipped"] == 1
        assert {p.nickname for p in await other.db.list_players()} == {"Alice", ""}
    finally:
        await other.close()


def _backup(channels, players) -> bytes:
    return json.dumps({"format": EXPORT_FORMAT, "channels": channels, "players": players}).encode()


def _channel(channel_id, *, umo=None, subscribed=True, locale=None):
    return {
        "umo": f"bot:GroupMessage:{channel_id}" if umo is None else umo,
        "platform": "bot",
        "platform_type": "aiocqhttp",
        "channel_id": channel_id,
        "subscribed": subscribed,
        "locale": locale,
        "name": "",
    }


def _player(user_id, steam_id, channel_id="1"):
    return {
        "umo": f"bot:GroupMessage:{channel_id}",
        "platform": "bot",
        "channel_id": channel_id,
        "user_id": user_id,
        "steam_id": steam_id,
        "nickname": "",
        "rank": {},
    }


async def test_import_validates_rows_before_writing(services, admin):
    raw = _backup(
        [_channel("1"), _channel("2", umo=""), _channel("3", umo=None) | {"umo": None}],
        [
            _player("a", 123),
            _player("b", 2**70),  # used to raise OverflowError half-way through the import
            _player("c", -5),
            _player("d", "12345678901234"),
            _player("e", True),
            _player("f", "456"),
        ],
    )
    report = await admin.import_data("t", raw, dry_run=False)
    assert report["channelsAdded"] == 1 and report["playersAdded"] == 2 and report["invalid"] == 6
    assert sorted(p.steam_id for p in await services.db.list_players()) == [123, 456]
    assert [c.channel_id for c in await services.db.list_channels()] == ["1"]


async def test_import_is_all_or_nothing(services, admin, monkeypatch):
    from dota2tracker.data.database import Database

    calls = 0
    original = Database._restore_channel

    async def flaky(conn, channel, now, report):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("disk full")
        await original(conn, channel, now, report)

    monkeypatch.setattr(Database, "_restore_channel", staticmethod(flaky))
    with pytest.raises(RuntimeError):
        await admin.import_data("t", _backup([_channel("1"), _channel("2")], [_player("a", 1)]), dry_run=False)
    assert await services.db.list_channels() == [] and await services.db.list_players() == []
    # The connection is usable afterwards (no transaction left open).
    await services.db.subscribe_channel("bot:GroupMessage:9", "bot", "aiocqhttp", "9")
    assert len(await services.db.list_channels()) == 1


async def test_import_keeps_unsubscribed_channels_and_umo_uniqueness(services, admin):
    await services.db.subscribe_channel("bot:GroupMessage:1", "bot", "aiocqhttp", "1")
    raw = _backup(
        [
            _channel("5", subscribed=False, locale="en-US"),
            _channel("6", umo="bot:GroupMessage:1"),  # UMO already used by channel 1
        ],
        [],
    )
    report = await admin.import_data("t", raw, dry_run=False)
    assert report["channelsAdded"] == 1 and report["channelsSkipped"] == 1
    channels = {c.channel_id: c for c in await services.db.list_channels()}
    assert set(channels) == {"1", "5"}
    assert channels["5"].subscribed is False and channels["5"].locale == "en-US"
    assert channels["1"].channel_id == "1"  # not overwritten by channel 6


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
    # The OpenDota daily call counter is bookkeeping, not a cache: it cannot be reset from the WebUI.
    await services.cache.add_opendota_request_log("matches/1")
    with pytest.raises(AdminError):
        await admin.clear_cache("webui:a", "opendota_request_log")
    assert await services.cache.today_opendota_request_count() == 1
    stats = {entry["namespace"]: entry["clearable"] for entry in (await admin.diagnostics())["cache"]}
    assert stats["opendota_request_log"] is False
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


async def test_expired_rows_are_purged_hourly(services):
    from dota2tracker.data.cache import NS_WEEKLY_META

    await services.db.cache_set(NS_WEEKLY_META, "old", {"x": 1}, expires_at=1)
    await services._purge_expired()  # just purged on open: nothing happens yet
    assert (await services.db.cache_stats())[0]["entries"] == 1
    services._purged_at -= 3601
    await services._purge_expired()
    assert await services.db.cache_stats() == []
