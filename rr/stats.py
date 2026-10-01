"""Plain titles, groups and display formats for the statistics in the roster data.

What the raw columns are (checked against known real stat lines):
    actual_*   this season            actualz_*  last season
    proj_*     rest-of-season projection
    prht/prsp/prrp   power rankings (hitters / starters / relievers), with 7- or 14-day
                     and first-/second-half versions
Batting stats carry "bat_", pitching stats "pit_".
"""
from __future__ import annotations

import re

import pandas as pd

# stat -> (title, format). Formats: int, dec1, dec2, avg3 (.280), pct1 (25.8%)
STAT_NAMES = {
    "PT": ("PLAYING TIME (PA/IP)", "dec1"), "WAR": ("WAR", "dec1"),
    "PA": ("PA", "int"), "HR": ("HR", "int"), "SB": ("SB", "int"),
    "AVG": ("AVG", "avg3"), "OBP": ("OBP", "avg3"), "SLG": ("SLG", "avg3"), "OPS": ("OPS", "avg3"),
    "WRC+": ("wRC+", "int"), "K%": ("K%", "pct1"), "BB%": ("BB%", "pct1"),
    "EVENTS": ("BATTED BALL EVENTS", "int"), "EV": ("EXIT VELO", "dec1"),
    "BARREL%": ("BARREL%", "pct1"), "HARDHIT%": ("HARD-HIT%", "pct1"),
    "GS": ("GS", "int"), "SV": ("SV", "int"), "H": ("H", "int"), "SO": ("SO", "int"), "BB": ("BB", "int"),
    "ERA": ("ERA", "dec2"), "IP": ("IP", "dec1"), "BB/9": ("BB/9", "dec2"), "K/9": ("K/9", "dec2"),
    "PIVFA": ("FASTBALL VELO", "dec1"), "PIFA%": ("FASTBALL %", "pct1"), "PISL%": ("SLIDER %", "pct1"),
    "PIFC%": ("CUTTER %", "pct1"), "PICU%": ("CURVE %", "pct1"), "PICH%": ("CHANGEUP %", "pct1"),
}
BOTH_SIDES = {"WAR", "K%", "BB%", "EVENTS", "EV", "BARREL%", "HARDHIT%", "PIVFA", "PIFA%", "PISL%",
              "PIFC%", "PICU%", "PICH%"}  # stats that exist for hitters and pitchers: say which
BATTER_NOTE = {"EV": "EXIT VELO", "PIVFA": "FASTBALL VELO SEEN", "PIFA%": "FASTBALLS SEEN %",
               "PISL%": "SLIDERS SEEN %", "PIFC%": "CUTTERS SEEN %", "PICU%": "CURVES SEEN %", "PICH%": "CHANGEUPS SEEN %"}
PITCHER_NOTE = {"EV": "EXIT VELO ALLOWED", "BARREL%": "BARREL% ALLOWED", "HARDHIT%": "HARD-HIT% ALLOWED",
                "EVENTS": "BATTED BALL EVENTS ALLOWED"}

RANKS = {
    "prht": "POWER RANK: HITTER", "prht7": "POWER RANK: HITTER, LAST 7 DAYS",
    "prht1h": "POWER RANK: HITTER, 1ST HALF", "prht2h": "POWER RANK: HITTER, 2ND HALF",
    "prsp": "POWER RANK: STARTER", "prsp14": "POWER RANK: STARTER, LAST 14 DAYS",
    "prsp1h": "POWER RANK: STARTER, 1ST HALF", "prsp2h": "POWER RANK: STARTER, 2ND HALF",
    "prrp": "POWER RANK: RELIEVER", "prrp14": "POWER RANK: RELIEVER, LAST 14 DAYS",
    "prrp1h": "POWER RANK: RELIEVER, 1ST HALF", "prrp2h": "POWER RANK: RELIEVER, 2ND HALF",
}
_STAT_RE = re.compile(r"^(actualz|actual|proj)_(?:(bat|pit)_)?(.+?)(\d*)$", re.I)
_MINORS_RE = re.compile(r"^(AAA|AA|A|Ap|Rk2|Rk|SS)_(PA|IP|SP_IP|RP_IP|Hit_Pts|P_Pts|SP_Pts|RP_Pts|Hit_Rank|P_Rank|SP_Rank|RP_Rank)$")
_MINOR_LEVEL = {"AAA": "AAA", "AA": "AA", "A": "HIGH-A", "Ap": "SINGLE-A", "Rk": "ROOKIE", "Rk2": "ROOKIE (2)", "SS": "SHORT SEASON"}


