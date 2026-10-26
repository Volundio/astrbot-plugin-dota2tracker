"""Static identifiers shared by every layer of the plugin."""

from __future__ import annotations

from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_dota2tracker"
PLUGIN_DISPLAY_NAME = "Dota2 Tracker"
PLUGIN_VERSION = "1.1.0"
#: Upstream project this plugin is ported from (GPL-3.0).
UPSTREAM_REPOSITORY = "https://github.com/sjtdev/koishi-plugin-dota2tracker"
UPSTREAM_DOCS = "https://sjtdev.github.io/koishi-plugin-dota2tracker/"

PACKAGE_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = PACKAGE_DIR.parent
LOCALES_DIR = PACKAGE_DIR / "locales"
TEMPLATES_DIR = PACKAGE_DIR / "templates"
ASSETS_DIR = PACKAGE_DIR / "assets"
GRAPHQL_DIR = PACKAGE_DIR / "data" / "graphql"
FIXTURES_DIR = PACKAGE_DIR / "fixtures"

#: Runtime languages; the first entry is the fallback.
SUPPORTED_LOCALES = ("zh-CN", "en-US")
LANGUAGE_TAGS: dict[str, dict[str, str]] = {
    "en-US": {"graphql": "ENGLISH", "valve": "english"},
    "zh-CN": {"graphql": "S_CHINESE", "valve": "schinese"},
}

#: Platform types with protocol extras (tip recall, member list, group card/avatar).
ONEBOT_PLATFORM_TYPES = ("aiocqhttp",)
#: Platforms verified end to end. Others (telegram, discord, …) work through the
#: generic AstrBot API on a best-effort basis without the OneBot extras.
VERIFIED_PLATFORM_TYPES = ONEBOT_PLATFORM_TYPES
#: Platforms where subscriptions and proactive pushes make no sense.
UNSUPPORTED_PLATFORM_TYPES: tuple[str, ...] = ()

#: 30 days in seconds (match cache and sent-match markers).
DAYS_30 = 30 * 24 * 60 * 60

RANK_BRACKETS = (
    "UNCALIBRATED",
    "HERALD",
    "GUARDIAN",
    "CRUSADER",
    "ARCHON",
    "LEGEND",
    "ANCIENT",
    "DIVINE",
    "IMMORTAL",
)

STEAM_CDN = "https://cdn.akamai.steamstatic.com/apps/dota2/images/dota_react"
