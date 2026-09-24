"""Build a FICTIONAL sample snapshot so you can try the dashboard before the first real pull.

    python make_demo_data.py                     # writes ./demo_data (raw + published)
    RR_DATA_DIR=demo_data streamlit run dashboard.py

Every player, salary and injury here is made up. The page structure imitates
a Next.js page (__NEXT_DATA__ with react-query entries), but it's a guess;
the real field names will differ.
"""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rr import config, store  # noqa: E402

FIRST = ["Mateo", "Colin", "Darius", "Evan", "Rafael", "Tucker", "Jonah", "Kai", "Luis", "Brady", "Nolan",
         "Emilio", "Grant", "Wes", "Ignacio", "Reid", "Andrés", "Cal", "Dominic", "Hollis", "Yusei", "Beau",
         "Marco", "Silas", "Owen", "Tomás", "Jace", "Keaton", "Ruben", "Theo"]
LAST = ["Arroyo", "Whitlock", "Pennington", "Castellano", "Brandt", "Okafor", "Delacruz", "Harlan", "Voss",
        "Maldonado", "Kincaid", "Ferreira", "Sutter", "Lockhart", "Ibarra", "Calloway", "Thorne", "Quintero",
        "Ashby", "Navarro", "Pruitt", "Estrada", "Holloway", "Serrano", "Winslow", "Barrera", "Cobb", "Ridley",
        "Montalvo", "Greer"]
POS_HIT = ["C", "C", "1B", "2B", "SS", "3B", "LF", "CF", "RF", "DH", "UT", "OF", "IF"]
INJURIES = ["Hamstring strain", "Shoulder inflammation", "UCL sprain", "Oblique strain", "Ankle sprain",
            "Forearm tightness", "Concussion", "Back spasms"]
IL = ["10IL", "15IL", "60IL", "DTD"]
COACH_ROLES = ["Manager", "Bench Coach", "Pitching Coach", "Hitting Coach", "First Base Coach", "Third Base Coach"]


def next_page(queries: list[tuple[str, list[dict]]]) -> dict:
    return {"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": [k, 1], "state": {"data": rows, "status": "success"}} for k, rows in queries]},
        "nav": [{"name": n, "slug": s} for n, s in list(config.TEAMS.items())]}},
        "page": "/roster-resource/x", "buildId": "demo"}


