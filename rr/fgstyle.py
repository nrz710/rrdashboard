"""FanGraphs / RosterResource look, in one place.

What was checked against the live site (www.fangraphs.com/roster-resource,
Sept 2026): the brand green (#50ae26, the site's theme-color), the tab names
and order, the AL/NL team-abbreviation strip, upper-case column headers,
group header rows such as "ORIGINAL SIGNING INFO", section titles with a
collapse arrow, IL statuses like "60IL", and the legend entry
"Acquired since end of <last> season" (shown as a darker green row).

What is approximate: exact greys, font and sizes. I could read the page's
text but not its stylesheet, so those are set as tokens below. Open a real
RosterResource page next to the dashboard and adjust TOKENS if you want a
pixel match; everything (dashboard, HTML report, Excel) reads from here.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

import pandas as pd

from . import config

TOKENS = {
    "green": "#50ae26",          # FanGraphs brand green (verified)
    "green_dark": "#3f8a1e",
    "link": "#2e7d0f",
    "text": "#000000",
    "muted": "#666666",
    "header_bg": "#e6e6e6",      # column header row
    "group_bg": "#f4f4f4",       # "ORIGINAL SIGNING INFO" style row
    "border": "#c8c8c8",
    "row_alt": "#f7f7f7",
    "acquired_bg": "#cfe8c1",    # "Acquired since end of last season"
    "il_bg": "#f6d3d3",          # injured list rows
    "font": 'Arial, "Helvetica Neue", Helvetica, sans-serif',
    "font_size": "12px",
}

# Header labels: RosterResource shows short upper-case headers.
HEADER_LABELS = {
    "playername": "PLAYER", "name": "PLAYER", "player": "PLAYER", "fullname": "PLAYER",
    "pos": "POS", "position": "POS", "age": "AGE", "bats": "BATS", "throws": "THR", "thr": "THR",
    "howacquired": "HOW ACQUIRED", "servicetime": "MLB SERVICE TIME", "options": "OPTIONS",
    "injury": "INJURY/SURGERY", "status": "STATUS", "role": "ROLE", "team": "TEAM",
    "transaction": "TRANSACTION/UPDATE", "date": "DATE", "contract": "CONTRACT",
}

# Column groups drawn above the headers when these columns appear side by side.
COLUMN_GROUPS = {
    "ORIGINAL SIGNING INFO": {"year", "signingteam", "rd", "pick"},
    "PROSPECT RANK": {"ovrrank", "orgrank"},
    "POWER RANK": {"ovr", "last7days", "last14days", "last21days"},
}

_ID_COLS = {"playerid", "fgid", "fangraphsid", "idfg", "fgplayerid", "mlbamid", "xmlbamid", "mlbid",
            "keymlbam", "mlbamplayerid"}
_NAME_COLS = ["playername", "fullname", "playerfullname", "player", "name"]
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                       "sep", "oct", "nov", "dec"], 1)}
_ACQ_RE = re.compile(r"\b([A-Za-z]{3})'(\d{2})\b")
_IL_RE = re.compile(r"^\s*(\d{1,2}\s?IL|\d{1,2}-day IL|IL)\s*$", re.I)
_MONEY_COLS = re.compile(r"salary|aav|payroll|luxury|cbt|guarantee", re.I)


def norm(col) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).split(".")[-1].lower())


def header_label(col) -> str:
    n = norm(col)
    if n in HEADER_LABELS:
        return HEADER_LABELS[n]
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(col).split(".")[-1]).replace("_", " ")
    return s.upper()


# ----------------------------------------------------------------- legend ---
def _acquired_recently(value, season: int) -> bool:
    if not isinstance(value, str):
        return False
    m = _ACQ_RE.search(value)
    if not m or m.group(1).lower() not in _MONTHS:
        return False
    month, yy = _MONTHS[m.group(1).lower()], int(m.group(2))
    return yy == season % 100 or (yy == (season - 1) % 100 and month >= 11)


def row_class(row: pd.Series, cols_by_norm: dict[str, str], season: int) -> str:
    for c in ("status", "ilstatus"):
        if c in cols_by_norm and isinstance(row[cols_by_norm[c]], str) and _IL_RE.match(row[cols_by_norm[c]]):
            return "fg-il"
    if "howacquired" in cols_by_norm and _acquired_recently(row[cols_by_norm["howacquired"]], season):
        return "fg-acq"
    return ""


def legend_html(classes: set[str], season: int) -> str:
    items = []
    if "fg-acq" in classes:
        items.append(("fg-acq", f"Acquired since end of {season - 1} season"))
    if "fg-il" in classes:
        items.append(("fg-il", "Injured list"))
    if not items:
        return ""
    chips = "".join(f"<span class='fg-chip {c}'>{html.escape(t)}</span>" for c, t in items)
    return f"<div class='fg-legend'><b>Legend:</b>{chips}</div>"


# ------------------------------------------------------------------ cells ---
def _fmt(value, col: str) -> tuple[str, bool]:
    """-> (html text, is_numeric)"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "", False
    if isinstance(value, bool):
        return ("Y" if value else "N"), False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        money = bool(_MONEY_COLS.search(str(col)))
        if float(value).is_integer():
            v = int(value)
            txt = f"${v:,}" if money else (f"{v:,}" if abs(v) >= 10000 else str(v))
        else:
            txt = f"${value:,.2f}" if money else f"{value:.3f}".rstrip("0").rstrip(".")
        return txt, True
    return html.escape(str(value)), False


