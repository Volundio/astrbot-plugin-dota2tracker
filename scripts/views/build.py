"""Build ``views/<name>/index.html`` from ``scripts/views/src``.

AstrBot only serves files inside each ``views/<name>/`` directory, so the shared
``common.css``/``common.js`` are inlined into every page. Edit the sources here
and run ``python scripts/views/build.py``; ``--check`` fails when the generated
pages are out of date (used by the test suite).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "src"
DST = ROOT / "views"
VIEWS = ("overview", "subscriptions", "templates", "diagnostics")


def render(name: str) -> str:
    css = (SRC / "common.css").read_text(encoding="utf-8").rstrip()
    js = (SRC / "common.js").read_text(encoding="utf-8").rstrip()
    html = (SRC / f"{name}.html").read_text(encoding="utf-8")
    return html.replace("/*@COMMON_CSS@*/", css).replace("/*@COMMON_JS@*/", js)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for name in VIEWS:
        out = DST / name / "index.html"
        html = render(name)
        if check:
            if not out.is_file() or out.read_text(encoding="utf-8") != html:
                stale.append(name)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"{out.relative_to(ROOT)}: {len(html)} bytes")
    if stale:
        print("stale views:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
