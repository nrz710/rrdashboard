"""Build the public website (GitHub Pages) from the published snapshots.

    python -m rr site --out _site            # from ./data (the `data` branch in CI)
    python -m rr site --out _site --demo     # invented sample data, for a preview

Output:
    _site/index.html                  the dashboard (runs entirely in the visitor's browser)
    _site/fg.css                      RosterResource styling, generated from rr/fgstyle.py
    _site/data/index.json             snapshots, refresh status, teams, pages, labels
    _site/data/<snapshot>/meta.json   tables and coverage for one snapshot
    _site/data/<snapshot>/t<n>.json   one table: display-ready rows, team, player key, row shading
    _site/data/<snapshot>/players.json

All the Python-side logic (team names, row shading, which columns to hide, player
matching) is applied here, once, so the browser only filters, sorts and totals.
Nothing here contacts FanGraphs.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config, fgstyle, views
from .gate import Gate, GateClosed
from .sources import FileReader, PublishedSource

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_FG_ID = ("playerid", "fgid", "fangraphsid", "idfg", "fgplayerid")


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")


def _json_values(df: pd.DataFrame) -> list[list]:
    df = df.copy()
    for c in df.columns:  # whole numbers stored as floats (because of blanks) -> 32, not 32.0
        if pd.api.types.is_float_dtype(df[c]):
            vals = df[c].dropna()
            if len(vals) and (vals == vals.round()).all() and vals.abs().max() < 2**53:
                df[c] = df[c].astype("Int64")
    return json.loads(df.to_json(orient="values", date_format="iso", default_handler=str))


def _status(data_dir: Path) -> dict:
    try:
        s = Gate(data_dir / "state", config.MIN_REFRESH_INTERVAL_HOURS).status()
    except GateClosed:
        return {}
    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    return {"last_attempt": iso(s["last_attempt"]), "last_success": iso(s["last_success"]),
            "next_allowed": iso(s["next_allowed"]) if s["last_attempt"] else None, "halted": s["halted"]}


# Clean, upper-case titles for the table menus. Matched against the end of the table's
# internal name; anything not listed gets a title made from its last part.
TABLE_TITLES = {
    "dataRoster": "ROSTER", "dataBullpenUsage.dataPlayers": "BULLPEN USAGE",
    "dataBullpenUsage.dateList": "BULLPEN USAGE DATES", "dataFreeAgents": "FREE AGENTS",
    "dataLineups.dataPlayers": "LINEUPS", "dataLineups.gameList": "LINEUP GAMES",
    "dataProbableStarters.gameList": "PROBABLE STARTERS", "dataProspectsGrid.prospects": "TOP PROSPECTS",
    "dataProspectsGrid.teamRank": "PROSPECT TEAM RANKINGS", "dataRecentTransactions": "RECENT TRANSACTIONS",
    "dataRosterBreakdown.acquired": "ROSTER BREAKDOWN: HOW ACQUIRED",
    "dataRosterBreakdown.country": "ROSTER BREAKDOWN: COUNTRY", "dataStandings": "STANDINGS",
    "dataTeamList": "TEAM LIST", "dataTeamRanking.bat": "TEAM RANKINGS: BATTING",
    "dataTeamRanking.fld": "TEAM RANKINGS: FIELDING", "dataTeamRanking.rp": "TEAM RANKINGS: RELIEF PITCHING",
    "dataTeamRanking.sp": "TEAM RANKINGS: STARTING PITCHING",
    "dataContract": "CONTRACTS", "dataContract.contractSummary": "CONTRACT SUMMARY",
    "dataContract.contractYears": "CONTRACT YEARS", "dataContract.incentives": "CONTRACT INCENTIVES",
    "dataContract.incentives.Incentives": "CONTRACT INCENTIVE DETAILS",
    "dataContract.incentivesAll": "ALL INCENTIVES", "dataContract.incentivesAll.data": "ALL INCENTIVE DETAILS",
    "dataIncentives": "INCENTIVES", "dataIncentives.Incentives": "INCENTIVE DETAILS",
    "dataOtherPayments": "OTHER PAYMENTS", "dataOtherPaymentsLuxuryTax": "OTHER PAYMENTS (LUXURY TAX)",
    "dataOverall": "PAYROLL BY SEASON",
    "transaction-tracker": "TRANSACTIONS", "injury-report": "INJURY REPORT", "useTeamInfoBySeason": "TEAM INFO",
    "closer-depth-charts > dataPlayers": "CLOSER DEPTH CHART", "dataPlayers.pitcherUsage": "RECENT RELIEVER USAGE",
    "dateList": "DATES", "depthChartsData": "DEPTH CHART", "lineupData.lineupTracker.dataPlayers": "LINEUP TRACKER",
    "lineupData.lineupTracker.gameList": "LINEUP TRACKER GAMES", "teamInfo": "TEAM INFO",
    "breakdowns/active-roster": "26-MAN ROSTER BREAKDOWN", "breakdowns/40-man-roster": "40-MAN ROSTER BREAKDOWN",
    "demo-coaches": "COACHES", "free-agent-tracker": "FREE AGENT TRACKER",
}
_GENERIC = {"players", "data", "list", "gamelist", "datelist", "items", "rows"}


def table_title(tid: str) -> str:
    for key, title in sorted(TABLE_TITLES.items(), key=lambda kv: -len(kv[0])):
        if tid == key or tid.endswith("> " + key) or tid.endswith("." + key) or tid.endswith(" " + key):
            return title
    tail = tid.split(" > ")[-1]
    parts = [p for p in re.split(r"[.\[\]*]+", tail) if p]
    words = lambda p: re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", re.sub(r"^data(?=[A-Z])", "", p)).replace("-", " ")
    if len(parts) > 1 and re.sub(r"^data(?=[A-Z])", "", parts[-1]).lower() in _GENERIC:
        return f"{words(parts[-2])}: {words(parts[-1])}".upper()
    return words(parts[-1] if parts else tid).upper()


# Money columns: shown as $1,000,000 (or $1.000 in SHORT format).
_MONEY = re.compile(r"salary|aav|payroll|luxury|cbt|guarante|bonus|buyout|earned|paymentamount|incentivevalue|"
                    r"arbitration|prearb|contracttotal|projection|otherpayments|playerbenefits|bonuspool|40manminor")


def money_columns(df: pd.DataFrame, cols: list[str]) -> list[str]:
    out = []
    for c in cols:
        n = fgstyle.norm(c)
        if not _MONEY.search(n) or n.startswith(("is", "has")):  # skip yes/no flags like is_pre_arb
            continue
        vals = pd.to_numeric(df[c], errors="coerce")
        if vals.notna().sum() and vals.notna().sum() >= 0.8 * df[c].notna().sum():
            out.append(c)
    return out


_NOISE = re.compile(r"(loaddate|upurl|route$|mlbauto|minorbamid|minormasterid|retroid|npbbisid|^dbid$|statsid|"
                    r"^csid$|valueoverride|^oplayerid$|^teamid$|^playerteamid$|hidden|^dbteam$|"
                    r"^type$|^typeid|^loaddate|^contractid$)")


def _is_noise(col: str, cols: set) -> bool:
    n = fgstyle.norm(col)
    if _NOISE.search(n) or n in fgstyle._ID_COLS:
        return True
    base = re.sub(r"\d+$", "", str(col))  # age1, playerid2... duplicates of another column
    return base != str(col) and base in cols


def _add_season_age(df: pd.DataFrame, columns: list[str], season: int) -> tuple[pd.DataFrame, list[str]]:
    """Depth-chart style tables give current age to one decimal plus the pull date; add the
    age on June 30 of the season next to it."""
    by_norm = {fgstyle.norm(c): c for c in columns}
    if "age" not in by_norm or "loaddate" not in by_norm:
        return df, columns
    from .payroll import season_age
    df = df.copy()
    df["SeasonAge"] = [season_age(a, d, season) for a, d in zip(df[by_norm["age"]], df[by_norm["loaddate"]])]
    i = columns.index(by_norm["age"])
    return df, columns[:i] + ["SeasonAge"] + columns[i:]


def _table_payload(df: pd.DataFrame, columns: list[str], per_team: bool, season: int) -> dict:
    df, columns = _add_season_age(df, list(columns), season)
    data_cols = [c for c in columns if c in df.columns]
    if per_team:
        tslug = [s or None for s in df["_slug"]]
    else:
        col, tslug = views.team_slugs(df[data_cols]) if data_cols else (None, [None] * len(df))
        if col is not None and col.lower() in ("team", "teamabbname", "tm"):
            data_cols.remove(col)
    # drop columns with no values at all in this table
    data_cols = [c for c in data_cols if df[c].map(lambda v: v is not None and v == v and v != "").any()]
    body = df[data_cols].copy()
    for c in body.columns:  # shorter numbers: 108.7966031264559 -> 108.7966
        if pd.api.types.is_float_dtype(body[c]):
            body[c] = body[c].round(4)
    by_norm = {fgstyle.norm(c): c for c in body.columns}
    cls = [fgstyle.row_class(row, by_norm, season) for _, row in body.iterrows()]
    name_col = next((by_norm[n] for n in fgstyle._NAME_COLS if n in by_norm), None)
    fg_id_col = next((by_norm[n] for n in _FG_ID if n in by_norm), None)
    url_col = by_norm.get("upurl")
    colset = set(map(str, data_cols))
    hidden = [c for c in data_cols if name_col and fgstyle.norm(c) in fgstyle._ID_COLS]
    useful = [c for c in data_cols if c not in hidden and not _is_noise(c, colset)]
    if name_col in useful:  # player name first
        useful = [name_col] + [c for c in useful if c != name_col]
    if "SeasonAge" in useful:  # season age instead of current age
        useful = [c for c in useful if fgstyle.norm(c) != "age"]
    return {
        "columns": data_cols,
        "labels": {c: ("SEASON AGE" if c == "SeasonAge" else fgstyle.header_label(c)) for c in data_cols},
        "tips": {"SeasonAge": f"Age on June 30, {season}. Worked out from FanGraphs' age (to one decimal), so it "
                              "can be a year off for birthdays within about two weeks of June 30."}
                if "SeasonAge" in data_cols else {},
        "money_cols": money_columns(body, data_cols),
        "hidden": hidden, "default_cols": useful[:10], "name_col": name_col, "fg_id_col": fg_id_col,
        "noise": [c for c in data_cols if c not in hidden and c not in useful],
        "url_col": url_col, "per_team": per_team, "has_team": any(tslug),
        "rows": _json_values(body) if data_cols else [[] for _ in range(len(df))],
        "tslug": tslug, "pkey": [p if isinstance(p, str) else None for p in df["_pkey"]], "cls": cls,
    }


def _derived_payload(df: pd.DataFrame, columns: list[str], money: list[str], cells: dict | None,
                     name_col: str | None, default_cols: list[str]) -> dict:
    body = df[columns]
    return {
        "columns": columns, "labels": {c: (c if c[:2] == "20" else c.upper()) for c in columns},
        "tips": {}, "hidden": [], "default_cols": default_cols, "name_col": name_col, "fg_id_col": None,
        "url_col": None, "urls": [u if isinstance(u, str) else None for u in df.get("_url", [None] * len(df))],
        "per_team": True, "has_team": True, "money_cols": money, "cell_cls": cells or {},
        "rows": _json_values(body), "tslug": [x or None for x in df["_slug"]],
        "pkey": [x if isinstance(x, str) else None for x in df.get("_pkey", [None] * len(df))],
        "cls": [""] * len(df),
    }


def _payroll_tables(src, sid: str, meta: dict, season: int, log) -> list[tuple[dict, dict]]:
    """RosterResource-style PAYROLL grid and PAYROLL SUMMARY, from the payroll pages' data."""
    from . import payroll

    def find(page, suffix):
        for t in meta["tables"]:
            if t["page"] == page and (t["table"].endswith("> " + suffix) or t["table"].endswith("." + suffix)):
                return src.table(sid, page, t["table"])
        return None
    years_df, summary_df = find("payroll", "dataContract.contractYears"), find("payroll", "dataContract.contractSummary")
    roster, overall = find("depth-charts", "dataRoster"), find("payroll", "dataOverall")
    out = []
    if years_df is not None and "Type" in years_df.columns:
        grid, cells, years = payroll.build(years_df, summary_df, roster, season)
        cols = ["Player", "Pos", "Age", "Contract", *years]
        payload = _derived_payload(grid, cols, years, cells, "Player", cols)
        payload["legend"] = payroll.LEGEND
        payload["tips"] = {"Age": f"Season age: age on June 30, {season}."}
        out.append(({"table": "payroll-grid", "title": "PAYROLL", "rows": len(grid)}, payload))
        log(f"Payroll grid: {len(grid)} players, {years[0]}-{years[-1]}.")
    if overall is not None and "Season" in overall.columns:
        summ, years = payroll.summary(overall, season)
        out.append(({"table": "payroll-summary", "title": "PAYROLL SUMMARY", "rows": len(summ)},
                    _derived_payload(summ, ["Item", *years], years, None, None, ["Item", *years])))
    return out


