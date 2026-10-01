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
                    r"^type$|^typeid|^loaddate|^contractid$|^altmmid$|^refid$|^kboid$|^kbobisid$)")


def _is_noise(col: str, cols: set) -> bool:
    n = fgstyle.norm(col)
    if _NOISE.search(n) or n in fgstyle._ID_COLS:
        return True
    base = re.sub(r"\d+$", "", str(col))  # age1, playerid2... duplicates of another column
    return base != str(col) and base in cols


PAYROLL_LABELS = {"type": "CONTRACT STATUS", "typeatsigning": "STATUS AT SIGNING", "status": "OPTION DECISION",
                  "contracttype": "CONTRACT TYPE", "payrolltype": "PAYROLL TYPE"}


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


def _table_payload(df: pd.DataFrame, columns: list[str], per_team: bool, season: int,
                   page_is_payroll: bool = False) -> dict:
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
    from . import stats
    body = stats.blank_empty_batting(df[data_cols])  # pitchers' zero batting lines -> blank
    for c in body.columns:  # shorter numbers: 108.7966031264559 -> 108.7966
        if pd.api.types.is_float_dtype(body[c]):
            body[c] = body[c].round(4)
    by_norm = {fgstyle.norm(c): c for c in body.columns}
    # injured-list shading stays fixed; "acquired since" is a toggle in the page, so it isn't baked in
    cls = [c if c != "fg-acq" else "" for c in (fgstyle.row_class(row, by_norm, season) for _, row in body.iterrows())]
    acq_col = by_norm.get("howacquired") or by_norm.get("acquired")
    name_col = next((by_norm[n] for n in fgstyle._NAME_COLS if n in by_norm), None)
    fg_id_col = next((by_norm[n] for n in _FG_ID if n in by_norm), None)
    url_col = by_norm.get("upurl")
    colset = set(map(str, data_cols))
    stat_labels, stat_groups, stat_formats, stat_dups = stats.catalog(data_cols, season)
    hidden = [c for c in data_cols if name_col and fgstyle.norm(c) in fgstyle._ID_COLS]
    payroll_words = ("type", "typeatsigning")  # on payroll pages "Type" is the contract status, not a code
    useful = [c for c in data_cols if c not in hidden and c not in stat_dups
              and (not _is_noise(c, colset) or (page_is_payroll and fgstyle.norm(c) in payroll_words))]
    if name_col in useful:  # player name first
        useful = [name_col] + [c for c in useful if c != name_col]
    if "SeasonAge" in useful:  # season age instead of current age
        useful = [c for c in useful if fgstyle.norm(c) != "age"]
    return {
        "columns": data_cols,
        "labels": {c: ("SEASON AGE" if c == "SeasonAge" else
                       (PAYROLL_LABELS.get(fgstyle.norm(c)) if page_is_payroll else None)
                       or stat_labels.get(c) or fgstyle.header_label(c)) for c in data_cols},
        "groups": stat_groups, "group_order": stats.group_order(season), "formats": stat_formats,
        "tips": {"SeasonAge": f"Age on June 30, {season}. Worked out from the age in the data (to one decimal), so it "
                              "can be a year off for birthdays within about two weeks of June 30."}
                if "SeasonAge" in data_cols else {},
        "money_cols": money_columns(body, data_cols),
        "hidden": hidden, "default_cols": useful[:10], "name_col": name_col, "fg_id_col": fg_id_col,
        "noise": [c for c in data_cols if c not in hidden and c not in useful],
        # contract status words (GUARANTEED, ARB 2, CLUB OPTION...) are centered
        "center_cols": [c for c in data_cols if fgstyle.norm(c) in ("type", "typeatsigning", "status", "contracttype",
                                                                      "payrolltype") and page_is_payroll],
        "url_col": url_col, "per_team": per_team, "has_team": any(tslug),
        "rows": _json_values(body) if data_cols else [[] for _ in range(len(df))],
        "tslug": tslug, "pkey": [p if isinstance(p, str) else None for p in df["_pkey"]], "cls": cls,
        "acq": [fgstyle.acquired_month(v) for v in body[acq_col]] if acq_col else None,
    }


def _derived_payload(df: pd.DataFrame, columns: list[str], money: list[str], cells: dict | None,
                     name_col: str | None, default_cols: list[str]) -> dict:
    body = df[columns]
    return {
        "columns": columns, "labels": {c: (c if c[:2] == "20" else c.upper()) for c in columns},
        "tips": {}, "hidden": [], "default_cols": default_cols, "name_col": name_col, "fg_id_col": None,
        "url_col": None, "urls": [u if isinstance(u, str) else None for u in df.get("_url", [None] * len(df))],
        "per_team": True, "has_team": True, "money_cols": money, "cell_cls": cells or {},
        "rows": _json_values(body), "tslug": [x if isinstance(x, str) and x else None for x in df["_slug"]],
        "pkey": [x if isinstance(x, str) else None for x in df.get("_pkey", [None] * len(df))],
        "cls": [""] * len(df),
    }