def main(out_dir: Path = Path("demo_data")) -> Path:
    rng = random.Random(7)
    season = config.current_season()
    snap = store.snapshots_dir(out_dir) / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_SAMPLE_{season}"
    snap.mkdir(parents=True)
    results = []
    pid = 30000
    everyone = []

    def save(page, team, payload):
        store.save_raw(snap, page, team, {"url": config.page_url(page, team), "status": 200,
                                         "fetched_at": datetime.now(timezone.utc).isoformat(), "source": "SAMPLE",
                                         "payload": payload})
        results.append({"page": page, "team": team, "url": config.page_url(page, team), "ok": True})

    used_names = set()
    # Injury report, transactions and closers are league-wide pages (one request each), the way
    # the tool now fetches them. Closers use a grouped layout to exercise the parser.
    all_inj, all_txn, closer_groups = [], [], []
    for team_name, slug in config.TEAMS.items():
        roster = []
        for i in range(26):
            while True:
                nm = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if nm not in used_names:
                    break
            if slug == "cubs" and i == 0:  # one deliberate duplicate name, to show ambiguity handling
                nm = everyone[0][1]["PlayerName"]
            used_names.add(nm)
            pid += rng.randint(1, 40)
            pitcher = i >= 13
            roster.append({
                "playerid": pid, "xMLBAMID": 660000 + pid, "PlayerName": nm,
                "Pos": ("SP" if i < 18 else "RP") if pitcher else POS_HIT[i % len(POS_HIT)],
                "Age": rng.randint(22, 36), "Bats": rng.choice("RRRLLS"), "Throws": rng.choice("RRRL"),
                "HowAcquired": rng.choice([f"Drafted {rng.randint(1, 12)}th Rd '{rng.randint(15, 23)}",
                                           f"Trade ({rng.choice(list(config.TEAM_ABBR.values()))}) {rng.choice(['Jul', 'Dec', 'Mar'])}'{rng.randint(20, 25)}",
                                           f"Free Agent ({rng.choice(list(config.TEAM_ABBR.values()))}) {rng.choice(['Nov', 'Dec', 'Jan'])}'{rng.randint(23, 25)}",
                                           f"Waivers ({rng.choice(list(config.TEAM_ABBR.values()))}) {rng.choice(['Apr', 'Aug'])}'{season % 100}"]),
                "Roster": "Active", "Options": rng.choice([0, 1, 2, 3]), "ServiceTime": f"{rng.randint(0, 9)}.{rng.randint(0, 171):03d}",
            })
        everyone += [(slug, p) for p in roster]
        save("depth-charts", slug, next_page([("depthChart", roster)]))

        pay = [{"playerid": p["playerid"], "PlayerName": p["PlayerName"],
                f"Salary{season}": rng.choice([0.76, 0.78, 0.8, 1.2, 2.5, 4.0, 7.5, 12.0, 18.5, 25.0, 32.0]) * 1_000_000,
                "Contract": rng.choice(["Pre-arb", "Arb 1", "Arb 2", "Arb 3", "3 yr / $24M", "6 yr / $162M", "1 yr / $9M"]),
                "FreeAgent": season + rng.randint(1, 6)} for p in roster]
        save("payroll", slug, next_page([("payroll", pay)]))

        all_inj += [{"Team": config.TEAM_ABBR[slug], "PlayerName": p["PlayerName"], "Injury": rng.choice(INJURIES), "Status": rng.choice(IL),
                "InjuryDate": str(date(season, 9, 1) - timedelta(days=rng.randint(1, 120))),
                "ExpectedReturn": rng.choice(["Late Sep", "Oct", "2027", "TBD"])}
               for p in rng.sample(roster, rng.randint(1, 4))]

        all_txn += [{"Date": str(date(season, 9, 20) - timedelta(days=rng.randint(0, 150))),
                     "teamName": f"{config.SLUG_TO_TEAM[slug]}" if slug not in ("cubs", "white-sox") else
                     ("Chicago Cubs" if slug == "cubs" else "Chicago White Sox"), "PlayerName": p["PlayerName"],
                "Transaction": rng.choice(["Recalled from Triple-A", "Optioned to Triple-A", "Placed on 10-day IL",
                                           "Activated from IL", "Selected contract", "Designated for assignment"])}
               for p in rng.sample(roster, 5)]

        pen = [p for p in roster if p["Pos"] == "RP"]
        closer = [{"playerid": p["playerid"], "PlayerName": p["PlayerName"],
                   "Role": ["Closer", "Setup", "Setup", "Middle"][min(i, 3)], "Saves": [28, 4, 2, 0][min(i, 3)] + rng.randint(0, 9)}
                  for i, p in enumerate(pen[:5])]
        closer_groups.append({"team": config.TEAM_ABBR[slug], "division": "", "relievers": closer})

        lineup = [{"PlayerName": p["PlayerName"], "playerid": p["playerid"], "GS": rng.randint(20, 150),
                   "MostCommonSpot": rng.randint(1, 9), "VsLHP": rng.choice(["Starts", "Sits", "Platoon"])}
                  for p in roster[:13]]
        save("lineup-tracker", slug, next_page([("lineupTracker", lineup)]))

    save("injury-report", None, next_page([("injuryReport", all_inj)]))
    save("transaction-tracker", None, next_page([("transactions", sorted(all_txn, key=lambda t: t["Date"], reverse=True))]))
    save("closer-depth-chart", None, next_page([("closerDepthChart", closer_groups)]))

    save("breakdowns/active-roster", None, next_page([("activeRoster", [
        {"Team": n, "Players": 26, "Pitchers": 13, "Position Players": 13, "Avg Age": round(rng.uniform(26, 30.5), 1),
         "Homegrown": rng.randint(6, 16)} for n in config.TEAMS])]))
    save("breakdowns/40-man-roster", None, next_page([("fortyMan", [
        {"Team": n, "Players": rng.choice([38, 39, 40]), "On 60-day IL": rng.randint(0, 5), "Open Spots": rng.randint(0, 2)}
        for n in config.TEAMS])]))
    save("breakdowns/coaches", None, next_page([("coaches", [
        {"Team": n, "Role": r, "Coach": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "Since": rng.randint(2015, season)}
        for n in config.TEAMS for r in COACH_ROLES])]))
    fa = rng.sample(everyone, 40)
    save("free-agent-tracker", None, next_page([("freeAgents", [
        {"playerid": p["playerid"], "PlayerName": p["PlayerName"], "Pos": p["Pos"], "Age": p["Age"] + 1,
         "PrevTeam": config.SLUG_TO_TEAM[s], "Status": rng.choice(["Unsigned", "Unsigned", "Signed"]),
         "ProjectedAAV": f"${rng.randint(1, 30)}M"} for s, p in fa])]))

    store.write_manifest(snap, {"kind": "SAMPLE", "season": season, "planned": len(results), "ok": len(results),
                                "status": "sample data", "started_at": datetime.now(timezone.utc).isoformat(),
                                "finished_at": datetime.now(timezone.utc).isoformat(), "results": results})
    print(f"Wrote fictional sample snapshot to {snap}")
    from rr import published
    published.publish(out_dir, snap, log=lambda _: None)
    print("Published it for the dashboard.")
    return snap


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo_data"))