def describe(col: str, season: int) -> dict | None:
    """-> {"label", "group", "format", "dup"} for a statistics column, else None."""
    m = _STAT_RE.match(col)
    if m:
        when_key, side, stat, digits = m.group(1).lower(), (m.group(2) or "").lower(), m.group(3).upper(), m.group(4)
        if (stat + digits) in STAT_NAMES:  # e.g. "BB/9": the 9 is part of the name, not a second copy
            stat, digits = stat + digits, ""
        info = STAT_NAMES.get(stat)
        if info is None:
            return None
        title, fmt = info
        if side == "bat" and stat in BATTER_NOTE:
            title = BATTER_NOTE[stat]
        elif side == "pit" and stat in PITCHER_NOTE:
            title = PITCHER_NOTE[stat]
        elif stat in BOTH_SIDES and side:
            title = f"{title} ({'BAT' if side == 'bat' else 'PITCH'})"
        when = {"actual": str(season), "actualz": str(season - 1), "proj": "ROS PROJ"}[when_key]
        kind = {"bat": "BATTING", "pit": "PITCHING", "": "OVERALL"}[side]
        group = {"actual": f"{season} STATS", "actualz": f"{season - 1} STATS",
                 "proj": "REST-OF-SEASON PROJECTIONS"}[when_key] + f": {kind}"
        label = f"{when} {title}" + (" (ALTERNATE)" if digits else "")  # a second copy computed differently
        return {"label": label, "group": group, "format": fmt, "dup": bool(digits)}
    if col in ("PA", "IP"):  # this season's totals, kept outside the batting / pitching sets
        return {"label": f"{season} {col}" + (" (ALTERNATE)" if col == "PA" else " (EXACT)"),
                "group": f"{season} STATS: OVERALL", "format": "int" if col == "PA" else "dec1", "dup": col == "PA"}
    if col.lower() in RANKS:
        return {"label": RANKS[col.lower()], "group": "POWER RANKINGS", "format": "int", "dup": False}
    mm = _MINORS_RE.match(col)
    if mm:
        level, what = _MINOR_LEVEL[mm.group(1)], mm.group(2).replace("_", " ").upper().replace("HIT PTS", "HITTER POINTS").replace("P PTS", "PITCHER POINTS")
        return {"label": f"{level} {what}", "group": "MINOR LEAGUES", "format": "int" if "RANK" in what or "PA" in what else "dec1", "dup": False}
    if col in ("Overall_Rank", "Overall_7_Rank", "Overall_14_Rank", "Overall_21_Rank", "Overall_1H_Rank",
               "Overall_2H_Rank", "Org_Rank", "Org_Rank_Next", "Ovr_Rank", "Ovr_Rank_Next", "Pts", "P_Pts", "RP_Pts",
               "Hit_Pts", "IP_minor", "PA1"):
        return {"label": col.replace("_", " ").upper().replace("OVR", "OVERALL"), "group": "MINOR LEAGUES",
                "format": "int" if "RANK" in col.upper() else "dec1", "dup": False}
    return None


GROUP_ORDER = ["{s} STATS: OVERALL", "{s} STATS: BATTING", "{s} STATS: PITCHING",
               "{p} STATS: OVERALL", "{p} STATS: BATTING", "{p} STATS: PITCHING",
               "REST-OF-SEASON PROJECTIONS: OVERALL", "REST-OF-SEASON PROJECTIONS: BATTING",
               "REST-OF-SEASON PROJECTIONS: PITCHING", "POWER RANKINGS", "MINOR LEAGUES"]


def catalog(columns: list[str], season: int) -> tuple[dict, dict, dict, list[str]]:
    """-> (labels, groups, formats, duplicate columns) for the statistics among `columns`."""
    labels, groups, formats, dups = {}, {}, {}, []
    for c in columns:
        d = describe(c, season)
        if d is None:
            continue
        labels[c], groups[c], formats[c] = d["label"], d["group"], d["format"]
        if d["dup"]:
            dups.append(c)
    return labels, groups, formats, dups


def group_order(season: int) -> list[str]:
    return [g.format(s=season, p=season - 1) for g in GROUP_ORDER]


def blank_empty_batting(body: pd.DataFrame) -> pd.DataFrame:
    """Pitchers carry batting lines of zeros and a wRC+ of -100 (meaning no plate appearances).
    Show those as blank rather than as real zeros."""
    body = body.copy()
    for prefix in ("actual_bat_", "actualz_bat_", "proj_bat_"):
        cols = [c for c in body.columns if c.startswith(prefix)]
        if not cols:
            continue
        wrc = next((c for c in cols if c.lower().endswith("wrc+")), None)
        pa = next((c for c in cols if c.lower().endswith("_pa")), None)
        empty = pd.Series(False, index=body.index)
        if wrc is not None:
            empty |= pd.to_numeric(body[wrc], errors="coerce").eq(-100)
        if pa is not None:
            empty |= pd.to_numeric(body[pa], errors="coerce").eq(0)
        if empty.any():
            body.loc[empty, cols] = None
    return body
