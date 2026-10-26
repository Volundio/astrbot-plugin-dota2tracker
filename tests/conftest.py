"""Shared pytest fixtures."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# AstrBot writes data/cmd_config.json under its root on import; keep it out of the repo.
os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="d2t-astrbot-root-"))

from dota2tracker.constants import FIXTURES_DIR
from dota2tracker.data.static import DotaConstants
from dota2tracker.i18n.service import I18nService
from dota2tracker.presentation.assets import ImageAssets
from dota2tracker.presentation.previews import PreviewContext
from dota2tracker.presentation.renderer import Renderer

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture(scope="session")
def constants() -> DotaConstants:
    return DotaConstants.load()


@pytest.fixture(scope="session")
def i18n(constants) -> I18nService:
    return I18nService(default_locale="zh-CN", hero_ids=constants.hero_ids)


@pytest.fixture(scope="session")
def assets() -> ImageAssets:
    return ImageAssets()


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("dota2tracker.tests")


def load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixture_loader():
    return load


@pytest.fixture(scope="session")
def strict_renderer(i18n, assets) -> Renderer:
    """Renderer without t2i: ``render_html`` with ``StrictUndefined``."""
    return Renderer(
        i18n=i18n,
        assets=assets,
        html_render=None,
        options=lambda: {"type": "png"},
        strict=True,
    )


@pytest.fixture
def preview_context(constants, i18n, assets):
    def factory(locale: str = "zh-CN", variant: str = "") -> PreviewContext:
        return PreviewContext(constants, i18n, assets, locale, TZ, variant=variant)

    return factory


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite3"
