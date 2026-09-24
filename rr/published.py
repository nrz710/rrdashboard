"""Pre-parse a snapshot into small files the dashboard can load quickly. Free, no database.

    data/published/index.json                    list of published snapshots, newest first
    data/published/<snapshot>/meta.json          tables, columns, coverage, player-match stats
    data/published/<snapshot>/t/<n>.json.gz      one table, all teams stacked, player keys attached
    data/published/<snapshot>/players.json.gz    every matched player

The heavy work (parsing each page, matching players across tools, working out coverage)
happens once here, right after a pull, instead of every time the dashboard opens. The
dashboard then downloads only the few small files a view needs. On GitHub these files
live on the repo's `data` branch next to the raw snapshots and the pull log.

A snapshot is written to a temporary folder and moved into place in one step, and
index.json is updated last, so the dashboard never sees a half-written snapshot.
Nothing here contacts FanGraphs.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable

import pandas as pd

from . import config, parse, players, store, views

INTERNAL = ["_slug", "_ord", "_pkey", "_match", "_team"]


def published_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "published"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")


def _write_frame(path: Path, df: pd.DataFrame) -> None:
    text = df.to_json(orient="split", index=False, date_format="iso", default_handler=str)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


def read_frame(data: bytes) -> pd.DataFrame:
    obj = json.loads(gzip.decompress(data).decode("utf-8"))
    return pd.DataFrame(obj["data"], columns=obj["columns"])


def league_wide_warnings(tables: dict) -> list[str]:
    """Sanity checks for tools fetched once league-wide instead of 30 times (no extra requests).
    Flags pages that look like they didn't include every team, e.g. if FanGraphs only
    embeds the first page of a long list."""
    warnings = []
    for page in sorted(config.LEAGUE_WIDE_INSTEAD_OF_PER_TEAM):
        mine = {tid: df for (p, team, tid), df in tables.items() if p == page and team is None}
        if not mine:
            continue
        best_teams, biggest = 0, 0
        for df in mine.values():
            biggest = max(biggest, len(df))
            col = views.team_column(df)
            if col is not None:
                best_teams = max(best_teams, df[col].map(config.team_slug_of).nunique())
        label = config.PAGE_LABELS.get(page, page)
        if best_teams == 0:
            warnings.append(f"{label}: no team column recognized in the league-wide page, so it can't be "
                            f"split by team. Check it with `python -m rr inspect --page {page}`.")
        elif best_teams < 25:
            warnings.append(f"{label}: the league-wide page only mentions {best_teams} teams. If that's "
                            "unexpected, remove it from LEAGUE_WIDE_INSTEAD_OF_PER_TEAM in rr/config.py.")
        if biggest in (30, 50, 100, 200):
            warnings.append(f"{label}: the largest table has exactly {biggest} rows, which looks like a page-size "
                            "limit. Compare against the site; if rows are missing, go back to per-team pulls "
                            "for this tool (rr/config.py).")
    return warnings


def publish(data_dir: Path, snap_dir: Path, *, keep: int = config.PUBLISHED_RETENTION,
            log: Callable[[str], None] = print) -> dict:
    data_dir, snap_dir = Path(data_dir), Path(snap_dir)
    sid = snap_dir.name
    manifest = store.read_manifest(snap_dir)
    avail = store.available(snap_dir)

    # 1. Parse every saved page.
    tables: dict[tuple[str, str | None, str], pd.DataFrame] = {}
    for page, teams in avail.items():
        for team in teams:
            rec = store.load_raw(snap_dir, page, team)
            if rec is None:
                continue
            for tid, df in parse.discover_tables(rec["payload"], team_slug=team).items():
                tables[(page, team, tid)] = df
    log(f"Parsed {sum(len(t) for t in avail.values())} pages into {len(tables)} tables.")
    warnings = league_wide_warnings(tables)
    for w in warnings:
        log(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "WARNING: ") + w)

    # 2. Match players across every table.
    idx = players.PlayerIndex((p, t, tid, df) for (p, t, tid), df in tables.items())
    log(f"Matched {len(idx.players):,} players across {len(idx.tables)} player tables.")

    # 3. Write the snapshot to a temporary folder.
    out_root = published_dir(data_dir)
    tmp = out_root / f".tmp-{sid}"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "t").mkdir(parents=True)

    by_table: dict[tuple[str, str], list[tuple[str, pd.DataFrame]]] = defaultdict(list)
    for (page, team, tid), df in tables.items():
        by_table[(page, tid)].append((team or "", df))

    meta_tables, n_rows = [], 0
    for n, ((page, tid), parts) in enumerate(by_table.items()):
        cols: list[str] = []
        digests = set()
        for _, df in parts:
            cols += [c for c in df.columns if c not in cols]
            digests.add(int(pd.util.hash_pandas_object(df.astype(str), index=False).sum()))
        ti = idx.tables.get((page, tid))
        if ti is not None:  # player table: already stacked, with player keys attached
            stacked = ti.df
        else:
            frames = []
            for team, df in parts:
                d = df.copy()
                d["_slug"], d["_ord"] = team, range(len(d))
                frames.append(d)
            stacked = pd.concat(frames, ignore_index=True, sort=False)
            stacked["_pkey"] = stacked["_match"] = stacked["_team"] = None
        stacked = stacked[[c for c in cols if c in stacked.columns] + INTERNAL]
        _write_frame(tmp / "t" / f"{n}.json.gz", stacked)
        n_rows += len(stacked)
        meta_tables.append({
            "page": page, "table": tid, "file": f"t/{n}.json.gz", "teams": len(parts),
            "rows": sum(len(df) for _, df in parts), "same": len(parts) > 1 and len(digests) == 1,
            "columns": cols, "name_col": ti.name_col if ti else None, "player": ti is not None,
            "stats": dict(ti.stats) if ti else {}})

    _write_frame(tmp / "players.json.gz", idx.players)
    public_manifest = {k: v for k, v in manifest.items() if k != "results"}
    _write_json(tmp / "meta.json", {
        "id": sid, "manifest": public_manifest, "warnings": warnings,
        "available": {p: [t or "" for t in ts] for p, ts in avail.items()},
        "tables": meta_tables})

    # 4. Move into place, then update the index (last, so readers never see a partial snapshot).
    final = out_root / sid
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    pruned = prune_published(data_dir, keep)
    write_index(data_dir)
    log(f"Published {sid}: {len(meta_tables)} tables, {n_rows:,} rows, {len(idx.players):,} players.")
    if pruned:
        log(f"Removed {len(pruned)} older published snapshot(s).")
    return {"snapshot": sid, "tables": len(meta_tables), "rows": n_rows, "players": len(idx.players),
            "warnings": warnings, "pruned": pruned}


def list_published(data_dir: Path) -> list[Path]:
    root = published_dir(data_dir)
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                   and (p / "meta.json").exists()), reverse=True)


def prune_published(data_dir: Path, keep: int) -> list[str]:
    doomed = list_published(data_dir)[max(1, keep):]
    for p in doomed:
        shutil.rmtree(p)
    return [p.name for p in doomed]


def write_index(data_dir: Path) -> None:
    entries = []
    for p in list_published(data_dir):
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        entries.append({"id": meta["id"], "manifest": meta["manifest"]})
    root = published_dir(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / ".index.json.tmp"
    _write_json(tmp, {"snapshots": entries})
    tmp.replace(root / "index.json")
