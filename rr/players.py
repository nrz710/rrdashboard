"""Tie rows about the same player together across every page and team.

How a row is matched to a player, strongest first:
  1. Player IDs. Any column that looks like a FanGraphs or MLBAM id. Rows
     that share any id are the same player (so a table with only an MLBAM id
     still links to one with only a FanGraphs id, as long as some third
     table carries both).
  2. Name, only when it's unambiguous. A normalized name ("José Ramírez Jr."
     -> "jose ramirez") links to an id-backed player only if exactly one
     such player has that name.
  3. Ambiguous names (two players with IDs share the name) are NOT merged.
     They're kept under a flagged name key so nothing is silently misattributed.

Column detection is heuristic because the real field names haven't been
verified yet. Adjust ID_COLUMNS / NAME_COLUMNS / TEAM_COLUMNS after the
first real pull (`python -m rr inspect` shows the columns).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

from . import config

# normalized column name (lowercase, alphanumerics only, last dotted segment) -> id namespace
ID_COLUMNS = {
    "playerid": "fg", "fgid": "fg", "fangraphsid": "fg", "idfg": "fg", "fgplayerid": "fg",
    "mlbamid": "mlbam", "xmlbamid": "mlbam", "mlbid": "mlbam", "keymlbam": "mlbam", "mlbamplayerid": "mlbam",
}
NAME_COLUMNS = ["playername", "fullname", "playerfullname", "player", "name"]
TEAM_COLUMNS = ["team", "teamname", "teamabbr", "teamabbrev", "abbname", "org", "organization", "club", "tm"]

_TEAM_WORDS = {n.lower() for n in config.TEAMS} | {
    "red sox", "white sox", "yankees", "orioles", "rays", "blue jays", "guardians", "tigers", "royals",
    "twins", "astros", "angels", "mariners", "rangers", "braves", "marlins", "mets", "phillies",
    "nationals", "cubs", "reds", "brewers", "pirates", "cardinals", "diamondbacks", "rockies",
    "dodgers", "padres", "giants",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
INTERNAL = ["_pkey", "_match", "_team", "_slug", "_ord"]


def norm_col(col) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).split(".")[-1].lower())


def norm_name(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.count(",") == 1:  # "Last, First"
        last, first = (p.strip() for p in s.split(","))
        s = f"{first} {last}"
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z\s'-]", " ", s).replace("'", "").replace("-", " ")
    parts = [p for p in s.split() if p not in _SUFFIXES]
    return " ".join(parts) or None


def _clean_id(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s and s.lower() not in {"nan", "none", "0", "-1", ""} else None


def identify(df: pd.DataFrame) -> tuple[dict[str, str], str | None]:
    """-> ({column: id namespace}, name column or None)"""
    by_norm = {norm_col(c): c for c in df.columns}
    ids = {by_norm[n]: ns for n, ns in ID_COLUMNS.items() if n in by_norm}
    name_col = next((by_norm[n] for n in NAME_COLUMNS if n in by_norm), None)
    if name_col is not None:
        vals = df[name_col].dropna().astype(str).str.lower().head(100)
        if len(vals) and vals.map(lambda v: any(w in v for w in _TEAM_WORDS)).mean() > 0.5:
            name_col = None  # a list of teams, not people
    return ids, name_col


def _team_col(df: pd.DataFrame) -> str | None:
    by_norm = {norm_col(c): c for c in df.columns}
    return next((by_norm[n] for n in TEAM_COLUMNS if n in by_norm), None)


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


AGGS = ("first", "all", "count", "sum", "mean", "max", "min")


def _agg(series: pd.Series, how: str):
    s = series.dropna()
    if how == "count":
        return len(series)
    if s.empty:
        return None
    if how == "first":
        return s.iloc[0]
    if how == "all":
        return "; ".join(dict.fromkeys(s.astype(str)))
    nums = pd.to_numeric(s.astype(str).str.replace(r"[$,%]", "", regex=True), errors="coerce").dropna()
    if nums.empty:
        return None
    return getattr(nums, how)()


@dataclass
class TableInfo:
    page: str
    table: str
    id_cols: dict[str, str]
    name_col: str | None
    df: pd.DataFrame  # stacked across teams, plus _pkey/_match/_team
    stats: Counter = field(default_factory=Counter)


class IndexBase:
    """Lookups and combining, shared by the in-memory index (local files) and the
    published-files index (sources.PublishedIndex). Subclasses provide `players` and the
    accessors table_keys / name_col / columns_of / table_df / match_stats."""

    players: pd.DataFrame

    def player_rows(self, pkey: str) -> list[tuple[str, str, pd.DataFrame]]:
        out = []
        for page, tid in self.table_keys():
            d = self.table_df(page, tid)
            rows = d[d["_pkey"] == pkey]
            if len(rows):
                out.append((page, tid, rows))
        return out

    # -- lookups ----------------------------------------------------------------

    def name_of(self, pkey: str) -> str:
        hit = self.players.loc[self.players["key"] == pkey, "Player"]
        return hit.iloc[0] if len(hit) else pkey

    def search(self, text: str, limit: int = 50) -> pd.DataFrame:
        q = norm_name(text) or ""
        if not q:
            return self.players.head(0)
        m = self.players["Player"].map(lambda n: q in (norm_name(n) or ""))
        return self.players[m].sort_values("Sources", ascending=False).head(limit)

    def profile(self, pkey: str) -> list[tuple[str, str, pd.DataFrame]]:
        """-> [(page, table, rows)] for every table mentioning the player."""
        out = []
        for page, tid, rows in self.player_rows(pkey):
            rows = rows.rename(columns={"_team": "Team"})
            rows = rows.drop(columns=[c for c in INTERNAL if c in rows.columns])
            out.append((page, tid, rows.dropna(axis=1, how="all").reset_index(drop=True)))
        return sorted(out, key=lambda x: list(config.ALL_PAGES).index(x[0]) if x[0] in config.ALL_PAGES else 99)

    def profile_long(self, pkey: str) -> pd.DataFrame:
        """One tidy table (Source, Team, Field, Value) - good for a single export sheet."""
        recs = []
        for page, tid, rows in self.profile(pkey):
            src = config.ALL_PAGES.get(page, page)
            for i, row in rows.iterrows():
                for col, val in row.items():
                    if col == "Team" or val is None or (isinstance(val, float) and pd.isna(val)):
                        continue
                    recs.append({"Source": src if len(rows) == 1 else f"{src} #{i + 1}",
                                 "Team": row.get("Team", ""), "Field": col, "Value": str(val)})
        return pd.DataFrame(recs, columns=["Source", "Team", "Field", "Value"])

    # -- combining ----------------------------------------------------------------

    def values(self, page: str, table: str, column: str, how: str = "first") -> pd.Series:
        d = self.table_df(page, table)
        d = d[d["_pkey"].notna()]
        return d.groupby("_pkey")[column].agg(lambda s: _agg(s, how))

    def combine(self, spec: dict) -> pd.DataFrame:
        """spec = {"fields": [{"page","table","column","agg","label"}],
                   "rows_from": "any" | <index into fields>,
                   "teams": ["*"] | [display names],
                   "include_ambiguous": bool}"""
        keys = set(self.table_keys())
        fields = [f for f in spec.get("fields", []) if (f["page"], f["table"]) in keys]
        if not fields:
            return pd.DataFrame(columns=["Player", "Team"])
        cols = {}
        for f in fields:
            label = f.get("label") or f"{f['column']} ({config.ALL_PAGES.get(f['page'], f['page'])})"
            if f["column"] in self.columns_of(f["page"], f["table"]):
                cols[label] = self.values(f["page"], f["table"], f["column"], f.get("agg", "first"))
        out = pd.DataFrame(cols)
        rf = spec.get("rows_from", "any")
        if isinstance(rf, int) and 0 <= rf < len(fields):
            f = fields[rf]
            keep = set(self.table_df(f["page"], f["table"])["_pkey"].dropna())
            out = out[out.index.isin(keep)]
        p = self.players.set_index("key")
        out.insert(0, "Team", p["Team"].reindex(out.index).fillna(""))
        out.insert(0, "Player", p["Player"].reindex(out.index).fillna(pd.Series(out.index, index=out.index)))
        if not spec.get("include_ambiguous", False):
            out = out[~out.index.astype(str).str.startswith("name?:")]
        teams = spec.get("teams") or ["*"]
        if "*" not in teams:
            out = out[out["Team"].isin(teams)]
        out = out.sort_values(["Team", "Player"])
        sort_by = spec.get("sort_by")
        if sort_by and sort_by in out.columns and sort_by != "Team":
            out = out.sort_values(sort_by, kind="stable")
        return out.reset_index(drop=True)

    def match_report(self) -> pd.DataFrame:
        rows = []
        for page, tid in self.table_keys():
            st = Counter(self.match_stats(page, tid))
            rows.append({"Page": config.ALL_PAGES.get(page, page), "Table": tid, "Rows": sum(st.values()),
                         "By ID": st["id"], "By name": st["name"],
                         "Name only (no ID anywhere)": st["name-only"],
                         "Ambiguous name": st["ambiguous"], "Unmatched": st["none"]})
        return pd.DataFrame(rows)


class PlayerIndex(IndexBase):
    """Build once per snapshot from (page, team_slug|None, table_id, DataFrame) tuples."""

    def __init__(self, tables: Iterable[tuple[str, str | None, str, pd.DataFrame]]):
        stacked: dict[tuple[str, str], list[pd.DataFrame]] = defaultdict(list)
        meta: dict[tuple[str, str], tuple] = {}
        for page, team, tid, df in tables:
            ids, name_col = identify(df)
            if not ids and name_col is None:
                continue
            d = df.copy()
            d["_slug"] = team or ""
            d["_ord"] = range(len(d))
            tcol = _team_col(d)
            if team:
                d["_team"] = config.SLUG_TO_TEAM.get(team, team)
            elif tcol:  # league-wide page: use its own team column, normalized to team names
                d["_team"] = d[tcol].map(lambda v: config.SLUG_TO_TEAM.get(config.team_slug_of(v) or "", v))
            else:
                d["_team"] = None
            stacked[(page, tid)].append(d)
            meta[(page, tid)] = (ids, name_col)

        uf = _UF()
        id_name: dict[str, Counter] = defaultdict(Counter)
        frames: dict[tuple[str, str], pd.DataFrame] = {}
        for key, parts in stacked.items():
            d = pd.concat(parts, ignore_index=True, sort=False)
            ids, name_col = meta[key]
            tokens_col, names_col = [], []
            for _, row in d.iterrows():
                toks = [f"{ns}:{v}" for c, ns in ids.items() if (v := _clean_id(row.get(c)))]
                for t in toks[1:]:
                    uf.union(toks[0], t)
                nm = norm_name(row.get(name_col)) if name_col else None
                raw = row.get(name_col) if name_col else None
                if toks and nm:
                    id_name[toks[0]][(nm, raw)] += 1
                tokens_col.append(toks)
                names_col.append(nm)
            d["_tok"], d["_nm"] = tokens_col, names_col
            frames[key] = d

        # Canonical key per id group (prefer FanGraphs id), and name -> groups.
        group_members: dict[str, set[str]] = defaultdict(set)
        for tok in list(uf.p):
            group_members[uf.find(tok)].add(tok)
        canon = {root: sorted(m, key=lambda t: (not t.startswith("fg:"), t))[0] for root, m in group_members.items()}
        name_to_groups: dict[str, set[str]] = defaultdict(set)
        display: dict[str, Counter] = defaultdict(Counter)
        for tok, names in id_name.items():
            g = canon[uf.find(tok)]
            for (nm, raw), n in names.items():
                name_to_groups[nm].add(g)
                display[g][raw] += n

        self.tables: dict[tuple[str, str], TableInfo] = {}
        teams_seen: dict[str, Counter] = defaultdict(Counter)
        for key, d in frames.items():
            ids, name_col = meta[key]
            pkeys, matches = [], []
            for toks, nm, raw in zip(d["_tok"], d["_nm"], d[name_col] if name_col else [None] * len(d)):
                if toks:
                    pk, how = canon[uf.find(toks[0])], "id"
                elif nm and len(name_to_groups.get(nm, ())) == 1:
                    pk, how = next(iter(name_to_groups[nm])), "name"
                elif nm and len(name_to_groups.get(nm, ())) > 1:
                    pk, how = f"name?:{nm}", "ambiguous"
                elif nm:
                    pk, how = f"name:{nm}", "name-only"
                else:
                    pk, how = None, "none"
                pkeys.append(pk)
                matches.append(how)
                if pk and raw is not None and how in ("name-only", "ambiguous"):
                    display[pk][raw] += 1
            d = d.drop(columns=["_tok", "_nm"])
            d["_pkey"], d["_match"] = pkeys, matches
            for pk, tm in zip(d["_pkey"], d["_team"]):
                if pk and isinstance(tm, str):
                    teams_seen[pk][tm] += 1
            self.tables[key] = TableInfo(key[0], key[1], ids, name_col, d, Counter(matches))

        rows = []
        all_keys = {pk for t in self.tables.values() for pk in t.df["_pkey"].dropna()}
        for pk in all_keys:
            n_src = sum(1 for t in self.tables.values() if (t.df["_pkey"] == pk).any())
            name = display[pk].most_common(1)[0][0] if display[pk] else pk.split(":", 1)[-1].title()
            rows.append({"key": pk, "Player": name,
                         "Team": teams_seen[pk].most_common(1)[0][0] if teams_seen[pk] else "",
                         "Sources": n_src, "Ambiguous": pk.startswith("name?:")})
        self.players = (pd.DataFrame(rows, columns=["key", "Player", "Team", "Sources", "Ambiguous"])
                        .sort_values(["Player", "Team"]).reset_index(drop=True))

    # -- accessors for IndexBase ---------------------------------------------------
    def table_keys(self) -> list[tuple[str, str]]:
        return list(self.tables)

    def name_col(self, page: str, table: str) -> str | None:
        return self.tables[(page, table)].name_col

    def columns_of(self, page: str, table: str) -> list[str]:
        return [c for c in self.tables[(page, table)].df.columns if c not in INTERNAL]

    def table_df(self, page: str, table: str) -> pd.DataFrame:
        return self.tables[(page, table)].df

    def match_stats(self, page: str, table: str) -> dict:
        return dict(self.tables[(page, table)].stats)

