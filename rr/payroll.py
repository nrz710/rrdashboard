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
LEGEND = {cls: label for _, cls, label in STATUS} | {"pay-est": "Estimate / projected"}

SERVICE_DAYS = 172  # days in an MLB service year


def service_years(v) -> float | None:
    """'9.070' (9 years, 70 days) -> 9.407."""
    if v is None:
        return None
    txt = str(v).strip()
    if not re.fullmatch(r"\d+(\.\d{1,3})?", txt):
        return None
    years, _, days = txt.partition(".")
    return int(years) + (int(days.ljust(3, "0")) if days else 0) / SERVICE_DAYS


def projected_status(service_now: float | None, season: int, year: int) -> str | None:
    """Status for a season the contract data leaves blank, from MLB's service-time rules:
    6+ years of service -> free agent, 3+ -> arbitration, otherwise pre-arbitration.
    service_now is the service time as of this (late) season, i.e. after the current season."""
    if service_now is None:
        return None
    before = service_now + (year - 1 - season)  # service when that season's decisions are made
    if before >= 6:
        return "pay-fa"
    if before >= 3:
        return "pay-arb"
    return "pay-prearb"


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
            "_pkey": (s.get("_pkey") if s is not None and isinstance(s.get("_pkey"), str)
                      else next((k for k in g.get("_pkey", pd.Series(dtype=object)) if isinstance(k, str)), None)),
            "_url": s.get("UPURL") if s is not None else None,
            "_mlbam": mlbam, "_on_roster": r is not None, "_last": int(g["Season"].max()),
        }
        classes = {}
        seen_fa = False
        service = service_years(s.get("servicetime")) if s is not None else None
        if service is None and r is not None:
            service = service_years(r.get("servicetime"))
        last_contract = max((int(x) for x, t in zip(g["Season"], g["Type"]) if status_class(t) != "pay-fa"), default=season)
        for y in years:
            yr = g[g["Season"] == int(y)]
            if yr.empty:
                # nothing on file for this season: project it from service time (marked as projected)
                proj = projected_status(service, season, int(y)) if int(y) > last_contract and not seen_fa else None
                if proj == "pay-fa":
                    row[y], classes[y] = "FA", "pay-fa pay-est"
                    seen_fa = True
                elif proj in ("pay-arb", "pay-prearb"):
                    row[y], classes[y] = ("ARB" if proj == "pay-arb" else "PRE-ARB"), proj + " pay-est"
                else:
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
            if amount is None and cls and cls != "pay-guaranteed":
                row[y] = re.sub(r"\s*\(.*$", "", str(top["Type"]).upper())  # e.g. "ARB 2", "MUTUAL OPTION": no amount
            else:
                row[y] = round(amount) if amount is not None else None
            classes[y] = (cls + (" pay-est" if est and amount is not None else "")).strip()
        rows.append((row, classes))

    # A player who changed teams this season is on both payrolls (each lists its share of the money).
    # Future seasons go on the row of the team he's with now, using a recorded status from either
    # team's page before falling back to a projection.
    by_player: dict = {}
    for i, (row, _) in enumerate(rows):
        by_player.setdefault(row["_mlbam"], []).append(i)
    for idxs in by_player.values():
        if len(idxs) < 2:
            continue
        def rank(i):
            row = rows[i][0]
            now = row[years[0]] if isinstance(row[years[0]], (int, float)) else 0
            return (row["_on_roster"], row["_last"], now)
        keep = max(idxs, key=rank)  # the team he's with now
        for y in years[1:]:
            # a recorded status wins over a projection, whichever team's page it's on
            recorded = [i for i in idxs if rows[i][1][y] and "pay-est" not in rows[i][1][y].split()]
            src = keep if keep in recorded else (recorded[0] if recorded else keep)  # his current team's record first
            value, cls = rows[src][0][y], rows[src][1][y]
            for i in idxs:
                rows[i][0][y], rows[i][1][y] = None, ""
            rows[keep][0][y], rows[keep][1][y] = value, cls
    for row, _ in rows:
        for k in ("_mlbam", "_on_roster", "_last"):
            row.pop(k, None)

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


def status_label(cls: str) -> str:
    """'pay-arb pay-est' -> 'Arbitration (est.)'."""
    parts = (cls or "").split()
    if not parts:
        return ""
    base = LEGEND.get(parts[0], "")
    return f"{base} (projected)" if "pay-est" in parts[1:] and base else base


def yearly(grid: pd.DataFrame, cells: dict, years: list[str]) -> tuple[pd.DataFrame, dict, list[str]]:
    """YEARLY PAYROLL & STATUS: Player, then for each season its salary and its status in words.
    -> (table, {salary column: [css class per row]}, column order)."""
    out = grid[["Player", "_slug", "_pkey", "_url"]].copy()
    cols = ["Player"]
    for y in years:
        out[y] = grid[y]
        out[f"{y} Status"] = [status_label(c) for c in cells[y]]
        cols += [y, f"{y} Status"]
    return out, {y: cells[y] for y in years}, cols
