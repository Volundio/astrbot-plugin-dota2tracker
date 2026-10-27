"""Command metadata shared by ``main.py`` registration, argument parsing and help output.

Each command is registered once, exactly like the Koishi original: the name
``dota2tracker.<key>`` plus its Chinese aliases. (An extra ``/dota2tracker
<key>`` command-group form was dropped in 1.1.1: as a second AstrBot handler
it escaped per-command permissions configured on the first one.)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "COMMANDS",
    "COMMANDS_BY_KEY",
    "GROUP",
    "CommandSpec",
    "OptionSpec",
    "extract_raw_args",
    "help_rows",
    "option_entries",
    "wants_help",
]

GROUP = "dota2tracker"


@dataclass(frozen=True)
class OptionSpec:
    name: str
    short: str
    value: str | None = None  # "string" | "number" for options taking a value

    @property
    def syntax(self) -> str:
        """Koishi ``option.syntax`` (``-t, --template <value>``)."""
        text = f"{self.short}, --{self.name}"
        return f"{text} <value>" if self.value else text

    @property
    def flags(self) -> tuple[str, ...]:
        return (self.short, f"--{self.name}")


@dataclass(frozen=True)
class CommandSpec:
    key: str
    aliases: tuple[str, ...]
    arguments: str = ""
    options: tuple[OptionSpec, ...] = ()

    @property
    def legacy_name(self) -> str:
        return f"{GROUP}.{self.key}"

    @property
    def names(self) -> tuple[str, ...]:
        """Default trigger names (AstrBot may rename them at runtime)."""
        return (self.legacy_name, *self.aliases)

    @property
    def i18n_prefix(self) -> str:
        return f"commands.{GROUP}.{self.key}"


PARSE = OptionSpec("parse", "-p")
TEMPLATE = OptionSpec("template", "-t", "string")
HERO = OptionSpec("hero", "-o", "string")
RANDOM = OptionSpec("random", "-r")
DAYS = OptionSpec("days", "-d", "number")

#: Registration order of the Koishi plugin (help table order).
COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("help", ("DOTA2指南", "DOTA2帮助", "DOTA2说明")),
    CommandSpec("subscribe", ("订阅本群",)),
    CommandSpec("unsubscribe", ("取消订阅",)),
    CommandSpec("bind", ("绑定",), "<steam_id> [nick_name]"),
    CommandSpec("unbind", ("取消绑定",)),
    CommandSpec("rename", ("改名",), "<nick_name>"),
    CommandSpec("query-members", ("查询群友",)),
    CommandSpec("query-match", ("查询比赛",), "<match_id>", (PARSE, TEMPLATE)),
    CommandSpec("query-recent-match", ("查询最近比赛",), "[input_data]", (PARSE, TEMPLATE)),
    CommandSpec("query-player", ("查询玩家",), "<input_data>", (HERO,)),
    CommandSpec("query-hero", ("查询英雄",), "<input_data>", (RANDOM,)),
    CommandSpec("query-item", ("查询物品",), "<input_data>"),
    CommandSpec("hero-of-the-day", ("今日英雄",), "<input_data>", (DAYS,)),
)
COMMANDS_BY_KEY = {spec.key: spec for spec in COMMANDS}


def option_entries(raw_text, spec: CommandSpec) -> list[tuple[str, str]]:
    """``(syntax, description)`` for each option that has a description.

    The locale descriptions repeat the flags (``"-p, --parse 说明"``); like
    the original ``buildHelpMessage`` the leading flag is stripped.
    """
    entries = []
    for option in spec.options:
        description = raw_text(f"{spec.i18n_prefix}.options.{option.name}")
        if not description:
            continue
        for flag in option.flags:
            if description.startswith(flag + " "):
                description = description[len(flag) + 1 :]
                break
        entries.append((option.syntax, description))
    return entries


def help_rows(raw_text, text) -> list[list[str]]:
    """Rows of the help table (``MessageBuilder.buildHelpMessage``).

    ``raw_text(key)`` returns the raw locale entry (``ctx.i18n._data``) and
    ``text(key)`` the rendered translation.
    """
    return [
        [
            spec.legacy_name,
            "\n".join(spec.aliases),
            spec.arguments,
            text(f"{spec.i18n_prefix}.usage") or "",
            "\n".join(f"[{syntax}] {description}" for syntax, description in option_entries(raw_text, spec)),
            text(f"{spec.i18n_prefix}.examples") or "",
        ]
        for spec in COMMANDS
    ]


def wants_help(options: dict[str, object]) -> bool:
    """Koishi's implicit ``-h`` / ``--help`` option."""
    return bool(options.get("h") or options.get("help"))


def extract_raw_args(message: str, names: Iterable[str]) -> str:
    """Text after the command name that triggered the handler.

    ``names`` are the handler's current complete command names (they follow
    renames done in AstrBot's command management). AstrBot strips the wake
    prefix from ``message_str``; a leftover ``/`` is tolerated, and names
    containing spaces match any run of whitespace. Unknown forms return the
    text minus its first token.
    """
    text = message.strip().lstrip("/")
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        pattern = r"\s+".join(re.escape(part) for part in name.split())
        found = re.match(rf"^{pattern}(?:\s+|$)", text)
        if found:
            return text[found.end() :].strip()
    parts = text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""
