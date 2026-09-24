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
    return json.loads(df.to_json(orient="values", date_format="iso", default_handler=str))


def _status(data_dir: Path) -> dict:
    try:
        s = Gate(data_dir / "state", config.MIN_REFRESH_INTERVAL_HOURS).status()
    except GateClosed:
        return {}
    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    return {"last_attempt": iso(s["last_attempt"]), "last_success": iso(s["last_success"]),
            "next_allowed": iso(s["next_allowed"]) if s["last_attempt"] else None, "halted": s["halted"]}


def _table_payload(df: pd.DataFrame, columns: list[str], per_team: bool, season: int) -> dict:
    data_cols = [c for c in columns if c in df.columns]
    if per_team:
        tslug = [s or None for s in df["_slug"]]
    else:
        col = views.team_column(df[data_cols]) if data_cols else None
        if col is not None:
            tslug = [config.team_slug_of(v) for v in df[col]]
            data_cols.remove(col)
        else:
            tslug = [None] * len(df)
    body = df[data_cols]
    by_norm = {fgstyle.norm(c): c for c in body.columns}
    cls = [fgstyle.row_class(row, by_norm, season) for _, row in body.iterrows()]
    name_col = next((by_norm[n] for n in fgstyle._NAME_COLS if n in by_norm), None)
    fg_id_col = next((by_norm[n] for n in _FG_ID if n in by_norm), None)
    hidden = [c for c in data_cols if name_col and fgstyle.norm(c) in fgstyle._ID_COLS]
    return {
        "columns": data_cols, "labels": {c: fgstyle.header_label(c) for c in data_cols},
        "hidden": hidden, "name_col": name_col, "fg_id_col": fg_id_col,
        "per_team": per_team, "has_team": any(tslug),
        "rows": _json_values(body) if data_cols else [[] for _ in range(len(df))],
        "tslug": tslug, "pkey": [p if isinstance(p, str) else None for p in df["_pkey"]], "cls": cls,
    }


def build(data_dir: Path, out_dir: Path, *, log=print) -> dict:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "data").mkdir(parents=True)
    shutil.copy(WEB_DIR / "index.html", out_dir / "index.html")
    (out_dir / "fg.css").write_text(fgstyle.css(), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is

    src = PublishedSource(FileReader(data_dir))
    snaps = src.snapshots()
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
            tables.append({"file": f"t{n}.json", "page": t["page"], "table": t["table"], "teams": t["teams"],
                           "rows": t["rows"], "same": t["same"], "player": t["player"], "stats": t["stats"],
                           "per_team": per_team, "has_team": payload["has_team"],
                           "name_col": payload["name_col"],
                           "columns": [c for c in payload["columns"]]})
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
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshots": [{"id": s["id"], "manifest": s["manifest"]} for s in snaps],
        "status": _status(data_dir), "season": season,
        "teams": teams,
        "pages": [{"key": k, "label": v, "team_tool": k in config.TEAM_TOOLS} for k, v in config.PAGE_LABELS.items()],
        "header_labels": fgstyle.HEADER_LABELS,
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
