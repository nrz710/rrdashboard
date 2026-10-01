"""Payroll grid, season age, table titles."""
import pandas as pd

from rr import payroll
from rr.site import table_title


def test_season_age_is_age_on_june_30():
    # Pete Crow-Armstrong (born Mar 25, 2002): 24.5 on Sep 24, 2026 -> 24 on June 30, 2026
    assert payroll.season_age("24.5", "2026-09-24T15:12:14", 2026) == 24
    # Seiya Suzuki (born Aug 18, 1994): 32.1 in late September -> 31 on June 30
    assert payroll.season_age("32.1", "2026-09-24T15:12:14", 2026) == 31
    assert payroll.season_age(None, "2026-09-24", 2026) is None


def test_status_classes():
    assert payroll.status_class("GUARANTEED") == "pay-guaranteed"
    assert payroll.status_class("ARB 2") == "pay-arb"
    assert payroll.status_class("Pre-ARB") == "pay-prearb"
    assert payroll.status_class("CLUB OPTION (NON-GUARANT") == "pay-club"
    assert payroll.status_class("FREE AGENT (NOT DISPLAYE") == "pay-fa"
    assert payroll.status_class("POST OPT OUT") == "pay-player"


def test_grid_current_season_plus_six():
    cy = pd.DataFrame([
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2026, "Type": "GUARANTEED", "Salary": 35e6, "isEstimate": 0},
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2027, "Type": "CLUB OPTION", "Salary": 20e6, "isEstimate": 0},
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2028, "Type": "FREE AGENT", "Salary": None},
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2026, "Type": "PRE-ARB", "Salary": 760000, "isEstimate": 0},
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2027, "Type": "ARB 1", "Salary": None, "ArbSalaryProjection": 2.1e6},
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2028, "Type": "ARB 2", "Salary": None},
        {"_slug": "cubs", "MLBAMID": 3, "Season": 2025, "Type": "GUARANTEED", "Salary": 1e6},  # not on 2026 payroll
    ])
    summ = pd.DataFrame([{"_slug": "cubs", "MLBAMID": 1, "playerName": "Star", "description": "5 yr",
                          "startSeason": 2022, "endSeasonAll": 2027, "UPURL": "/players/star/1/stats"},
                         {"_slug": "cubs", "MLBAMID": 1, "playerName": "Star", "description": "extension",
                          "startSeason": 2030, "endSeasonAll": 2034}])
    grid, cells, years = payroll.build(cy, summ, None, 2026)
    assert years == [str(y) for y in range(2026, 2033)]
    star = grid[grid["Player"] == "Star"].iloc[0]
    assert star["Contract"] == "5 yr"  # the contract in force this season, not the future extension
    assert list(star[years[:3]]) == [35000000, 20000000, "FA"]
    i = list(grid["Player"]).index("Star")
    assert [cells[y][i] for y in years[:3]] == ["pay-guaranteed", "pay-club", "pay-fa"]
    j = [k for k in range(len(grid)) if grid.iloc[k]["Player"] != "Star"][0]
    assert list(grid.iloc[j][years[:3]]) == [760000, 2100000, "ARB 2"]
    assert cells["2027"][j] == "pay-arb pay-est"  # projected arbitration salary
    assert len(grid) == 2  # player 3 isn't on this season's payroll


def test_clean_titles():
    assert table_title("payroll2020 > dataContract.contractYears") == "CONTRACT YEARS"
    assert table_title("depth-charts-all > dataRoster") == "ROSTER"
    assert table_title("lineup-tracker > lineupData.lineupTracker.dataPlayers") == "LINEUP TRACKER"
    assert table_title("something > dataFooBar.dataPlayers") == "FOO BAR: PLAYERS"