FA_YEARS: dict = {}  # player key -> first free-agent season, filled while building the payroll tables
YEARLY_PAGE = "yearly-payroll"   # a selection of its own in the dashboard (built here, never fetched)
YEARLY_LABEL = "Yearly Payroll & Status"


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
        FA_YEARS.clear()
        FA_YEARS.update(payroll.free_agent_year(grid, cells, years))
        log(f"Payroll grid: {len(grid)} players, {years[0]}-{years[-1]}.")
        # Its own selection: YEARLY PAYROLL & STATUS (one table, a salary and a status column per season)
        yt, ycells, ycols = payroll.yearly(grid, cells, years)
        ypay = _derived_payload(yt, ycols, years, ycells, "Player", years)
        ypay["labels"] = {c: c.upper() for c in ycols}
        ypay["legend"] = payroll.LEGEND
        ypay["always_cols"] = ["Player"]
        ypay["center_cols"] = [c for c in ycols if c.endswith(" Status")]
        ypay["tips"] = {**{y: f"{y} salary, colored by contract status. Italic = estimate or projected "
                              "(arbitration projections; seasons with no contract on file projected from service time: "
                              "6+ years = free agent, 3+ = arbitration, else pre-arbitration)" for y in years},
                        **{f"{y} Status": f"{y} contract status" for y in years}}
        out.append(({"table": "yearly-payroll", "title": "YEARLY PAYROLL & STATUS", "rows": len(yt),
                     "page": YEARLY_PAGE, "player": True}, ypay))
    if overall is not None and "Season" in overall.columns:
        summ, years = payroll.summary(overall, season)
        out.append(({"table": "payroll-summary", "title": "PAYROLL SUMMARY", "rows": len(summ)},
                    _derived_payload(summ, ["Item", *years], years, None, None, ["Item", *years])))
    return out


STATS_PAGE = "player-stats"   # a selection of its own (built here from the leaderboards, never fetched)
STATS_LABEL = "Player Stats"


