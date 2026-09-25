"""Panels: saved recipes for what appears on the dashboard (plain JSON).

kind "table"    {"title","page","teams": [slug...] or ["*"] for all,"table","columns","filter","sort_by","ascending"}
kind "summary"  {"title","page","teams","table","by","value","how"}           league roll-ups
kind "combined" {"title","spec": see PlayerIndex.combine}                      one row per player
kind "player"   {"title","player_key","player_name"}                           one player, every tool

Because teams can be "*" and players are stored by key, a saved layout
re-applies cleanly to every future snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from . import config


@dataclass
class Ctx:
    tables_for: Callable  # (page, team) -> {table_id: DataFrame}
    avail: dict            # page -> [team slugs] (or [None])
    index: Callable        # () -> player index, built lazily
    frame_fn: Callable | None = None     # (page, teams, table) -> DataFrame | None; fast path (published files)
    coverage_fn: Callable | None = None  # (page) -> DataFrame

    def teams(self, page: str, teams: list) -> list:
        have = self.avail.get(page, [])
        return list(have) if "*" in (teams or []) else [t for t in teams if t in have]

    def frame(self, page: str, teams: list, table: str):
        if self.frame_fn is not None:
            return self.frame_fn(page, teams, table)
        return combined_frame(self.tables_for, page, teams, table)

    def fetched_per_team(self, page: str) -> bool:
        """True if this snapshot holds one copy of the page per team (older snapshots,
        or tools not in LEAGUE_WIDE_INSTEAD_OF_PER_TEAM)."""
        return any(self.avail.get(page, []))

    def view_frame(self, page: str, teams: list, table: str):
        """The table for the chosen teams (or ["*"] for all), whichever way it was fetched."""
        if self.fetched_per_team(page):
            return self.frame(page, self.teams(page, teams), table)
        df = self.frame(page, [None], table)
        if df is None:
            return None
        df = with_team(df)
        if "Team" in df.columns and teams and "*" not in teams and any(teams):
            names = {config.SLUG_TO_TEAM.get(t, t) for t in teams if t}
            df = df[df["Team"].isin(names)].reset_index(drop=True)
        return df

    def coverage(self, page: str):
        if self.coverage_fn is not None:
            return self.coverage_fn(page)
        from .league import coverage
        return coverage(self.tables_for, page, self.avail.get(page, []))


_TEAM_COLS = ["team", "teamabbname", "teamabbr", "teamabbrev", "abbname", "teamname", "org",
              "organization", "club", "tm"]
_TEAM_ID_COLS = ["teamid", "playerteamid"]


def team_column(df: pd.DataFrame) -> str | None:
    """The column naming each row's team in a league-wide table, if there is one."""
    col, _ = team_column_and_kind(df)
    return col


def team_column_and_kind(df: pd.DataFrame) -> tuple[str | None, bool]:
    """-> (column, is_numeric_team_id). Named/abbreviated columns win over numeric ids."""
    import re
    by_norm = {re.sub(r"[^a-z0-9]", "", str(c).split(".")[-1].lower()): c for c in df.columns}
    for names, numeric in ((_TEAM_COLS, False), (_TEAM_ID_COLS, True)):
        for n in names:
            c = by_norm.get(n)
            if c is None:
                continue
            vals = df[c].dropna().head(300)
            hits = vals.map(lambda v: config.team_slug_of(v if numeric else str(v), numeric=numeric))
            if len(hits) and hits.notna().mean() >= 0.8:
                return c, numeric
    return None, False


def team_slugs(df: pd.DataFrame) -> tuple[str | None, list]:
    """-> (team column used, the team slug for every row)."""
    col, numeric = team_column_and_kind(df)
    if col is None:
        return None, [None] * len(df)
    return col, [config.team_slug_of(v if numeric else (v if isinstance(v, str) else str(v)), numeric=numeric)
                 if v is not None else None for v in df[col]]


