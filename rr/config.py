"""Settings for the RosterResource dashboard.

Anything that affects how often or how hard we hit FanGraphs lives here,
but the 48-hour floor itself is hard-coded in gate.py and cannot be lowered
from this file.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

BASE_URL = "https://www.fangraphs.com/roster-resource"

# Display name -> URL slug
TEAMS: dict[str, str] = {
    "Athletics": "athletics",
    "Orioles": "orioles",
    "Red Sox": "red-sox",
    "White Sox": "white-sox",
    "Cleveland": "guardians",
    "Detroit": "tigers",
    "Astros": "astros",
    "Royals": "royals",
    "Angels": "angels",
    "Twins": "twins",
    "Yankees": "yankees",
    "Seattle": "mariners",
    "Tampa Bay": "rays",
    "Rangers": "rangers",
    "Toronto": "blue-jays",
    "Arizona": "diamondbacks",
    "Atlanta": "braves",
    "Cubs": "cubs",
    "Cincinnati": "reds",
    "Colorado": "rockies",
    "Dodgers": "dodgers",
    "Miami": "marlins",
    "Milwaukee": "brewers",
    "Mets": "mets",
    "Philadelphia": "phillies",
    "Pittsburgh": "pirates",
    "San Diego": "padres",
    "San Francisco": "giants",
    "St. Louis": "cardinals",
    "Washington": "nationals",
}
SLUG_TO_TEAM = {slug: name for name, slug in TEAMS.items()}

# Abbreviations and league, as shown in RosterResource's team strip.
TEAM_ABBR: dict[str, str] = {
    "athletics": "ATH", "orioles": "BAL", "red-sox": "BOS", "white-sox": "CHW", "guardians": "CLE",
    "tigers": "DET", "astros": "HOU", "royals": "KCR", "angels": "LAA", "twins": "MIN",
    "yankees": "NYY", "mariners": "SEA", "rays": "TBR", "rangers": "TEX", "blue-jays": "TOR",
    "diamondbacks": "ARI", "braves": "ATL", "cubs": "CHC", "reds": "CIN", "rockies": "COL",
    "dodgers": "LAD", "marlins": "MIA", "brewers": "MIL", "mets": "NYM", "phillies": "PHI",
    "pirates": "PIT", "padres": "SDP", "giants": "SFG", "cardinals": "STL", "nationals": "WSN",
}
AL = ["athletics", "orioles", "red-sox", "white-sox", "guardians", "tigers", "astros", "royals",
      "angels", "twins", "yankees", "mariners", "rays", "rangers", "blue-jays"]
NL = [s for s in TEAM_ABBR if s not in AL]
ABBR_TO_SLUG = {a: s for s, a in TEAM_ABBR.items()}
NAME_TO_ABBR = {name: TEAM_ABBR[slug] for name, slug in TEAMS.items()}

# Pages that take a team slug: URL = {BASE_URL}/{key}/{slug}
# Labels follow RosterResource's own tab names, in RosterResource's order.
PAGE_LABELS: dict[str, str] = {
    "depth-charts": "Depth Charts",
    "payroll": "Payroll",
    "transaction-tracker": "Transaction Tracker",
    "injury-report": "Injury Report",
    "closer-depth-chart": "Closer Depth Chart",
    "lineup-tracker": "In-Season Tools",
    "breakdowns/active-roster": "Breakdowns: 26-Man Roster",
    "breakdowns/40-man-roster": "Breakdowns: 40-Man Roster",
    "breakdowns/coaches": "Breakdowns: Coaches",
    "free-agent-tracker": "Free Agent Tracker",
}

# Fewer requests ("lever 1"): these tools have a league-wide page that lists every team
# (RosterResource's "Show All MLB Teams" view), so each is fetched ONCE instead of 30
# times. That takes a pull from 184 requests to 97 without changing the pacing or the
# 48-hour limit. To go back to per-team pulls for any of them, remove it from this set.
LEAGUE_WIDE_INSTEAD_OF_PER_TEAM: set[str] = {"injury-report", "transaction-tracker", "closer-depth-chart"}

_PER_TEAM_TOOLS = ["depth-charts", "payroll", "transaction-tracker", "injury-report",
                   "closer-depth-chart", "lineup-tracker"]

# Pages fetched once per team: URL = {BASE_URL}/{key}/{slug}
TEAM_PAGES: dict[str, str] = {k: PAGE_LABELS[k] for k in _PER_TEAM_TOOLS
                              if k not in LEAGUE_WIDE_INSTEAD_OF_PER_TEAM}

# Pages fetched once: URL = {BASE_URL}/{key}
LEAGUE_PAGES: dict[str, str] = {k: PAGE_LABELS[k] for k in PAGE_LABELS if k not in TEAM_PAGES}

# Tools whose data is per team, however they're fetched. The dashboard's All Teams tab and
# team strip cover these; for league-wide fetches it filters on the page's own team column.
TEAM_TOOLS: list[str] = list(_PER_TEAM_TOOLS)

ALL_PAGES = dict(PAGE_LABELS)

# --- Politeness -----------------------------------------------------------

# None = send the HTTP library's own default User-Agent (python-requests/x.y).
# If you set one, it must be an honest, non-browser string, e.g.
#   "rr-dashboard/0.1 (personal research; you@example.com)"
# fetch.py refuses to send anything that looks like a browser.
USER_AGENT: str | None = None

# Gap between individual page requests within one refresh.
REQUEST_DELAY_SECONDS = 6.0
REQUEST_JITTER_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 30

# Abort a refresh after this many page failures in a row (no retries, ever).
MAX_CONSECUTIVE_FAILURES = 3

# Refresh interval in hours. Values below 48 are ignored (gate.py enforces 48).
MIN_REFRESH_INTERVAL_HOURS = 48

# For manual backfills only: page key -> query parameter that selects a season.
# Deliberately empty: I have not verified which RosterResource pages accept a
# season parameter, and guessing would mean firing requests at URLs that may
# not exist. Fill in only after confirming in a browser, e.g.
#   {"free-agent-tracker": "season"}
SEASON_PARAM: dict[str, str] = {}

# --- Storage --------------------------------------------------------------

# The table each tool opens on in the dashboard (matched against the end of the table name).
PREFERRED_TABLES: dict[str, str] = {
    "depth-charts": "dataRoster",
    "payroll": "dataContract.contractSummary",
    "lineup-tracker": "depthChartsData",
    "closer-depth-chart": "dataPlayers",
    "injury-report": "injury-report",
    "transaction-tracker": "transaction-tracker",
}

# Retention. Everything lives in ./data locally and on the repo's `data` branch on GitHub,
# which is replaced by a single commit each time, so git history never grows.
RAW_RETENTION = 2         # raw page snapshots kept (enough to re-parse with `rr reingest`)
PUBLISHED_RETENTION = 8   # pre-parsed snapshots the dashboard can choose from

DATA_DIR = Path(os.environ.get("RR_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
STATE_DIR = DATA_DIR / "state"


# Team names as they may appear inside RosterResource data -> our slug.
_TEAM_ALIASES = {
    "athletics": ["ATH", "OAK", "A's", "Oakland Athletics", "Athletics"],
    "orioles": ["BAL", "Baltimore Orioles", "Orioles"], "red-sox": ["BOS", "Boston Red Sox", "Red Sox"],
    "white-sox": ["CHW", "CWS", "Chicago White Sox", "White Sox"], "guardians": ["CLE", "Cleveland Guardians", "Guardians"],
    "tigers": ["DET", "Detroit Tigers", "Tigers"], "astros": ["HOU", "Houston Astros", "Astros"],
    "royals": ["KCR", "KC", "Kansas City Royals", "Royals"], "angels": ["LAA", "Los Angeles Angels", "Angels"],
    "twins": ["MIN", "Minnesota Twins", "Twins"], "yankees": ["NYY", "New York Yankees", "Yankees"],
    "mariners": ["SEA", "Seattle Mariners", "Mariners"], "rays": ["TBR", "TB", "Tampa Bay Rays", "Rays"],
    "rangers": ["TEX", "Texas Rangers", "Rangers"], "blue-jays": ["TOR", "Toronto Blue Jays", "Blue Jays"],
    "diamondbacks": ["ARI", "AZ", "Arizona Diamondbacks", "Diamondbacks", "D-backs"],
    "braves": ["ATL", "Atlanta Braves", "Braves"], "cubs": ["CHC", "Chicago Cubs", "Cubs"],
    "reds": ["CIN", "Cincinnati Reds", "Reds"], "rockies": ["COL", "Colorado Rockies", "Rockies"],
    "dodgers": ["LAD", "Los Angeles Dodgers", "Dodgers"], "marlins": ["MIA", "Miami Marlins", "Marlins"],
    "brewers": ["MIL", "Milwaukee Brewers", "Brewers"], "mets": ["NYM", "New York Mets", "Mets"],
    "phillies": ["PHI", "Philadelphia Phillies", "Phillies"], "pirates": ["PIT", "Pittsburgh Pirates", "Pirates"],
    "padres": ["SDP", "SD", "San Diego Padres", "Padres"], "giants": ["SFG", "SF", "San Francisco Giants", "Giants"],
    "cardinals": ["STL", "St. Louis Cardinals", "Cardinals"], "nationals": ["WSN", "WSH", "Washington Nationals", "Nationals"],
}
_ALIAS_TO_SLUG = {a.lower(): slug for slug, names in _TEAM_ALIASES.items() for a in names}
_ALIAS_TO_SLUG.update({slug: slug for slug in _TEAM_ALIASES})
_ALIAS_TO_SLUG.update({name.lower(): slug for name, slug in TEAMS.items()})


# FanGraphs' numeric TeamId, as listed in RosterResource's own team list (dataTeamList).
FG_TEAM_ID: dict[int, str] = {
    1: "angels", 2: "orioles", 3: "red-sox", 4: "white-sox", 5: "guardians", 6: "tigers", 7: "royals",
    8: "twins", 9: "yankees", 10: "athletics", 11: "mariners", 12: "rays", 13: "rangers", 14: "blue-jays",
    15: "diamondbacks", 16: "braves", 17: "cubs", 18: "reds", 19: "rockies", 20: "marlins", 21: "astros",
    22: "dodgers", 23: "brewers", 24: "nationals", 25: "mets", 26: "phillies", 27: "pirates",
    28: "cardinals", 29: "padres", 30: "giants",
}


def team_slug_of(value, numeric: bool = False) -> str | None:
    """Map a team as written in the data (abbreviation, name, slug; or, with numeric=True,
    FanGraphs' TeamId) to our slug."""
    if numeric:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return FG_TEAM_ID.get(int(f)) if f == f and float(int(f)) == f else None
    if not isinstance(value, str):
        return None
    return _ALIAS_TO_SLUG.get(value.strip().lower())


def current_season() -> int:
    return datetime.now().year


def page_url(page_key: str, team_slug: str | None) -> str:
    if page_key in TEAM_PAGES:
        if not team_slug:
            raise ValueError(f"{page_key} needs a team slug")
        return f"{BASE_URL}/{page_key}/{team_slug}"
    if page_key in LEAGUE_PAGES:
        return f"{BASE_URL}/{page_key}"
    raise KeyError(page_key)
