# RosterResource dashboard

A personal dashboard built on the public FanGraphs RosterResource pages. It saves
snapshots of the pages, then lets you compile the same table across all 30 teams,
look up everything the tools say about one player, combine columns from different
tools into one row per player, arrange it all on a dashboard, and export it to
Excel, CSV, or a printable HTML report. Tables and exports are styled to look like
RosterResource.

**It's a public website on GitHub Pages:** anyone with the link
(`https://<your-username>.github.io/<repository>/`) can use it in a browser, on a computer
or a phone, with no account. It runs entirely inside each visitor's browser, so there's no
server to keep awake, and each visitor's panels and exports are their own.

**It runs entirely on GitHub, for free:** the repository holds the code, GitHub Actions
runs the scheduled refresh and publishes the website, and the data lives on the
repository's `data` branch. It's all managed in the browser: no installs, no GitHub
Desktop, no command line. The whole project installs from a single file
(`RRDASH_INSTALL.txt`, the same text as `.github/workflows/rrdash.yml`); to launch it,
follow **[DEPLOY.md](DEPLOY.md)**.

## How the pieces fit

    RosterResource workflow, refresh (6 h)    `data` branch                 same workflow, site + deploy
    gate check → refresh → publish → push  →  pull log, raw + parsed   →   web/index.html + data → GitHub Pages
    (at most one pull per 48 h)               snapshots                     https://you.github.io/repo/

After each pull, `publish` does the heavy work once: it parses every page, matches players
across tools, and writes pre-parsed files. `python -m rr site` then turns those into the
website's data (team names, row shading and player keys already applied), and the page in
`web/index.html` only has to filter, sort and total in the browser. The `data` branch is
replaced by a single commit each time, so the repository doesn't grow.

An optional Streamlit version of the same dashboard (`dashboard.py`) is still included for
anyone who prefers a server-hosted app; the website doesn't need it.

## Two rules the code enforces

**1. No browser User-Agent.** Requests go out with the `requests` library's own
default (`python-requests/x.y`). `rr/fetch.py` refuses any User-Agent that looks like
a browser and re-checks the final request right before sending. There are no retries,
proxies, or challenge solving. A 403/429/503, a Cloudflare challenge header, or a
"Just a moment" page stops the run immediately and writes a `HALT` file; every later
run refuses until someone investigates and clears it. On GitHub, the workflow also
opens an issue.

**2. At most one refresh per 48 hours, enforced in code** (`rr/gate.py`). The 48-hour
floor is a constant that config can raise but not lower. It's measured from the start
of the last *attempt*, so failing or misfiring jobs can't exceed it. The check and the
attempt record happen together under an exclusive lock, and fail closed on an
unreadable log or a timestamp in the future. On GitHub, the attempt is pushed to the
`data` branch *before* the first request, so the window is on record even if the job
dies midway. Recurring pulls are current-season only; `backfill` is a separate,
interactive, by-hand command that still goes through the same gate.

## How many requests a pull makes

97. Depth charts, payroll and the lineup tracker are fetched once per team (3 × 30).
The injury report, transaction tracker and closer depth chart are fetched once each
from RosterResource's league-wide "Show All MLB Teams" pages, and the four league pages
(breakdowns and free agents) once each. At 6 to 8 seconds apart that's about 12 minutes.

After each pull, `publish` checks that the three league-wide pages really cover the
league (no extra requests). It warns if a page mentions fewer than 25 teams, has no
recognizable team column, or has a suspiciously round row count that looks like a
page-size limit. If a warning turns out to be real, remove that tool from
`LEAGUE_WIDE_INSTEAD_OF_PER_TEAM` in `rr/config.py` to go back to per-team pulls for it.

## Running and maintaining it (browser only)