def test_yearly_payroll_and_status_table():
    cy = pd.DataFrame([
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2026, "Type": "GUARANTEED", "Salary": 35e6, "isEstimate": 0},
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2027, "Type": "CLUB OPTION", "Salary": 20e6, "isEstimate": 0},
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2026, "Type": "PRE-ARB", "Salary": 760000, "isEstimate": 0},
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2027, "Type": "ARB 1", "Salary": None, "ArbSalaryProjection": 2.1e6},
    ])
    summ = pd.DataFrame([{"_slug": "cubs", "MLBAMID": m, "playerName": n, "_pkey": f"mlbam:{m}"} for m, n in [(1, "A"), (2, "B")]])
    grid, cells, years = payroll.build(cy, summ, None, 2026)
    t, colors, cols = payroll.yearly(grid, cells, years)
    assert cols[:5] == ["Player", "2026", "2026 Status", "2027", "2027 Status"] and len(cols) == 1 + 2 * 7
    a = t[t["Player"] == "A"].iloc[0]
    assert (a["2026"], a["2026 Status"], a["2027"], a["2027 Status"]) == (35000000, "Guaranteed", 20000000, "Club option")
    b = t[t["Player"] == "B"].iloc[0]
    assert b["2027 Status"] == "Arbitration (projected)" and b["_pkey"] == "mlbam:2"
    assert set(colors) == set(years)


def test_page_javascript_parses():
    """Catches syntax errors in web/index.html (runs where Node.js is available, as on GitHub)."""
    import shutil, subprocess, pathlib, pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = (pathlib.Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
    js = html.split("<script>", 1)[1].split("</script>", 1)[0]
    r = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=js,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_player_key_falls_back_to_contract_years_and_statuses_without_amounts_are_named():
    cy = pd.DataFrame([
        {"_slug": "cubs", "MLBAMID": 5, "Season": 2026, "Type": "GUARANTEED", "Salary": 2e6, "_pkey": "mlbam:5"},
        {"_slug": "cubs", "MLBAMID": 5, "Season": 2027, "Type": "MUTUAL OPTION", "Salary": None, "_pkey": "mlbam:5"},
    ])
    grid, cells, years = payroll.build(cy, None, None, 2026)  # no contract summary for this player
    row = grid.iloc[0]
    assert row["_pkey"] == "mlbam:5" and row["2027"] == "MUTUAL OPTION" and cells["2027"][0] == "pay-mutual"


def test_blank_seasons_projected_from_service_time():
    """Expiring deals with nothing on file for later seasons (e.g. Kevin Gausman after 2026)."""
    cy = pd.DataFrame([
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2026, "Type": "GUARANTEED", "Salary": 23e6},   # veteran
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2026, "Type": "GUARANTEED", "Salary": 8.1e6},  # 1-yr arb deal
        {"_slug": "cubs", "MLBAMID": 3, "Season": 2026, "Type": "PRE-ARB", "Salary": 780000},     # rookie
        {"_slug": "cubs", "MLBAMID": 4, "Season": 2026, "Type": "GUARANTEED", "Salary": 19e6},
        {"_slug": "cubs", "MLBAMID": 4, "Season": 2027, "Type": "FREE AGENT", "Salary": None},     # recorded FA
    ])
    summ = pd.DataFrame([{"_slug": "cubs", "MLBAMID": m, "playerName": n, "servicetime": st}
                         for m, n, st in [(1, "Vet", "9.070"), (2, "Arb", "5.100"), (3, "Rookie", "1.050"), (4, "Rec", "7.000")]])
    grid, cells, years = payroll.build(cy, summ, None, 2026)
    get = lambda n: grid[grid["Player"] == n].iloc[0]
    idx = lambda n: list(grid["Player"]).index(n)
    assert get("Vet")["2027"] == "FA" and pd.isna(get("Vet")["2028"]) and cells["2027"][idx("Vet")] == "pay-fa pay-est"
    assert list(get("Arb")[["2027", "2028"]]) == ["ARB", "FA"] and cells["2027"][idx("Arb")] == "pay-arb pay-est"
    assert get("Rookie")["2027"] == "PRE-ARB" and cells["2029"][idx("Rookie")] == "pay-arb pay-est"
    assert cells["2027"][idx("Rec")] == "pay-fa"  # recorded status is used as-is, not projected
    t, _, _ = payroll.yearly(grid, cells, years)
    assert t[t["Player"] == "Vet"].iloc[0]["2027 Status"] == "Free agent (projected)"


