"""Build the anonymised preview/test fixtures shipped in ``dota2tracker/fixtures``.

Inputs are public API downloads (OpenDota ``/matches/{id}`` and the Valve
datafeed) placed in a working directory::

    python scripts/build_fixtures.py <download_dir> <opendota_match_id>

Player names and account IDs are replaced by fictional values; Stratz-only
structures (player profile, daily report data, hero recommendation input) are
synthesised with fixed random seeds so the output is reproducible.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dota2tracker.data.static import DotaConstants  # noqa: E402
from dota2tracker.domain.opendota import transform_opendota_match  # noqa: E402

OUT = ROOT / "dota2tracker" / "fixtures"
NAMES = [
    "李火旺",
    "Ghost Walker",
    "天地一刀斩",
    "灰色轨迹",
    "May thy knife chip",
    "o.O",
    "其疾如风",
    "SaGA",
    "lil poti",
    "张三",
]
DEFAULT_AVATAR = "https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg"
PLAYER_FIELDS = [
    "account_id",
    "player_slot",
    "hero_id",
    "hero_variant",
    "level",
    "leaver_status",
    "party_id",
    "lane",
    "benchmarks",
    "kills",
    "deaths",
    "assists",
    "isRadiant",
    "net_worth",
    "item_0",
    "item_1",
    "item_2",
    "item_3",
    "item_4",
    "item_5",
    "backpack_0",
    "backpack_1",
    "backpack_2",
    "item_neutral",
    "hero_damage",
    "tower_damage",
    "last_hits",
    "denies",
    "gold_per_min",
    "xp_per_min",
    "hero_healing",
    "randomed",
    "personaname",
    "rank_tier",
    "gold_t",
    "xp_t",
    "camps_stacked",
    "damage_taken",
    "stuns",
    "purchase_log",
    "additional_units",
    "kills_log",
    "permanent_buffs",
]
MATCH_FIELDS = [
    "match_id",
    "radiant_win",
    "lobby_type",
    "game_mode",
    "region",
    "start_time",
    "duration",
    "radiant_score",
    "dire_score",
    "radiant_gold_adv",
    "radiant_xp_adv",
    "tower_status_radiant",
    "tower_status_dire",
    "barracks_status_radiant",
    "barracks_status_dire",
    "picks_bans",
    "od_data",
]
RANK_TIERS = [54, 62, 45, 71, None, 33, 80, 51, 43, 65]


def dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{name}: {(OUT / name).stat().st_size // 1024} KiB")


def anonymise_match(raw: dict) -> dict:
    match = {key: raw.get(key) for key in MATCH_FIELDS}
    match["objectives"] = [o for o in raw.get("objectives") or [] if o.get("type") == "building_kill"]
    players = []
    for index, player in enumerate(raw.get("players") or []):
        trimmed = {key: player.get(key) for key in PLAYER_FIELDS if key in player}
        trimmed["account_id"] = 100000001 + index
        trimmed["personaname"] = NAMES[index % len(NAMES)]
        trimmed["rank_tier"] = RANK_TIERS[index % len(RANK_TIERS)]
        players.append(trimmed)
    match["players"] = players
    match["match_id"] = 8000000001
    return match


def stratz_match(od_match: dict, constants: DotaConstants) -> dict:
    """Synthesize a parsed Stratz ``MatchInfo`` response from the OpenDota match."""
    rng = random.Random(20251026)
    match = transform_opendota_match(od_match, constants)
    match.pop("odParsed", None)
    match["lobbyType"] = "RANKED"
    match["gameMode"] = "ALL_PICK_RANKED"
    match["rank"] = 64
    match["radiantKills"] = _per_minute(od_match.get("radiant_score") or 0, len(match["radiantNetworthLeads"]), rng)
    match["direKills"] = _per_minute(od_match.get("dire_score") or 0, len(match["radiantNetworthLeads"]), rng)
    leads = match["radiantNetworthLeads"]
    match["winRates"] = [round(min(0.95, max(0.05, 0.5 + lead / 40000)), 4) for lead in leads[1:]]
    hero_by_name = {constants.hero_name(h): h for h in constants.hero_ids()}
    deaths: dict[int, list[int]] = {}
    for raw, player in zip(od_match["players"], match["players"], strict=True):
        kills = [{"time": entry["time"]} for entry in raw.get("kills_log") or []]
        player["stats"]["killEvents"] = kills
        for entry in raw.get("kills_log") or []:
            victim = hero_by_name.get(entry.get("key"))
            if victim is not None:
                deaths.setdefault(victim, []).append(entry["time"])
        player["stats"]["assistEvents"] = [
            {"time": t} for t in sorted(rng.sample(range(60, 1900), k=min(8, player["assists"] or 0)))
        ]
        buffs = []
        for buff in raw.get("permanent_buffs") or []:
            if buff.get("permanent_buff") == 12:
                buffs.append({"abilityId": None, "itemId": 609, "stackCount": 0})
            if buff.get("permanent_buff") == 2:
                buffs.append({"abilityId": None, "itemId": 108, "stackCount": 0})
        if player["hero"]["id"] == 48:
            buffs.append({"abilityId": None, "itemId": 247, "stackCount": 0})
        player["stats"]["matchPlayerBuffEvent"] = buffs or None
        received = player["stats"]["heroDamageReport"]["receivedTotal"]
        total = received["physicalDamage"]
        received.update(
            {"physicalDamage": int(total * 0.55), "magicalDamage": int(total * 0.4), "pureDamage": int(total * 0.05)}
        )
        dealt = player["stats"]["heroDamageReport"]["dealtTotal"]
        dealt.update({"slowDuration": rng.randint(0, 9000), "disableDuration": rng.randint(0, 4000)})
        player["steamAccount"]["seasonLeaderboardRank"] = None
        player["dotaPlus"] = {"level": rng.randint(1, 30)} if rng.random() < 0.7 else None
    match["players"][6]["steamAccount"]["seasonLeaderboardRank"] = 87
    for player in match["players"]:
        player["stats"]["deathEvents"] = [{"time": t} for t in deaths.get(player["hero"]["id"], [])]
    return {"match": match}


def _per_minute(total: int, minutes: int, rng: random.Random) -> list[int]:
    values = [0] * max(1, minutes - 1)
    for _ in range(total):
        values[rng.randrange(len(values))] += 1
    return values


def player_profile(constants: DotaConstants, anonymous: bool = False) -> dict:
    rng = random.Random(7 if not anonymous else 8)
    heroes = constants.hero_ids()
    steam_id = 100000001
    matches = []
    now = 1761480000
    for index in range(0 if anonymous else 25):
        hero_id = rng.choice(heroes[:60])
        radiant = rng.random() < 0.5
        win = rng.random() < 0.55
        parsed = rng.random() < 0.85
        lane = rng.choice(["SAFE_LANE", "MID_LANE", "OFF_LANE", "JUNGLE", "SAFE_LANE", "OFF_LANE"])
        kills, deaths, assists = rng.randint(0, 18), rng.randint(0, 12), rng.randint(0, 25)
        others = [
            {
                "steamAccount": {"id": 200000000 + i},
                "isRadiant": i < 4 if radiant else i >= 4,
                "lane": "SAFE_LANE",
                "kills": rng.randint(0, 12),
                "deaths": rng.randint(0, 12),
                "assists": rng.randint(0, 20),
                "position": f"POSITION_{(i % 5) + 1}",
                "award": "NONE",
                "imp": rng.randint(-20, 40),
                "hero": {"id": heroes[i], "shortName": constants.hero_short_name(heroes[i])},
            }
            for i in range(9)
        ]
        own = {
            "steamAccount": {"id": steam_id},
            "isRadiant": radiant,
            "lane": lane,
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "position": f"POSITION_{rng.randint(1, 5)}",
            "award": "NONE",
            "imp": rng.randint(-25, 45) if parsed else None,
            "hero": {"id": hero_id, "shortName": constants.hero_short_name(hero_id)},
        }
        outcome = rng.choice(["RADIANT_VICTORY", "RADIANT_STOMP", "DIRE_VICTORY", "DIRE_STOMP", "TIE"])
        matches.append(
            {
                "id": 8000000100 + index,
                "rank": rng.choice([45, 51, 52, 53, 54, 55, 61]),
                "lobbyType": "RANKED",
                "gameMode": rng.choice(["ALL_PICK_RANKED", "TURBO", "ALL_PICK_RANKED"]),
                "startDateTime": now - index * 30000,
                "parsedDateTime": (now - index * 30000 + 2600) if parsed else None,
                "durationSeconds": rng.randint(1100, 3400),
                "didRadiantWin": radiant == win,
                "topLaneOutcome": outcome,
                "midLaneOutcome": rng.choice(["RADIANT_VICTORY", "DIRE_STOMP", "TIE"]),
                "bottomLaneOutcome": rng.choice(["RADIANT_STOMP", "DIRE_VICTORY", "TIE"]),
                "radiantKills": [rng.randint(0, 3) for _ in range(30)] if parsed else None,
                "direKills": [rng.randint(0, 3) for _ in range(30)] if parsed else None,
                "players": [own, *others],
            }
        )
    performance = []
    for hero_id in rng.sample(heroes[:80], 25):
        match_count = rng.randint(2, 160)
        performance.append(
            {
                "hero": {"id": hero_id, "shortName": constants.hero_short_name(hero_id)},
                "imp": rng.randint(-15, 35),
                "winCount": rng.randint(0, match_count),
                "matchCount": match_count,
            }
        )
    performance.sort(key=lambda h: -h["matchCount"])
    player = {
        "steamAccount": {
            "avatar": DEFAULT_AVATAR,
            "name": "李火旺" if not anonymous else "Hidden Player",
            "seasonRank": 54 if not anonymous else None,
            "seasonLeaderboardRank": None,
            "id": steam_id,
            "isAnonymous": anonymous,
        },
        "guildMember": None if anonymous else {"guild": {"tag": "D2T"}},
        "matchCount": 2873,
        "winCount": 1490,
        "performance": {"imp": 12},
        "heroesPerformance": performance[:25],
        "matches": matches,
    }
    extra = {
        "player": {
            "heroesPerformance": [] if anonymous else performance,
            "dotaPlus": None
            if anonymous
            else [{"heroId": h["hero"]["id"], "level": rng.randint(1, 30)} for h in performance[:9]],
        }
    }
    return {"playerQuery": {"player": player}, "playerExtraQuery": extra}


def daily_players(stratz: dict, constants: DotaConstants) -> dict:
    rng = random.Random(42)
    base = 1761408000  # target day start (2025-10-26 00:00 +08:00) used by tests
    squad = [100000001, 100000002, 100000003, 100000007]
    match_pool = []
    for index in range(9):
        start = base + 3600 * (index + 2) - (86400 if index >= 6 else 0)
        radiant_win = rng.random() < 0.5
        players = []
        for position, steam_id in enumerate(squad + [300000000 + i for i in range(6)]):
            hero_id = constants.hero_ids()[(index * 7 + position * 5) % 120]
            players.append(
                {
                    "heroId": hero_id,
                    "kills": rng.randint(0, 16),
                    "deaths": rng.randint(0, 11),
                    "assists": rng.randint(0, 24),
                    "imp": rng.randint(-20, 40),
                    "isRadiant": position < 2 or position in (4, 5, 6),
                    "heroDamage": rng.randint(4000, 52000),
                    "towerDamage": rng.choice([0, 300, 2200, 6400, 12500]),
                    "heroHealing": rng.choice([0, 0, 1800, 12500]),
                    "networth": rng.randint(5000, 32000),
                    "steamAccount": {"id": steam_id},
                }
            )
        match_pool.append(
            {
                "id": 8000000200 + index,
                "didRadiantWin": radiant_win,
                "parsedDateTime": start + 2500,
                "startDateTime": start,
                "durationSeconds": rng.randint(1300, 3300),
                "players": players,
            }
        )
    result = []
    for steam_id, name in zip(squad, ["李火旺", "Ghost Walker", "天地一刀斩", "其疾如风"], strict=True):
        result.append(
            {
                "steamAccount": {"id": steam_id, "name": name, "avatar": DEFAULT_AVATAR},
                "matches": [m for m in match_pool if any(p["steamAccount"]["id"] == steam_id for p in m["players"])],
            }
        )
    extensions = []
    for match in match_pool:
        extensions.append(
            {
                "matchId": match["id"],
                "startTime": match["startDateTime"],
                "data": {
                    "matchId": match["id"],
                    "players": [
                        {
                            "steamAccountId": p["steamAccount"]["id"],
                            "rankSnapshot": {"medal": 5, "star": 3, "leaderboard": None, "inTop100": None},
                            "mvpScore": round(rng.uniform(4, 60), 3),
                            "titles": rng.sample(
                                [
                                    f"dota2tracker.template.titles.{t}"
                                    for t in ("MVP", "Rich", "Nuker", "Healer", "Idle", "Utility")
                                ],
                                k=rng.randint(0, 3),
                            ),
                            "utilityScore": rng.choice([0, 300, 1800, 2600]),
                            "laneResult": "tie",
                            "partyId": None,
                        }
                        for p in match["players"]
                    ],
                },
            }
        )
    return {"targetDate": base, "data": {"players": result}, "extensions": extensions}


def recommendation(constants: DotaConstants) -> dict:
    rng = random.Random(99)
    heroes = constants.hero_ids()
    lifetime = [
        {
            "heroId": h,
            "matchCount": rng.randint(5, 300),
            "winCount": rng.randint(2, 150),
            "lastPlayedDateTime": 1761480000 - rng.randint(0, 9) * 86400,
        }
        for h in heroes[:40]
    ]
    recent = [
        {
            "heroId": h["heroId"],
            "matchCount": rng.randint(1, 12),
            "winCount": rng.randint(0, 8),
            "lastPlayedDateTime": h["lastPlayedDateTime"],
            "imp": rng.randint(-10, 40),
        }
        for h in lifetime[:12]
    ]
    meta = {}
    for pos in range(1, 6):
        meta[f"pos{pos}"] = [
            {"heroId": h, "winCount": rng.randint(800, 6000), "matchCount": rng.randint(1500, 11000)}
            for h in rng.sample(heroes, 30)
        ]
    for entries in meta.values():
        for entry in entries:
            entry["winCount"] = min(entry["winCount"], entry["matchCount"])
    return {
        "player": {
            "steamAccount": {"seasonRank": 54, "isAnonymous": False},
            "matches": [{"rank": 54} for _ in range(25)],
            "lifetimePerformance": lifetime,
            "recentPerformance": recent,
        },
        "heroStats": meta,
    }


def members() -> dict:
    return {
        "players": [
            {
                "steamAccount": {"id": 100000001 + i, "avatar": DEFAULT_AVATAR, "name": NAMES[i], "seasonRank": 54},
                "matches": [
                    {
                        "didRadiantWin": (i + j) % 3 != 0,
                        "startDateTime": 1761480000 - (i + 1) * 86400 * (3 if i == 3 else 1) - j * 7200,
                        "players": [
                            {
                                "isRadiant": True,
                                "kills": 3,
                                "deaths": 2,
                                "assists": 7,
                                "steamAccount": {"id": 100000001 + i},
                                "hero": {"shortName": "luna"},
                                "imp": 10,
                            }
                        ],
                    }
                    for j in range(10 if i != 4 else 0)
                ],
            }
            for i in range(6)
        ]
    }


def trim_patch_notes(source: Path, lang: str) -> list[dict]:
    patches = []
    for version in ("7.40c", "7.41", "7.41a", "7.41b", "7.41c", "7.41d", "7.41e", "7.41f"):
        path = source / f"patch_{version}_{lang}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data["heroes"] = [h for h in data.get("heroes") or [] if h.get("hero_id") == 48]
        data["items"] = [i for i in data.get("items") or [] if i.get("ability_id") in (1, 145)]
        data.pop("general_notes", None)
        data.pop("neutral_items", None)
        data.pop("neutral_creeps", None)
        patches.append(data)
    return patches


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    source = Path(argv[1])
    raw = json.loads((source / f"ranked_{argv[2]}.json").read_text(encoding="utf-8"))
    constants = DotaConstants.load()
    od_match = anonymise_match(raw)
    dump("opendota_match.json", od_match)
    dump("stratz_match.json", stratz_match(od_match, constants))
    dump("player_profile.json", player_profile(constants))
    dump("player_profile_anonymous.json", player_profile(constants, anonymous=True))
    dump("daily_report.json", daily_players({}, constants))
    dump("hero_recommendation.json", recommendation(constants))
    dump("members.json", members())
    for lang, locale in (("schinese", "zh-CN"), ("english", "en-US")):
        hero = json.loads((source / f"hero48_{lang}.json").read_text(encoding="utf-8"))["result"]["data"]["heroes"][0]
        dump(f"hero_details.{locale}.json", hero)
        items = json.loads((source / f"itemlist_{lang}.json").read_text(encoding="utf-8"))["result"]["data"][
            "itemabilities"
        ]
        dump(f"item_list.{locale}.json", items)
        dump(
            f"item_details.{locale}.json",
            {
                str(item_id): json.loads((source / f"item{item_id}_{lang}.json").read_text(encoding="utf-8"))["result"][
                    "data"
                ]["items"][0]
                for item_id in (1, 145)
            },
        )
        dump(f"patch_notes.{locale}.json", trim_patch_notes(source, lang))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
