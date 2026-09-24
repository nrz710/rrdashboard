"""python -m rr <command>

  refresh      Pull the current season (gated: once per 48 h). For cron.
  status       Show last pull, next allowed pull, halt state. No network.
  inspect      List the tables found in a saved snapshot. No network.
  clear-halt   Re-enable refreshes after a suspected block.
  backfill     Manual, interactive one-off for a past season (still gated).
  compile      Stack a page's tables across all teams into one Excel file. No network.
  player       Show everything saved about one player, across all tools. No network.
  publish      Pre-parse the newest snapshot into fast files for the dashboard. No network.
  reingest     Re-publish a stored raw snapshot (after a parser change). No network.
  prune        Delete old raw and published snapshots per the retention settings. No network.
  site         Build the public website (GitHub Pages) from published snapshots. No network.
  record-prior-pull  Log a pull made elsewhere (e.g. by an earlier version) so the 48-hour
               gate counts it. Can only make the gate stricter. No network.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from datetime import datetime

from . import config, export, league, parse, players, published, store
from .fetch import build_session
from .gate import Gate, GateClosed, _fmt
from .refresh import EXIT_GATE, plan_jobs, run_refresh


def _gate() -> Gate:
    return Gate(config.STATE_DIR, config.MIN_REFRESH_INTERVAL_HOURS)


def _validate(teams, pages) -> tuple[list[str], list[str]]:
    teams = teams or list(config.TEAMS.values())
    pages = pages or list(config.ALL_PAGES)
    bad_t = [t for t in teams if t not in config.SLUG_TO_TEAM]
    bad_p = [p for p in pages if p not in config.ALL_PAGES]
    if bad_t or bad_p:
        raise SystemExit(f"Unknown teams {bad_t} / pages {bad_p}.\n"
                         f"Teams: {' '.join(config.TEAMS.values())}\nPages: {' '.join(config.ALL_PAGES)}")
    return teams, pages


def cmd_status(_args) -> int:
    try:
        s = _gate().status()
    except GateClosed as exc:
        print(exc)
        return EXIT_GATE
    print(f"Last pull attempt : {_fmt(s['last_attempt'])}")
    print(f"Last full success : {_fmt(s['last_success'])}")
    print(f"Next pull allowed : {_fmt(s['next_allowed'])}")
    print(f"Halted            : {'YES - ' + s['halt_reason'].strip() if s['halted'] else 'no'}")
    snaps = store.list_snapshots(config.DATA_DIR)
    pubs = published.list_published(config.DATA_DIR)
    print(f"Raw snapshots     : {len(snaps)}" + (f" (latest {snaps[0].name})" if snaps else ""))
    print(f"Published         : {len(pubs)}" + (f" (latest {pubs[0].name})" if pubs else ""))
    size = sum(f.stat().st_size for f in config.DATA_DIR.rglob("*") if f.is_file()) if config.DATA_DIR.exists() else 0
    print(f"Data folder size  : {size / 1_048_576:.1f} MB")
    return 0


def cmd_refresh(args) -> int:
    teams, pages = _validate(args.teams, args.pages)
    season = config.current_season()  # recurring job: current season only, never a parameter
    jobs = plan_jobs(teams, pages)
    gate = _gate()
    if args.dry_run:
        try:
            gate.check()
            print("Gate: open - a real run would proceed now.")
        except GateClosed as exc:
            print(f"Gate: closed - {exc}")
        mins = len(jobs) * (config.REQUEST_DELAY_SECONDS + config.REQUEST_JITTER_SECONDS / 2) / 60
        print(f"Would fetch {len(jobs)} pages (~{mins:.0f} min at current pacing):")
        for j in jobs:
            print("  ", j.url)
        return 0
    hook = None
    if args.before_fetch:
        def hook():
            subprocess.run(shlex.split(args.before_fetch), check=True)
    try:
        return run_refresh(gate=gate, data_dir=config.DATA_DIR, jobs=jobs, kind="current",
                           season=season, session=build_session(config.USER_AGENT), before_fetch=hook)
    except GateClosed as exc:
        print(f"Not fetching. {exc}")
        return EXIT_GATE


def cmd_backfill(args) -> int:
    season = args.season
    if season >= config.current_season():
        raise SystemExit("Backfill is for past seasons. The current season comes from `refresh`.")
    pages = [p for p in config.ALL_PAGES if p in config.SEASON_PARAM]
    if not pages:
        print("No pages have a verified season parameter in config.SEASON_PARAM, so there is "
              "nothing safe to backfill. Nothing was fetched and the 48-hour window is untouched.")
        return 1
    if not sys.stdin.isatty():
        raise SystemExit("Backfill is manual-only and needs an interactive terminal.")
    jobs = plan_jobs(list(config.TEAMS.values()), pages, season=season, season_param=config.SEASON_PARAM)
    gate = _gate()
    try:
        gate.check()
    except GateClosed as exc:
        print(f"Not fetching. {exc}")
        return EXIT_GATE
    print(f"One-off backfill of {season}: {len(jobs)} requests. This uses the 48-hour window.")
    if input(f"Type {season} to confirm: ").strip() != str(season):
        print("Cancelled.")
        return 1
    try:
        return run_refresh(gate=gate, data_dir=config.DATA_DIR, jobs=jobs, kind="backfill",
                           season=season, session=build_session(config.USER_AGENT))
    except GateClosed as exc:
        print(f"Not fetching. {exc}")
        return EXIT_GATE


def cmd_clear_halt(_args) -> int:
    print("Halt cleared." if _gate().clear_halt() else "Not halted.")
    return 0


def cmd_inspect(args) -> int:
    snaps = store.list_snapshots(config.DATA_DIR)
    if args.snapshot:
        snaps = [s for s in snaps if s.name.startswith(args.snapshot)]
    if not snaps:
        print("No matching snapshot.")
        return 1
    snap: Path = snaps[0]
    m = store.read_manifest(snap)
    print(f"{snap.name}: status={m['status']} ok={m.get('ok')}/{m['planned']}")
    for page, teams in store.available(snap).items():
        if args.page and page != args.page:
            continue
        team = args.team if args.team in teams else teams[0]
        rec = store.load_raw(snap, page, team)
        tables = parse.discover_tables(rec["payload"], team_slug=team)
        print(f"\n== {config.ALL_PAGES.get(page, page)}  [{page}{' / ' + team if team else ''}]  "
              f"source={rec.get('source')}  {len(tables)} tables")
        for tid, df in sorted(tables.items(), key=lambda kv: -len(kv[1])):
            cols = ", ".join(map(str, df.columns[:8])) + (" ..." if df.shape[1] > 8 else "")
            print(f"   {len(df):>5} rows x {df.shape[1]:<3} {tid}\n         {cols}")
    return 0


def _latest_snapshot(prefix: str | None) -> Path | None:
    snaps = store.list_snapshots(config.DATA_DIR)
    if prefix:
        snaps = [s for s in snaps if s.name.startswith(prefix)]
    return snaps[0] if snaps else None


def _tables_for(snap: Path):
    cache: dict = {}

    def tables_for(page, team):
        if (page, team) not in cache:
            rec = store.load_raw(snap, page, team)
            cache[(page, team)] = {} if rec is None else parse.discover_tables(rec["payload"], team_slug=team)
        return cache[(page, team)]
    return tables_for


def cmd_compile(args) -> int:
    snap = _latest_snapshot(args.snapshot)
    if snap is None:
        print("No matching snapshot.")
        return 1
    avail = store.available(snap)
    pages = [args.page] if args.page else [p for p in config.ALL_PAGES if p in avail]
    tf = _tables_for(snap)
    items = []
    for page in pages:
        for tid, df in league.compile_page(tf, page, avail.get(page, [])).items():
            items.append((f"{config.ALL_PAGES.get(page, page)} {tid.split('.')[-1]}", df))
            print(f"{len(df):>6} rows  {config.ALL_PAGES.get(page, page)}: {tid}")
    out = Path(args.out or f"league_{snap.name}.xlsx")
    out.write_bytes(export.to_excel(items, {"title": "League-wide compilation", "snapshot": snap.name,
                                            "exported": datetime.now().strftime("%Y-%m-%d %H:%M")}))
    print(f"Wrote {out}")
    return 0


def cmd_player(args) -> int:
    snap = _latest_snapshot(args.snapshot)
    if snap is None:
        print("No matching snapshot.")
        return 1
    tf = _tables_for(snap)
    idx = players.PlayerIndex((p, t, tid, df) for p, ts in store.available(snap).items()
                              for t in ts for tid, df in tf(p, t).items())
    hits = idx.search(args.name)
    if hits.empty:
        print("No match.")
        return 1
    if len(hits) > 1:
        print(hits[["Player", "Team", "Sources"]].to_string(index=False))
        print("\nShowing the first match. Be more specific to pick another.\n")
    key = hits.iloc[0]["key"]
    print(f"== {idx.name_of(key)} ==")
    for page, tid, rows in idx.profile(key):
        print(f"\n[{config.ALL_PAGES.get(page, page)}] {tid}")
        print(rows.to_string(index=False))
    return 0


def cmd_publish(args) -> int:
    snap = _latest_snapshot(args.snapshot)
    if snap is None:
        print("No raw snapshot to publish.")
        return 0
    if not store.available(snap):
        print(f"{snap.name} has no saved pages; nothing to publish.")
        return 0
    published.publish(config.DATA_DIR, snap)
    return 0


def cmd_reingest(args) -> int:
    snap = _latest_snapshot(args.snapshot_id)
    if snap is None:
        print(f"No raw snapshot matching {args.snapshot_id!r}. Only the newest {config.RAW_RETENTION} are kept.")
        return 1
    published.publish(config.DATA_DIR, snap)
    return 0


def cmd_record_prior_pull(args) -> int:
    import json
    import os
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(args.timestamp.strip().replace("Z", "+00:00"))
    except ValueError:
        print("Use a time like 2026-09-24T08:49:03+00:00")
        return 1
    if ts.tzinfo is None:
        print("Include the time zone, e.g. 2026-09-24T08:49:03+00:00")
        return 1
    if ts > datetime.now(timezone.utc):
        print("That time is in the future; nothing recorded.")
        return 1
    gate = _gate()
    entry = {"ts": ts.isoformat(timespec="seconds"), "event": "attempt_start", "note": "recorded by hand"}
    with open(gate.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(f"Recorded a prior pull at {entry['ts']}.")
    return cmd_status(args)


def cmd_site(args) -> int:
    from . import site
    if args.demo:
        site.build_demo(Path(args.out))
    else:
        site.build(config.DATA_DIR, Path(args.out))
    return 0


def cmd_prune(args) -> int:
    gone = store.prune(config.DATA_DIR, args.keep_raw)
    gone_pub = published.prune_published(config.DATA_DIR, args.keep_published)
    published.write_index(config.DATA_DIR)
    print(f"Removed {len(gone)} raw and {len(gone_pub)} published snapshot(s)." if gone or gone_pub
          else "Nothing to prune.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m rr", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="gated pull of the current season")
    r.add_argument("--teams", nargs="+", metavar="SLUG", help="subset of team slugs (default: all 30)")
    r.add_argument("--pages", nargs="+", metavar="PAGE", help="subset of page keys (default: all)")
    r.add_argument("--dry-run", action="store_true", help="show gate state and planned URLs; no network")
    r.add_argument("--before-fetch", metavar="CMD",
                   help="command to run after the attempt is logged and before any request "
                        "(CI uses it to push the log first); if it fails, nothing is fetched")
    r.set_defaults(fn=cmd_refresh)

    sub.add_parser("status", help="show gate state").set_defaults(fn=cmd_status)
    sub.add_parser("clear-halt", help="re-enable after a block").set_defaults(fn=cmd_clear_halt)

    b = sub.add_parser("backfill", help="manual one-off past season")
    b.add_argument("--season", type=int, required=True)
    b.set_defaults(fn=cmd_backfill)

    i = sub.add_parser("inspect", help="list tables in a saved snapshot (offline)")
    i.add_argument("--snapshot", help="snapshot name prefix (default: latest)")
    i.add_argument("--page")
    i.add_argument("--team")
    i.set_defaults(fn=cmd_inspect)

    c = sub.add_parser("compile", help="league-wide Excel of a page's tables (offline)")
    c.add_argument("--page", choices=list(config.ALL_PAGES))
    c.add_argument("--snapshot")
    c.add_argument("--out")
    c.set_defaults(fn=cmd_compile)

    pl = sub.add_parser("player", help="everything about one player (offline)")
    pl.add_argument("name")
    pl.add_argument("--snapshot")
    pl.set_defaults(fn=cmd_player)

    pr = sub.add_parser("prune", help="keep only the newest N snapshots (offline)")
    pr.add_argument("--keep-raw", type=int, default=config.RAW_RETENTION)
    pr.add_argument("--keep-published", type=int, default=config.PUBLISHED_RETENTION)
    pr.set_defaults(fn=cmd_prune)

    pu = sub.add_parser("publish", help="pre-parse the newest snapshot for the dashboard (offline)")
    pu.add_argument("--snapshot", help="raw snapshot name prefix (default: newest)")
    pu.set_defaults(fn=cmd_publish)

    ri = sub.add_parser("reingest", help="re-publish a stored raw snapshot (offline)")
    ri.add_argument("snapshot_id")
    ri.set_defaults(fn=cmd_reingest)

    si = sub.add_parser("site", help="build the public website from published snapshots (offline)")
    si.add_argument("--out", default="_site")
    si.add_argument("--demo", action="store_true", help="use invented sample data")
    si.set_defaults(fn=cmd_site)

    rp = sub.add_parser("record-prior-pull", help="log a pull made elsewhere (gate only gets stricter)")
    rp.add_argument("timestamp", help="e.g. 2026-09-24T08:49:03+00:00")
    rp.set_defaults(fn=cmd_record_prior_pull)

    args = p.parse_args(argv)
    return args.fn(args)
