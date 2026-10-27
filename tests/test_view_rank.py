"""rank_fun view model and template."""

from __future__ import annotations

import pytest

from dota2tracker.domain.rank import rank_display
from dota2tracker.presentation.previews import build_preview
from dota2tracker.presentation.viewmodels.rank import build_rank_fun_view


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("variant", ["", "down"])
def test_rank_fun_renders(strict_renderer, preview_context, locale, variant):
    view = build_preview("rank_fun", preview_context(locale, variant))
    html = strict_renderer.render_html("rank_fun", view, locale)
    kind = "bei" if variant == "down" else "xi"
    assert f'<body class="{kind}"' in html
    assert "d2t-asset-" not in html
    assert "PLACEHOLDER" not in html


def test_rank_fun_message_escapes_name(i18n, assets):
    def t(key, params=None):
        return i18n.t("zh-CN", key, params)

    def t_html(key, params=None):
        return i18n.t_html("zh-CN", key, params)

    view = build_rank_fun_view(
        "<b>x</b>",
        'a"b',
        True,
        rank_display({"rank": 15}),
        rank_display({"rank": 80, "leader": 5}),
        "2025/01/01",
        t,
        t_html,
        assets,
    )
    message = str(view["message"])
    assert "&lt;b&gt;x&lt;/b&gt;" in message
    assert '<img src="a&#34;b" />' in message
    assert '<span class="rank prev">' in message and '<span class="rank curr">' in message
    assert view["curr"]["leader"] == "5"
    assert view["curr"]["medal"] == assets.url("medal_8c")
    assert view["prev"]["leader"] == ""


def test_rank_fun_nickname_cannot_move_the_rank_markup(i18n, assets):
    from dota2tracker.domain.rank import rank_display
    from dota2tracker.presentation.viewmodels.rank import build_rank_fun_view

    view = build_rank_fun_view(
        "CURR_PLACEHOLDER<b>x</b>",
        "https://a/b.png",
        True,
        rank_display({"rank": 15}),
        rank_display({"rank": 21}),
        "date",
        lambda key, params=None: i18n.t("zh-CN", key, params),
        lambda key, params=None: i18n.t_html("zh-CN", key, params),
        assets,
    )
    message = str(view["message"])
    assert "CURR_PLACEHOLDER&lt;b&gt;x&lt;/b&gt;" in message  # the name stays text
    assert message.index('<span class="rank prev">') < message.index('<span class="rank curr">')
    assert message.count('<span class="rank curr">') == 1
