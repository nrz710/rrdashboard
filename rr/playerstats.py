"""PLAYER STATS: one row per player from FanGraphs' major-league leaderboards.

Platform year (the season in progress, or the one just finished before April):
    GS, innings at each position (C 1B 2B 3B SS LF CF RF), PA, HR, AVG, wRC+, xwOBA, OAA,
    fWAR, fWAR/700 PA
Last 3 years (platform year and the two before, combined by FanGraphs):
    GS, PA, wRC+, OAA, fWAR, fWAR/700 PA

fWAR/700 PA = fWAR / PA * 700 (blank when PA is 0).

Leaderboard columns are found by name, tolerantly (case and punctuation ignored), and the
build reports anything it couldn't find, so a change on FanGraphs' side is visible in the
website build log rather than silently producing blank columns.
"""
from __future__ import annotations

import math
import re

import pandas as pd

POSITIONS = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"]
FORMATS = {"GS": "int", "INN": "dec1", "PA": "int", "HR": "int", "AVG": "avg3", "wRC+": "int",
           "xwOBA": "avg3", "OAA": "int", "fWAR": "dec1", "fWAR/700": "dec1"}


def _n(s: str) -> str:
    return re.sub(r"[^a-z0-9+]", "", str(s).lower())


def col(df: pd.DataFrame | None, *names: str) -> str | None:
    if df is None:
        return None
    by = {_n(c): c for c in df.columns if not str(c).startswith("_")}
    for name in names:
        if _n(name) in by:
            return by[_n(name)]
    return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _pid(v) -> str | None:
    f = _num(v)
    return str(int(f)) if f is not None else (str(v).strip() or None) if isinstance(v, str) else None


def _rows_by_player(df: pd.DataFrame | None) -> dict:
    """playerid -> list of row dicts (fielding has one row per position)."""
    out: dict = {}
    if df is None:
        return out
    pid = col(df, "playerid", "PlayerId", "IDfg")
    if pid is None:
        return out
    for r in df.to_dict("records"):
        k = _pid(r.get(pid))
        if k:
            out.setdefault(k, []).append(r)
    return out


def _sum(rows, c):
    vals = [_num(r.get(c)) for r in rows] if c else []
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _first(rows, c):
    for r in rows or []:
        v = _num(r.get(c)) if c else None
        if v is not None:
            return v
    return None


def _text(v):
    """Leaderboard name / team fields can be links: '<a href="...">Aaron Judge</a>' -> 'Aaron Judge'."""
    return re.sub(r"<[^>]+>", "", v).strip() if isinstance(v, str) else v


def _per700(war, pa):
    return war / pa * 700 if war is not None and pa else None


