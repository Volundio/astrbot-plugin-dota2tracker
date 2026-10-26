"""Command metadata shared by ``main.py`` registration, argument parsing and the help table.

Each command is reachable three ways in AstrBot:

* ``/dota2tracker <name>`` – the command group form (AstrBot names may not contain spaces);
* ``/<中文别名>`` – the original Chinese aliases;
* ``/dota2tracker.<name>`` – the original Koishi command name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["COMMANDS", "COMMANDS_BY_KEY", "CommandSpec", "OptionSpec", "extract_raw_args", "help_rows"]

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
    def display_name(self) -> str:
        return f"{GROUP} {self.key}"

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


def help_rows(raw_text, text) -> list[list[str]]:
    """Rows of the help table (``MessageBuilder.buildHelpMessage``).

    ``raw_text(key)`` returns the raw locale entry (``ctx.i18n._data``) and
    ``text(key)`` the rendered translation.
    """
    rows = []
    for spec in COMMANDS:
        options = []
        for option in spec.options:
            description = raw_text(f"{spec.i18n_prefix}.options.{option.name}")
            if not description:
                continue
            for flag in option.flags:
                if description.startswith(flag + " "):
                    description = description[len(flag) + 1 :]
                    break
            options.append(f"[{option.syntax}] {description}")
        rows.append(
            [
                spec.display_name,
                "\n".join([*spec.aliases, spec.legacy_name]),
                spec.arguments,
                text(f"{spec.i18n_prefix}.usage") or "",
                "\n".join(options),
                text(f"{spec.i18n_prefix}.examples") or "",
            ]
        )
    return rows


def extract_raw_args(message: str, spec: CommandSpec) -> str:
    """Text after the command name, whichever of the three forms was used.

    AstrBot strips the wake prefix from ``message_str``; a leftover ``/`` is
    tolerated. Unknown forms return the text unchanged minus its first token.
    """
    text = message.strip().lstrip("/")
    names = sorted([spec.legacy_name, *spec.aliases], key=len, reverse=True)
    group = re.match(rf"^{re.escape(GROUP)}\s+{re.escape(spec.key)}(?:\s+|$)", text)
    if group:
        return text[group.end() :].strip()
    for name in names:
        if text == name:
            return ""
        if text.startswith(name) and text[len(name)].isspace():
            return text[len(name) :].strip()
    parts = text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""
