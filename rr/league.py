"""League-wide compilation: the same table from every team, stacked in one place."""
from __future__ import annotations

import pandas as pd

from . import config
from .views import combined_frame


def coverage(tables_for, page: str, teams: list) -> pd.DataFrame:
    """Which tables exist on this page, for how many teams, and how many rows in total."""
    found: dict[str, list] = {}
    digests: dict[str, set] = {}
    for t in teams:
        for tid, df in tables_for(page, t).items():
            found.setdefault(tid, []).append(len(df))
            digests.setdefault(tid, set()).add(int(pd.util.hash_pandas_object(df.astype(str), index=False).sum()))
    # A table that is byte-for-byte the same on every team's page is almost
    # always site chrome (menus, team lists), so it sorts to the bottom.
    rows = [{"Table": tid, "Teams": len(v), "Rows": sum(v),
             "Same for every team": len(v) > 1 and len(digests[tid]) == 1} for tid, v in found.items()]
    return (pd.DataFrame(rows, columns=["Table", "Teams", "Rows", "Same for every team"])
            .sort_values(["Same for every team", "Teams", "Rows"], ascending=[True, False, False])
            .reset_index(drop=True))


def compile_page(tables_for, page: str, teams: list, min_teams: int = 2) -> dict[str, pd.DataFrame]:
    """Every table on a page that appears for at least `min_teams` teams, stacked league-wide."""
    cov = coverage(tables_for, page, teams)
    if page in config.LEAGUE_PAGES:
        min_teams = 1
    out = {}
    keep = (cov["Teams"] >= min_teams) & ~cov["Same for every team"]
    for tid in cov.loc[keep, "Table"]:
        df = combined_frame(tables_for, page, teams, tid)
        if df is not None:
            out[tid] = df
    return out


def summarize(df: pd.DataFrame, by: str, value: str | None, how: str) -> pd.DataFrame:
    """how='count': rows per (by x value). how in sum/mean/max/min: numeric value per `by`."""
    if by not in df.columns:
        return df.head(0)
    if how == "count":
        if value and value in df.columns and value != by:
            t = pd.crosstab(df[by], df[value].fillna("(blank)"), margins=True, margins_name="Total")
            t = t.reset_index().rename_axis(None, axis=1)
            if by == "Team":  # RosterResource orders teams by abbreviation
                body = t[t["Team"] != "Total"].copy()
                body = body.iloc[body["Team"].map(lambda n: config.NAME_TO_ABBR.get(n, n)).argsort()]
                t = pd.concat([body, t[t["Team"] == "Total"]], ignore_index=True)
            return t
        return df.groupby(by).size().rename("Rows").reset_index()
    nums = pd.to_numeric(df[value].astype(str).str.replace(r"[$,%]", "", regex=True), errors="coerce")
    g = nums.groupby(df[by]).agg(how).rename(f"{how} of {value}")
    return g.reset_index().sort_values(g.name, ascending=False).reset_index(drop=True)