- **Refresh:** automatic, every 6 hours, gated to at most one pull per 48 hours.
- **One workflow, several tasks:** Actions → *RosterResource* → *Run workflow* → *What to do*:
  `refresh`, `install`, `website`, `tests`, `clear-halt`, `republish` (after a parser change),
  `record-prior-pull`. Only `refresh` can contact FanGraphs, and only when the gate allows.
- **Settings and code:** edit files on github.com with the pencil icon (or press `.` on the
  repository page for the in-browser editor). Commits redeploy the dashboard automatically.
- **Sample data:** set the repository variable `SITE_DEMO` to `true` and run the task
  `website` to show invented data, e.g. before the first pull.
- **Tests:** run automatically on every commit (Actions → *Tests*).

Exit codes for `refresh`, shown in the workflow log: 0 complete, 1 partial, 2 blocked
(halted), 3 gate closed, 5 the attempt couldn't be saved first so nothing was fetched.

## Optional: command line

Not needed for anything above. If you ever work with the code on a computer
(`pip install -r requirements-dev.txt`), these are available:

    python make_demo_data.py && RR_DATA_DIR=demo_data streamlit run dashboard.py   # sample app
    python -m rr refresh --dry-run     # gate state and the 97 planned URLs; no network
    python -m rr status | inspect | compile | player "name" | publish | prune
    python -m pytest -q tests

Recurring pulls are current-season only; `python -m rr backfill` is an interactive,
by-hand command for past seasons and does nothing until `config.SEASON_PARAM` is filled in.

## The dashboard

- **All Teams**: pick a RosterResource tool and see one of its tables for all 30 teams
  stacked together, with the AL/NL team strip to narrow it down. Roll up by team, or
  download every table on that page league-wide as one workbook.
- **Player Lookup**: everything every tool says about one player.
- **Combine by Player**: columns from different tools, side by side, one row per player.
  Players are matched by FanGraphs/MLBAM ID first, then by name only when exactly one
  player has that name; shared names are flagged, never guessed.
- **Custom Panel**, **Dashboard**, **Export**: build, arrange, and download.

## Matching the RosterResource look

`rr/fgstyle.py` holds the styling for the dashboard, the HTML report, and the Excel
export. Checked against the live site: the FanGraphs green (#50ae26), tab names, the
AL/NL abbreviation strip, upper-case headers, green section bars, group header rows,
IL statuses, and the "Acquired since end of last season" row shading. The exact greys,
font and sizes are approximations kept in `TOKENS` at the top of that file.

## Not verified yet

The parser doesn't assume field names: it reads each page's embedded Next.js JSON and
treats every list of records as a table. Player matching and some styling rules look
for column names such as `playerid`, `PlayerName`, `HowAcquired` and `Status`; after
the first real pull, run `python -m rr inspect` and adjust the lists at the top of
`rr/players.py` and `rr/fgstyle.py` if the real names differ.

## Layout

    web/index.html          the public website (runs in the browser; never contacts FanGraphs)
    rr/site.py              builds the website's data from published snapshots
    dashboard.py            optional Streamlit version of the dashboard
    make_demo_data.py       fictional sample snapshot (raw + published)
    rr/gate.py              48-hour limit, lock, pull log, HALT
    rr/gatecheck.py         quick gate check for CI (standard library only)
    rr/fetch.py             HTTP with honest User-Agent and block detection
    rr/refresh.py           one gated, paced refresh
    rr/parse.py             page JSON -> tables
    rr/players.py           player matching and combining
    rr/league.py            all-teams compilation and roll-ups
    rr/published.py         pre-parsed files for the dashboard
    rr/sources.py           dashboard data: published files (local or GitHub), or raw
    rr/fgstyle.py           RosterResource styling (tables, CSS, tokens)
    rr/export.py            Excel / CSV / HTML exports
    rr/store.py             raw snapshots on disk (gzipped)
    scripts/push-data.sh    save ./data as the `data` branch (run by the workflow)
    .github/workflows/rrdash.yml   the one workflow: install, refresh, website, tests
    tools/                  builds the one-file installer (make_installer.py + template)