def build(frames: dict, season: int) -> tuple[pd.DataFrame, list[str], dict, dict, dict, dict, list[str]]:
    """frames: page key -> published stacked table (or None).
    -> (table with _pkey/_url/_team, columns, labels, formats, groups, tips, problems)."""
    first = season - 2
    span = f"{first}-{str(season)[2:]}"
    bat, bat_sc, bat3 = (frames.get(k) for k in ("stats/bat-platform", "stats/bat-statcast-platform", "stats/bat-3yr"))
    fld, fld_sc, fld3, fld3_sc = (frames.get(k) for k in ("stats/fld-platform", "stats/fld-statcast-platform",
                                                          "stats/fld-3yr", "stats/fld-statcast-3yr"))
    problems = []

    def need(df, label, *names):
        c = col(df, *names)
        if df is not None and c is None:
            problems.append(f"{label}: no {names[0]} column")
        return c

    # where each stat lives
    b = {k: need(bat, "batting " + str(season), *v) for k, v in
         {"PA": ("PA",), "HR": ("HR",), "AVG": ("AVG",), "wRC+": ("wRC+", "wRC"), "WAR": ("WAR", "fWAR")}.items()}
    xw_src = bat if col(bat, "xwOBA") else bat_sc
    xw = need(xw_src, "batting statcast " + str(season), "xwOBA")
    b3 = {k: need(bat3, f"batting {first}-{season}", *v) for k, v in
          {"PA": ("PA",), "wRC+": ("wRC+", "wRC"), "WAR": ("WAR", "fWAR")}.items()}

    def fld_cols(std, sc, label):
        gs_df = std if col(std, "GS") else sc
        return {"pos": col(std, "Pos", "Position") or col(sc, "Pos", "Position"), "inn_df": std if col(std, "Inn") else sc,
                "inn": col(std, "Inn") or col(sc, "Inn"), "gs_df": gs_df, "gs": need(gs_df, label, "GS"),
                "oaa": need(std, label, "OAA")}
    f1 = fld_cols(fld, fld_sc, f"fielding {season}")
    f3 = fld_cols(fld3, fld3_sc, f"fielding {first}-{season}")

    B, BSC, B3 = _rows_by_player(bat), _rows_by_player(xw_src), _rows_by_player(bat3)
    F_INN, F_GS, F_OAA = _rows_by_player(f1["inn_df"]), _rows_by_player(f1["gs_df"]), _rows_by_player(fld)
    F3_GS, F3_OAA = _rows_by_player(f3["gs_df"]), _rows_by_player(fld3)

    cols = (["Player"] + [f"{season} GS"] + [f"{season} INN {p}" for p in POSITIONS]
            + [f"{season} {s}" for s in ("PA", "HR", "AVG", "wRC+", "xwOBA", "OAA", "fWAR", "fWAR/700")]
            + [f"{span} {s}" for s in ("GS", "PA", "wRC+", "OAA", "fWAR", "fWAR/700")])
    rows = []
    for pid in sorted(set(B) | set(F_INN) | set(F_GS) | set(B3) | set(F3_GS)):
        src = (B.get(pid) or F_INN.get(pid) or B3.get(pid) or F_GS.get(pid) or F3_GS.get(pid))[0]
        name_c = col(pd.DataFrame([src]), "PlayerName", "Name", "PlayerNameRoute")
        team_c = col(pd.DataFrame([src]), "TeamNameAbb", "team_name", "TeamName", "Team")
        r = {"Player": _text(src.get(name_c)) if name_c else None, "_team": _text(src.get(team_c)) if team_c else None,
             "_pkey": next((x.get("_pkey") for rs in (B.get(pid), F_INN.get(pid), B3.get(pid))
                            for x in (rs or []) if isinstance(x.get("_pkey"), str)), f"fg:{pid}"),
             "_url": f"/statss.aspx?playerid={pid}"}
        r[f"{season} GS"] = _sum(F_GS.get(pid, []), f1["gs"])
        for p in POSITIONS:
            r[f"{season} INN {p}"] = _sum([x for x in F_INN.get(pid, []) if str(x.get(f1["pos"], "")).upper() == p], f1["inn"])
        pa, war = _first(B.get(pid), b["PA"]), _first(B.get(pid), b["WAR"])
        r.update({f"{season} PA": pa, f"{season} HR": _first(B.get(pid), b["HR"]), f"{season} AVG": _first(B.get(pid), b["AVG"]),
                  f"{season} wRC+": _first(B.get(pid), b["wRC+"]), f"{season} xwOBA": _first(BSC.get(pid), xw),
                  f"{season} OAA": _sum(F_OAA.get(pid, []), f1["oaa"]), f"{season} fWAR": war,
                  f"{season} fWAR/700": _per700(war, pa)})
        pa3, war3 = _first(B3.get(pid), b3["PA"]), _first(B3.get(pid), b3["WAR"])
        r.update({f"{span} GS": _sum(F3_GS.get(pid, []), f3["gs"]), f"{span} PA": pa3,
                  f"{span} wRC+": _first(B3.get(pid), b3["wRC+"]), f"{span} OAA": _sum(F3_OAA.get(pid, []), f3["oaa"]),
                  f"{span} fWAR": war3, f"{span} fWAR/700": _per700(war3, pa3)})
        rows.append(r)
    df = pd.DataFrame(rows, columns=cols + ["_team", "_pkey", "_url"])

    def stat_of(c):
        tail = c.split(" ", 1)[1]
        return "INN" if tail.startswith("INN ") else tail
    labels = {c: ("PLAYER" if c == "Player" else
                  f"{c.split(' ')[0]} INN ({c.split(' ')[-1]})" if " INN " in c else
                  c.split(" ", 1)[0] + " " + {"GS": "GS", "PA": "PA", "HR": "HR", "AVG": "AVG", "wRC+": "wRC+",
                                              "xwOBA": "xwOBA", "OAA": "OAA", "fWAR": "fWAR",
                                              "fWAR/700": "fWAR/700 PA"}[stat_of(c)]) for c in cols}
    formats = {c: FORMATS[stat_of(c)] for c in cols if c != "Player"}
    groups = {}
    for c in cols[1:]:
        if c.startswith(span):
            groups[c] = f"{span.replace('-', '–')} (LAST 3 YEARS)"
        elif " INN " in c:
            groups[c] = f"{season} INNINGS BY POSITION"
        else:
            groups[c] = f"{season} (PLATFORM YEAR)"
    tips = {c: "fWAR per 700 plate appearances = fWAR ÷ PA × 700" for c in cols if c.endswith("fWAR/700")}
    tips.update({c: f"Innings played at {c.split(' ')[-1]}" for c in cols if " INN " in c})
    tips.update({c: f"{first}–{season} combined" for c in cols if c.startswith(span)})
    return df, cols, labels, formats, groups, tips, problems