def with_team(df: pd.DataFrame) -> pd.DataFrame:
    """Give a league-wide table a leading Team column (team names), like per-team tables have."""
    col, slugs = team_slugs(df)
    if col is None:
        return df
    names = [config.SLUG_TO_TEAM.get(s or "", v) for s, v in zip(slugs, df[col])]
    out = df.drop(columns=[col]) if col.lower() in ("team", "teamabbname", "tm") else df.copy()
    if "Team" in out.columns:
        out = out.drop(columns=["Team"])
    out.insert(0, "Team", names)
    return out


def combined_frame(tables_for, page: str, teams: list, table: str) -> pd.DataFrame | None:
    frames = []
    for team in teams:
        df = tables_for(page, team).get(table)
        if df is None:
            continue
        df = df.copy()
        if team:
            df.insert(0, "Team", config.SLUG_TO_TEAM.get(team, team))
        frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else None


def apply_view(df: pd.DataFrame, panel: dict) -> pd.DataFrame:
    cols = [c for c in panel.get("columns") or [] if c in df.columns]
    if cols:
        if "Team" in df.columns and "Team" not in cols:
            cols = ["Team", *cols]
        df = df[cols]
    needle = (panel.get("filter") or "").strip().lower()
    if needle:
        mask = df.apply(lambda s: s.map(lambda v: needle in str(v).lower() if not pd.isna(v) else False)).any(axis=1)
        df = df[mask]
    sort_by = panel.get("sort_by")
    if sort_by and sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=panel.get("ascending", True), kind="stable")
    return df.reset_index(drop=True)


def panel_frame(ctx: Ctx, panel: dict) -> pd.DataFrame | None:
    kind = panel.get("kind", "table")
    if kind in ("table", "summary"):
        df = ctx.view_frame(panel["page"], panel.get("teams") or ["*"], panel["table"])
        if df is None:
            return None
        if kind == "summary":
            from .league import summarize
            return summarize(df, panel["by"], panel.get("value"), panel["how"])
        return apply_view(df, panel)
    if kind == "combined":
        return ctx.index().combine(panel["spec"])
    if kind == "player":
        df = ctx.index().profile_long(panel["player_key"])
        return df if len(df) else None
    raise ValueError(f"unknown panel kind {kind!r}")


def display_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Stringify columns that mix types, which the table widget can't render."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            kinds = {type(v) for v in out[c].dropna()}
            if len(kinds) > 1:
                out[c] = out[c].map(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return out


_PANEL_KINDS = {"table", "summary", "combined", "player"}
_MAX_PANELS = 50


def validate_layout(obj) -> list[dict]:
    """Check an uploaded layout file. Visitors can upload anything, so accept only the
    shapes this app writes, with plain values, and keep the size reasonable."""
    if not isinstance(obj, list):
        raise ValueError("expected a list of panels")
    if len(obj) > _MAX_PANELS:
        raise ValueError(f"at most {_MAX_PANELS} panels")
    out = []
    for p in obj:
        if not isinstance(p, dict) or p.get("kind", "table") not in _PANEL_KINDS:
            raise ValueError("unknown panel type")
        if not isinstance(p.get("title", ""), str) or len(p.get("title", "")) > 200:
            raise ValueError("bad panel title")
        kind = p.get("kind", "table")
        need = {"table": ["page", "table"], "summary": ["page", "table", "by", "how"],
                "combined": ["spec"], "player": ["player_key"]}[kind]
        if any(k not in p for k in need):
            raise ValueError(f"{kind} panel is missing fields")
        if kind in ("table", "summary") and not (isinstance(p["page"], str) and isinstance(p["table"], str)):
            raise ValueError("bad page or table")
        if kind == "combined" and not (isinstance(p["spec"], dict) and isinstance(p["spec"].get("fields", []), list)
                                        and len(p["spec"].get("fields", [])) <= 60):
            raise ValueError("bad combined panel")
        out.append(p)
    return out
