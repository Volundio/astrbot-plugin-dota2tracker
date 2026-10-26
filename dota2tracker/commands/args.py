"""Koishi-like argument parsing for command handlers.

AstrBot passes the text after the command name; Koishi's ``Argv`` semantics
are reproduced: whitespace separated tokens, ``"…"``, ``'…'``, ``“…”`` and
``‘…’`` quoting, ``-p`` flags, ``-t value`` / ``-t=value`` / ``--template value``
options and combined short flags (``-pt match_2``). Positional arguments keep
their order; unknown options are collected but ignored by handlers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .registry import OptionSpec

__all__ = ["ParsedArgs", "parse_args", "tokenize"]

_QUOTES = {'"': '"', "'": "'", "“": "”", "‘": "’"}
_NUMBER = re.compile(r"^-?(\d+\.?\d*|\.\d+)$")


@dataclass
class ParsedArgs:
    args: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    def arg(self, index: int) -> str | None:
        return self.args[index] if index < len(self.args) else None


def tokenize(text: str) -> list[tuple[str, bool]]:
    """Split ``text`` into ``(token, quoted)`` pairs."""
    tokens: list[tuple[str, bool]] = []
    index, length = 0, len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        char = text[index]
        if char in _QUOTES:
            closing = text.find(_QUOTES[char], index + 1)
            if closing == -1:
                tokens.append((text[index + 1 :], True))
                break
            tokens.append((text[index + 1 : closing], True))
            index = closing + 1
            continue
        end = index
        while end < length and not text[end].isspace():
            end += 1
        tokens.append((text[index:end], False))
        index = end
    return tokens


def _convert(option: OptionSpec, value: str | None) -> Any:
    if option.value == "number":
        if value is None:
            return math.nan
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return math.nan
    return "" if value is None else value


def parse_args(text: str | None, options: tuple[OptionSpec, ...] = ()) -> ParsedArgs:
    by_short = {option.short.lstrip("-"): option for option in options}
    by_long = {option.name: option for option in options}
    result = ParsedArgs()
    tokens = tokenize(text or "")
    index = 0
    while index < len(tokens):
        token, quoted = tokens[index]
        index += 1
        if quoted or not token.startswith("-") or token == "-" or _NUMBER.match(token):
            result.args.append(token)
            continue
        body, _, inline_value = token.lstrip("-").partition("=")
        has_inline = "=" in token
        if token.startswith("--"):
            names = [body]
            lookup = by_long
        else:
            names = list(body)
            lookup = by_short
        for position, name in enumerate(names):
            option = lookup.get(name)
            if option is None:
                result.options[name] = inline_value if has_inline and position == len(names) - 1 else True
                continue
            if option.value is None:
                result.options[option.name] = True
                continue
            if position != len(names) - 1:
                result.options[option.name] = _convert(option, None)
                continue
            if has_inline:
                value: str | None = inline_value
            elif index < len(tokens) and (
                tokens[index][1] or not tokens[index][0].startswith("-") or _NUMBER.match(tokens[index][0])
            ):
                value = tokens[index][0]
                index += 1
            else:
                value = None
            result.options[option.name] = _convert(option, value)
    return result
