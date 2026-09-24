"""Where the dashboard gets its data. All free: local files, or the repo's `data` branch.

  PublishedSource  reads the pre-parsed files that `python -m rr publish` writes
                   (fast: only the files a view needs are loaded). It works with a
                   FileReader (local ./data folder) or a GitHubReader (the private repo's
                   `data` branch, read with a read-only token).
  LocalSource      parses raw snapshots directly. Slower; used only when nothing has
                   been published yet, e.g. right after a local `refresh`.

Published snapshots never change, so their files are cached for the life of the app.
"""
from __future__ import annotations

import json
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

from . import config, league, parse, players, published, store, views
from .gate import Gate, GateClosed


# ------------------------------------------------------------------- readers ---
class FileReader:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.label = "Local files"

    def read(self, path: str) -> bytes | None:
        p = self.root / path
        return p.read_bytes() if p.exists() else None


class ReaderError(RuntimeError):
    pass


class GitHubReader:
    """Reads single files from a branch of a (private) GitHub repo via the contents API."""

    def __init__(self, repo: str, branch: str = "data", token: str | None = None):
        self.repo, self.branch, self.token = repo, branch, token
        self.label = "GitHub"
        self.session = requests.Session()  # default User-Agent; this talks to GitHub, not FanGraphs
        self._lock = threading.Lock()      # one shared reader serves every visitor

    def read(self, path: str) -> bytes | None:
        headers = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        with self._lock:
            resp = self.session.get(url, params={"ref": self.branch}, headers=headers, timeout=60)
        if resp.status_code == 404:
            return None
        if resp.status_code == 429 or (resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"):
            raise ReaderError("GitHub's hourly read limit was reached. It resets within the hour.")
        if resp.status_code in (401, 403):
            raise ReaderError(f"GitHub refused access to {self.repo} (HTTP {resp.status_code}). "
                              "Check the token in the app's secrets (DEPLOY.md, part 4).")
        resp.raise_for_status()
        return resp.content


# ------------------------------------------------------------ published data ---
class PublishedSource:
    kind = "published"

    def __init__(self, reader):
        self.reader = reader
        self.label = reader.label

    def _json(self, path: str):
        data = self.reader.read(path)
        return None if data is None else json.loads(data.decode("utf-8"))

    def snapshots(self) -> list[dict]:
        idx = self._json("published/index.json")
        return idx["snapshots"] if idx else []

    @lru_cache(maxsize=32)
    def meta(self, sid: str) -> dict:
        m = self._json(f"published/{sid}/meta.json")
        if m is None:
            raise ReaderError(f"Snapshot {sid} is no longer available. Reload the page.")
        m["by_key"] = {(t["page"], t["table"]): t for t in m["tables"]}
        return m

    def warnings(self, sid: str) -> list[str]:
        return self.meta(sid).get("warnings", [])

    @lru_cache(maxsize=32)
    def available(self, sid: str) -> dict:
        return {p: ([t or None for t in ts] if any(ts) else [None])
                for p, ts in self.meta(sid)["available"].items()}

    @lru_cache(maxsize=256)
    def coverage(self, sid: str, page: str) -> pd.DataFrame:
        rows = [{"Table": t["table"], "Teams": t["teams"], "Rows": t["rows"], "Same for every team": t["same"]}
                for t in self.meta(sid)["tables"] if t["page"] == page]
        return (pd.DataFrame(rows, columns=["Table", "Teams", "Rows", "Same for every team"])
                .sort_values(["Same for every team", "Teams", "Rows"], ascending=[True, False, False])
                .reset_index(drop=True))

    @lru_cache(maxsize=512)
    def table(self, sid: str, page: str, table: str) -> pd.DataFrame | None:
        """The whole stacked table (all teams) with its internal columns."""
        t = self.meta(sid)["by_key"].get((page, table))
        if t is None:
            return None
        data = self.reader.read(f"published/{sid}/{t['file']}")
        return None if data is None else published.read_frame(data)

    @lru_cache(maxsize=512)
    def frame(self, sid: str, page: str, teams: tuple, table: str):
        df = self.table(sid, page, table)
        if df is None:
            return None
        slugs = [t for t in teams if t]
        if slugs:  # page fetched per team
            order = {s: i for i, s in enumerate(slugs)}  # same order the teams were asked for
            df = df[df["_slug"].isin(slugs)].copy()
            if df.empty:
                return None
            df["_o"] = df["_slug"].map(lambda s: order.get(s, 999))
            df = df.sort_values(["_o", "_ord"], kind="stable")
            team = df["_slug"].map(lambda s: config.SLUG_TO_TEAM.get(s, s))
            df = df.drop(columns=published.INTERNAL + ["_o"])
            df.insert(0, "Team", team.values)
        else:
            df = df.sort_values("_ord", kind="stable").drop(columns=published.INTERNAL)
        return df.reset_index(drop=True)

    @lru_cache(maxsize=1024)
    def tables_for(self, sid: str, page: str, team: str | None) -> dict:
        out = {}
        for t in self.meta(sid)["tables"]:
            if t["page"] != page:
                continue
            df = self.table(sid, page, t["table"])
            if df is None:
                continue
            df = df[df["_slug"] == (team or "")].sort_values("_ord", kind="stable")
            if len(df):
                out[t["table"]] = df.drop(columns=published.INTERNAL).reset_index(drop=True)
        return out

    @lru_cache(maxsize=8)
    def index(self, sid: str) -> "PublishedIndex":
        return PublishedIndex(self, sid)

    def gate_status(self) -> dict:
        """The refresh schedule, read from the pull log stored alongside the data."""
        log, halt = self.reader.read("state/pull_log.jsonl"), self.reader.read("state/HALT")
        with tempfile.TemporaryDirectory() as tmp:
            if log is not None:
                (Path(tmp) / "pull_log.jsonl").write_bytes(log)
            if halt is not None:
                (Path(tmp) / "HALT").write_bytes(halt)
            return Gate(Path(tmp), config.MIN_REFRESH_INTERVAL_HOURS).status()


class PublishedIndex(players.IndexBase):
    """Player lookups from the pre-matched published files."""

    def __init__(self, src: PublishedSource, sid: str):
        self.src, self.sid = src, sid
        data = src.reader.read(f"published/{sid}/players.json.gz")
        self.players = (published.read_frame(data) if data is not None
                        else pd.DataFrame(columns=["key", "Player", "Team", "Sources", "Ambiguous"]))
        self.players["Team"] = self.players["Team"].fillna("")
        self._meta = {k: t for k, t in src.meta(sid)["by_key"].items() if t["player"]}

    def table_keys(self):
        return list(self._meta)

    def name_col(self, page, table):
        return self._meta[(page, table)]["name_col"]

    def columns_of(self, page, table):
        return list(self._meta[(page, table)]["columns"])

    def match_stats(self, page, table):
        return self._meta[(page, table)]["stats"]

    def table_df(self, page, table):
        return self.src.table(self.sid, page, table)


# --------------------------------------------------------- raw local fallback ---
class LocalSource:
    kind = "local"
    label = "Local files (unpublished)"

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def _snap(self, sid: str) -> Path:
        return store.snapshots_dir(self.data_dir) / sid

    def snapshots(self) -> list[dict]:
        return [{"id": p.name, "manifest": store.read_manifest(p)} for p in store.list_snapshots(self.data_dir)]

    def available(self, sid: str) -> dict:
        return store.available(self._snap(sid))

    def warnings(self, sid: str) -> list[str]:
        return []

    @lru_cache(maxsize=4096)
    def tables_for(self, sid: str, page: str, team: str | None) -> dict:
        rec = store.load_raw(self._snap(sid), page, team)
        return {} if rec is None else parse.discover_tables(rec["payload"], team_slug=team)

    def frame(self, sid: str, page: str, teams: tuple, table: str):
        return views.combined_frame(lambda p, t: self.tables_for(sid, p, t), page, list(teams), table)

    @lru_cache(maxsize=256)
    def coverage(self, sid: str, page: str) -> pd.DataFrame:
        return league.coverage(lambda p, t: self.tables_for(sid, p, t), page, self.available(sid).get(page, []))

    @lru_cache(maxsize=8)
    def index(self, sid: str) -> players.IndexBase:
        av = self.available(sid)
        return players.PlayerIndex((p, t, tid, df) for p, ts in av.items() for t in ts
                                   for tid, df in self.tables_for(sid, p, t).items())

    def gate_status(self) -> dict:
        return Gate(self.data_dir / "state", config.MIN_REFRESH_INTERVAL_HOURS).status()


def local_source(data_dir: Path):
    """Published files if there are any, otherwise raw snapshots."""
    if (published.published_dir(data_dir) / "index.json").exists():
        return PublishedSource(FileReader(data_dir))
    return LocalSource(data_dir)


def gate_status_safe(src) -> dict | str:
    try:
        return src.gate_status()
    except GateClosed as exc:
        return str(exc)