def _player_stats_table(src, sid: str, meta: dict, index_players: pd.DataFrame | None, log) -> tuple[dict, dict, list] | None:
    from . import playerstats
    frames, sizes = {}, {}
    for key in config.STATS_PAGE_KEYS:
        best = None
        for t in meta["tables"]:
            if t["page"] != key:
                continue
            df = src.table(sid, key, t["table"])
            if df is not None and playerstats.col(df, "playerid") and (best is None or len(df) > len(best)):
                best = df
        frames[key], sizes[key] = best, (0 if best is None else len(best))
    if not any(v is not None for v in frames.values()):
        return None
    season = config.stats_season()
    try:
        season = int(meta["manifest"].get("stats_season") or season)
    except (TypeError, ValueError):
        pass
    df, cols, labels, formats, groups, tips, problems = playerstats.build(frames, season)
    # team for filtering: the leaderboard's team, or (for players with two teams this year) his current one
    current = {}
    if index_players is not None and len(index_players):
        current = {k: config.NAME_TO_ABBR.get(t) for k, t in zip(index_players["key"], index_players["Team"])}
    df["_slug"] = [config.team_slug_of(str(t)) if config.team_slug_of(str(t)) else config.team_slug_of(str(current.get(k) or ""))
                   for t, k in zip(df["_team"], df["_pkey"])]
    defaults = [c for c in cols[1:] if not c.split(" ", 1)[0].count("-") and " INN " not in c]  # platform year, no innings
    payload = _derived_payload(df, cols, [], None, "Player", defaults)
    payload.update({"labels": labels, "formats": formats, "groups": groups, "tips": tips, "always_cols": ["Player"],
                    "group_order": [g for g in dict.fromkeys(groups[c] for c in cols[1:])]})
    warnings = [f"Player stats: {p}" for p in problems]
    if 0 < sizes.get("stats/bat-platform", 0) < 300:
        warnings.append(f"Player stats: the {season} batting leaderboard listed only {sizes['stats/bat-platform']} players; "
                        "FanGraphs may not have included everyone.")
    missing = [k for k, v in sizes.items() if not v]
    if missing:
        warnings.append("Player stats: no data from " + ", ".join(missing))
    log(f"Player stats: {len(df)} players, {season} and {season - 2}-{season}; "
        + ("all stats found." if not problems else "missing: " + "; ".join(problems)))
    info = {"table": "player-stats", "title": "PLAYER STATS", "rows": len(df), "page": STATS_PAGE, "player": True}
    return info, payload, warnings


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
        FA_YEARS.clear()
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
            payload = _table_payload(df, t["columns"], per_team, season, page_is_payroll=t["page"] == "payroll")
            _dump(sdir / f"t{n}.json", payload)
            tables.append({"file": f"t{n}.json", "page": t["page"], "table": t["table"],
                           "title": table_title(t["table"]), "teams": t["teams"],
                           "rows": t["rows"], "same": t["same"], "stats": t["stats"],
                           "player": t["player"] and t["page"] not in config.STATS_PAGE_KEYS,
                           "per_team": per_team, "has_team": payload["has_team"],
                           "name_col": payload["name_col"], "money_cols": payload["money_cols"],
                           "columns": [c for c in payload["columns"]]})
        for i, (info, payload) in enumerate(_payroll_tables(src, sid, meta, season, log)):
            _dump(sdir / f"p{i}.json", payload)
            tables.append({"file": f"p{i}.json", "page": "payroll", "teams": len(set(payload["tslug"])),
                           "same": False, "player": False, "stats": {}, "per_team": True, "has_team": True,
                           "name_col": payload["name_col"], "money_cols": payload["money_cols"],
                           "columns": payload["columns"], **info})
        if any(t["page"] == YEARLY_PAGE for t in tables):
            avail = {**avail, YEARLY_PAGE: avail.get("payroll", [])}
        stat_warnings = []
        built = _player_stats_table(src, sid, meta, src.index(sid).players, log)
        if built:
            info, payload, stat_warnings = built
            _dump(sdir / "s0.json", payload)
            tables.append({"file": "s0.json", "teams": len({x for x in payload["tslug"] if x}), "same": False, "stats": {},
                           "per_team": True, "has_team": True, "name_col": "Player", "money_cols": [],
                           "columns": payload["columns"], **info})
            avail = {**avail, STATS_PAGE: sorted({x for x in payload["tslug"] if x})}
        seen: dict = {}
        for t in tables:  # titles must be unique within a page
            key = (t["page"], t["title"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                t["title"] = f"{t['title']} ({seen[key]})"
        # per-player facts for the highlight toggles: when acquired, first free-agent season
        pinfo: dict = {}
        for t in meta["tables"]:
            if not t["player"] or t["page"] not in ("depth-charts", "lineup-tracker"):
                continue
            df = src.table(sid, t["page"], t["table"])
            col = next((c for c in df.columns if fgstyle.norm(c) in ("howacquired", "acquired")), None)
            if col is None:
                continue
            for k, v in zip(df["_pkey"], df[col]):
                when = fgstyle.acquired_month(v)
                if isinstance(k, str) and when and "acq" not in pinfo.get(k, {}):
                    pinfo.setdefault(k, {})["acq"] = when
        for k, fa in FA_YEARS.items():
            pinfo.setdefault(k, {})["fa"] = int(fa)
        idx = src.index(sid)
        p = idx.players
        _dump(sdir / "players.json", {"key": list(p["key"]), "name": [str(x) for x in p["Player"]],
                                      "team": [x or "" for x in p["Team"]], "sources": [int(x) for x in p["Sources"]],
                                      "ambiguous": [bool(x) for x in p["Ambiguous"]]})
        _dump(sdir / "meta.json", {"id": sid, "manifest": meta["manifest"], "warnings": meta.get("warnings", []) + stat_warnings,
                                   "available": {k: [t or "" for t in v] for k, v in avail.items()},
                                   "pinfo": pinfo,
                                   "tables": tables})
        log(f"Site: {sid} with {len(tables)} tables.")

    teams = [{"slug": s, "name": config.SLUG_TO_TEAM[s], "abbr": config.TEAM_ABBR[s],
              "league": "AL" if s in config.AL else "NL"} for s in config.TEAMS.values()]
    _dump(out_dir / "data" / "index.json", {
        "generated": build_id,
        "snapshots": [{"id": s["id"], "manifest": s["manifest"]} for s in snaps],
        "status": _status(data_dir), "season": season,
        "teams": teams,
        "pages": [{"key": k, "label": v, "team_tool": k in config.TEAM_TOOLS} for k, v in config.PAGE_LABELS.items()]
                 + [{"key": YEARLY_PAGE, "label": YEARLY_LABEL, "team_tool": True, "built": True},
                    {"key": STATS_PAGE, "label": STATS_LABEL, "team_tool": True, "built": True}],
        "header_labels": fgstyle.HEADER_LABELS,
        "preferred": config.PREFERRED_TABLES,
        "attribution": "Powered by FanGraphs",
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
