"""Image URLs for templates (port of ``ViewRenderer.getImageUrl``).

AstrBot renders HTML on a remote t2i service that cannot read local files, so
local template images are embedded as data URIs. Templates call
``img("medal_5")`` which yields a ``d2t-asset-<token>://`` placeholder; after
Jinja rendering :meth:`ImageAssets.inline` replaces placeholders. The token is
random per instance so user text (nicknames, Steam names) can never forge a
placeholder and get image data spliced into it:

* used once → inlined ``data:`` URI;
* used several times in ``<img src>`` → a tiny placeholder plus one shared
  copy in a script that assigns ``src`` while the document is parsing (the
  load event still waits for those images);
* CSS ``url()`` / SVG ``href`` occurrences are always inlined.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import secrets
from collections import Counter
from functools import lru_cache
from pathlib import Path

from ..constants import ASSETS_DIR, STEAM_CDN

__all__ = ["ImageAssets"]

TRANSPARENT_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
_NAME = r"([A-Za-z0-9_.+\-]+)"
_SAFE = re.compile(r"^[A-Za-z0-9_.+\-]+$")


@lru_cache(maxsize=256)
def _encode(path: Path) -> str:
    """Base64 data URI; raster images are re-encoded as WebP when that is smaller."""
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".svg":
        mime = "image/svg+xml"
    elif path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        try:
            import io

            from PIL import Image

            with Image.open(path) as image:
                buffer = io.BytesIO()
                image.save(buffer, "WEBP", quality=90, method=4)
            if len(buffer.getvalue()) < len(raw):
                raw, mime = buffer.getvalue(), "image/webp"
        except Exception:
            pass
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class ImageAssets:
    def __init__(self, images_dir: Path = ASSETS_DIR / "images", cdn: str = STEAM_CDN) -> None:
        self.images_dir = images_dir
        self.cdn = cdn.rstrip("/")
        #: unguessable placeholder scheme, e.g. ``d2t-asset-1f0c9a7b3e5d://``
        self.scheme = f"d2t-asset-{secrets.token_hex(8)}://"
        scheme = re.escape(self.scheme)
        self._placeholder = re.compile(scheme + _NAME)
        self._img_src = re.compile(rf"""(<img\b[^>]*?\s)src=(["']){scheme}{_NAME}\2""", re.I)

    def url(self, image: object, kind: str | None = "local", fmt: str | None = "png") -> str:
        """``getImageUrl(image, type = Local, format = png)``."""
        name = "undefined" if image is None else str(image)
        kind = kind or "local"
        fmt = fmt or "png"
        if kind == "local":
            return f"{self.scheme}{name}.{fmt}"
        return f"{self.cdn}/{kind}/{name}.{fmt}"

    def data_uri(self, filename: str) -> str | None:
        if not _SAFE.match(filename) or ".." in filename:
            return None
        path = self.images_dir / filename
        if not path.is_file():
            return None
        return _encode(path)

    def exists(self, filename: str) -> bool:
        return self.data_uri(filename) is not None

    def inline(self, html: str) -> str:
        names = self._placeholder.findall(html)
        if not names:
            return html
        img_counts = Counter(match.group(3) for match in self._img_src.finditer(html))
        shared: dict[str, str] = {}

        def img_replacement(match: re.Match[str]) -> str:
            prefix, quote, name = match.group(1), match.group(2), match.group(3)
            uri = self.data_uri(name)
            if uri is None:
                return f"{prefix}src={quote}{quote}"
            if img_counts[name] > 1:
                shared[name] = uri
                return f"{prefix}src={quote}{TRANSPARENT_PIXEL}{quote} data-d2t-asset={quote}{name}{quote}"
            return f"{prefix}src={quote}{uri}{quote}"

        html = self._img_src.sub(img_replacement, html)
        html = self._placeholder.sub(lambda m: self.data_uri(m.group(1)) or "", html)
        if shared:
            script = (
                "<script>(function(){var a="
                + json.dumps(shared, separators=(",", ":"))
                + ';document.querySelectorAll("img[data-d2t-asset]").forEach(function(e){'
                'var s=a[e.getAttribute("data-d2t-asset")];if(s){e.src=s;}});})();</script>'
            )
            index = html.lower().rfind("</body>")
            html = html[:index] + script + html[index:] if index != -1 else html + script
        return html
