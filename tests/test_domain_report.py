"""domain.report: daily bundles (periods, de-dup, spotlight rules, impact) and legacy report."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from dota2tracker.domain.report import (
    ReportTranslator,
    build_legacy_report,
    format_daily_report_bundles,
    format_duration,
)

TARGET = datetime(2025, 1, 2, tzinfo=UTC)
NOW = int(TARGET.timestamp()) + 3600
OLD = int(TARGET.timestamp()) - 3600


def t(key, params=None):
    if key.endswith("date_format"):
        return "yyyy-MM-dd"
    return key if params is None else f"{key}{list(params)}"


def user(steam_id, uid=None, nickname=None, channel="c1"):
    return SimpleNamespace(id=uid or steam_id, platform="p", channel_id=channel, steam_id=steam_id, nickname=nickname)


def player(steam_id, radiant=True, k=0, d=0, a=0, hero=1, dmg=0, tower=0, heal=0, nw=0, imp=0):
    return {
        "steamAccount": {"id": steam_id},
        "isRadiant": radiant,
        "kills": k,
        "deaths": d,
        "assists": a,
        "heroId": hero,
        "heroDamage": dmg,
        "towerDamage": tower,
        "heroHealing": heal,
        "networth": nw,
        "imp": imp,
    }


def match(match_id, players, radiant_win=True, start=NOW, duration=1800):
    return {
        "id": match_id,
        "didRadiantWin": radiant_win,
        "startDateTime": start,
        "durationSeconds": duration,
        "players": players,
    }


def ext(match_id, scores):
    """scores: {steam_id: (mvpScore, utilityScore, titles)}"""
    return {
        "matchId": match_id,
        "data": {
            "players": [
                {"steamAccountId": sid, "mvpScore": m, "utilityScore": u, "titles": titles}
                for sid, (m, u, titles) in scores.items()
            ]
        },
    }


def data_for(matches, steam_ids):
    return {
        "players": [
            {
                "steamAccount": {"id": sid, "name": f"n{sid}", "avatar": f"a{sid}"},
                "matches": [m for m in matches if any(p["steamAccount"]["id"] == sid for p in m["players"])],
            }
            for sid in steam_ids
        ]
    }


def run(matches, users, extensions, constants):
    ids = sorted({u.steam_id for u in users})
    translators = {f"p:{u.channel_id}": ReportTranslator(t, "en-US", lambda h: f"hero{h}") for u in users}
    return format_daily_report_bundles(
        data_for(matches, ids),
        users,
        extensions,
        constants,
        TARGET,
        translators,
        lambda name: f"img:{name}",
        lambda name: f"banner:{name}",
        "footer",
    )


def card_types(report):
    return [c["type"] for c in report["spotlights"]["cards"]]


def test_format_duration():
    assert format_duration(0) == "0:00"
    assert format_duration(3725.9) == "62:05"


def test_periods_and_dedup(constants):
    matches = [
        match(1, [player(1, True, k=5), player(2, True, k=3)], radiant_win=True),
        match(2, [player(1, True, k=1), player(2, False, k=2)], radiant_win=False),
        match(3, [player(1, True)], radiant_win=False, start=OLD),
    ]
    bundles = run(matches, [user(1), user(2)], [], constants)
    assert len(bundles) == 1
    report = bundles[0]["report"]
    stats = report["headerStats"]
    # match 1 is shared by both players but counted once; match 3 is in the previous period
    assert stats["matches"]["value"] == 2
    assert stats["kills"]["value"] == "11"
    # match 2: player 2 (dire) won → squad win
    assert stats["winRate"]["value"] == "100.0%"
    assert stats["winRate"]["hasComparison"] is True
    assert stats["winRate"]["subtext"].endswith("▲ 100.0%")
    assert stats["duration"]["value"] == "60:00"
    assert report["meta"] == {
        "date": "2025-01-02",
        "summary": "dota2tracker.template.report.daily.meta.summary['c1']",
        "footerId": "footer",
    }


def test_no_current_matches_skips_channel(constants):
    matches = [match(1, [player(1)], start=OLD)]
    assert run(matches, [user(1)], [], constants) == []


def test_player_without_current_matches_excluded(constants):
    matches = [match(1, [player(1, k=1)]), match(2, [player(2)], start=OLD)]
    report = run(matches, [user(1), user(2)], [], constants)[0]["report"]
    assert [r["player"]["name"] for r in report["squad"]] == ["n1"]


def test_mvp_highest_score_and_kda_tiebreak(constants):
    matches = [match(1, [player(1, k=1, d=1), player(2, k=10, d=1), player(3, k=5)])]
    extensions = [ext(1, {1: (50, 0, []), 2: (50, 0, []), 3: (40, 0, [])})]
    report = run(matches, [user(1), user(2), user(3)], extensions, constants)[0]["report"]
    mvp = report["spotlights"]["mvp"]
    assert mvp["type"] == "MVP" and mvp["player"]["name"] == "n2"
    assert mvp["score"]["value"] == "50.0"
    assert card_types(report) == ["MVP"]


def test_lvp_requires_loss_and_quarter_ratio(constants):
    players = [player(1, True), player(2, True), player(3, False)]
    # average over the three extension players = 30 → ratio of player 2 = 5/30 < 0.25
    extensions = [ext(1, {1: (45, 0, []), 2: (5, 0, []), 3: (40, 0, [])})]
    lost = run([match(1, players, radiant_win=False)], [user(1), user(2)], extensions, constants)[0]["report"]
    assert card_types(lost) == ["MVP", "LVP"]
    assert lost["spotlights"]["lvp"]["player"]["name"] == "n2"
    assert lost["spotlights"]["secondary"]["type"] == "LVP"
    won = run([match(1, players, radiant_win=True)], [user(1), user(2)], extensions, constants)[0]["report"]
    assert "LVP" not in card_types(won)
    # ratio exactly 0.25 does not qualify
    edge = [ext(1, {1: (10, 0, []), 2: (2.5, 0, []), 3: (17.5, 0, [])})]
    assert "LVP" not in card_types(
        run([match(1, players, radiant_win=False)], [user(1), user(2)], edge, constants)[0]["report"]
    )


def test_lvp_lowest_ratio_wins(constants):
    m1 = match(1, [player(1, True, k=4, d=1), player(2, True, k=1, d=5)], radiant_win=False)
    extensions = [ext(1, {1: (2, 0, []), 2: (2, 0, []), 9: (100, 0, [])})]
    report = run([m1], [user(1), user(2)], extensions, constants)[0]["report"]
    # equal ratios → lower average KDA is worse (player 2 has more deaths)
    assert report["spotlights"]["lvp"]["player"]["name"] == "n2"


@pytest.mark.parametrize(
    ("kind", "qualifies", "fails"),
    [
        ("UTILITY", {"util": 1201}, {"util": 1200}),
        ("HEALER", {"heal": 10001}, {"heal": 10000}),
        ("DEMOLISHER", {"tower": 10001}, {"tower": 10000}),
    ],
)
def test_threshold_cards(constants, kind, qualifies, fails):
    def build(values):
        p = player(1, heal=values.get("heal", 0), tower=values.get("tower", 0))
        return run([match(1, [p])], [user(1)], [ext(1, {1: (10, values.get("util", 0), [])})], constants)[0]["report"]

    assert kind in card_types(build(qualifies))
    assert kind not in card_types(build(fails))
    card = next(c for c in build(qualifies)["spotlights"]["cards"] if c["type"] == kind)
    assert card["score"]["value"] == next(iter(qualifies.values())).__format__(",")


def test_card_order_and_badges(constants):
    p1 = player(1, heal=20000, tower=20000, k=3, d=1, hero=1)
    extensions = [ext(1, {1: (30, 5000, ["k.Rich", "k.Missing"])})]

    def tr(key, params=None):
        return {"k.Rich": "Rich-#FFD700"}.get(key, t(key, params))

    translators = {"p:c1": ReportTranslator(tr, "en-US", lambda h: "AM")}
    report = format_daily_report_bundles(
        data_for([match(1, [p1])], [1]), [user(1)], extensions, constants, TARGET, translators,
        lambda n: n, lambda n: f"banner:{n}", "f",
    )[0]["report"]  # fmt: skip
    assert card_types(report) == ["MVP", "UTILITY", "HEALER", "DEMOLISHER"]
    mvp = report["spotlights"]["mvp"]
    assert mvp["badges"] == [{"text": "Rich", "hexColor": "#FFD700"}, {"text": "k.Missing", "hexColor": "#FFA500"}]
    assert mvp["player"]["kda"] == "3/1/0 (3.0)"
    assert mvp["player"]["heroBannerUrl"] == "banner:antimage"
    assert report["spotlights"]["cards"][1]["score"]["value"] == "5,000"


def test_impact_normalisation(constants):
    matches = [
        match(1, [player(1, dmg=8000, tower=2000, nw=20000), player(2, dmg=4000, tower=1000, nw=10000)]),
    ]
    report = run(matches, [user(1), user(2)], [], constants)[0]["report"]
    rows = {r["player"]["name"]: r for r in report["squad"]}
    assert rows["n1"]["impact"] == {"damage": {"heroPercent": 80, "buildingsPercent": 20}, "networth": {"percent": 100}}
    assert rows["n2"]["impact"] == {"damage": {"heroPercent": 40, "buildingsPercent": 10}, "networth": {"percent": 50}}
    assert [r["rank"] for r in report["squad"]] == [1, 2]


def test_rows_hero_pool(constants):
    matches = [
        match(i, [player(1, hero=h)], radiant_win=w)
        for i, (h, w) in enumerate([(1, True), (2, False), (2, False), (3, True), (5, True)], 1)
    ]
    report = run(matches, [user(1, nickname="Nick")], [], constants)[0]["report"]
    row = report["squad"][0]
    assert row["player"] == {"name": "Nick", "avatarUrl": "a1", "winCount": 3, "loseCount": 2}
    assert [(h["wins"], h["losses"]) for h in row["heroes"]] == [(1, 0), (1, 0), (1, 0)]
    assert row["plusHeroesCount"] == 1
    assert row["kda"] == {"ratio": "0.0", "detail": "0.0 / 0.0 / 0.0"}


def test_fixture_report(constants, fixture_loader):
    fixture = fixture_loader("daily_report.json")
    users = [user(s, channel="g") for s in (100000001, 100000002, 100000003, 100000007)]
    translators = {"p:g": ReportTranslator(t, "en-US", str)}
    bundles = format_daily_report_bundles(
        fixture["data"], users, fixture["extensions"], constants,
        datetime.fromtimestamp(fixture["targetDate"], UTC), translators, str, str, "f",
    )  # fmt: skip
    report = bundles[0]["report"]
    assert report["headerStats"]["matches"]["value"] == 6
    assert len(report["squad"]) == 4
    assert card_types(report)[0] == "MVP"


# ------------------------------------------------------------------ legacy
def test_legacy_combinations():
    m1 = match(1, [player(1, True, k=4, d=2, a=6, imp=10), player(2, True, k=2, d=1, a=1, imp=-30)], radiant_win=True)
    m2 = match(2, [player(1, False, k=1, d=3, a=2, imp=20)], radiant_win=True)
    m3 = match(3, [player(2, True)], radiant_win=False)
    data = data_for([m1, m2, m3], [1, 2, 3])["players"]
    channel = SimpleNamespace(platform="p", channel_id="c1")
    other = SimpleNamespace(platform="p", channel_id="empty")
    bindings = [user(1, uid=11), user(2, uid=12)]
    result = build_legacy_report([channel, other], bindings, data, {11: "Alias"})
    assert len(result) == 1
    entry = result[0]
    p1, p2 = entry["players"]
    assert p1["name"] == "Alias" and p2["name"] == "n2"
    assert (p1["winCount"], p1["loseCount"]) == (1, 1)
    assert (p1["avgKills"], p1["avgDeaths"], p1["avgAssists"]) == (2.5, 2.5, 4)
    assert p1["avgKDA"] == 2.6
    assert p1["avgImp"] == 15
    combos = {c["name"]: c for c in entry["combinations"]}
    assert set(combos) == {"Alias/n2", "Alias", "n2"}
    assert combos["Alias/n2"]["winCount"] == 1
    assert combos["Alias"]["winCount"] == 0 and len(combos["Alias"]["matches"]) == 1
    assert combos["n2"]["winCount"] == 0


# ----------------------------------------------------------------- 1.1.1 fixes
def test_negative_mvp_scores_still_build_the_report(constants):
    """A day of bad games (MVP score < -1 because of a negative IMP) used to crash the spotlight lookup."""
    matches = [match(7, [player(1, False, d=10, imp=-40)], radiant_win=True)]
    bundles = run(matches, [user(1)], [ext(7, {1: (-4.5, 0, [])})], constants)
    report = bundles[0]["report"]
    assert report["spotlights"]["mvp"]["type"] == "MVP"
    assert report["spotlights"]["mvp"]["player"]["kda"].startswith("0/10/0")


def test_report_window_has_an_end(constants):
    """Matches after the reported day belong to the next report (they were counted twice)."""
    tomorrow = int(TARGET.timestamp()) + 86400 + 60
    matches = [match(1, [player(1, k=1)]), match(2, [player(1, k=9)], start=tomorrow)]
    report = run(matches, [user(1)], [], constants)[0]["report"]
    assert report["headerStats"]["matches"]["value"] == 1
    assert report["headerStats"]["kills"]["value"] == "1"
    explicit = format_daily_report_bundles(
        data_for(matches, [1]),
        [user(1)],
        [],
        constants,
        TARGET,
        {"p:c1": ReportTranslator(t, "en-US", str)},
        str,
        str,
        "footer",
        end_date=datetime.fromtimestamp(tomorrow + 1, UTC),
    )
    assert explicit[0]["report"]["headerStats"]["matches"]["value"] == 2


def test_one_broken_channel_does_not_stop_the_others(constants):
    # Stratz omitted player 2 from match 5: looking up the player's own entry fails.
    matches = [match(4, [player(1, k=3)]), match(5, [player(3)])]
    data = data_for(matches, [1])
    data["players"].append({"steamAccount": {"id": 2}, "matches": [matches[1]]})
    data["players"].append(None)
    users = [user(1, channel="ok"), user(2, channel="broken")]
    translators = {key: ReportTranslator(t, "en-US", str) for key in ("p:ok", "p:broken")}
    errors = []
    bundles = format_daily_report_bundles(
        data, users, [], constants, TARGET, translators, str, str, "f", on_error=lambda key, e: errors.append(key)
    )
    assert [b["channelId"] for b in bundles] == ["ok"]
    assert errors == ["p:broken"]
    with pytest.raises(LookupError):
        format_daily_report_bundles(data, users, [], constants, TARGET, translators, str, str, "f")


def test_legacy_report_isolates_broken_bindings():
    channel_ok = SimpleNamespace(platform="p", channel_id="ok")
    channel_bad = SimpleNamespace(platform="p", channel_id="bad")
    good = user(1, uid=1, channel="ok")
    bad = user(2, uid=2, channel="bad")
    players_data = [
        {"steamAccount": {"id": 1, "name": "one"}, "matches": [match(1, [player(1, k=2)])]},
        {"steamAccount": {"id": 2, "name": "two"}, "matches": [match(2, [player(9)])]},
        None,
    ]
    errors = []
    results = build_legacy_report(
        [channel_ok, channel_bad], [good, bad], players_data, {}, on_error=lambda key, e: errors.append(key)
    )
    assert [r["channel"].channel_id for r in results] == ["ok"]
    assert errors == ["p:bad"]
