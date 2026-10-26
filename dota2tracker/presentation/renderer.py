"""HTML rendering: Jinja2 (local, sandboxed) → AstrBot ``html_render`` (t2i).

Templates are rendered locally with a sandboxed Jinja2 environment; only
loops, conditions and escaping happen in templates while view models prepare
every value. The resulting HTML is handed to AstrBot's ``Star.html_render``
through the pass-through template ``{{ html | safe }}``.

Screenshot geometry: the Koishi version captured the ``<body>`` element of a
1280px wide page. AstrBot's t2i service takes full-page screenshots, so every
template declares its body width (``viewport_width``); content sized pages
(help table, roster, hero-of-the-day) use a tiny viewport together with
``width: max-content`` so the screenshot hugs the content.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

from ..application.models import RenderedImage
from ..constants import TEMPLATES_DIR
from ..i18n.service import I18nService
from ..utils.jsnum import format_number, js_str, js_to_fixed, to_locale_string
from .assets import ImageAssets

__all__ = ["PASS_THROUGH_TEMPLATE", "TEMPLATES", "RenderError", "Renderer", "TemplateSpec"]

#: The marker comment stops AstrBot from injecting its 1.2 MB Shiki runtime.
PASS_THROUGH_TEMPLATE = "{{ html | safe }}{# astrbot-t2i-shiki-runtime #}"
DYNAMIC_VIEWPORT = 64
DEFAULT_TIMEOUT = 120.0

HtmlRender = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[str]]


class TemplateEnvironment(SandboxedEnvironment):
    """Sandboxed environment where ``obj.key`` prefers dictionary keys.

    View models are plain dicts; without this ``player.items`` would resolve
    to ``dict.items`` instead of the ``items`` entry.
    """

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(obj, dict) and attribute in obj:
            return obj[attribute]
        return super().getattr(obj, attribute)


class RenderError(RuntimeError):
    """Rendering failed (template error, t2i service error or timeout)."""


@dataclass(frozen=True)
class TemplateSpec:
    file: str
    width: int = 800
    height: int | None = None
    dynamic: bool = False
    kind: str = ""


TEMPLATES: dict[str, TemplateSpec] = {
    "match_1": TemplateSpec("match/match_1.html.j2", 800, kind="match"),
    "match_2": TemplateSpec("match/match_2.html.j2", 800, kind="match"),
    "match_2+": TemplateSpec("match/match_2_plus.html.j2", 800, kind="match"),
    "player_1": TemplateSpec("player/player_1.html.j2", 800, kind="player"),
    "hero_1": TemplateSpec("hero/hero_1.html.j2", 800, kind="hero"),
    "item": TemplateSpec("item/item.html.j2", 404, kind="item"),
    "itemlist": TemplateSpec("item/itemlist.html.j2", 232, kind="item"),
    "daily": TemplateSpec("report/daily.html.j2", 1024, kind="report"),
    "daily_legacy": TemplateSpec("report/daily_legacy.html.j2", 600, kind="report"),
    "rank_fun": TemplateSpec("rank/rank_fun.html.j2", 1024, 768, kind="rank"),
    "help": TemplateSpec("misc/help.html.j2", dynamic=True, kind="help"),
    "members": TemplateSpec("misc/members.html.j2", dynamic=True, kind="members"),
    "hero_of_the_day": TemplateSpec("misc/hero_of_the_day.html.j2", dynamic=True, kind="hero_of_the_day"),
}


def _fixed(value: Any, digits: int = 0) -> str:
    return js_to_fixed(value, digits)


def _ejs_value(value: Any) -> str:
    """``<%= value %>``: ``null``/``undefined`` print nothing, numbers use JS formatting."""
    return "" if value is None else js_str(value)


#: Templates use ``var(--font-*)`` and Tailwind ``font-*`` classes; they map to the
#: renderer machine's default system families (no bundled or embedded fonts).
FONT_STYLE_BLOCK = """
    <style>
      :root {
        --font-sans: sans-serif;
        --font-serif: serif;
        --font-mono: monospace;
      }
      .font-sans { font-family: var(--font-sans) !important; }
      .font-serif { font-family: var(--font-serif) !important; }
      .font-mono { font-family: var(--font-mono) !important; }
      body {
        font-family: var(--font-sans);
      }
    </style>
    """


class Renderer:
    def __init__(
        self,
        *,
        i18n: I18nService,
        assets: ImageAssets,
        html_render: HtmlRender | None,
        options: Callable[[], dict[str, Any]],
        logger: logging.Logger | None = None,
        templates_dir: Path = TEMPLATES_DIR,
        strict: bool = False,
        concurrency: int = 2,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._i18n = i18n
        self._assets = assets
        self._html_render = html_render
        self._options = options
        self._logger = logger or logging.getLogger(__name__)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        self.env = TemplateEnvironment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(enabled_extensions=("j2", "html"), default_for_string=True),
            undefined=StrictUndefined if strict else ChainableUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._templates_dir = templates_dir
        self._css_cache: dict[str, Markup] = {}
        self.env.filters.update(
            {
                "js": _ejs_value,
                "fixed": _fixed,
                "num": format_number,
                "locale_num": to_locale_string,
            }
        )
        self.render_count = 0
        self.failure_count = 0
        self.last_error: str | None = None
        self.last_duration: float | None = None

    @property
    def assets(self) -> ImageAssets:
        return self._assets

    def _globals(self, locale: str) -> dict[str, Any]:
        i18n = self._i18n

        def t(key: str, params: Any = None) -> str:
            value = i18n.t(locale, key, params)
            return "" if value is None else value

        def t_opt(key: str, params: Any = None) -> str | None:
            return i18n.t(locale, key, params)

        def t_html(key: str, params: Any = None) -> Markup:
            value = i18n.t_html(locale, key, params)
            return Markup("") if value is None else value

        return {
            "t": t,
            "t_opt": t_opt,
            "t_html": t_html,
            "img": self._assets.url,
            "css": self._css,
            "locale": locale,
        }

    def _css(self, relative: str) -> Markup:
        """Inline a static stylesheet from the templates directory (not parsed by Jinja)."""
        cached = self._css_cache.get(relative)
        if cached is None:
            root = self._templates_dir.resolve()
            target = (root / relative).resolve()
            if target.suffix != ".css" or root not in target.parents:
                raise ValueError(f"invalid stylesheet path: {relative}")
            cached = Markup(target.read_text(encoding="utf-8"))
            self._css_cache[relative] = cached
        return cached

    def render_html(self, template: str, data: dict[str, Any], locale: str) -> str:
        """Render a registered template (or a ``.j2`` path) to standalone HTML."""
        spec = TEMPLATES.get(template)
        file = spec.file if spec else template
        context = {**self._globals(locale), **data}
        html = self.env.get_template(file).render(context)
        html = _inject_head(html, FONT_STYLE_BLOCK)
        return self._assets.inline(html)

    async def render_image(self, template: str, data: dict[str, Any], locale: str) -> RenderedImage:
        if self._html_render is None:
            raise RenderError("html_render is not available")
        spec = TEMPLATES.get(template) or TemplateSpec(template)
        try:
            html = self.render_html(template, data, locale)
        except Exception as error:
            self._record_failure(error)
            raise RenderError(f"template {template} failed: {error}") from error

        options = dict(self._options())
        options["full_page"] = True
        if spec.dynamic:
            options["viewport_width"] = DYNAMIC_VIEWPORT
            options["viewport_height"] = DYNAMIC_VIEWPORT
        else:
            options["viewport_width"] = spec.width
            options["viewport_height"] = spec.height or DYNAMIC_VIEWPORT
        mime = "image/png" if options.get("type", "png") == "png" else "image/jpeg"
        if mime == "image/png":
            options.pop("quality", None)

        loop = asyncio.get_running_loop()
        started = loop.time()
        async with self._semaphore:
            try:
                path = await asyncio.wait_for(
                    self._html_render(PASS_THROUGH_TEMPLATE, {"html": html}, options), timeout=self._timeout
                )
                data_bytes = await asyncio.to_thread(_read_and_remove, path)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._record_failure(error)
                raise RenderError(f"t2i render of {template} failed: {error}") from error
        self.render_count += 1
        self.last_duration = loop.time() - started
        self.last_error = None
        return RenderedImage(data=data_bytes, mime=mime, template=template)

    def _record_failure(self, error: BaseException) -> None:
        self.failure_count += 1
        self.last_error = f"{type(error).__name__}: {error}"[:500]
        self._logger.error("Template rendering failed: %s", self.last_error)

    def status(self) -> dict[str, Any]:
        return {
            "renders": self.render_count,
            "failures": self.failure_count,
            "last_error": self.last_error,
            "last_duration": self.last_duration,
        }


def _inject_head(html: str, block: str) -> str:
    """``html.replace("</head>", block + "</head>")`` (first occurrence only)."""
    index = html.find("</head>")
    if index == -1:
        return block + html
    return html[:index] + block + html[index:]


def _read_and_remove(path: str) -> bytes:
    if not isinstance(path, str) or not path:
        raise RenderError("html_render returned no file path")
    if path.startswith(("http://", "https://")):
        raise RenderError("html_render returned a URL; expected a local file")
    target = Path(path)
    data = target.read_bytes()
    with contextlib.suppress(OSError):
        os.remove(target)
    if not data:
        raise RenderError("empty image returned by html_render")
    return data
