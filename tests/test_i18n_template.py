"""The i18n template interpreter must render exactly like @satorijs/element."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dota2tracker.i18n.template import parse_template, render_html, render_text, satori_trim

CASES = json.loads((Path(__file__).parent / "fixtures" / "satori_reference.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["src"][:24] for c in CASES])
def test_matches_satori_markup_and_plain(case):
    assert render_html(case["src"], case["ctx"]) == case["markup"]
    assert render_text(case["src"], case["ctx"]) == case["plain"]


def test_satori_trim_only_removes_newline_whitespace():
    assert satori_trim("  text  ") == "  text  "
    assert satori_trim("\n  text \n ") == "text"
    assert satori_trim("line1\nline2\n") == "line1\nline2"


def test_unknown_identifiers_are_falsy_and_safe():
    nodes = parse_template("{#if __import__}x{:else}y{/if}", {})
    assert [n.content for n in nodes] == ["y"]
    assert render_text("{#if a.b.c}x{/if}", {"a": None}) == ""
    assert render_text("{(1 + 2) * 3}", {}) == "9"
