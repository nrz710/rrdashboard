"""RosterResource dashboard.  Run:  streamlit run dashboard.py

Read-only with respect to FanGraphs: this app never contacts fangraphs.com.
Hosted, it reads the pre-parsed files on the repo's `data` branch from GitHub (only the
files a view needs). Locally, it reads the same files from ./data.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rr import config, export, fgstyle, players, sources, views  # noqa: E402
from rr.gate import GateClosed, _fmt  # noqa: E402

st.set_page_config(page_title="RosterResource dashboard", page_icon="⚾", layout="wide")
st.html(fgstyle.STREAMLIT_CSS)


# ------------------------------------------------------------ data source ---
def _setting(key: str, default=None):
    env = os.environ.get(f"RR_DASHBOARD_{key.upper()}")
    if env:
        return env
    try:
        return st.secrets.get("rr", {}).get(key, default)
    except Exception:  # no secrets file
        return default


@st.cache_resource(show_spinner="Preparing sample data…")
def get_source(repo: str | None, branch: str, token: str | None, demo: bool):
    if demo:  # fictional sample data, generated on the server; lets you try the app before the first pull
        import tempfile

        import make_demo_data
        root = Path(tempfile.mkdtemp(prefix="rr_demo_"))
        make_demo_data.main(root)
        return sources.local_source(root)
    if repo:
        return sources.PublishedSource(sources.GitHubReader(repo, branch, token))
    return sources.local_source(config.DATA_DIR)


DEMO = str(_setting("demo", "")).strip().lower() in ("1", "true", "yes")
SRC = get_source(_setting("github_repo"), _setting("data_branch", "data"), _setting("github_token"), DEMO)


@st.cache_data(ttl=600, show_spinner=False)
def _snapshots(_kind: str) -> list[dict]:
    return SRC.snapshots()


def team_name(slug):
    return config.SLUG_TO_TEAM.get(slug, slug) if slug else "League-wide"


def page_name(key):
    return config.ALL_PAGES.get(key, key)


def snap_label(m: dict) -> str:
    started = datetime.fromisoformat(m["started_at"]).astimezone().strftime("%b %d, %Y %H:%M")
    return f"{started} ({m['status']}, {m.get('ok', 0)}/{m['planned']} pages)"


def show(df: pd.DataFrame | None, **kw):
    st.html(fgstyle.table_html(df, **kw))


def add_panel(panel: dict):
    st.session_state.panels.append(panel)
    st.toast(f"Added “{panel['title']}” to the dashboard")


def team_strip(key: str, available: list[str]) -> list[str]:
    """RosterResource-style AL / NL abbreviation strip. Nothing selected = all teams."""
    picked = []
    for label, slugs in (("American League", config.AL), ("National League", config.NL)):
        opts = [config.TEAM_ABBR[s] for s in slugs if s in available]
        if opts:
            picked += st.pills(label, opts, selection_mode="multi", key=f"{key}::{label}") or []
    return [config.ABBR_TO_SLUG[a] for a in picked]


if "panels" not in st.session_state:
    st.session_state.panels = []
if "combo_fields" not in st.session_state:
    st.session_state.combo_fields = []  # base list; the editor's live edits sit on top of it
    st.session_state.combo_ver = 0


def set_combo_fields(fields: list[dict]):
    st.session_state.combo_fields = fields
    st.session_state.combo_ver += 1  # fresh editor, so old edits aren't re-applied


panels: list[dict] = st.session_state.panels

# ------------------------------------------------------------ access code ---
def access_gate() -> None:
    """Optional. Off unless an `access_code` secret is set; then visitors enter it once
    per browser session. Without it, anyone with the link can use the dashboard."""
    code = _setting("access_code")
    if not code or st.session_state.get("rr_access_ok"):
        return
    st.html(fgstyle.page_title_html("RosterResource dashboard", "Enter the access code to continue."))
    entered = st.text_input("Access code", type="password")
    if entered:
        if hmac.compare_digest(entered.encode(), str(code).encode()):
            st.session_state.rr_access_ok = True
            st.rerun()
        st.error("That code isn't right.")
    st.stop()


access_gate()


@st.cache_data(ttl=600, show_spinner=False)
def _gate_status(_kind: str):
    return sources.gate_status_safe(SRC)


@st.cache_resource
def _last_manual_check() -> dict:
    return {"t": 0.0}


# ---------------------------------------------------------------- sidebar ---
with st.sidebar:
    try:
        snaps = _snapshots(SRC.kind)
    except Exception:  # noqa: BLE001
        st.error("The data can't be loaded right now. Please try again in a few minutes.")
        st.stop()
    if not snaps:
        st.info("No data has been published yet. Check back after the first scheduled refresh.")
        st.stop()
    by_id = {x["id"]: x["manifest"] for x in snaps}
    st.html(fgstyle.section_html("Data"))
    sid = st.selectbox("Snapshot", list(by_id), format_func=lambda i: snap_label(by_id[i]),
                       help="Each snapshot is one pull from FanGraphs RosterResource. The newest is first.")
    MANIFEST = by_id[sid]
    avail = SRC.available(sid)
    if st.button("Check for newer data", width="stretch"):
        # Shared by every visitor; at most one re-check a minute, and it only reads GitHub.
        last = _last_manual_check()
        if time.time() - last["t"] > 60:
            last["t"] = time.time()
            _snapshots.clear()
            _gate_status.clear()
        st.rerun()

    with st.expander("Refresh status"):
        s = _gate_status(SRC.kind)
        if isinstance(s, str) or s is None:
            st.caption("Status unavailable.")
        else:
            st.caption(f"Source: {SRC.label}. Last pull: {_fmt(s['last_attempt'])}. Next pull no sooner than "
                       + (_fmt(s["next_allowed"]) if s["last_attempt"] else "now")
                       + ". Pulls happen at most once every 48 hours.")
            if s["halted"]:
                st.warning("Automatic refreshes are paused. The owner has been notified.")
        for w in SRC.warnings(sid):
            st.caption(f"Data check: {w}")
        if SRC.kind == "local":
            st.caption("Reading raw snapshots. Run `python -m rr publish` for faster loading.")

    st.html(fgstyle.section_html("Layout"))
    st.caption("Your panels are private to your browser session. Save them to a file to reuse later "
               "or to hand to someone else.")
    st.download_button("Save layout", json.dumps(panels, indent=2), "rr_layout.json",
                       "application/json", disabled=not panels, width="stretch")
    uploaded = st.file_uploader("Load a saved layout", type="json")
    if uploaded is not None and st.button("Replace current panels with this layout", width="stretch"):
        try:
            st.session_state.panels = views.validate_layout(json.loads(uploaded.getvalue()[:1_000_000]))
            st.rerun()
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(f"That file isn't a layout saved from this dashboard ({exc}).")

ctx = views.Ctx(tables_for=lambda p, t: SRC.tables_for(sid, p, t), avail=avail,
                index=lambda: SRC.index(sid),
                frame_fn=lambda p, teams, tb: SRC.frame(sid, p, tuple(teams), tb),
                coverage_fn=lambda p: SRC.coverage(sid, p))

st.html(fgstyle.page_title_html("RosterResource dashboard",
                                f"Snapshot {snap_label(MANIFEST)}. {export.ATTRIBUTION}."))
if DEMO:
    st.warning("Sample mode: every player, team figure, salary and injury shown here is invented. "
               "Remove `demo = true` from the app's secrets to show real data.")


# ------------------------------------------------------ All teams (league) ---
def league_tab():
    pages = [p for p in config.TEAM_TOOLS if p in avail]
    if not pages:
        st.info("This snapshot has no team tools.")
        return
    page = st.pills("Page", pages, format_func=page_name, default=pages[0], key="lg_page") or pages[0]
    per_team = ctx.fetched_per_team(page)
    teams_all = avail[page] if per_team else list(config.TEAM_ABBR)
    chosen = team_strip(f"lg_teams::{page}", teams_all)

    cov = ctx.coverage(page)
    if cov.empty:
        st.warning("No tables found on this page.")
        return
    info = cov.set_index("Table")
    c1, c2, c3, c4 = st.columns([3, 3, 2, 2])
    table = c1.selectbox("Table", list(cov["Table"]), key=f"lg_table::{page}",
                         format_func=lambda t: f"{t}  ("
                         + (f"{info.at[t, 'Teams']} teams, " if per_team else "league-wide page, ")
                         + f"{info.at[t, 'Rows']} rows"
                         + (", same on every page, probably site navigation)" if info.at[t, "Same for every team"] else ")"))
    df = ctx.view_frame(page, ["*"], table)
    if df is None:
        st.warning("That table has no rows in this snapshot.")
        return
    if not per_team and "Team" not in df.columns:
        st.caption("This league-wide table has no team column I recognize, so the team strip "
                   "can't filter it. Pick a different table, or add the column name to "
                   "_TEAM_COLS in rr/views.py.")
    all_cols = [c for c in df.columns if c != "Team"]
    cols = c2.multiselect("Columns", all_cols, default=all_cols[:10], key=f"lg_cols::{page}::{table}")
    flt = c3.text_input("Keep rows containing", key=f"lg_flt::{page}::{table}")
    sort_by = c4.selectbox("Sort by", ["Team", *cols], key=f"lg_sort::{page}::{table}")

    who = "All 30 teams" if not chosen else ", ".join(config.TEAM_ABBR[t] for t in chosen)
    panel = {"kind": "table", "page": page, "teams": chosen or ["*"], "table": table, "columns": cols,
             "filter": flt, "sort_by": sort_by, "ascending": True, "title": f"{page_name(page)}: {who}"}
    view = views.panel_frame(ctx, panel)
    st.html(fgstyle.section_html(f"{page_name(page)}: {who}", f"{len(view):,} rows"))
    show(view)

    a, b = st.columns(2)
    if a.button("Add this table to the dashboard", type="primary", key="lg_add"):
        add_panel(panel)
    b.download_button(f"Download every {page_name(page)} table, all teams (.xlsx)",
                      export.to_excel([(tid, f) for tid in cov.loc[~cov["Same for every team"], "Table"]
                                       if (f := ctx.view_frame(page, ["*"], tid)) is not None],
                                      {"title": f"{page_name(page)}, all teams", "snapshot": snap_label(MANIFEST),
                                       "exported": fgstyle.exported_stamp()}),
                      f"{page.replace('/', '-')}_all_teams.xlsx", width="stretch", key="lg_dl")

    with st.expander("Roll up by team"):
        r1, r2, r3 = st.columns(3)
        how = r1.selectbox("Show", ["count", "sum", "mean", "max", "min"], key="lg_how",
                           format_func=lambda h: {"count": "Row counts", "sum": "Total", "mean": "Average",
                                                  "max": "Highest", "min": "Lowest"}[h])
        by = r2.selectbox("Group by", ["Team", *all_cols], key=f"lg_by::{table}")
        value = r3.selectbox("Split by" if how == "count" else "Of", ["(none)", *all_cols], key=f"lg_val::{table}")
        value = None if value == "(none)" else value
        if how != "count" and not value:
            st.caption("Pick a numeric column to total or average.")
        else:
            summ = {"kind": "summary", "page": page, "teams": chosen or ["*"], "table": table,
                    "by": by, "value": value, "how": how,
                    "title": f"{page_name(page)}: {how} {'of ' + value + ' ' if value and how != 'count' else ''}by {by}"}
            show(views.panel_frame(ctx, summ))
            if st.button("Add roll-up to the dashboard", key="lg_add_sum"):
                add_panel(summ)


# ------------------------------------------------------------ Player lookup ---
def player_tab():
    with st.spinner("Loading players…"):
        idx = ctx.index()
    q = st.text_input("Find a player", placeholder="Type part of a name", key="pl_q")
    if not q:
        st.caption(f"{len(idx.players):,} players found across {len(idx.table_keys())} player tables in this snapshot.")
        return
    hits = idx.search(q)
    if hits.empty:
        st.info(f"No player matching “{q}”.")
        return
    by_key = hits.set_index("key")
    key = st.selectbox("Player", list(hits["key"]), key="pl_pick",
                       format_func=lambda k: f"{by_key.at[k, 'Player']} ({by_key.at[k, 'Team'] or 'no team'}), "
                                             f"in {by_key.at[k, 'Sources']} tools")
    name = idx.name_of(key)
    prof = idx.profile(key)
    h, btn = st.columns([5, 1])
    team = by_key.at[key, "Team"]
    h.html(f"<div class='fg-player-name'>{name}</div><div class='fg-player-meta'>"
           f"{team or 'No team found'}{' (' + config.NAME_TO_ABBR[team] + ')' if team in config.NAME_TO_ABBR else ''}"
           f". Appears in {len(prof)} tool{'s' if len(prof) != 1 else ''}.</div>")
    if btn.button("Add to dashboard", type="primary", key="pl_add", width="stretch"):
        add_panel({"kind": "player", "player_key": key, "player_name": name, "title": f"Player card: {name}"})
    if key.startswith("name?:"):
        st.warning("More than one player has this name, so these rows couldn't be matched to one of them.")
    for page, tid, rows in prof:
        st.html(fgstyle.section_html(page_name(page), tid))
        show(rows, legend=False)


# --------------------------------------------------------- Combine by player ---
def combine_tab():
    with st.spinner("Loading players…"):
        idx = ctx.index()
    base_fields: list[dict] = st.session_state.combo_fields
    st.caption("Pick columns from any tools. Each player becomes one row, with the columns side by side.")

    by_page: dict[str, list[str]] = {}
    for (page, tid) in idx.table_keys():
        by_page.setdefault(page, []).append(tid)
    if not by_page:
        st.warning("No tables with player names or IDs were found in this snapshot.")
        return
    c1, c2, c3, c4 = st.columns([2, 3, 3, 1.3])
    page = c1.selectbox("From", [p for p in config.ALL_PAGES if p in by_page], format_func=page_name, key="cb_page")
    tid = c2.selectbox("Table", by_page[page], key=f"cb_table::{page}")
    cols = c3.multiselect("Columns", [c for c in idx.columns_of(page, tid) if c != idx.name_col(page, tid)],
                          key=f"cb_cols::{page}::{tid}")
    how = c4.selectbox("If several rows", players.AGGS, key="cb_how",
                       help="first: first value. all: every distinct value, joined. count: number of rows. "
                            "sum / mean / max / min: numeric.")
    current = st.session_state.get("cb_current", base_fields)
    if st.button("Add columns", disabled=not cols, key="cb_addcols"):
        set_combo_fields(current + [{"page": page, "table": tid, "column": c, "agg": how,
                                     "tool": page_name(page), "label": f"{c} ({page_name(page)})"} for c in cols])
        st.rerun()

    if not base_fields:
        st.info("Add columns from two or more tools to build a combined player table.")
        return

    edited = st.data_editor(
        pd.DataFrame(base_fields), num_rows="dynamic", width="stretch", hide_index=True,
        key=f"cb_editor{st.session_state.combo_ver}",
        column_order=["label", "agg", "tool", "column", "table"],
        column_config={
            "label": st.column_config.TextColumn("Column heading"),
            "agg": st.column_config.SelectboxColumn("If several rows", options=list(players.AGGS)),
            "tool": st.column_config.TextColumn("Tool", disabled=True),
            "column": st.column_config.TextColumn("Source column", disabled=True),
            "table": st.column_config.TextColumn("Table", disabled=True),
        })
    fields = [r for r in edited.to_dict("records") if isinstance(r.get("column"), str) and r["column"]]
    st.session_state.cb_current = fields
    if not fields:
        st.info("All columns removed. Add some from above.")
        return

    first_of_source: dict[tuple, int] = {}
    for i, f in enumerate(fields):
        first_of_source.setdefault((f["page"], f["table"]), i)
    opts = ["any", *first_of_source.values()]
    r1, r2, r3 = st.columns([2, 2, 1])
    rows_from = r1.selectbox("Include players who appear in", opts, key="cb_rows",
                             format_func=lambda o: "Any of these tools" if o == "any"
                             else f"{page_name(fields[o]['page'])} ({fields[o]['table']})")
    sort_opts = ["Team", "Player", *[f.get("label") or f["column"] for f in fields]]
    sort_by = r2.selectbox("Sort by", sort_opts, key="cb_sort")
    amb = r3.toggle("Include ambiguous names", False, key="cb_amb")
    team_filter = team_strip("cb_teams", list(config.TEAM_ABBR))

    spec = {"fields": fields, "rows_from": rows_from, "include_ambiguous": amb,
            "teams": [config.SLUG_TO_TEAM[t] for t in team_filter] or ["*"], "sort_by": sort_by}
    result = idx.combine(spec)
    if sort_by in result.columns:
        result = result.sort_values(sort_by, kind="stable").reset_index(drop=True)
    title = st.text_input("Panel title", "Combined player view", key="cb_title")
    st.html(fgstyle.section_html(title, f"{len(result):,} players"))
    show(result)

    t1, t2 = st.columns(2)
    if t1.button("Add to the dashboard", type="primary", width="stretch", key="cb_add"):
        add_panel({"kind": "combined", "title": title, "spec": spec})
    if t2.button("Clear columns", width="stretch", key="cb_clear"):
        set_combo_fields([])
        st.session_state.cb_current = []
        st.rerun()

    with st.expander("How players were matched"):
        st.caption("Rows are joined by player ID when a table has one, otherwise by name when exactly one "
                   "player has that name. Ambiguous names are kept apart rather than guessed.")
        show(idx.match_report(), legend=False)


# ------------------------------------------------------------ Custom panel ---
def custom_tab():
    pages_here = [p for p in config.ALL_PAGES if p in avail]
    page = st.pills("Page", pages_here, format_func=page_name, default=pages_here[0], key="cu_page") or pages_here[0]
    if ctx.fetched_per_team(page):
        teams = team_strip(f"cu_teams::{page}", avail[page])
        if not teams:
            st.info("Pick one or more teams above.")
            return
    elif page in config.TEAM_TOOLS:  # fetched league-wide: team strip is an optional filter
        teams = team_strip(f"cu_teams::{page}", list(config.TEAM_ABBR)) or ["*"]
    else:
        teams = [None]
    cov = ctx.coverage(page)
    sizes = dict(zip(cov["Table"], cov["Rows"]))
    if not sizes:
        st.warning("No tables found. `python -m rr inspect` shows what was saved for this page.")
        return
    c1, c2 = st.columns([2, 3])
    table = c1.selectbox("Table", sorted(sizes, key=lambda k: -sizes[k]),
                         format_func=lambda k: f"{k}  ({sizes[k]} rows)", key=f"cu_table::{page}")
    base = ctx.view_frame(page, teams, table)
    if base is None:
        st.info("That table isn't on the selected teams' pages.")
        return
    all_cols = [c for c in base.columns if c != "Team"]
    cols = c2.multiselect("Columns (order matters)", all_cols, default=all_cols[:10], key=f"cu_cols::{page}::{table}")
    f1, f2, f3 = st.columns([2, 2, 1])
    flt = f1.text_input("Keep rows containing", key=f"cu_flt::{page}::{table}")
    sort_by = f2.selectbox("Sort by", ["(none)", *(["Team"] if "Team" in base.columns else []), *cols], key=f"cu_sort::{page}::{table}")
    asc = f3.toggle("Ascending", True, key=f"cu_asc::{page}::{table}")
    who = ("All teams" if teams == ["*"] else "League-wide" if teams == [None]
           else ", ".join(config.TEAM_ABBR.get(t, t) for t in teams))
    title = st.text_input("Panel title", f"{page_name(page)}: {who}", key="cu_title")
    panel = {"kind": "table", "title": title, "page": page, "teams": teams, "table": table, "columns": cols,
             "filter": flt, "sort_by": None if sort_by == "(none)" else sort_by, "ascending": asc}
    st.html(fgstyle.section_html(title))
    show(views.apply_view(base, panel))
    if st.button("Add to the dashboard", type="primary", key="cu_add"):
        add_panel(panel)


# --------------------------------------------------------------- Dashboard ---
def describe(p: dict) -> str:
    k = p.get("kind", "table")
    if k == "combined":
        return "Combined by player from " + ", ".join(dict.fromkeys(page_name(f["page"]) for f in p["spec"]["fields"]))
    if k == "player":
        return "Every tool that mentions this player"
    return page_name(p["page"])


def board_tab():
    if not panels:
        st.info("The dashboard is empty. Add tables from the other tabs.")
        return
    for i, p in enumerate(panels):
        df = views.panel_frame(ctx, p)
        h, up, down, rm = st.columns([20, 1, 1, 1], vertical_alignment="center")
        h.html(fgstyle.section_html(p["title"], f"{describe(p)}. {0 if df is None else len(df):,} rows"))
        if up.button("", icon=":material/arrow_upward:", key=f"up{i}", disabled=i == 0, help="Move up"):
            panels[i - 1], panels[i] = panels[i], panels[i - 1]
            st.rerun()
        if down.button("", icon=":material/arrow_downward:", key=f"dn{i}", disabled=i == len(panels) - 1, help="Move down"):
            panels[i + 1], panels[i] = panels[i], panels[i + 1]
            st.rerun()
        if rm.button("", icon=":material/close:", key=f"rm{i}", help="Remove"):
            panels.pop(i)
            st.rerun()
        if df is None:
            st.warning("Not available in the selected snapshot.")
        else:
            show(df)
        st.html(f"<div class='fg-src'>{export.ATTRIBUTION}.</div>")


def export_tab():
    if not panels:
        st.info("Exports contain exactly the panels on your dashboard, in order. Add some first.")
        return
    title = st.text_input("Report title", "Roster report", key="ex_title")
    items = [(p["title"], df) for p in panels if (df := views.panel_frame(ctx, p)) is not None]
    meta = {"title": title, "snapshot": snap_label(MANIFEST), "exported": fgstyle.exported_stamp()}
    stem = "".join(ch if ch.isalnum() else "_" for ch in title).strip("_") or "report"
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download Excel (.xlsx)", export.to_excel(items, meta), f"{stem}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    c2.download_button("Download CSVs (.zip)", export.to_csv_zip(items, meta), f"{stem}.zip",
                       "application/zip", width="stretch")
    c3.download_button("Download printable HTML", export.to_html(items, meta), f"{stem}.html",
                       "text/html", width="stretch")
    st.caption("All three keep the RosterResource look: green section bars, grey header rows, and the "
               "same row shading for recent acquisitions and injured players. The HTML report prints to PDF.")


tabs = st.tabs(["All Teams", "Player Lookup", "Combine by Player", "Custom Panel",
                f"Dashboard ({len(panels)})", "Export"])
for tab, fn in zip(tabs, [league_tab, player_tab, combine_tab, custom_tab, board_tab, export_tab]):
    with tab:
        fn()
