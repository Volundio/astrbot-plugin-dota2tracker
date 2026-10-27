"""AstrBot entry point for the Dota2 Tracker port.

Only wiring lives here: commands are thin adapters to
:class:`dota2tracker.commands.handlers.CommandHandlers`, background work is
owned by :class:`dota2tracker.plugin.Services`, and the WebUI view API calls
:class:`dota2tracker.application.admin.AdminService`.

Ported from https://github.com/sjtdev/koishi-plugin-dota2tracker (GPL-3.0).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star_handler import star_handlers_registry

from .dota2tracker.application.admin import AdminError, AdminService
from .dota2tracker.application.models import ChatContext
from .dota2tracker.commands.registry import COMMANDS_BY_KEY, extract_raw_args
from .dota2tracker.config import load_config
from .dota2tracker.constants import ONEBOT_PLATFORM_TYPES, PLUGIN_NAME
from .dota2tracker.platform import AstrBotSender, EventTips, OneBotGateway, chat_context, to_chain
from .dota2tracker.plugin import Platform, Services

PLUGIN_LOGGER: logging.Logger = logger  # AstrBot logger (std logging.Logger)


class Dota2TrackerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.raw_config = config if config is not None else {}
        plugin_config, issues = load_config(self.raw_config)
        for issue in issues:
            PLUGIN_LOGGER.log(
                logging.ERROR if issue.level == "error" else logging.WARNING,
                "dota2tracker config %s: %s",
                issue.field,
                issue.message,
            )
        gateway = OneBotGateway(context, PLUGIN_LOGGER)
        self.services = Services(
            plugin_config,
            issues,
            Path(StarTools.get_data_dir(PLUGIN_NAME)),
            PLUGIN_LOGGER,
            Platform(
                html_render=self._html_render,
                sender=AstrBotSender(context, PLUGIN_LOGGER),
                members=gateway,
                profiles=gateway,
                onebot_available=lambda: bool(self._onebot_platform_id()),
                running_platforms=lambda: sorted(
                    {p.meta().name for p in self.context.platform_manager.platform_insts if p.meta().name != "webchat"}
                ),
            ),
        )
        self.admin = AdminService(
            self.services,
            {
                "stratz": lambda: self.services.stratz.query_verifying_player(86745912),
                "valve": self.services.valve.query_last_patch_number,
                "opendota": self._check_opendota,
            },
        )
        self._register_web_apis()

    async def _html_render(self, tmpl: str, data: dict[str, Any], options: dict[str, Any]) -> str:
        return await self.html_render(tmpl, data, return_url=False, options=options)

    async def _check_opendota(self) -> None:
        if self.services.opendota is None:
            raise RuntimeError("OpenDota fallback is disabled")
        await self.services.opendota.query_match_info(8000000000)

    # ------------------------------------------------------------ lifecycle
    async def initialize(self) -> None:
        await self.services.open()
        # Plugin (re)loads after start-up never see ``on_astrbot_loaded``.
        if self.context.platform_manager.platform_insts:
            await self.services.start()

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        await self.services.start()

    async def terminate(self) -> None:
        await self.services.close()

    # ------------------------------------------------------------- commands
    def _command_names(self, key: str) -> list[str]:
        """Current trigger names of the handler (follows renames in AstrBot's command management)."""
        handler_name = "cmd_" + key.replace("-", "_")
        metadata = star_handlers_registry.get_handler_by_full_name(f"{type(self).__module__}_{handler_name}")
        for event_filter in metadata.event_filters if metadata else []:
            if isinstance(event_filter, CommandFilter):
                return list(event_filter.get_complete_command_names())
        return list(COMMANDS_BY_KEY[key].names)

    def _unsupported(self, event: AstrMessageEvent, chat: ChatContext) -> Any | None:
        if self.services.bindings.platform_supported(chat.platform_type):
            return None
        return event.plain_result(
            self.services.i18n.t(self.services.i18n.default_locale, "dota2tracker.platform_unsupported")
            or "Dota2 Tracker is not available on this platform."
        )

    async def _run(self, event: AstrMessageEvent, key: str) -> AsyncIterator[Any]:
        chat = chat_context(event)
        if (refusal := self._unsupported(event, chat)) is not None:
            yield refusal
            return
        tips = EventTips(event, recall=self.services.config.auto_recall_tips, logger=PLUGIN_LOGGER)
        raw = extract_raw_args(event.get_message_str(), self._command_names(key))
        try:
            async for message in self.services.commands.run(key, chat, raw, tips):
                if not message.empty:
                    yield event.chain_result(to_chain(message).chain)
        finally:
            await tips.finish()
        event.stop_event()

    @filter.command("dota2tracker")
    async def cmd_root(self, event: AstrMessageEvent):
        """Sub-command overview (Koishi ``dota2tracker`` / ``dota2tracker -h``)."""
        chat = chat_context(event)
        if (refusal := self._unsupported(event, chat)) is not None:
            yield refusal
            return
        async for message in self.services.commands.root(chat):
            yield event.chain_result(to_chain(message).chain)
        event.stop_event()

    @filter.command("dota2tracker.help", alias={"DOTA2指南", "DOTA2帮助", "DOTA2说明"})
    async def cmd_help(self, event: AstrMessageEvent):
        async for r in self._run(event, "help"):
            yield r

    @filter.command("dota2tracker.subscribe", alias={"订阅本群"})
    async def cmd_subscribe(self, event: AstrMessageEvent):
        async for r in self._run(event, "subscribe"):
            yield r

    @filter.command("dota2tracker.unsubscribe", alias={"取消订阅"})
    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        async for r in self._run(event, "unsubscribe"):
            yield r

    @filter.command("dota2tracker.bind", alias={"绑定"})
    async def cmd_bind(self, event: AstrMessageEvent):
        async for r in self._run(event, "bind"):
            yield r

    @filter.command("dota2tracker.unbind", alias={"取消绑定"})
    async def cmd_unbind(self, event: AstrMessageEvent):
        async for r in self._run(event, "unbind"):
            yield r

    @filter.command("dota2tracker.rename", alias={"改名"})
    async def cmd_rename(self, event: AstrMessageEvent):
        async for r in self._run(event, "rename"):
            yield r

    @filter.command("dota2tracker.query-members", alias={"查询群友"})
    async def cmd_query_members(self, event: AstrMessageEvent):
        async for r in self._run(event, "query-members"):
            yield r

    @filter.command("dota2tracker.query-match", alias={"查询比赛"})
    async def cmd_query_match(self, event: AstrMessageEvent):
        async for r in self._run(event, "query-match"):
            yield r

    @filter.command("dota2tracker.query-recent-match", alias={"查询最近比赛"})
    async def cmd_query_recent_match(self, event: AstrMessageEvent):
        async for r in self._run(event, "query-recent-match"):
            yield r

    @filter.command("dota2tracker.query-player", alias={"查询玩家"})
    async def cmd_query_player(self, event: AstrMessageEvent):
        async for r in self._run(event, "query-player"):
            yield r

    @filter.command("dota2tracker.query-hero", alias={"查询英雄"})
    async def cmd_query_hero(self, event: AstrMessageEvent):
        async for r in self._run(event, "query-hero"):
            yield r

    @filter.command("dota2tracker.query-item", alias={"查询物品"})
    async def cmd_query_item(self, event: AstrMessageEvent):
        async for r in self._run(event, "query-item"):
            yield r

    @filter.command("dota2tracker.hero-of-the-day", alias={"今日英雄"})
    async def cmd_hero_of_the_day(self, event: AstrMessageEvent):
        async for r in self._run(event, "hero-of-the-day"):
            yield r

    # -------------------------------------------------------------- web API
    def _register_web_apis(self) -> None:
        routes: list[tuple[str, Callable[[], Awaitable[Any]], list[str], str]] = [
            ("status", self._api_status, ["GET"], "插件运行状态"),
            ("refresh", self._api_refresh, ["POST"], "刷新成员缓存与状态"),
            ("subscriptions", self._api_subscriptions, ["GET"], "订阅与绑定列表"),
            ("subscriptions/subscribe", self._api_set_subscribed, ["POST"], "订阅/取消订阅频道"),
            ("subscriptions/locale", self._api_set_locale, ["POST"], "设置频道语言"),
            ("players/rename", self._api_rename, ["POST"], "修改绑定昵称"),
            ("players/unbind", self._api_unbind, ["POST"], "解除绑定"),
            ("export", self._api_export, ["GET"], "导出订阅与绑定"),
            ("import/preview", self._api_import_preview, ["POST"], "预检导入文件"),
            ("import/apply", self._api_import_apply, ["POST"], "导入订阅与绑定"),
            ("templates", self._api_templates, ["GET"], "模板列表与字体状态"),
            ("templates/preview", self._api_template_preview, ["POST"], "渲染模板预览"),
            ("diagnostics", self._api_diagnostics, ["GET"], "诊断信息"),
            ("api-check", self._api_check, ["POST"], "外部 API 连通性测试"),
            ("cache/clear", self._api_cache_clear, ["POST"], "清理缓存"),
            ("errors/clear", self._api_errors_clear, ["POST"], "清空最近错误"),
            ("diagnostic-export", self._api_diagnostic_export, ["GET"], "下载诊断包"),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(f"/{PLUGIN_NAME}/{route}", self._wrap(handler), methods, desc)

    def _wrap(self, handler: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
        async def view() -> Any:
            try:
                result = await handler()
            except AdminError as error:
                return error_response(str(error), status_code=error.status)
            except Exception as error:
                self.services.reporter.report(error, "webui")
                return error_response("internal_error", status_code=500)
            if hasattr(result, "status_code"):
                return result
            return json_response({"status": "ok", "data": result})

        return view

    @staticmethod
    def _actor() -> str:
        return f"webui:{request.username or 'unknown'}"

    @staticmethod
    async def _body() -> dict[str, Any]:
        body = await request.json(default={})
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _int(body: dict[str, Any], key: str) -> int:
        try:
            return int(body.get(key))
        except (TypeError, ValueError) as error:
            raise AdminError(f"invalid_{key}") from error

    async def _api_status(self) -> Any:
        return await self.admin.status()

    async def _api_refresh(self) -> Any:
        return await self.admin.refresh(self._actor())

    async def _api_subscriptions(self) -> Any:
        return await self.admin.subscriptions()

    async def _api_set_subscribed(self) -> Any:
        body = await self._body()
        return await self.admin.set_subscribed(self._actor(), self._int(body, "id"), bool(body.get("subscribed")))

    async def _api_set_locale(self) -> Any:
        body = await self._body()
        return await self.admin.set_locale(self._actor(), self._int(body, "id"), body.get("locale") or None)

    async def _api_rename(self) -> Any:
        body = await self._body()
        return await self.admin.rename(self._actor(), self._int(body, "id"), str(body.get("nickname") or ""))

    async def _api_unbind(self) -> Any:
        body = await self._body()
        return await self.admin.unbind(self._actor(), self._int(body, "id"))

    async def _api_export(self) -> Any:
        data = await self.admin.export_data()
        await self.services.db.add_audit(self._actor(), "export")
        return json_response(data, headers={"Content-Disposition": 'attachment; filename="dota2tracker-bindings.json"'})

    async def _read_upload(self) -> bytes:
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            raise AdminError("import_missing_file")
        name = Path(upload.filename or "").name.lower()
        if not name.endswith(".json"):
            raise AdminError("import_invalid_extension")
        data = await upload.read(2 * 1024 * 1024 + 1)
        await upload.close()
        return data

    def _onebot_platform_id(self) -> str:
        for platform in self.context.platform_manager.platform_insts:
            if platform.meta().name in ONEBOT_PLATFORM_TYPES:
                return platform.meta().id
        return ""

    async def _api_import_preview(self) -> Any:
        return await self.admin.import_data(self._actor(), await self._read_upload(), dry_run=True)

    async def _api_import_apply(self) -> Any:
        return await self.admin.import_data(self._actor(), await self._read_upload(), dry_run=False)

    async def _api_templates(self) -> Any:
        return self.admin.templates()

    async def _api_template_preview(self) -> Any:
        body = await self._body()
        return await self.admin.preview(
            str(body.get("template") or ""), str(body.get("locale") or ""), str(body.get("variant") or "")
        )

    async def _api_diagnostics(self) -> Any:
        return await self.admin.diagnostics()

    async def _api_check(self) -> Any:
        body = await self._body()
        return await self.admin.api_check(self._actor(), str(body.get("target") or ""))

    async def _api_cache_clear(self) -> Any:
        body = await self._body()
        return await self.admin.clear_cache(self._actor(), str(body.get("namespace") or ""))

    async def _api_errors_clear(self) -> Any:
        await self.admin.clear_errors(self._actor())
        return {"cleared": True}

    async def _api_diagnostic_export(self) -> Any:
        bundle = await self.admin.diagnostic_bundle()
        await self.services.db.add_audit(self._actor(), "diagnostic_export")
        return json_response(
            json.loads(json.dumps(bundle, default=str)),
            headers={"Content-Disposition": 'attachment; filename="dota2tracker-diagnostics.json"'},
        )
