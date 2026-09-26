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
