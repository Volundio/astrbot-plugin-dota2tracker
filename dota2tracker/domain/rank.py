"""Rank change detection (port of ``MatchWatcherTask.detectRankChanges``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.jsnum import js_parse_int, js_str, js_truthy

__all__ = ["RankChange", "RankDecision", "decide_rank_change", "is_rising", "rank_display"]

_MISSING = object()


def rank_display(source: dict[str, Any] | None) -> dict[str, Any]:
    """``{medal, star, leader, inTop100}`` built from a stored ``{rank, leader}`` object."""
    source = source or {}
    rank = source.get("rank")
    text = js_str(rank) if rank is not None else ""
    medal = js_parse_int(text[0] if text else "0")
    star = js_parse_int(text[1] if len(text) > 1 else "0")
    leader = source.get("leader")
    in_top100 = None
    if js_truthy(leader):
        in_top100 = "8c" if leader <= 10 else "8b" if leader <= 100 else None
    return {"medal": medal, "star": star, "leader": leader, "inTop100": in_top100}


@dataclass(frozen=True)
class RankChange:
    previous: dict[str, Any]
    current: dict[str, Any]
    rising: bool


@dataclass(frozen=True)
class RankDecision:
    """``store`` – persist ``latest``; ``broadcast`` – a change worth announcing."""

    store: bool
    broadcast: RankChange | None = None


def _num(value: Any) -> float:
    return 0 if value is None else value


def is_rising(previous: dict[str, Any], latest: dict[str, Any]) -> bool:
    prev_rank, prev_leader = previous.get("rank"), previous.get("leader")
    rank, leader = latest.get("rank"), latest.get("leader")
    return (
        _num(rank) > _num(prev_rank)
        or (rank == prev_rank and _num(leader) < _num(prev_leader))
        or (_num(leader) > 0 and prev_leader is None)
    )


def decide_rank_change(
    stored: dict[str, Any], latest: dict[str, Any] | None, *, broadcast_star: bool, broadcast_leader: bool
) -> RankDecision:
    """Compare the stored rank with the latest Stratz values.

    * Empty stored rank (legacy rows / new bindings): store silently.
    * Medal changes are always broadcast; stars and leaderboard positions only
      when the corresponding switches are enabled. When nothing qualifies the
      stored value is intentionally left untouched (original behaviour).
    """
    stored_rank = stored.get("rank", _MISSING)
    stored_leader = stored.get("leader", _MISSING)
    latest_rank = latest.get("rank", _MISSING) if latest is not None else _MISSING
    latest_leader = latest.get("leader", _MISSING) if latest is not None else _MISSING
    if stored_rank == latest_rank and stored_leader == latest_leader:
        return RankDecision(store=False)
    if latest is None:
        # Target fix: the original threw when Stratz omitted a bound player.
        return RankDecision(store=False)
    if not stored:
        return RankDecision(store=True)
    previous = rank_display(stored)
    current = rank_display(latest)
    if (
        previous["medal"] != current["medal"]
        or (previous["star"] != current["star"] and broadcast_star)
        or (previous["leader"] != current["leader"] and broadcast_leader)
    ):
        return RankDecision(store=True, broadcast=RankChange(previous, current, is_rising(stored, latest)))
    return RankDecision(store=False)
