"""Behavioural parity tests for the JavaScript compatibility helpers."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dota2tracker.utils import jsnum
from dota2tracker.utils.koishi_random import KoishiRandom, enhanced_simple_hash_to_seed, seeded_random
from dota2tracker.utils.timefmt import from_seconds, luxon_diff, luxon_format, relative_time

FIXTURES = Path(__file__).parent / "fixtures"
JS = json.loads((FIXTURES / "js_reference.json").read_text(encoding="utf-8"))
RANDOM = json.loads((FIXTURES / "js_random_reference.json").read_text(encoding="utf-8"))


def _num(value):
    # JSON cannot encode NaN/Infinity; the reference stores the String() result too.
    return value


@pytest.mark.parametrize(("expected", "value"), JS["toString"])
def test_js_str_matches_number_to_string(expected, value):
    if value is None:
        number = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[expected]
    else:
        number = float(value)
    assert jsnum.js_str(number) == expected


@pytest.mark.parametrize(("value", "digits", "expected"), JS["toFixed"])
def test_js_to_fixed(value, digits, expected):
    number = math.nan if value is None else value
    if value is None and expected in ("Infinity", "-Infinity"):
        number = math.inf if expected == "Infinity" else -math.inf
    assert jsnum.js_to_fixed(number, digits) == expected


@pytest.mark.parametrize(("value", "expected"), JS["round"])
def test_js_round(value, expected):
    assert jsnum.js_round(value) == expected


@pytest.mark.parametrize("row", JS["locale"])
def test_to_locale_string(row):
    value, expected, *_ = row
    assert jsnum.to_locale_string(value) == expected


def test_js_div_and_number_helpers():
    assert jsnum.js_div(1, 0) == math.inf
    assert jsnum.js_div(-1, 0) == -math.inf
    assert math.isnan(jsnum.js_div(0, 0))
    assert jsnum.format_number(1234567) == "1,234,567"
    assert jsnum.format_number(1234.5678) == "1,234.5,678"  # same quirk as the JS regex
    assert jsnum.js_parse_int("54") == 54
    assert math.isnan(jsnum.js_parse_int("abc"))
    assert jsnum.js_number(" 12 ") == 12
    assert jsnum.js_number("0x10") == 16
    assert jsnum.js_number("1e3") == 1000
    assert math.isnan(jsnum.js_number("12a"))
    assert jsnum.round_to_decimal_places(12.345) == 12.35  # 12.345 * 100 == 1234.5 in IEEE 754
    assert jsnum.round_to_decimal_places(1 / 3) == 0.33
    assert jsnum.utf16_length("😀") == 2
    assert jsnum.js_object_key_order(["b", "10", "2", "a", "01"]) == ["2", "10", "b", "a", "01"]


@pytest.mark.parametrize(("text", "expected"), RANDOM["seeds"])
def test_seed_hash(text, expected):
    assert enhanced_simple_hash_to_seed(text) == expected


@pytest.mark.parametrize("row", RANDOM["picks"])
def test_koishi_random_sequence(row):
    hero_ids = ["1", "2", "3", "5", "10", "23", "100", "145", "155"]
    rnd = seeded_random(row["s"])
    assert rnd.pick(hero_ids) == row["pick"]
    assert rnd.bool(0.5) == row["bool"]
    assert rnd.int(0, 8) == row["int08"]
    assert rnd.int(0, 30) == row["int030"]
    assert rnd.int(0, 20) == row["int020"]
    assert str(rnd.weighted_pick({"3": 2.5, "10": 7.25, "1": 0.5, "100": 12})) == row["weighted"]
    assert rnd.pick(["a", "b", "c", "d"], 2) == row["pickArr"]


def test_weighted_pick_returns_none_when_total_is_zero():
    assert KoishiRandom(lambda: 0.5).weighted_pick({1: 0, 2: 0}) is None


@pytest.mark.parametrize("fixture", ["luxon_reference_shanghai.json", "luxon_reference_newyork.json"])
def test_luxon_format_and_relative(fixture):
    data = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    tz = ZoneInfo(data["zone"])
    for seconds, fmt, locale, expected in data["format"]:
        assert luxon_format(from_seconds(seconds, tz), fmt, locale) == expected, (seconds, fmt, locale)
    base = from_seconds(1761480000, tz)
    for delta, locale, expected in data["relative"]:
        target = datetime.fromtimestamp(1761480000 - delta, tz)
        assert relative_time(target, base, locale) == expected, (delta, locale)
    for delta, expected in data["diffMonths"]:
        target = datetime.fromtimestamp(1761480000 - delta, tz)
        assert luxon_diff(base, target, "months") == pytest.approx(expected), delta
    for a, b, months, days, years, rel_zh, rel_en in data["pairs"]:
        ta, tb = from_seconds(a, tz), from_seconds(b, tz)
        assert luxon_diff(tb, ta, "months") == pytest.approx(months)
        assert luxon_diff(tb, ta, "days") == pytest.approx(days)
        assert luxon_diff(tb, ta, "years") == pytest.approx(years)
        assert relative_time(ta, tb, "zh-cn") == rel_zh
        assert relative_time(ta, tb, "en-us") == rel_en
