"""Static checks for the WebUI views and their i18n files."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / ".astrbot-plugin" / "i18n"
VIEWS = ("overview", "subscriptions", "templates", "diagnostics")


def _load(locale: str) -> dict:
    return json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))


def _get(data, key):
    for part in key.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _leaves(data, prefix=""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _leaves(value, f"{prefix}{key}.")
        else:
            yield prefix + key


def test_generated_views_are_up_to_date():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/views/build.py"), "--check"], capture_output=True)
    assert result.returncode == 0, result.stdout.decode()


def test_i18n_locales_have_same_keys():
    assert set(_leaves(_load("zh-CN"))) == set(_leaves(_load("en-US")))


@pytest.mark.parametrize("view", VIEWS)
def test_view_keys_exist(view):
    html = (ROOT / "views" / view / "index.html").read_text(encoding="utf-8")
    literal = set(re.findall(r'["\'](views\.[A-Za-z0-9_.\-]+)["\']', html))
    dynamic = set(re.findall(r"`(views\.[A-Za-z0-9_.]+)\.\$\{", html))
    assert literal
    for locale in ("zh-CN", "en-US"):
        data = _load(locale)
        assert isinstance(_get(data, f"views.{view}.title"), str)
        assert [k for k in literal if not isinstance(_get(data, k), str)] == []
        assert [k for k in dynamic if not isinstance(_get(data, k), dict)] == []


@pytest.mark.parametrize("view", VIEWS)
def test_views_do_not_touch_parent_or_storage(view):
    html = (ROOT / "views" / view / "index.html").read_text(encoding="utf-8")
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "window.parent", "window.top", "innerHTML"):
        assert forbidden not in html


def test_config_schema_is_translated():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    for locale in ("zh-CN", "en-US"):
        config = _load(locale).get("config") or {}
        for key, item in schema.items():
            assert isinstance(_get(config, f"{key}.description"), str), (locale, key)
            if item["type"] == "object":
                for child in item["items"]:
                    assert isinstance(_get(config, f"{key}.{child}.description"), str), (locale, key, child)