def table_html(df: pd.DataFrame, *, max_rows: int = 1000, hide_ids: bool = True,
               link_players: bool = True, season: int | None = None, legend: bool = True) -> str:
    season = season or config.current_season()
    if df is None or df.empty:
        return "<div class='fg-empty'>No rows.</div>"
    cols_by_norm = {norm(c): c for c in df.columns}
    name_col = next((cols_by_norm[n] for n in _NAME_COLS if n in cols_by_norm), None)
    fg_id_col = next((cols_by_norm[n] for n in ("playerid", "fgid", "fangraphsid", "idfg", "fgplayerid")
                      if n in cols_by_norm), None)
    shown = [c for c in df.columns if not (hide_ids and name_col and norm(c) in _ID_COLS)]

    # group header row
    groups, i = [], 0
    while i < len(shown):
        g = next((label for label, members in COLUMN_GROUPS.items() if norm(shown[i]) in members), None)
        span = 1
        while g and i + span < len(shown) and norm(shown[i + span]) in COLUMN_GROUPS[g]:
            span += 1
        groups.append((g if g and span > 1 else "", span if g and span > 1 else 1))
        i += span
    group_row = ""
    if any(g for g, _ in groups):
        group_row = "<tr class='fg-group'>" + "".join(
            f"<th colspan='{n}'>{html.escape(g)}</th>" for g, n in groups) + "</tr>"

    head = "<tr>" + "".join(f"<th title='{html.escape(str(c))}'>{html.escape(header_label(c))}</th>"
                            for c in shown) + "</tr>"
    body, seen = [], set()
    for _, row in df.head(max_rows).iterrows():
        cls = row_class(row, cols_by_norm, season)
        seen.add(cls)
        cells = []
        for c in shown:
            txt, is_num = _fmt(row[c], c)
            if c == name_col and link_players and fg_id_col is not None and txt:
                pid = row[fg_id_col]
                if pid is not None and not (isinstance(pid, float) and pd.isna(pid)):
                    pid = str(pid).removesuffix(".0")
                    txt = (f"<a href='https://www.fangraphs.com/statss.aspx?playerid={html.escape(pid)}' "
                           f"target='_blank' rel='noopener'>{txt}</a>")
            if norm(c) == "team" and txt in config.NAME_TO_ABBR:
                txt = f"<span title='{txt}'>{config.NAME_TO_ABBR[txt]}</span>"
            cells.append(f"<td class='{'num' if is_num else ''}'>{txt}</td>")
        body.append(f"<tr class='{cls}'>" + "".join(cells) + "</tr>")
    more = (f"<div class='fg-more'>Showing {max_rows:,} of {len(df):,} rows. Exports include every row.</div>"
            if len(df) > max_rows else "")
    lg = legend_html(seen - {""}, season) if legend else ""
    return (f"{lg}<div class='fg-wrap'><table class='fg-table'><thead>{group_row}{head}</thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>{more}")


