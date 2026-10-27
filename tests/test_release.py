"""scripts/release.py: version consistency, CHANGELOG extraction and the plugin zip."""

from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("d2t_release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

## [1.2.0-rc.1] - 2026-11-01

Release candidate.

### 新增

- 模块：说明。

**Full Changelog**: https://example.invalid/compare/v1.1.1...v1.2.0-rc.1

## [1.1.1] - 2026-10-27

Fixes.

### 修复

- A：B。

## [1.1.0] - 2026-10-27

Older.
"""


def test_versions_agree():
    versions = release.read_versions()
    assert len(set(versions.values())) == 1
    assert release.current_version() == versions["metadata.yaml"]
    # Every released version needs a CHANGELOG entry.
    assert release.changelog_notes(release.current_version()).strip()


def test_version_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(
        release,
        "read_versions",
        lambda: {"metadata.yaml": "1.2.0", "dota2tracker/constants.py": "1.1.9", "pyproject.toml": "1.2.0"},
    )
    with pytest.raises(release.ReleaseError, match="disagree"):
        release.current_version()


def test_changelog_section_extraction():
    notes = release.changelog_notes("1.1.1", CHANGELOG)
    assert notes == "Fixes.\n\n### 修复\n\n- A：B。\n"
    prerelease = release.changelog_notes("1.2.0-rc.1", CHANGELOG)
    assert prerelease.startswith("Release candidate.") and "**Full Changelog**" in prerelease
    assert "Fixes." not in prerelease
    assert release.changelog_notes("1.1.0", CHANGELOG) == "Older.\n"
    with pytest.raises(release.ReleaseError, match="no entry"):
        release.changelog_notes("1.1", CHANGELOG)  # no prefix matches


def test_main_reports_errors_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(release, "current_version", lambda: (_ for _ in ()).throw(release.ReleaseError("boom")))
    assert release.main(["version"]) == 1
    assert "::error::boom" in capsys.readouterr().err
    monkeypatch.undo()
    assert release.main([]) == 2  # usage


def test_package_contents(tmp_path):
    version = release.current_version()
    archive = release.build_package(tmp_path, version)
    assert archive.name == f"astrbot_plugin_dota2tracker-v{version}.zip"
    names = zipfile.ZipFile(archive).namelist()
    assert all(name.startswith("astrbot_plugin_dota2tracker/") for name in names)
    inner = {name.split("/", 1)[1] for name in names}
    for required in ("main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt", "logo.png", "LICENSE"):
        assert required in inner
    assert any(name.startswith("views/overview/") for name in inner)
    assert any(name.startswith(".astrbot-plugin/i18n/") for name in inner)
    assert any(name.startswith("dota2tracker/templates/") for name in inner)
    for excluded in ("tests/", "scripts/", ".github/", "old/", "pyproject.toml", "requirements-dev.txt"):
        assert not any(name.startswith(excluded) for name in inner)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in inner)
    digest, name = (tmp_path / f"{archive.name}.sha256").read_text().split()
    assert name == archive.name and digest == hashlib.sha256(archive.read_bytes()).hexdigest()
