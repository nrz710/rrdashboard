"""RosterResource-style payroll grid, built from the payroll pages' own data.

For each team: one row per player on the payroll this season, the contract summary, and
salary for the current season plus the following six, each cell colored by contract
status (guaranteed, arbitration, pre-arb, club / player / mutual option, vesting, free
agent...). Arbitration years show FanGraphs' projected salary, marked as estimates.

Also a per-team summary (estimated payroll, luxury-tax payroll, guaranteed, arbitration,
pre-arb, other payments) for the same seven seasons.

The colors are approximations of RosterResource's legend; they're CSS classes (pay-*)
defined in rr/fgstyle.py, so they can be tuned in one place.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

import pandas as pd

YEARS_AHEAD = 6  # current season + 6

# (pattern on the contract-year Type, css class, legend label), first match wins
STATUS = [
    (r"^FREE AGENT", "pay-fa", "Free agent"),
    (r"^NOT 40", "pay-minor", "Not on 40-man"),
    (r"^ARB", "pay-arb", "Arbitration"),
    (r"^PRE-ARB", "pay-prearb", "Pre-arbitration"),
    (r"^CLUB OPTION", "pay-club", "Club option"),
    (r"^(PLAYER OPTION|OPT OUT|POST OPT OUT)", "pay-player", "Player option / opt-out"),
    (r"^MUTUAL", "pay-mutual", "Mutual option"),
    (r"^VESTING", "pay-vesting", "Vesting option"),
    (r"^GUARANTEED", "pay-guaranteed", "Guaranteed"),
]
LEGEND = {cls: label for _, cls, label in STATUS} | {"pay-est": "Estimate"}


def status_class(type_: str | None) -> str:
    t = (type_ or "").strip().upper()
    for pat, cls, _ in STATUS:
        if re.search(pat, t):
            return cls
    return ""


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def season_age(age, loaddate, season: int) -> int | None:
    """Age on June 30 of the season (FanGraphs' convention), from a current age given to
    one decimal and the date that age was computed."""
    a = _num(age)
    if a is None:
        return None
    try:
        when = datetime.fromisoformat(str(loaddate)[:19])
    except ValueError:
        return None
    birth_years = when.year + (when.timetuple().tm_yday - 1) / 365.25 - a
    june30 = season + (datetime(season, 6, 30).timetuple().tm_yday - 1) / 365.25
    return int(math.floor(june30 - birth_years + 1e-9))


def _by_player(df: pd.DataFrame, id_col: str) -> dict:
    out = {}
    for _, r in df.iterrows():
        k = (r["_slug"], _num(r.get(id_col)))
        if k[1] is not None and k not in out:
            out[k] = r
    return out


def _current_contracts(summary: pd.DataFrame, season: int) -> dict:
    """Per (team, player): the contract in force this season (not a future extension)."""
    out: dict = {}
    for _, r in summary.iterrows():
        k = (r["_slug"], _num(r.get("MLBAMID")))
        if k[1] is None:
            continue
        start, end = _num(r.get("startSeason")), _num(r.get("endSeasonAll")) or _num(r.get("endSeason"))
        current = start is not None and end is not None and start <= season <= end
        if k not in out or (current and not out[k][1]):
            out[k] = (r, current)
    return {k: v[0] for k, v in out.items()}


def build(contract_years: pd.DataFrame, summary: pd.DataFrame | None, roster: pd.DataFrame | None,
          season: int) -> tuple[pd.DataFrame, dict, list]:
    """-> (grid rows with _slug/_pkey/_url, {season column: [css class per row]}, season columns)."""
    years = [str(season + i) for i in range(YEARS_AHEAD + 1)]
    cy = contract_years.copy()
    cy["_mlbam"] = cy["MLBAMID"].map(_num)
    cy = cy[cy["_mlbam"].notna()]
    info = _current_contracts(summary, season) if summary is not None and len(summary) else {}
    ros = _by_player(roster, "mlbamid") if roster is not None and len(roster) else {}

    rows, cells = [], {y: [] for y in years}
    for (slug, mlbam), g in cy.groupby(["_slug", "_mlbam"], sort=False):
        this = g[g["Season"] == season]
        if this.empty or all(status_class(t) == "pay-fa" for t in this["Type"]):
            continue  # not on this season's payroll
        s = info.get((slug, mlbam))
        r = ros.get((slug, mlbam))
        row = {
            "Player": (s.get("playerName") if s is not None else None) or (r.get("player") if r is not None else None),
            "Pos": r.get("position") if r is not None else None,
            "Age": season_age(r.get("age"), r.get("loaddate"), season) if r is not None else None,
            "Contract": s.get("description") if s is not None else None,
            "_slug": slug,
            "_pkey": s.get("_pkey") if s is not None and isinstance(s.get("_pkey"), str) else None,
            "_url": s.get("UPURL") if s is not None else None,
        }
        classes = {}
        seen_fa = False
        for y in years:
            yr = g[g["Season"] == int(y)]
            if yr.empty:
                row[y], classes[y] = None, ""
                continue
            # prefer a year that has money over a free-agent placeholder
            yr = yr.assign(_has=yr["Salary"].map(_num).notna()).sort_values("_has", ascending=False)
            top = yr.iloc[0]
            cls = status_class(top["Type"])
            amount = _num(top.get("Salary"))
            est = _num(top.get("isEstimate")) == 1
            if amount is None and cls == "pay-arb":
                amount, est = _num(top.get("ArbSalaryProjection")), True
            if cls == "pay-fa":
                row[y], classes[y] = ("FA", cls) if not seen_fa else (None, "")
                seen_fa = True
                continue
            if amount is None and cls in ("pay-arb", "pay-prearb"):
                row[y] = re.sub(r"\s*\(.*$", "", str(top["Type"]).upper())  # e.g. "ARB 2", no amount yet
            else:
                row[y] = round(amount) if amount is not None else None
            classes[y] = (cls + (" pay-est" if est and amount is not None else "")).strip()
        rows.append((row, classes))

    rows.sort(key=lambda rc: (rc[0]["_slug"], -(rc[0][years[0]] if isinstance(rc[0][years[0]], (int, float)) else -1)))
    grid = pd.DataFrame([r for r, _ in rows], columns=["Player", "Pos", "Age", "Contract", *years, "_slug", "_pkey", "_url"])
    for _, c in rows:
        for y in years:
            cells[y].append(c[y])
    return grid, cells, years


SUMMARY_ROWS = [
    ("Payroll (est.)", "estPayroll"),
    ("Luxury tax payroll (est.)", "estLuxuryTaxPayroll"),
    ("Guaranteed", "TotalGuaranteed"),
    ("Arbitration", "TotalArbitration"),
    ("Pre-arbitration", "TotalPreArb"),
    ("Other payments", "TotalOtherPaymentsAll"),
    ("Buyouts", "TotalBuyouts"),
]


def summary(overall: pd.DataFrame, season: int) -> tuple[pd.DataFrame, list]:
    years = [str(season + i) for i in range(YEARS_AHEAD + 1)]
    out = []
    for slug, g in overall.groupby("_slug", sort=False):
        by_season = {int(_num(s)): r for s, r in zip(g["Season"], g.to_dict("records")) if _num(s) is not None}
        for label, col in SUMMARY_ROWS:
            if col not in g.columns:
                continue
            row = {"Item": label, "_slug": slug}
            for y in years:
                v = _num(by_season.get(int(y), {}).get(col))
                row[y] = round(v) if v is not None else None
            if any(row[y] is not None for y in years):
                out.append(row)
    return pd.DataFrame(out, columns=["Item", *years, "_slug"]), years
