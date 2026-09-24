"""Snapshots on disk.

data/snapshots/<YYYYmmddTHHMMSSZ>_<kind>_<season>/
    manifest.json                    what was planned, what worked
    raw/<page>/<team|_league>.json   exactly what came back (payload + metadata)

Only raw payloads are stored; tables are re-derived on load, so parser
improvements apply to old snapshots for free.
"""
from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

LEAGUE = "_league"


def snapshots_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "snapshots"


def snapshot_name(kind: str, season: int) -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{kind}_{season}"


def create_snapshot(data_dir: Path, name: str) -> Path:
    snap = snapshots_dir(data_dir) / name
    snap.mkdir(parents=True, exist_ok=False)
    return snap


def new_snapshot(data_dir: Path, kind: str, season: int) -> Path:
    return create_snapshot(data_dir, snapshot_name(kind, season))


def _raw_path(snap: Path, page_key: str, team: str | None) -> Path:
    return snap / "raw" / page_key.replace("/", "__") / f"{team or LEAGUE}.json.gz"


def save_raw(snap: Path, page_key: str, team: str | None, record: dict) -> Path:
    """Raw payloads are gzipped: ~10x smaller, which matters when they live in git."""
    path = _raw_path(snap, page_key, team)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    return path


def load_raw(snap: Path, page_key: str, team: str | None) -> dict | None:
    path = _raw_path(Path(snap), page_key, team)
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    legacy = path.with_suffix("")  # plain .json from earlier versions
    if legacy.exists():
        return json.loads(legacy.read_text(encoding="utf-8"))
    return None


def write_manifest(snap: Path, manifest: dict) -> None:
    (snap / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def read_manifest(snap: Path) -> dict:
    return json.loads((Path(snap) / "manifest.json").read_text(encoding="utf-8"))


def list_snapshots(data_dir: Path) -> list[Path]:
    root = snapshots_dir(data_dir)
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if (p / "manifest.json").exists()), reverse=True)


def available(snap: Path) -> dict[str, list[str | None]]:
    """page_key -> team slugs (or [None] for league pages) successfully saved."""
    out: dict[str, list[str | None]] = {}
    for r in read_manifest(snap).get("results", []):
        if r.get("ok"):
            out.setdefault(r["page"], []).append(r.get("team"))
    return out


def prune(data_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` snapshots. Never touches data/state."""
    keep = max(1, keep)
    doomed = list_snapshots(data_dir)[keep:]
    for snap in doomed:
        shutil.rmtree(snap)
    return doomed