def test_service_time_parsing():
    assert abs(payroll.service_years("9.070") - (9 + 70 / 172)) < 1e-9
    assert payroll.service_years("n/a") is None and payroll.service_years(None) is None


def test_player_on_two_payrolls_future_only_on_current_team():
    """Traded mid-season: the old team lists only its share of this season; future years stay
    with the team that holds the contract (seen live with Luis Rengifo, SDP -> MIL)."""
    cy = pd.DataFrame([
        {"_slug": "padres", "MLBAMID": 7, "Season": 2026, "Type": "GUARANTEED", "Salary": 342032},
        {"_slug": "brewers", "MLBAMID": 7, "Season": 2026, "Type": "GUARANTEED", "Salary": 2e6},
        {"_slug": "brewers", "MLBAMID": 7, "Season": 2027, "Type": "MUTUAL OPTION", "Salary": 5e6},
    ])
    summ = pd.DataFrame([{"_slug": s, "MLBAMID": 7, "playerName": "Luis R", "servicetime": "7.000"} for s in ("padres", "brewers")])
    roster = pd.DataFrame([{"_slug": "brewers", "mlbamid": 7, "player": "Luis R", "position": "2B", "age": "29.0",
                            "loaddate": "2026-09-24T00:00:00"}])
    grid, cells, years = payroll.build(cy, summ, roster, 2026)
    sd, mil = [list(grid["_slug"]).index(t) for t in ("padres", "brewers")]
    assert cells["2027"][mil] == "pay-mutual" and grid.iloc[mil]["2027"] == 5000000
    assert cells["2027"][sd] == "" and pd.isna(grid.iloc[sd]["2027"])   # no projected "FA" on the old team's row
    assert grid.iloc[sd]["2026"] == 342032                               # its share of this season stays


def test_traded_player_recorded_status_from_other_page_beats_projection():
    """Seen live: Rengifo's current team (SD) lists only 2026; the old team's page records a 2027 mutual option."""
    cy = pd.DataFrame([
        {"_slug": "padres", "MLBAMID": 9, "Season": 2026, "Type": "GUARANTEED", "Salary": 342032},
        {"_slug": "brewers", "MLBAMID": 9, "Season": 2026, "Type": "GUARANTEED", "Salary": 2e6},
        {"_slug": "brewers", "MLBAMID": 9, "Season": 2027, "Type": "MUTUAL OPTION", "Salary": 10e6},
    ])
    summ = pd.DataFrame([{"_slug": s, "MLBAMID": 9, "playerName": "L R", "servicetime": "8.000"} for s in ("padres", "brewers")])
    roster = pd.DataFrame([{"_slug": "padres", "mlbamid": 9, "player": "L R", "position": "UT", "age": "29.5",
                            "loaddate": "2026-09-24T00:00:00"}])
    grid, cells, _ = payroll.build(cy, summ, roster, 2026)
    sd, mil = [list(grid["_slug"]).index(t) for t in ("padres", "brewers")]
    assert cells["2027"][sd] == "pay-mutual" and grid.iloc[sd]["2027"] == 10000000   # recorded, on his current team
    assert cells["2027"][mil] == ""


def test_acquired_month_formats():
    from rr.fgstyle import acquired_month
    assert acquired_month("Trade (STL) Aug'26") == "2026-08"
    assert acquired_month("Free Agent (NYY) Jan'26") == "2026-01"
    assert acquired_month("Drafted 2nd Rd (56) '22") == "2022-07"   # the draft is in July
    assert acquired_month("Non-Drafted FA Jul'20") == "2020-07"
    assert acquired_month("n/a") is None and acquired_month(None) is None


