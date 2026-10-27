"""Release helpers used by ``.github/workflows/release.yml``.

    python scripts/release.py version             # print version/tag/prerelease (GitHub output format)
    python scripts/release.py notes <out.md>      # CHANGELOG.md section of the current version
    python scripts/release.py package <dist_dir>  # build the installable plugin zip (+ .sha256)

The version lives in three files (AGENTS.md "版本与更新记录"); every command
fails when they disagree or when CHANGELOG.md has no entry for the version.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "astrbot_plugin_dota2tracker"

#: Paths (relative to the repository root) shipped in the plugin zip.
PACKAGE_FILES = (
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "logo.png",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
)
PACKAGE_DIRS = ("dota2tracker", "views", ".astrbot-plugin")
#: Never shipped even when tracked inside a packaged directory.
EXCLUDED_SUFFIXES = (".pyc",)
EXCLUDED_PARTS = ("__pycache__",)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


class ReleaseError(Exception):
    pass


def _match(path: str, pattern: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    found = re.search(pattern, text, re.MULTILINE)
    if not found:
        raise ReleaseError(f"no version found in {path}")
    return found.group(1).strip().strip("'\"")


def read_versions() -> dict[str, str]:
    return {
        "metadata.yaml": _match("metadata.yaml", r"^version:\s*(\S+)\s*$"),
        "dota2tracker/constants.py": _match("dota2tracker/constants.py", r"^PLUGIN_VERSION\s*=\s*(\S+)\s*$"),
        "pyproject.toml": _match("pyproject.toml", r"^version\s*=\s*(\S+)\s*$"),
    }


def current_version() -> str:
    versions = read_versions()
    distinct = set(versions.values())
    if len(distinct) != 1:
        details = ", ".join(f"{path}={value}" for path, value in versions.items())
        raise ReleaseError(f"version numbers disagree: {details}")
    version = distinct.pop()
    if not _SEMVER.match(version):
        raise ReleaseError(f"version {version!r} is not a semantic version")
    return version


def changelog_notes(version: str, changelog: str | None = None) -> str:
    """Body of ``## [<version>] - <date>`` up to the next release heading."""
    text = changelog if changelog is not None else (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s|$)")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no entry for {version}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    body = "\n".join(lines[start + 1 : end]).strip()
    if not body:
        raise ReleaseError(f"CHANGELOG.md entry for {version} is empty")
    return body + "\n"


def tracked_files() -> list[str]:
    """Files to ship: tracked by git (so ignored/untracked junk never leaks into a release)."""
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", *PACKAGE_FILES, *PACKAGE_DIRS],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    files = sorted(
        path
        for path in output.split("\0")
        if path
        and not path.endswith(EXCLUDED_SUFFIXES)
        and not any(part in EXCLUDED_PARTS for part in Path(path).parts)
    )
    missing = [name for name in ("main.py", "metadata.yaml") if name not in files]
    if missing:
        raise ReleaseError(f"required files are not tracked: {', '.join(missing)}")
    return files


def build_package(dist: Path, version: str) -> Path:
    """Zip with a single top-level ``astrbot_plugin_dota2tracker/`` directory (AstrBot upload install)."""
    dist.mkdir(parents=True, exist_ok=True)
    archive = dist / f"{PLUGIN_NAME}-v{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in tracked_files():
            info = zipfile.ZipInfo(f"{PLUGIN_NAME}/{path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, (ROOT / path).read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    try:
        version = current_version()
        if command == "version":
            changelog_notes(version)  # fail early when the entry is missing
            print(f"version={version}")
            print(f"tag=v{version}")
            print(f"prerelease={'true' if '-' in version else 'false'}")
        elif command == "notes" and len(argv) == 2:
            Path(argv[1]).write_text(changelog_notes(version), encoding="utf-8")
        elif command == "package" and len(argv) == 2:
            archive = build_package(Path(argv[1]), version)
            print(f"archive={archive}")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except ReleaseError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
