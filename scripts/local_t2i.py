"""Local emulation of AstrBot's t2i service for development and tests.

AstrBot's ``Star.html_render`` posts ``{tmpl, tmpldata, options}`` to the t2i
service (``AstrBotDevs/astrbot-t2i-service``), which renders the template with
``jinja2.sandbox.SandboxedEnvironment``, writes the HTML to a file, loads it in
Chromium with the requested viewport/device scale and takes a full-page
screenshot. :class:`LocalT2I` reproduces exactly that pipeline with Playwright
so templates can be checked without a network renderer.

Usage as a script::

    PLAYWRIGHT_BROWSERS_PATH=... python scripts/local_t2i.py page.html out.png [width]
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

from jinja2.sandbox import SandboxedEnvironment

SCALE = {"normal": 1.0, "high": 1.3, "ultra": 1.8}


class LocalT2I:
    def __init__(self, output_dir: Path | None = None) -> None:
        self._playwright = None
        self._browser = None
        self.output_dir = output_dir or Path(tempfile.mkdtemp(prefix="d2t-t2i-"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.last_html: str | None = None
        self.last_options: dict[str, Any] | None = None
        self._counter = 0

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def __call__(self, tmpl: str, data: dict[str, Any], options: dict[str, Any]) -> str:
        """Signature used by :class:`dota2tracker.presentation.renderer.Renderer`."""
        html = SandboxedEnvironment().from_string(tmpl).render(data)
        merged = {"full_page": True, "type": "jpeg", "quality": 40} | dict(options or {})
        self.last_html = html
        self.last_options = merged
        self._counter += 1
        html_path = self.output_dir / f"page_{self._counter}.html"
        html_path.write_text(html, encoding="utf-8")
        suffix = merged.get("type") or "png"
        out = self.output_dir / f"shot_{self._counter}.{suffix}"
        context = await self._browser.new_context(
            device_scale_factor=SCALE.get(merged.get("device_scale_factor_level") or "normal", 1.0)
        )
        page = await context.new_page()
        try:
            await page.set_viewport_size(
                {"width": int(merged.get("viewport_width") or 800), "height": int(merged.get("viewport_height") or 720)}
            )
            await page.goto(html_path.as_uri())
            kwargs = {"path": str(out), "full_page": bool(merged.get("full_page", True)), "type": suffix}
            if suffix == "jpeg" and merged.get("quality"):
                kwargs["quality"] = int(merged["quality"])
            await page.screenshot(**kwargs)
        finally:
            await page.close()
            await context.close()
        return str(out)


async def _main(argv: list[str]) -> int:
    source, target = Path(argv[1]), Path(argv[2])
    width = int(argv[3]) if len(argv) > 3 else 800
    t2i = LocalT2I()
    await t2i.start()
    try:
        path = await t2i(
            "{{ html | safe }}",
            {"html": source.read_text(encoding="utf-8")},
            {"viewport_width": width, "viewport_height": 64, "type": "png"},
        )
        target.write_bytes(Path(path).read_bytes())
    finally:
        await t2i.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv)))