def test_first_free_agent_year_per_player():
    cy = pd.DataFrame([
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2026, "Type": "GUARANTEED", "Salary": 1e6, "_pkey": "a"},
        {"_slug": "cubs", "MLBAMID": 1, "Season": 2027, "Type": "FREE AGENT", "Salary": None, "_pkey": "a"},
        {"_slug": "cubs", "MLBAMID": 2, "Season": 2026, "Type": "GUARANTEED", "Salary": 30e6, "_pkey": "b"},
        {"_slug": "cubs", "MLBAMID": 2, **{"Season": 2027}, "Type": "GUARANTEED", "Salary": 30e6, "_pkey": "b"},
        {"_slug": "cubs", "MLBAMID": 3, "Season": 2026, "Type": "PRE-ARB", "Salary": 780000, "_pkey": "c"},
    ] + [{"_slug": "cubs", "MLBAMID": 2, "Season": y, "Type": "GUARANTEED", "Salary": 30e6, "_pkey": "b"} for y in range(2028, 2033)])
    summ = pd.DataFrame([{"_slug": "cubs", "MLBAMID": 3, "playerName": "C", "servicetime": "1.000"}])
    grid, cells, years = payroll.build(cy, summ, None, 2026)
    fa = payroll.free_agent_year(grid, cells, years)
    assert fa["a"] == 2027          # free agent in 2027 -> not under control through 2027
    assert fa["b"] == 2033          # signed through 2032 -> under control beyond the window
    assert fa["c"] == 2032          # pre-arb, 1 year of service after 2026: 6 years after 2031 -> FA in 2032


def test_stat_titles_groups_formats():
    from rr import stats
    d = lambda c: stats.describe(c, 2026)
    assert d("actual_bat_HR") == {"label": "2026 HR", "group": "2026 STATS: BATTING", "format": "int", "dup": False}
    assert d("actualz_pit_ERA")["label"] == "2025 ERA" and d("actualz_pit_ERA")["format"] == "dec2"
    assert d("proj_bat_wRC+")["label"] == "ROS PROJ wRC+"
    assert d("actual_bat_K%")["label"] == "2026 K% (BAT)" and d("actual_pit_K%")["label"] == "2026 K% (PITCH)"
    assert d("actual_bat_AVG")["format"] == "avg3" and d("actual_bat_Barrel%")["format"] == "pct1"
    assert d("actual_pit_EV")["label"] == "2026 EXIT VELO ALLOWED" and d("actual_bat_pivFA")["label"] == "2026 FASTBALL VELO SEEN"
    assert d("actual_bat_K%1")["dup"] is True and d("actual_bat_K%1")["label"] == "2026 K% (BAT) (ALTERNATE)"
    assert d("prht7")["label"] == "POWER RANK: HITTER, LAST 7 DAYS"
    assert d("player") is None and d("age") is None


def test_pitchers_empty_batting_lines_are_blank():
    from rr import stats
    df = pd.DataFrame({"actual_bat_PA": [712, 0], "actual_bat_AVG": [0.28, 0.0], "actual_bat_wRC+": [158, -100],
                       "actualz_bat_wRC+": [109, -100], "actualz_bat_HR": [31, 0], "actual_pit_ERA": [None, 3.74]})
    out = stats.blank_empty_batting(df)
    assert out.loc[0, "actual_bat_AVG"] == 0.28 and pd.isna(out.loc[1, "actual_bat_AVG"])
    assert pd.isna(out.loc[1, "actualz_bat_HR"]) and out.loc[1, "actual_pit_ERA"] == 3.74


def test_per_nine_stats_are_not_mistaken_for_copies():
    from rr import stats
    assert stats.describe("actual_pit_BB/9", 2026)["label"] == "2026 BB/9"
    assert stats.describe("proj_pit_K/9", 2026)["label"] == "ROS PROJ K/9"
    assert stats.describe("IP", 2026)["group"] == "2026 STATS: OVERALL"