def section_html(title: str, subtitle: str = "") -> str:
    sub = f"<span class='fg-section-sub'>{html.escape(subtitle)}</span>" if subtitle else ""
    return f"<div class='fg-section'><span>{html.escape(title)} ▼</span>{sub}</div>"


def page_title_html(title: str, note: str = "") -> str:
    n = f"<div class='fg-page-note'>{html.escape(note)}</div>" if note else ""
    return (f"<div class='fg-brandbar'><span class='fg-logo'>FanGraphs</span>"
            f"<span class='fg-brand-sub'>RosterResource tools</span></div>"
            f"<h1 class='fg-h1'>{html.escape(title)}</h1>{n}")


# -------------------------------------------------------------------- CSS ---
def css(scope: str = "") -> str:
    t = TOKENS
    s = scope
    return f"""
{s} .fg-table {{ border-collapse: collapse; width: 100%; font-family: {t['font']}; font-size: {t['font_size']};
  color: {t['text']}; font-variant-numeric: tabular-nums; }}
{s} .fg-table th {{ background: {t['header_bg']}; border: 1px solid {t['border']}; padding: 4px 6px;
  font-weight: 700; text-align: center; white-space: nowrap; position: sticky; top: 0; z-index: 1; }}
{s} .fg-table tr.fg-group th {{ background: {t['group_bg']}; font-size: 11px; color: #333; top: 0; }}
{s} .fg-table tr.fg-group + tr th {{ top: 22px; }}
{s} .fg-table td {{ border: 1px solid {t['border']}; padding: 3px 6px; white-space: nowrap; text-align: left; }}
{s} .fg-table td.num {{ text-align: center; }}
{s} .fg-table tbody tr:nth-child(even) td {{ background: {t['row_alt']}; }}
{s} .fg-table tbody tr.fg-acq td {{ background: {t['acquired_bg']}; }}
{s} .fg-table tbody tr.fg-il td {{ background: {t['il_bg']}; }}
{s} .fg-table tbody tr:hover td {{ background: #eef6e9; }}
{s} .fg-table a {{ color: {t['link']}; text-decoration: none; font-weight: 700; }}
{s} .fg-table a:hover {{ text-decoration: underline; }}
{s} .fg-wrap {{ overflow: auto; max-height: 520px; border: 1px solid {t['border']}; background: #fff; }}
{s} .fg-more, {s} .fg-empty {{ font: 11px {t['font']}; color: {t['muted']}; padding: 4px 0; }}
{s} .fg-legend {{ font: 11px {t['font']}; margin: 2px 0 6px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
{s} .fg-chip {{ border: 1px solid {t['border']}; padding: 1px 8px; }}
{s} .fg-chip.fg-acq {{ background: {t['acquired_bg']}; }}
{s} .fg-chip.fg-il {{ background: {t['il_bg']}; }}
{s} .fg-section {{ background: {t['green']}; color: #fff; font: 700 13px {t['font']}; text-transform: uppercase;
  padding: 5px 10px; margin: 0 0 6px; display: flex; justify-content: space-between; align-items: baseline;
  letter-spacing: .02em; }}
{s} .fg-section-sub {{ font-weight: 400; font-size: 11px; text-transform: none; opacity: .92; }}
{s} .fg-brandbar {{ display: flex; align-items: baseline; gap: 12px; border-bottom: 3px solid {t['green']};
  padding: 0 0 6px; margin: 0 0 10px; }}
{s} .fg-logo {{ font: 900 26px {t['font']}; color: {t['green']}; letter-spacing: -.02em; }}
{s} .fg-brand-sub {{ font: 12px {t['font']}; color: {t['muted']}; }}
{s} .fg-h1 {{ font: 700 22px {t['font']}; margin: 0 0 4px; color: {t['text']}; }}
{s} .fg-page-note {{ font: 12px {t['font']}; color: {t['muted']}; margin-bottom: 8px; }}
{s} .fg-player-name {{ font: 700 24px {t['font']}; margin: 4px 0 0; }}
{s} .fg-player-meta {{ font: 13px {t['font']}; color: {t['muted']}; margin: 0 0 10px; }}
{s} .fg-src {{ font: 11px {t['font']}; color: {t['muted']}; margin: 4px 0 14px; }}
"""


