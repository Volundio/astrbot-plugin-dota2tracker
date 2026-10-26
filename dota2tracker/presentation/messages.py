"""Text messages (port of the text parts of ``presentation/message.builder.ts``)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from ..i18n.service import I18nService
from ..utils.jsnum import js_div, js_str, js_to_fixed
from ..utils.koishi_random import seeded_random

__all__ = ["MessageBuilder"]


def _n(value: Any) -> Any:
    return 0 if value is None else value


def _template_value(value: Any) -> str:
    """``${value}`` inside a JS template literal."""
    return "null" if value is None else js_str(value)


def _pair(player: dict[str, Any], first: str, second: str) -> str:
    return f"{_template_value(player.get(first))}/{_template_value(player.get(second))}"


class MessageBuilder:
    def __init__(self, i18n: I18nService, url_types: Iterable[str]) -> None:
        self._i18n = i18n
        self._url_types = set(url_types)

    def _comments(self, locale: str, key: str) -> list[str]:
        raw = self._i18n.t(locale, f"dota2tracker.broadcast.{key}") or ""
        try:
            parsed = json.loads(f"[{raw}]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed]

    def match_message(self, locale: str, match: dict[str, Any], steam_ids: Iterable[int]) -> str:
        """``buildMatchMessage``: one line per tracked player plus the optional Stratz URL."""
        wanted = {int(s) for s in steam_ids}
        lines = []
        for player in match.get("players") or []:
            if player.get("steamAccountId") is None or int(player["steamAccountId"]) not in wanted:
                continue
            random = seeded_random(f"{match.get('id')}-{player.get('steamAccountId')}-{player.get('playerSlot')}")
            won = player.get("isRadiant") == match.get("didRadiantWin")
            ratio = js_div(_n(player.get("heroDamage")), _n(player.get("networth")))
            if won:
                good = (
                    player.get("deathContribution", 0) < 0.2
                    or player.get("killContribution", 0) > 0.75
                    or ratio > 1.5
                    or _n(player.get("towerDamage")) > 10000
                    or _n(player.get("imp")) > 0
                )
                comment_key = "WIN_POSITIVE" if good else "WIN_NEGATIVE"
            else:
                good = (
                    player.get("deathContribution", 0) < 0.25
                    or player.get("killContribution", 0) > 0.75
                    or ratio > 1.0
                    or _n(player.get("towerDamage")) > 5000
                    or _n(player.get("imp")) > 0
                )
                # Target fix: the original used LOSE_NEGATIVE in both branches (see DEVELOPMENT.md).
                comment_key = "LOSE_POSITIVE" if good else "LOSE_NEGATIVE"
            comment = random.pick(self._comments(locale, comment_key)) or ""
            kills, deaths, assists = _n(player.get("kills")), _n(player.get("deaths")), _n(player.get("assists"))
            line = self._i18n.text(
                locale,
                "dota2tracker.broadcast.message",
                {
                    "name": (player.get("steamAccount") or {}).get("name"),
                    "hero_name": self._i18n.display_name_for_hero(
                        (player.get("hero") or {}).get("id"), locale, random=random
                    ),
                    "comment": comment,
                    "kda": f"{js_to_fixed((kills + assists) / (deaths or 1), 2)} [{kills}/{deaths}/{assists}]",
                    "gpm_xpm": _pair(player, "goldPerMinute", "experiencePerMinute"),
                    "lh_dn": _pair(player, "numLastHits", "numDenies"),
                    "damage": _pair(player, "heroDamage", "towerDamage"),
                    "kc_dc": f"{js_to_fixed(player.get('killContribution', 0) * 100, 2)}%/"
                    f"{js_to_fixed(player.get('deathContribution', 0) * 100, 2)}%",
                },
            )
            lines.append(line + "\n")
        text = "".join(lines)
        if "match" in self._url_types:
            text += f"https://stratz.com/matches/{_template_value(match.get('id'))}"
        return text

    def player_message(self, steam_id: int) -> str:
        return f"https://stratz.com/players/{steam_id}" if "player" in self._url_types else ""

    def hero_message(self, hero: dict[str, Any] | None) -> str:
        if "hero" not in self._url_types:
            return ""
        match = re.match(r"^npc_dota_hero_(.+)$", str((hero or {}).get("name") or ""))
        return f"https://wiki.dota2.com.cn/hero/{match.group(1) if match else 'undefined'}.html"

    def rank_changed_message(self, locale: str, name: str, previous: dict[str, Any], current: dict[str, Any]) -> str:
        def describe(rank: dict[str, Any]) -> dict[str, Any]:
            return {
                "medal": self._i18n.t(locale, f"dota2tracker.template.ranks.{js_str(rank.get('medal'))}") or "",
                "star": rank.get("star"),
            }

        return self._i18n.text(
            locale,
            "dota2tracker.broadcast.rank_changed",
            {"name": name, "prev": describe(previous), "curr": describe(current)},
        )
