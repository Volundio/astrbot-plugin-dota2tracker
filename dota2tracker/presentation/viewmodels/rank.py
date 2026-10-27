"""View model for the ``rank_fun`` template (port of ``templates/rank/rank_fun.ejs``)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from markupsafe import Markup, escape

from ...domain.rank import rank_display
from ...utils.jsnum import js_str
from ...utils.timefmt import luxon_format
from ..assets import ImageAssets

__all__ = ["build_rank_fun_view", "preview_rank_fun", "rank_fun_date"]

Translate = Callable[..., Any]

PREVIEW_AVATAR = "https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg"


def _js_template(value: Any) -> str:
    """``${value}`` inside a JS template literal (``undefined``/``null`` are printed)."""
    if value is None:
        return "undefined"
    return js_str(value)


def _rank_html(css_class: str, rank: dict[str, Any], t: Translate) -> str:
    """``<span class="rank prev">${$t(ranks.medal)}${leader ?? star}</span>``."""
    medal_name = t("dota2tracker.template.ranks." + js_str(rank.get("medal")))
    leader = rank.get("leader")
    suffix = leader if leader is not None else rank.get("star")
    return f'<span class="rank {css_class}">{escape(_js_template(medal_name))}{escape(_js_template(suffix))}</span>'


def _rank_images(rank: dict[str, Any], assets: ImageAssets) -> dict[str, Any]:
    top = rank.get("inTop100")
    medal = top if top is not None else rank.get("medal")
    leader = rank.get("leader")
    return {
        "medal": assets.url(f"medal_{_js_template(medal)}"),
        "star": assets.url(f"star_{_js_template(rank.get('star'))}"),
        "leader": "" if leader is None else js_str(leader),
    }


def rank_fun_date(moment: datetime, locale: str) -> str:
    """``DateTime.now().toFormat(...)`` used by ``MatchWatcherTask`` for this template."""
    return luxon_format(moment, "yyyy/MM/dd HH时mm分" if locale == "zh-CN" else "yyyy/MM/dd HH:mm", locale)


def build_rank_fun_view(
    name: Any,
    avatar: Any,
    is_rising: bool,
    prev_rank: dict[str, Any],
    curr_rank: dict[str, Any],
    date_text: str,
    t: Translate,
    t_html: Callable[..., Markup | None],
    assets: ImageAssets,
) -> dict[str, Any]:
    """Build the context of ``rank/rank_fun.html.j2``.

    ``prev_rank``/``curr_rank`` are :func:`dota2tracker.domain.rank.rank_display`
    dicts. The congratulation message is the Satori markup of the translation
    (``name`` escaped, ``<br/>`` kept) with the three placeholders replaced by
    HTML snippets, exactly like the EJS template.
    """
    kind = "xi" if is_rising else "bei"
    key = "rank_fun_up_message" if is_rising else "rank_fun_down_message"
    # The name is inserted last, through an unguessable token, so a nickname that
    # contains "CURR_PLACEHOLDER" & co. cannot move the rank/avatar markup.
    name_token = f"D2TNAME{secrets.token_hex(8)}"
    raw = t_html(f"dota2tracker.template.{key}", {"name": name_token})
    message = "" if raw is None else str(raw)
    avatar_html = f'<img src="{escape(_js_template(avatar))}" />'
    message = message.replace("AVATAR_PLACEHOLDER", avatar_html, 1)
    message = message.replace("PREV_PLACEHOLDER", _rank_html("prev", prev_rank, t), 1)
    message = message.replace("CURR_PLACEHOLDER", _rank_html("curr", curr_rank, t), 1)
    message = message.replace(name_token, str(escape(_js_template(name))))
    return {
        "kind": kind,
        "background": assets.url(kind, None, "jpg"),
        "message": Markup(message),
        "prev": _rank_images(prev_rank, assets),
        "curr": _rank_images(curr_rank, assets),
        "date": date_text,
    }


def preview_rank_fun(ctx) -> dict[str, Any]:
    """``先锋5 → 卫士1`` (rising) or ``卫士2 → 卫士1`` with ``variant="down"``."""
    falling = ctx.variant == "down"
    previous = {"rank": 22 if falling else 15, "leader": None}
    current = {"rank": 21, "leader": None}
    moment = datetime.fromisoformat("2025-01-01T00:00:00+08:00")
    if ctx.tz is not None:
        moment = moment.astimezone(ctx.tz)

    def t_html(key: str, params: Any = None) -> Markup | None:
        return ctx.i18n.t_html(ctx.locale, key, params)

    return build_rank_fun_view(
        "张三" if ctx.locale == "zh-CN" else "John Doe",
        PREVIEW_AVATAR,
        not falling,
        rank_display(previous),
        rank_display(current),
        rank_fun_date(moment, ctx.locale),
        ctx.t,
        t_html,
        ctx.assets,
    )