# Streamlit chrome restyled to resemble the RosterResource tab strip and controls.
STREAMLIT_CSS = f"""
<style>
{css()}
.stMarkdown p, label p, .stCaption, input, textarea, [data-testid="stTab"] p,
button[data-variant="pills"], .stButton button p, .stDownloadButton button p {{ font-family: {TOKENS['font']}; }}
.block-container {{ padding-top: 1.2rem; max-width: 1400px; }}
/* Tab strip, like RosterResource's grey tabs with the active one in green */
[data-testid="stTabs"] [role="tablist"], div[data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 3px solid {TOKENS['green']}; }}
[data-testid="stTab"], button[data-baseweb="tab"] {{ background: #eeeeee; border: 1px solid {TOKENS['border']};
  border-bottom: none; padding: 6px 14px !important; border-radius: 0; }}
[data-testid="stTab"] p, button[data-baseweb="tab"] p {{ font-size: 13px; font-weight: 700; color: #333; }}
[data-testid="stTab"][aria-selected="true"], button[data-baseweb="tab"][aria-selected="true"] {{
  background: {TOKENS['green']}; border-color: {TOKENS['green']}; }}
[data-testid="stTab"][aria-selected="true"] p, button[data-baseweb="tab"][aria-selected="true"] p {{ color: #fff; }}
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] {{ display: none; }}
/* Page and team selectors as square RosterResource-style buttons */
button[data-variant="pills"] {{ border-radius: 0; min-height: 26px; padding: 2px 9px; font-size: 12px; font-weight: 700;
  border: 1px solid {TOKENS['border']}; background: #f4f4f4; color: #222; }}
button[data-variant="pills"][aria-pressed="true"], button[data-variant="pills"][aria-checked="true"],
button[data-variant="pills"][data-selected="true"] {{ background: {TOKENS['green']} !important;
  border-color: {TOKENS['green']} !important; color: #fff !important; }}
button[data-variant="pills"][aria-pressed="true"] *, button[data-variant="pills"][aria-checked="true"] *,
button[data-variant="pills"][data-selected="true"] * {{ color: #fff !important; }}
[data-testid="stButtonGroup"] [role="toolbar"] {{ gap: 2px; }}
div[data-testid="stExpander"] details {{ border-radius: 0; border-color: {TOKENS['border']}; }}
div[data-testid="stExpander"] summary {{ background: {TOKENS['green']}; }}
div[data-testid="stExpander"] summary p {{ font-weight: 700; text-transform: uppercase; font-size: 13px; color: #fff; }}
div[data-testid="stExpander"] summary svg {{ color: #fff; fill: #fff; }}
.stButton button, .stDownloadButton button {{ border-radius: 2px; }}
section[data-testid="stSidebar"] {{ border-right: 3px solid {TOKENS['green']}; }}
</style>
"""


def report_css() -> str:
    return css() + f"""
body {{ font-family: {TOKENS['font']}; max-width: 1200px; margin: 24px auto; padding: 0 16px; color: #000; }}
.fg-wrap {{ max-height: none; }}
.fg-table th {{ position: static; }}
@media print {{ body {{ margin: 0; max-width: none; }} .fg-block {{ break-inside: avoid-page; }}
  .fg-table th, .fg-section, .fg-table td {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }} }}
"""


def exported_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
