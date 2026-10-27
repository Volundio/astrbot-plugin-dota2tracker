"""Smoke tests for ``main.py`` wiring with a fake AstrBot context and events."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "astrbot_plugin_dota2tracker_under_test"

pytest.importorskip("astrbot.api.star")


def load_main():
    """Import ``main.py`` the way AstrBot does: as a submodule of the plugin package."""
    if PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = module
    return importlib.import_module(f"{PACKAGE}.main")


class FakePlatform:
    def __init__(self, platform_id: str, name: str) -> None:
        self._meta = SimpleNamespace(id=platform_id, name=name)

    def meta(self):
        return self._meta

    def get_client(self):
        return None


class FakeContext:
    def __init__(self) -> None:
        self.platform_manager = SimpleNamespace(platform_insts=[FakePlatform("napcat", "aiocqhttp")])
        self.registered_web_apis: list[tuple] = []
        self.sent: list[tuple] = []

    def register_web_api(self, route, handler, methods, desc):
        self.registered_web_apis.append((route, handler, methods, desc))

    def get_config(self, umo=None):
        return {}

    def get_platform_inst(self, platform_id):
        return next((p for p in self.platform_manager.platform_insts if p.meta().id == platform_id), None)

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return True


class FakeEvent:
    def __init__(self, text: str, *, platform: str = "aiocqhttp", private: bool = False, user: str = "42") -> None:
        self.text = text
        self.platform = platform
        self.private = private
        self.user = user
        self.unified_msg_origin = (
            f"napcat:{'FriendMessage' if private else 'GroupMessage'}:{user if private else '100'}"
        )
        self.message_obj = SimpleNamespace(group=None, message_id="1")
        self.stopped = False
        self.sent: list = []

    def is_private_chat(self):
        return self.private

    def get_sender_id(self):
        return self.user

    def get_group_id(self):
        return "" if self.private else "100"

    def get_platform_id(self):
        return "napcat"

    def get_platform_name(self):
        return self.platform

    def get_sender_name(self):
        return "Alice"

    def get_self_id(self):
        return "1"

    def get_message_str(self):
        return self.text

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)

    def stop_event(self):
        self.stopped = True

    async def send(self, chain):
        self.sent.append(chain)


@pytest.fixture
async def plugin(tmp_path, monkeypatch):
    from astrbot.api.star import StarTools

    main = load_main()
    monkeypatch.setattr(
        StarTools, "get_data_dir", classmethod(lambda cls, name=None: tmp_path / "data" / "plugin_data" / "x")
    )
    instance = main.Dota2TrackerPlugin(FakeContext(), {"stratz_api_token": "token"})
    await instance.initialize()
    yield instance
    await instance.terminate()


async def collect(gen):
    return [item async for item in gen]


def texts(results):
    out = []
    for kind, payload in results:
        out.append(payload if kind == "plain" else "".join(getattr(c, "text", "") for c in payload))
    return out


async def test_web_api_routes_registered(plugin):
    routes = {route for route, *_ in plugin.context.registered_web_apis}
    assert "/astrbot_plugin_dota2tracker/status" in routes
    assert "/astrbot_plugin_dota2tracker/templates/preview" in routes
    assert len(routes) == 17
    assert plugin.services.scheduler.running  # platforms already loaded -> started in initialize


async def test_subscribe_via_alias_and_koishi_name(plugin):
    event = FakeEvent("订阅本群")
    result = texts(await collect(plugin.cmd_subscribe(event)))
    assert result and event.stopped
    again = texts(await collect(plugin.cmd_subscribe(FakeEvent("dota2tracker.subscribe"))))
    assert again != result  # "already subscribed" text
    channel = (await plugin.services.db.list_channels())[0]
    assert channel.umo == "napcat:GroupMessage:100" and channel.platform == "napcat"

    status = await plugin.admin.status()
    assert status["platforms"] == {"onebot": True, "running": ["aiocqhttp"]}
    assert status["counts"]["subscribed_channels"] == 1


def _command_filters():
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.command_group import CommandGroupFilter
    from astrbot.core.star.star_handler import star_handlers_registry

    module = f"{PACKAGE}.main"
    found = []
    for metadata in star_handlers_registry:
        if metadata.handler_module_path != module:
            continue
        for event_filter in metadata.event_filters:
            assert not isinstance(event_filter, CommandGroupFilter)
            if isinstance(event_filter, CommandFilter):
                found.append((metadata.handler_name, event_filter))
    return found


async def test_each_command_has_a_single_handler(plugin):
    from dota2tracker.commands.registry import COMMANDS

    names = [name for _, f in _command_filters() for name in f.get_complete_command_names()]
    assert len(names) == len(set(names))
    for spec in COMMANDS:
        assert names.count(spec.legacy_name) == 1
        assert all(names.count(alias) == 1 for alias in spec.aliases)
    assert "dota2tracker" in names  # root overview


async def test_renamed_command_still_parses_arguments(plugin):
    event_filter = dict(_command_filters())["cmd_bind"]
    original = (event_filter.command_name, set(event_filter.alias))
    try:
        event_filter.command_name, event_filter.alias = "bd", {"绑"}
        event_filter._cmpl_cmd_names = None
        assert sorted(plugin._command_names("bind")) == ["bd", "绑"]
        captured = {}

        async def fake_run(key, chat, raw, tips):
            captured["raw"] = raw
            return
            yield

        plugin.services.commands.run = fake_run
        await collect(plugin.cmd_bind(FakeEvent("bd 123 小明")))
        assert captured["raw"] == "123 小明"
    finally:
        event_filter.command_name, event_filter.alias = original
        event_filter._cmpl_cmd_names = None


async def test_help_option_and_root_overview(plugin):
    [text] = texts(await collect(plugin.cmd_bind(FakeEvent("绑定 -h"))))
    assert text.startswith("指令：dota2tracker.bind <steam_id> [nick_name]")
    assert "使用示例：" in text and "    绑定 123456789" in text
    [text] = texts(await collect(plugin.cmd_query_match(FakeEvent("查询比赛 --help"))))
    assert "可用的选项有：" in text and "-p, --parse" in text
    [text] = texts(await collect(plugin.cmd_root(FakeEvent("dota2tracker"))))
    assert "可用的子指令有：" in text and "dota2tracker.hero-of-the-day" in text


async def test_storage_failure_is_not_leaked_to_chat(plugin):
    await plugin.services.db.close()
    [text] = texts(await collect(plugin.cmd_unbind(FakeEvent("取消绑定"))))
    assert text == "指令执行失败，请稍后再试。"
    assert "database" not in text


async def test_other_platforms_work(plugin):
    event = FakeEvent("订阅本群", platform="discord")
    result = texts(await collect(plugin.cmd_subscribe(event)))
    assert result and event.stopped
    [channel] = await plugin.services.db.list_channels()
    assert channel.platform_type == "discord"


async def test_denylisted_platform_is_rejected(plugin, monkeypatch):
    binding = sys.modules[f"{PACKAGE}.dota2tracker.application.binding"]
    monkeypatch.setattr(binding, "UNSUPPORTED_PLATFORM_TYPES", ("webchat",))
    [(kind, text)] = await collect(plugin.cmd_subscribe(FakeEvent("订阅本群", platform="webchat")))
    assert kind == "plain" and text
    assert await plugin.services.db.list_channels() == []


async def test_web_api_wrapper_maps_errors(plugin):
    view = plugin._wrap(plugin._api_subscriptions)
    response = await view()
    assert response.status_code == 200

    async def broken():
        raise RuntimeError("secret token")

    response = await plugin._wrap(broken)()
    assert response.status_code == 500 and b"secret" not in response.body
