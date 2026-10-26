"""Render a template preview from fixtures with the local t2i emulation.

    PLAYWRIGHT_BROWSERS_PATH=/code/.pw-browsers python scripts/preview_template.py <template> <locale> <out.png> [variant] [scale]

Writes ``out.png`` and ``out.png.html`` (the exact HTML sent to the renderer).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from local_t2i import LocalT2I  # noqa: E402

from dota2tracker.data.static import DotaConstants  # noqa: E402
from dota2tracker.i18n.service import I18nService  # noqa: E402
from dota2tracker.presentation.assets import ImageAssets  # noqa: E402
from dota2tracker.presentation.previews import PreviewContext, build_preview  # noqa: E402
from dota2tracker.presentation.renderer import Renderer  # noqa: E402


async def main(argv: list[str]) -> int:
    template, locale, out = argv[1], argv[2], Path(argv[3])
    variant = argv[4] if len(argv) > 4 else ""
    scale = argv[5] if len(argv) > 5 else "normal"
    constants = DotaConstants.load()
    i18n = I18nService(default_locale=locale, hero_ids=constants.hero_ids)
    assets = ImageAssets()
    t2i = LocalT2I(out.parent / "t2i")
    await t2i.start()
    try:
        renderer = Renderer(
            i18n=i18n,
            assets=assets,
            html_render=t2i,
            options=lambda: {"type": "png", "device_scale_factor_level": scale},
            strict=True,
        )
        ctx = PreviewContext(constants, i18n, assets, locale, ZoneInfo("Asia/Shanghai"), variant=variant)
        data = build_preview(template, ctx)
        image = await renderer.render_image(template, data, locale)
        out.write_bytes(image.data)
        Path(str(out) + ".html").write_text(t2i.last_html or "", encoding="utf-8")
        print(f"{template}: {len(image.data)} bytes image, {len(t2i.last_html or '')} bytes html")
    finally:
        await t2i.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
