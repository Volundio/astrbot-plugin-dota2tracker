"""Koishi-like argument parsing and command registry helpers."""

from __future__ import annotations

import math

import pytest

from dota2tracker.commands.args import parse_args, tokenize
from dota2tracker.commands.registry import COMMANDS, COMMANDS_BY_KEY, extract_raw_args, help_rows, wants_help

MATCH = COMMANDS_BY_KEY["query-match"].options
PLAYER = COMMANDS_BY_KEY["query-player"].options
HERO = COMMANDS_BY_KEY["query-hero"].options
DAYS = COMMANDS_BY_KEY["hero-of-the-day"].options
BIND_OPTIONS = COMMANDS_BY_KEY["bind"].options


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("  a   b ", [("a", False), ("b", False)]),
        ('"hello world" x', [("hello world", True), ("x", False)]),
        ("'a b'", [("a b", True)]),
        ("“中文 别名” 1", [("中文 别名", True), ("1", False)]),
        ("‘x y’", [("x y", True)]),
        ('"unterminated rest', [("unterminated rest", True)]),
        ('""', [("", True)]),
    ],
)
def test_tokenize(text, expected):
    assert tokenize(text) == expected


def test_positional_and_none():
    assert parse_args(None).args == []
    parsed = parse_args("123 “a b”")
    assert parsed.args == ["123", "a b"]
    assert parsed.arg(0) == "123" and parsed.arg(5) is None


def test_parse_flag_short_and_long():
    assert parse_args("-p 123", MATCH).options == {"parse": True}
    assert parse_args("123 --parse", MATCH).args == ["123"]
    assert parse_args("123 --parse", MATCH).options == {"parse": True}


@pytest.mark.parametrize("text", ["123 -t match_2", "-t match_2 123", "123 -t=match_2", "123 --template match_2"])
def test_template_value(text):
    parsed = parse_args(text, MATCH)
    assert parsed.args == ["123"]
    assert parsed.options == {"template": "match_2"}


def test_combined_short_flags():
    parsed = parse_args("-pt match_2 123", MATCH)
    assert parsed.options == {"parse": True, "template": "match_2"}
    assert parsed.args == ["123"]
    # A value option not in last position gets no value
    parsed = parse_args("-tp 123", MATCH)
    assert parsed.options == {"template": "", "parse": True}
    assert parsed.args == ["123"]


def test_template_missing_value():
    assert parse_args("123 -t", MATCH).options == {"template": ""}
    assert parse_args("-t -p", MATCH).options == {"template": "", "parse": True}


def test_hero_option_with_quoted_value():
    parsed = parse_args("“小 明” -o “敌法 师”", PLAYER)
    assert parsed.args == ["小 明"]
    assert parsed.options == {"hero": "敌法 师"}


def test_random_flag():
    assert parse_args("-r", HERO).options == {"random": True}


@pytest.mark.parametrize(
    ("text", "days"),
    [("-d 7", 7), ("-d 7.5", 7.5), ("-d=-3", -3), ("-d -3", -3), ("--days 90", 90)],
)
def test_number_option(text, days):
    assert parse_args(text, DAYS).options["days"] == days


@pytest.mark.parametrize("text", ["-d abc", "-d"])
def test_number_option_nan(text):
    parsed = parse_args(text, DAYS)
    assert math.isnan(parsed.options["days"])


def test_negative_numbers_are_positional():
    assert parse_args("-5 -1.5 .5", DAYS).args == ["-5", "-1.5", ".5"]


def test_unknown_options_collected():
    parsed = parse_args("123 -x --foo=bar --baz -", MATCH)
    assert parsed.args == ["123", "-"]
    assert parsed.options == {"x": True, "foo": "bar", "baz": True}


def test_quoted_dash_is_positional():
    assert parse_args('"-p"', MATCH).args == ["-p"]


# ----------------------------------------------------------------- registry
def test_registry_order_and_keys():
    assert [c.key for c in COMMANDS] == [
        "help",
        "subscribe",
        "unsubscribe",
        "bind",
        "unbind",
        "rename",
        "query-members",
        "query-match",
        "query-recent-match",
        "query-player",
        "query-hero",
        "query-item",
        "hero-of-the-day",
    ]


BIND = COMMANDS_BY_KEY["bind"].names


@pytest.mark.parametrize(
    "message",
    ["/绑定 123 小明", "绑定 123 小明", "dota2tracker.bind 123 小明", "  /dota2tracker.bind   123 小明"],
)
def test_extract_raw_args_forms(message):
    assert extract_raw_args(message, BIND) == "123 小明"


@pytest.mark.parametrize("message", ["绑定", "/绑定", "dota2tracker.bind", "  绑定  "])
def test_extract_raw_args_without_arguments(message):
    assert extract_raw_args(message, BIND) == ""


def test_extract_raw_args_alias_prefix_not_confused():
    # "绑定x" is not the "绑定" alias followed by whitespace: falls back to first-token split.
    assert extract_raw_args("绑定x 1", BIND) == "1"
    assert extract_raw_args("取消绑定", COMMANDS_BY_KEY["unbind"].names) == ""
    assert extract_raw_args("DOTA2帮助", COMMANDS_BY_KEY["help"].names) == ""


def test_extract_raw_args_follows_renamed_commands():
    # Names come from the live AstrBot filter: a renamed command or a name with spaces.
    assert extract_raw_args("/bd 123 小明", ["bd", "绑定"]) == "123 小明"
    assert extract_raw_args("dt   bind 123", ["dt bind"]) == "123"
    assert extract_raw_args("dt bind", ["dt bind"]) == ""


def test_extract_raw_args_keeps_quotes_and_options():
    names = COMMANDS_BY_KEY["query-match"].names
    assert extract_raw_args("/查询比赛 123 -p -t match_2", names) == "123 -p -t match_2"
    names = COMMANDS_BY_KEY["query-player"].names
    assert extract_raw_args("查询玩家 “a b” -o 敌法", names) == "“a b” -o 敌法"
    assert extract_raw_args("改名 a\nb", COMMANDS_BY_KEY["rename"].names) == "a\nb"


def test_wants_help():
    assert wants_help(parse_args("-h", BIND_OPTIONS).options)
    assert wants_help(parse_args("123 --help", BIND_OPTIONS).options)
    assert not wants_help(parse_args("123 x", BIND_OPTIONS).options)


def test_help_rows(i18n):
    rows = help_rows(lambda key: i18n.raw("zh-CN", key), lambda key: i18n.t("zh-CN", key))
    assert len(rows) == len(COMMANDS)
    assert all(len(row) == 6 for row in rows)
    bind = rows[3]
    # Same columns as the original: command name, then the other aliases.
    assert bind[0] == "dota2tracker.bind"
    assert bind[1] == "绑定"
    assert bind[2] == "<steam_id> [nick_name]"
    assert bind[3]
    match = rows[7]
    assert "[-p, --parse]" in match[4]
    # The locale has no description for -t, so (like Koishi) it is omitted.
    assert match[4] == "[-p, --parse] 是否等待解析比赛数据"
    assert rows[12][4].startswith("[-d, --days <value>] <number> ")
    # The flag prefix of the locale description is stripped once
    for line in match[4].split("\n"):
        assert not line.split("] ", 1)[1].startswith("-p ")
    assert rows[0][4] == ""
    assert rows[0][1] == "DOTA2指南\nDOTA2帮助\nDOTA2说明"


def test_help_rows_missing_text():
    rows = help_rows(lambda key: None, lambda key: None)
    assert rows[7][3:] == ["", "", ""]
