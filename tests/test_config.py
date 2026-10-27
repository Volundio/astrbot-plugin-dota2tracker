"""Tests for :mod:`dota2tracker.config`."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from dota2tracker.config import CustomItemAlias, PluginConfig, load_config

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"
SECRETS = ("stratz_api_token", "opendota_api_key", "proxy_address")


def codes(issues, name=None):
    return [(i.field, i.code) for i in issues if name is None or i.field == name]


def test_empty_config_uses_defaults_and_reports_missing_token():
    config, issues = load_config(None)
    assert config == PluginConfig()
    assert codes(issues) == [("stratz_api_token", "missing")]
    assert issues[0].level == "error"
    assert not config.has_stratz_token


def test_token_is_stripped():
    config, issues = load_config({"stratz_api_token": "  abc  "})
    assert config.stratz_api_token == "abc"
    assert config.has_stratz_token
    assert issues == []


@pytest.mark.parametrize(
    ("name", "value", "expected", "code"),
    [
        ("data_parsing_timeout_minutes", -5, 0, "range"),
        ("data_parsing_timeout_minutes", 5000, 1440, "range"),
        ("data_parsing_timeout_minutes", "30", 30, None),
        ("data_parsing_timeout_minutes", "abc", 60, "type"),
        ("data_parsing_timeout_minutes", 1.5, 60, "type"),
        ("data_parsing_timeout_minutes", 2.0, 2, None),
        ("max_send_item_count", 0, 1, "range"),
        ("max_send_item_count", 11, 10, "range"),
        ("daily_report_hours", 24, 23, "range"),
        ("patch_notes_retrieval_depth", 9, 5, "range"),
        ("render_jpeg_quality", 1, 10, "range"),
    ],
)
def test_integer_ranges(name, value, expected, code):
    config, issues = load_config({"stratz_api_token": "t", name: value})
    assert getattr(config, name) == expected
    assert codes(issues, name) == ([(name, code)] if code else [])


def test_booleans_accept_strings_and_reject_garbage():
    config, issues = load_config(
        {"stratz_api_token": "t", "rank_broad_switch": "TRUE", "use_hero_nicknames": "false", "auto_recall_tips": 3}
    )
    assert config.rank_broad_switch is True
    assert config.use_hero_nicknames is False
    assert config.auto_recall_tips is True
    assert codes(issues) == [("auto_recall_tips", "type")]


def test_none_values_fall_back_to_defaults_silently():
    config, issues = load_config({"stratz_api_token": "t", "max_send_item_count": None})
    assert config.max_send_item_count == 5
    assert issues == []


@pytest.mark.parametrize(
    ("name", "value", "default"),
    [
        ("template_match", "match_9", "match_1"),
        ("template_player", "x", "player_1"),
        ("template_hero", "x", "hero_1"),
        ("render_scale", "huge", "high"),
        ("render_image_type", "gif", "png"),
        ("opendota_ip_stack", "ipv6", "auto"),
        ("default_locale", "fr-FR", "zh-CN"),
    ],
)
def test_choices(name, value, default):
    config, issues = load_config({"stratz_api_token": "t", name: value})
    assert getattr(config, name) == default
    assert codes(issues, name) == [(name, "choice")]


def test_valid_choices_are_kept():
    config, issues = load_config(
        {
            "stratz_api_token": "t",
            "template_match": "match_2+",
            "render_scale": "ultra",
            "render_image_type": "jpeg",
            "default_locale": "en-US",
        }
    )
    assert (config.template_match, config.render_scale, config.render_image_type, config.default_locale) == (
        "match_2+",
        "ultra",
        "jpeg",
        "en-US",
    )
    assert issues == []


def test_url_in_message_type_list():
    config, issues = load_config(
        {"stratz_api_token": "t", "url_in_message_type": ["match", "bogus", "", 3, "match", "hero"]}
    )
    assert config.url_in_message_type == ("match", "hero")
    assert config.url_enabled("match") and not config.url_enabled("player")
    assert codes(issues) == [("url_in_message_type", "choice")]


def test_string_list_strips_and_dedupes():
    config, _ = load_config({"stratz_api_token": "t", "url_in_message_type": ["hero", " hero ", "match"]})
    assert config.url_in_message_type == ("hero", "match")
    config, issues = load_config({"stratz_api_token": "t", "url_in_message_type": [" match "]})
    assert config.url_in_message_type == ("match",)
    assert issues == []


def test_url_in_message_type_not_a_list():
    config, issues = load_config({"stratz_api_token": "t", "url_in_message_type": "match"})
    assert config.url_in_message_type == ()
    assert codes(issues) == [("url_in_message_type", "type")]


def test_custom_item_alias_including_template_list_entries():
    raw = [
        {"__template_key": "alias", "keyword": " 闪烁匕首 ", "alias": "跳刀"},
        {"keyword": "黑皇杖", "alias": "bkb"},
        {"__template_key": "alias", "keyword": "", "alias": "x"},
        "bad",
    ]
    config, issues = load_config({"stratz_api_token": "t", "custom_item_alias": raw})
    assert config.custom_item_alias == (CustomItemAlias("闪烁匕首", "跳刀"), CustomItemAlias("黑皇杖", "bkb"))
    assert codes(issues) == [("custom_item_alias", "incomplete"), ("custom_item_alias", "type")]


@pytest.mark.parametrize(
    ("value", "expected", "code"),
    [
        ([1, 10], (1, 10), None),
        (["1", "10"], (1, 10), None),
        ([7, 23], (7, 23), None),  # 7 = Sunday
        ([0, 10], (1, 10), "range"),
        ([8, 10], (1, 10), "range"),
        ([1, 24], (1, 10), "range"),
        (["a", 1], (1, 10), "type"),
        ([1], (1, 10), "type"),
        ("1,10", (1, 10), "type"),
    ],
)
def test_weekly_report_day_hours(value, expected, code):
    config, issues = load_config({"stratz_api_token": "t", "weekly_report_day_hours": value})
    assert config.weekly_report_day_hours == expected
    name = "weekly_report_day_hours"
    assert codes(issues, name) == ([(name, code)] if code else [])


def test_proxy_and_ipv4_warnings():
    config, issues = load_config(
        {
            "stratz_api_token": "t",
            "proxy_address": "http://127.0.0.1:7890",
            "enable_opendota_fallback": True,
            "opendota_ip_stack": "ipv4",
        }
    )
    assert config.proxy_address == "http://127.0.0.1:7890"
    assert codes(issues) == [("opendota_ip_stack", "ignored")]


@pytest.mark.parametrize(
    "proxy",
    ["127.0.0.1:7890", "socks4://user:SECRET@h:1", "http://", "ftp://h:1", "http://[::1"],
)
def test_invalid_proxy_is_an_error_and_ignored(proxy):
    config, issues = load_config({"stratz_api_token": "t", "proxy_address": proxy})
    assert config.proxy_address == ""
    assert codes(issues) == [("proxy_address", "format")]
    assert issues[0].level == "error"
    assert "SECRET" not in issues[0].message  # the value may contain credentials


@pytest.mark.parametrize("proxy", ["http://h:1", "HTTPS://h:1", "socks5://u:p@h:1", "socks5h://h:1"])
def test_valid_proxy_schemes(proxy):
    config, issues = load_config({"stratz_api_token": "t", "proxy_address": proxy})
    assert config.proxy_address == proxy and issues == []


def test_wrong_type_string_field_warns():
    config, issues = load_config({"stratz_api_token": 123})
    assert config.stratz_api_token == ""
    assert ("stratz_api_token", "type") in codes(issues)
    assert ("stratz_api_token", "missing") in codes(issues)


def test_redacted_and_repr_never_expose_secrets():
    config, _ = load_config(
        {
            "stratz_api_token": "SECRET-STRATZ-TOKEN",
            "opendota_api_key": "SECRET-OD-KEY",
            "proxy_address": "http://user:SECRET-PASS@proxy:1",
            "custom_item_alias": [{"keyword": "a", "alias": "b"}],
            "url_in_message_type": ["match"],
        }
    )
    data = config.redacted()
    assert data["stratz_api_token"] == {"configured": True}
    assert data["opendota_api_key"] == {"configured": True}
    assert data["proxy_address"] == {"configured": True}
    assert data["custom_item_alias"] == [{"keyword": "a", "alias": "b"}]
    assert data["url_in_message_type"] == ["match"]
    dumped = json.dumps(data, ensure_ascii=False)  # must be JSON serialisable
    text = dumped + repr(config) + str(config)
    assert "SECRET" not in text
    assert set(data) == {f.name for f in fields(PluginConfig)}
    assert PluginConfig().redacted()["stratz_api_token"] == {"configured": False}


# ------------------------------------------------------------------ schema
@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_defaults(schema: dict) -> dict:
    result = {}
    for name, spec in schema.items():
        if spec.get("type") == "object":
            result[name] = {k: v.get("default") for k, v in spec.get("items", {}).items()}
        else:
            result[name] = spec.get("default")
    return result


def test_schema_covers_every_field(schema):
    assert set(schema) == {f.name for f in fields(PluginConfig)}


def test_schema_marks_secrets(schema):
    for name in SECRETS:
        assert schema[name].get("secret") is True, name
    for name, spec in schema.items():
        if name not in SECRETS:
            assert not spec.get("secret"), name


def test_schema_defaults_match_dataclass(schema):
    config, issues = load_config(schema_defaults(schema))
    assert config == PluginConfig()
    assert codes(issues) == [("stratz_api_token", "missing")]


def test_schema_options_match_choices(schema):
    assert set(schema["opendota_ip_stack"]["options"]) == {"auto", "ipv4"}
    for name in ("template_match", "render_scale", "render_image_type", "default_locale"):
        options = schema[name].get("options")
        if options:
            for option in options:
                config, issues = load_config({"stratz_api_token": "t", name: option})
                assert getattr(config, name) == option
                assert issues == []
