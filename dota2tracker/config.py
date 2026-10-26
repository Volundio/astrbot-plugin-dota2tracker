"""Typed plugin configuration and validation.

AstrBot passes the raw ``AstrBotConfig`` (a ``dict`` subclass) to the Star.
The services never read that mapping directly: :func:`load_config` converts it
into an immutable :class:`PluginConfig` and reports every problem through
:class:`ConfigIssue` so WebUI views, commands and background tasks share one
validation result. Secrets are never included in ``__repr__`` or exports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Literal

from .constants import SUPPORTED_LOCALES

__all__ = [
    "MATCH_TEMPLATES",
    "ConfigIssue",
    "CustomItemAlias",
    "PluginConfig",
    "load_config",
]

MATCH_TEMPLATES = ("match_1", "match_2", "match_2+")
PLAYER_TEMPLATES = ("player_1",)
HERO_TEMPLATES = ("hero_1",)
URL_MESSAGE_TYPES = ("match", "player", "hero")
RENDER_SCALES = ("normal", "high", "ultra")
RENDER_IMAGE_TYPES = ("png", "jpeg")


@dataclass(frozen=True)
class ConfigIssue:
    """A validation finding. ``level`` is ``error`` (feature disabled) or ``warning``."""

    level: Literal["error", "warning"]
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class CustomItemAlias:
    keyword: str
    alias: str


@dataclass(frozen=True)
class PluginConfig:
    # --- 基础 ---
    stratz_api_token: str = field(default="", repr=False)
    data_parsing_timeout_minutes: int = 60
    proxy_address: str = field(default="", repr=False)
    suppress_api_network_errors: bool = False
    # --- OpenDota 回退 ---
    enable_opendota_fallback: bool = False
    opendota_api_key: str = field(default="", repr=False)
    opendota_ip_stack: Literal["auto", "ipv4"] = "auto"
    # --- 消息 ---
    default_locale: str = "zh-CN"
    use_hero_nicknames: bool = True
    url_in_message_type: tuple[str, ...] = ()
    max_send_item_count: int = 5
    show_item_list_at_too_much_items: bool = True
    custom_item_alias: tuple[CustomItemAlias, ...] = ()
    auto_recall_tips: bool = True
    # --- 段位 ---
    rank_broad_switch: bool = False
    rank_broad_star: bool = True
    rank_broad_leader: bool = True
    rank_broad_fun: bool = False
    # --- 报告 ---
    daily_report_switch: bool = False
    daily_report_hours: int = 6
    daily_report_show_combi: bool = True
    weekly_report_switch: bool = False
    weekly_report_day_hours: tuple[int, int] = (1, 10)
    weekly_report_show_combi: bool = True
    # --- 模板 ---
    template_match: str = "match_1"
    template_player: str = "player_1"
    template_hero: str = "hero_1"
    player_rank_estimate: bool = True
    patch_notes_retrieval_depth: int = 2
    # --- 渲染 ---
    render_scale: Literal["normal", "high", "ultra"] = "high"
    render_image_type: Literal["png", "jpeg"] = "png"
    render_jpeg_quality: int = 90

    @property
    def has_stratz_token(self) -> bool:
        return bool(self.stratz_api_token.strip())

    def url_enabled(self, kind: str) -> bool:
        return kind in self.url_in_message_type

    def redacted(self) -> dict[str, Any]:
        """Configuration snapshot safe for logs, WebUI and diagnostic exports."""
        data: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in _SECRET_FIELDS:
                data[item.name] = {"configured": bool(str(value).strip())}
            elif isinstance(value, tuple):
                data[item.name] = [
                    {"keyword": v.keyword, "alias": v.alias} if isinstance(v, CustomItemAlias) else v for v in value
                ]
            else:
                data[item.name] = value
        return data


_SECRET_FIELDS = frozenset({"stratz_api_token", "opendota_api_key", "proxy_address"})


class _Reader:
    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.raw = raw
        self.issues: list[ConfigIssue] = []

    def warn(self, name: str, code: str, message: str) -> None:
        self.issues.append(ConfigIssue("warning", name, code, message))

    def error(self, name: str, code: str, message: str) -> None:
        self.issues.append(ConfigIssue("error", name, code, message))

    def get(self, name: str, default: Any) -> Any:
        value = self.raw.get(name, default)
        return default if value is None else value

    def string(self, name: str, default: str = "") -> str:
        value = self.get(name, default)
        if not isinstance(value, str):
            self.warn(name, "type", f"{name} 应为字符串，已使用默认值。")
            return default
        return value.strip()

    def boolean(self, name: str, default: bool) -> bool:
        value = self.get(name, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        self.warn(name, "type", f"{name} 应为布尔值，已使用默认值 {default}。")
        return default

    def integer(self, name: str, default: int, minimum: int, maximum: int) -> int:
        value = self.get(name, default)
        try:
            number = int(value)
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
        except (TypeError, ValueError):
            self.warn(name, "type", f"{name} 应为整数，已使用默认值 {default}。")
            return default
        if number < minimum or number > maximum:
            clamped = min(max(number, minimum), maximum)
            self.warn(name, "range", f"{name}={number} 超出范围 [{minimum}, {maximum}]，已调整为 {clamped}。")
            return clamped
        return number

    def choice(self, name: str, default: str, options: Sequence[str]) -> str:
        value = self.get(name, default)
        if value not in options:
            self.warn(name, "choice", f"{name}={value!r} 不是可选值 {list(options)}，已使用默认值 {default}。")
            return default
        return value

    def string_list(self, name: str, options: Sequence[str] | None = None) -> tuple[str, ...]:
        value = self.get(name, [])
        if not isinstance(value, (list, tuple)):
            self.warn(name, "type", f"{name} 应为列表，已忽略。")
            return ()
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                continue
            item = item.strip()
            if options is not None and item not in options:
                self.warn(name, "choice", f"{name} 包含未知选项 {item!r}，已忽略。")
                continue
            if item not in result:
                result.append(item)
        return tuple(result)


def load_config(raw: Mapping[str, Any] | None) -> tuple[PluginConfig, list[ConfigIssue]]:
    """Convert the AstrBot plugin config into :class:`PluginConfig` and issues."""
    reader = _Reader(raw or {})
    defaults = PluginConfig()

    token = reader.string("stratz_api_token")
    if not token:
        reader.error(
            "stratz_api_token",
            "missing",
            "未配置 STRATZ API Token（stratz_api_token），无法查询数据，后台追踪任务不会启动。",
        )

    enable_opendota = reader.boolean("enable_opendota_fallback", defaults.enable_opendota_fallback)
    opendota_key = reader.string("opendota_api_key")
    ip_stack = reader.choice("opendota_ip_stack", defaults.opendota_ip_stack, ("auto", "ipv4"))
    proxy = reader.string("proxy_address")
    if proxy and "://" not in proxy:
        reader.warn("proxy_address", "format", "代理地址缺少协议前缀（如 http:// 或 socks5://），可能无法使用。")
    if enable_opendota and proxy and ip_stack == "ipv4":
        reader.warn("opendota_ip_stack", "ignored", "已配置代理，OpenDota 的 IPv4 强制选项将被忽略（与原插件一致）。")

    aliases: list[CustomItemAlias] = []
    raw_aliases = reader.get("custom_item_alias", [])
    if isinstance(raw_aliases, list):
        for index, entry in enumerate(raw_aliases):
            if not isinstance(entry, Mapping):
                reader.warn("custom_item_alias", "type", f"custom_item_alias[{index}] 格式错误，已忽略。")
                continue
            keyword = str(entry.get("keyword") or "").strip()
            alias = str(entry.get("alias") or "").strip()
            if not keyword or not alias:
                reader.warn("custom_item_alias", "incomplete", f"custom_item_alias[{index}] 缺少关键词或别名，已忽略。")
                continue
            aliases.append(CustomItemAlias(keyword=keyword, alias=alias))

    weekly = reader.get("weekly_report_day_hours", list(defaults.weekly_report_day_hours))
    weekly_pair = defaults.weekly_report_day_hours
    if isinstance(weekly, (list, tuple)) and len(weekly) == 2:
        try:
            day, hour = int(weekly[0]), int(weekly[1])
            if 1 <= day <= 7 and 0 <= hour <= 23:
                weekly_pair = (day, hour)
            else:
                reader.warn(
                    "weekly_report_day_hours", "range", "周报时间应为 [周几(1-7), 小时(0-23)]，已使用默认值 [1, 10]。"
                )
        except (TypeError, ValueError):
            reader.warn("weekly_report_day_hours", "type", "周报时间格式错误，已使用默认值 [1, 10]。")
    else:
        reader.warn("weekly_report_day_hours", "type", "周报时间应为两个数字 [周几, 小时]，已使用默认值 [1, 10]。")

    config = PluginConfig(
        stratz_api_token=token,
        data_parsing_timeout_minutes=reader.integer("data_parsing_timeout_minutes", 60, 0, 1440),
        proxy_address=proxy,
        suppress_api_network_errors=reader.boolean("suppress_api_network_errors", False),
        enable_opendota_fallback=enable_opendota,
        opendota_api_key=opendota_key,
        opendota_ip_stack=ip_stack,  # type: ignore[arg-type]
        default_locale=reader.choice("default_locale", defaults.default_locale, SUPPORTED_LOCALES),
        use_hero_nicknames=reader.boolean("use_hero_nicknames", True),
        url_in_message_type=reader.string_list("url_in_message_type", URL_MESSAGE_TYPES),
        max_send_item_count=reader.integer("max_send_item_count", 5, 1, 10),
        show_item_list_at_too_much_items=reader.boolean("show_item_list_at_too_much_items", True),
        custom_item_alias=tuple(aliases),
        auto_recall_tips=reader.boolean("auto_recall_tips", True),
        rank_broad_switch=reader.boolean("rank_broad_switch", False),
        rank_broad_star=reader.boolean("rank_broad_star", True),
        rank_broad_leader=reader.boolean("rank_broad_leader", True),
        rank_broad_fun=reader.boolean("rank_broad_fun", False),
        daily_report_switch=reader.boolean("daily_report_switch", False),
        daily_report_hours=reader.integer("daily_report_hours", 6, 0, 23),
        daily_report_show_combi=reader.boolean("daily_report_show_combi", True),
        weekly_report_switch=reader.boolean("weekly_report_switch", False),
        weekly_report_day_hours=weekly_pair,
        weekly_report_show_combi=reader.boolean("weekly_report_show_combi", True),
        template_match=reader.choice("template_match", "match_1", MATCH_TEMPLATES),
        template_player=reader.choice("template_player", "player_1", PLAYER_TEMPLATES),
        template_hero=reader.choice("template_hero", "hero_1", HERO_TEMPLATES),
        player_rank_estimate=reader.boolean("player_rank_estimate", True),
        patch_notes_retrieval_depth=reader.integer("patch_notes_retrieval_depth", 2, 1, 5),
        render_scale=reader.choice("render_scale", "high", RENDER_SCALES),  # type: ignore[arg-type]
        render_image_type=reader.choice("render_image_type", "png", RENDER_IMAGE_TYPES),  # type: ignore[arg-type]
        render_jpeg_quality=reader.integer("render_jpeg_quality", 90, 10, 100),
    )
    return config, reader.issues