def build(data_dir: Path, out_dir: Path, *, log=print) -> dict:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "data").mkdir(parents=True)
    build_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / "index.html").write_text(
        (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__BUILD__", build_id), encoding="utf-8")
    (out_dir / "fg.css").write_text(fgstyle.css(), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is

    from . import parse, published
    if data_dir.exists():
        published.ensure_current(data_dir, log=log)
    src = PublishedSource(FileReader(data_dir))
    snaps = []
    for snap in src.snapshots():
        if published.published_version(data_dir, snap["id"]) == parse.PARSER_VERSION:
            snaps.append(snap)
        else:
            log(f"Skipping {snap['id']}: parsed by an older version and its raw pages are no longer stored.")
    season = config.current_season()
    for snap in snaps:
        sid = snap["id"]
        meta = src.meta(sid)
        avail = src.available(sid)
        sdir = out_dir / "data" / sid
        sdir.mkdir()
        tables = []
        for n, t in enumerate(meta["tables"]):
            df = src.table(sid, t["page"], t["table"])
            if df is None:
                continue
            per_team = any(avail.get(t["page"], []))
            payload = _table_payload(df, t["columns"], per_team, season)
            _dump(sdir / f"t{n}.json", payload)
            tables.append({"file": f"t{n}.json", "page": t["page"], "table": t["table"],
                           "title": table_title(t["table"]), "teams": t["teams"],
                           "rows": t["rows"], "same": t["same"], "player": t["player"], "stats": t["stats"],
                           "per_team": per_team, "has_team": payload["has_team"],
                           "name_col": payload["name_col"], "money_cols": payload["money_cols"],
                           "columns": [c for c in payload["columns"]]})
        for i, (info, payload) in enumerate(_payroll_tables(src, sid, meta, season, log)):
            _dump(sdir / f"p{i}.json", payload)
            tables.append({"file": f"p{i}.json", "page": "payroll", "teams": len(set(payload["tslug"])),
                           "same": False, "player": False, "stats": {}, "per_team": True, "has_team": True,
                           "name_col": payload["name_col"], "money_cols": payload["money_cols"],
                           "columns": payload["columns"], **info})
        seen: dict = {}
        for t in tables:  # titles must be unique within a page
            key = (t["page"], t["title"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                t["title"] = f"{t['title']} ({seen[key]})"
        idx = src.index(sid)
        p = idx.players
        _dump(sdir / "players.json", {"key": list(p["key"]), "name": [str(x) for x in p["Player"]],
                                      "team": [x or "" for x in p["Team"]], "sources": [int(x) for x in p["Sources"]],
                                      "ambiguous": [bool(x) for x in p["Ambiguous"]]})
        _dump(sdir / "meta.json", {"id": sid, "manifest": meta["manifest"], "warnings": meta.get("warnings", []),
                                   "available": {k: [t or "" for t in v] for k, v in avail.items()},
                                   "tables": tables})
        log(f"Site: {sid} with {len(tables)} tables.")

    teams = [{"slug": s, "name": config.SLUG_TO_TEAM[s], "abbr": config.TEAM_ABBR[s],
              "league": "AL" if s in config.AL else "NL"} for s in config.TEAMS.values()]
    _dump(out_dir / "data" / "index.json", {
        "generated": build_id,
        "snapshots": [{"id": s["id"], "manifest": s["manifest"]} for s in snaps],
        "status": _status(data_dir), "season": season,
        "teams": teams,
        "pages": [{"key": k, "label": v, "team_tool": k in config.TEAM_TOOLS} for k, v in config.PAGE_LABELS.items()],
        "header_labels": fgstyle.HEADER_LABELS,
        "preferred": config.PREFERRED_TABLES,
        "attribution": "Source: FanGraphs RosterResource (fangraphs.com)",
    })
    log(f"Site written to {out_dir} ({len(snaps)} snapshot(s)).")
    return {"snapshots": len(snaps)}


def build_demo(out_dir: Path, log=print) -> dict:
    import sys
    sys.path.insert(0, str(WEB_DIR.parent))
    import make_demo_data
    root = Path(tempfile.mkdtemp(prefix="rr_site_demo_"))
    make_demo_data.main(root)
    return build(root, out_dir, log=log)
