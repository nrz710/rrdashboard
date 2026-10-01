"""Player Stats from FanGraphs leaderboards: pull -> parse -> match -> website table."""
import json
from datetime import datetime, timezone

import pytest

from rr import config, published, store


def _page(rows, key="leaders/major-league/data"):
    return {"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": [key, {"pos": "all"}], "state": {"data": {"data": rows, "totalCount": len(rows)}}}]}}}}


def _bat(pid, name, team, pa, hr, avg, wrc, war, xw=None):
    r = {"Name": f'<a href="statss.aspx?playerid={pid}">{name}</a>', "PlayerName": name, "Team": f"<a>{team}</a>",
         "TeamNameAbb": team, "playerid": pid, "xMLBAMID": 600000 + pid, "PA": pa, "HR": hr, "AVG": avg,
         "wRC+": wrc, "WAR": war}
    if xw is not None:
        r["xwOBA"] = xw
    return r


def _fld(pid, name, team, pos, inn, gs, oaa=None):
    r = {"PlayerName": name, "TeamNameAbb": team, "playerid": pid, "Pos": pos, "Inn": inn, "GS": gs}
    if oaa is not None:
        r["OAA"] = oaa
    return r


@pytest.fixture
def snapshot(tmp_path):
    snap = store.create_snapshot(tmp_path, "20260930T120000Z_current_2026")
    pages = {
        "stats/bat-platform": [_bat(1, "Pete Crow-Armstrong", "CHC", 700, 31, .247, 109, 5.4),
                               _bat(2, "Utility Guy", "- - -", 350, 8, .255, 98, 1.5)],
        "stats/bat-statcast-platform": [{**_bat(1, "Pete Crow-Armstrong", "CHC", 700, 31, .247, 109, 5.4), "xwOBA": .315},
                                        {**_bat(2, "Utility Guy", "- - -", 350, 8, .255, 98, 1.5), "xwOBA": .301}],
        "stats/bat-3yr": [_bat(1, "Pete Crow-Armstrong", "CHC", 1600, 60, .240, 101, 9.8),
                          _bat(2, "Utility Guy", "- - -", 0, 0, 0, 0, 0)],
        "stats/fld-platform": [_fld(1, "Pete Crow-Armstrong", "CHC", "CF", 1300.1, 150, 17),
                               _fld(2, "Utility Guy", "NYY", "2B", 400.0, 45, 2), _fld(2, "Utility Guy", "NYY", "SS", 210.2, 22, -1)],
        "stats/fld-statcast-platform": [_fld(1, "Pete Crow-Armstrong", "CHC", "CF", 1300.1, 150),
                                        _fld(2, "Utility Guy", "NYY", "2B", 400.0, 45), _fld(2, "Utility Guy", "NYY", "SS", 210.2, 22)],
        "stats/fld-3yr": [_fld(1, "Pete Crow-Armstrong", "CHC", "CF", 3500.0, 400, 40)],
        "stats/fld-statcast-3yr": [_fld(1, "Pete Crow-Armstrong", "CHC", "CF", 3500.0, 400)],
    }
    for key, rows in pages.items():
        store.save_raw(snap, key, None, {"url": config.page_url(key, None), "fetched_at": datetime.now(timezone.utc).isoformat(),
                                         "source": "next_data", "payload": _page(rows)})
    store.write_manifest(snap, {"kind": "current", "season": 2026, "stats_season": 2026, "planned": 7, "ok": 7,
                                "status": "complete", "started_at": datetime.now(timezone.utc).isoformat(),
                                "results": [{"page": k, "team": None, "ok": True} for k in pages]})
    published.publish(tmp_path, snap, log=lambda _: None)
    return tmp_path


def test_player_stats_table(snapshot, tmp_path):
    from rr import site
    out = tmp_path / "_site"
    site.build(snapshot, out, log=lambda _: None)
    sid = json.loads((out / "data" / "index.json").read_text())["snapshots"][0]["id"]
    meta = json.loads((out / "data" / sid / "meta.json").read_text())
    t = next(x for x in meta["tables"] if x["table"] == "player-stats")
    assert t["page"] == "player-stats" and t["title"] == "PLAYER STATS" and t["player"]
    assert not any(x["player"] for x in meta["tables"] if x["page"].startswith("stats/"))  # raw leaderboards stay hidden
    p = json.loads((out / "data" / sid / t["file"]).read_text())
    rows = {r[0]: dict(zip(p["columns"], r)) for r in p["rows"]}
    pca, ut = rows["Pete Crow-Armstrong"], rows["Utility Guy"]
    assert (pca["2026 GS"], pca["2026 INN CF"], pca["2026 PA"], pca["2026 HR"], pca["2026 wRC+"]) == (150, 1300.1, 700, 31, 109)
    assert pca["2026 xwOBA"] == 0.315 and pca["2026 OAA"] == 17 and pca["2026 fWAR"] == 5.4
    assert abs(pca["2026 fWAR/700"] - 5.4) < 1e-9                       # 5.4 / 700 * 700
    assert abs(pca["2024-26 fWAR/700"] - 9.8 / 1600 * 700) < 1e-9
    assert (pca["2024-26 GS"], pca["2024-26 OAA"], pca["2024-26 wRC+"]) == (400, 40, 101)
    assert (ut["2026 INN 2B"], ut["2026 INN SS"], ut["2026 GS"], ut["2026 OAA"]) == (400.0, 210.2, 67, 1)  # summed over positions
    assert ut["2024-26 fWAR/700"] is None                                # 0 PA -> blank, not a division error
    assert p["labels"]["2026 INN CF"] == "2026 INN (CF)" and p["labels"]["2026 fWAR/700"] == "2026 fWAR/700 PA"
    assert p["formats"]["2026 AVG"] == "avg3" and p["formats"]["2026 fWAR/700"] == "dec1"
    assert "2026 (PLATFORM YEAR)" in p["group_order"] and any("LAST 3 YEARS" in g for g in p["group_order"])
    assert p["tslug"][list(rows).index("Pete Crow-Armstrong")] == "cubs"
    assert "player-stats" in json.loads((out / "data" / sid / "meta.json").read_text())["available"]
    assert not [w for w in meta["warnings"] if "missing" in w or "no " in w.lower()]


def test_missing_stat_is_reported(tmp_path):
    from rr import playerstats
    import pandas as pd
    bat = pd.DataFrame([{"playerid": 1, "PlayerName": "A", "PA": 10, "HR": 1, "AVG": .3, "WAR": .1}])  # no wRC+
    df, cols, *_, problems = playerstats.build({"stats/bat-platform": bat}, 2026)
    assert any("wRC+" in p for p in problems) and len(df) == 1
