"""domain.rank: rank_display, is_rising and decide_rank_change."""

from __future__ import annotations

from dota2tracker.domain.rank import decide_rank_change, is_rising, rank_display


def decide(stored, latest, star=False, leader=False):
    return decide_rank_change(stored, latest, broadcast_star=star, broadcast_leader=leader)


def test_rank_display():
    assert rank_display({"rank": 54, "leader": None}) == {"medal": 5, "star": 4, "leader": None, "inTop100": None}
    assert rank_display({"rank": 80, "leader": 7})["inTop100"] == "8c"
    assert rank_display({"rank": 80, "leader": 55})["inTop100"] == "8b"
    assert rank_display({"rank": 80, "leader": 500})["inTop100"] is None
    assert rank_display(None) == {"medal": 0, "star": 0, "leader": None, "inTop100": None}


def test_unchanged_and_first_fill():
    assert decide({"rank": 54, "leader": None}, {"rank": 54, "leader": None}).store is False
    first = decide({}, {"rank": 54, "leader": None})
    assert first.store is True and first.broadcast is None


def test_missing_latest_player():
    result = decide({"rank": 54, "leader": None}, None)
    assert result.store is False and result.broadcast is None


def test_medal_always_broadcast():
    result = decide({"rank": 45, "leader": None}, {"rank": 51, "leader": None})
    assert result.store and result.broadcast is not None
    assert result.broadcast.rising is True
    assert result.broadcast.previous["medal"] == 4 and result.broadcast.current["medal"] == 5
    down = decide({"rank": 51, "leader": None}, {"rank": 45, "leader": None})
    assert down.broadcast.rising is False


def test_star_switch():
    stored, latest = {"rank": 52, "leader": None}, {"rank": 53, "leader": None}
    assert decide(stored, latest).store is False
    result = decide(stored, latest, star=True)
    assert result.store and result.broadcast.rising


def test_leader_switch():
    stored, latest = {"rank": 80, "leader": 120}, {"rank": 80, "leader": 90}
    assert decide(stored, latest).broadcast is None
    result = decide(stored, latest, leader=True)
    assert result.broadcast is not None and result.broadcast.rising is True
    assert decide({"rank": 80, "leader": 90}, {"rank": 80, "leader": 120}, leader=True).broadcast.rising is False


def test_is_rising():
    assert is_rising({"rank": 75, "leader": None}, {"rank": 80, "leader": None})
    assert is_rising({"rank": 80, "leader": None}, {"rank": 80, "leader": 900})
    assert not is_rising({"rank": 80, "leader": 10}, {"rank": 80, "leader": 20})
    assert not is_rising({"rank": 54, "leader": None}, {"rank": 53, "leader": None})
