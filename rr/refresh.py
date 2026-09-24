"""One refresh = one gated, paced pass over the planned pages."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from . import config, store
from .fetch import BlockedError, FetchError, fetch_page
from .gate import Gate

EXIT_OK, EXIT_PARTIAL, EXIT_BLOCKED, EXIT_GATE, EXIT_HOOK = 0, 1, 2, 3, 5


@dataclass(frozen=True)
class Job:
    page: str
    team: str | None
    url: str


def plan_jobs(teams: list[str], pages: list[str], season: int | None = None,
              season_param: dict[str, str] | None = None) -> list[Job]:
    """season/season_param are only used by manual backfills."""
    jobs = []
    for page in pages:
        suffix = ""
        if season is not None:
            suffix = f"?{season_param[page]}={season}"
        if page in config.TEAM_PAGES:
            jobs += [Job(page, t, config.page_url(page, t) + suffix) for t in teams]
        else:
            jobs.append(Job(page, None, config.page_url(page, None) + suffix))
    return jobs


def run_refresh(*, gate: Gate, data_dir: Path, jobs: list[Job], kind: str, season: int,
                session: requests.Session,
                delay: float = config.REQUEST_DELAY_SECONDS,
                jitter: float = config.REQUEST_JITTER_SECONDS,
                timeout: float = config.REQUEST_TIMEOUT_SECONDS,
                max_consecutive_failures: int = config.MAX_CONSECUTIVE_FAILURES,
                sleep: Callable[[float], None] = time.sleep,
                log: Callable[[str], None] = print,
                before_fetch: Callable[[], None] | None = None) -> int:
    """before_fetch runs after the attempt is logged and before the first request.
    CI uses it to push the log to GitHub first, so the 48-hour window is recorded
    durably even if the job dies mid-run. If it raises, nothing is fetched."""
    with gate.exclusive():
        name = store.snapshot_name(kind, season)
        # Check + log attempt_start in one step (under the gate's exclusive lock,
        # so two runs can't both pass). Recorded BEFORE the first request, so from here on
        # the 48-hour window is consumed no matter what happens next. Raises GateClosed.
        gate.reserve(snapshot=name, kind=kind, season=season, planned=len(jobs))
        snap = store.create_snapshot(data_dir, name)
        if before_fetch is not None:
            try:
                before_fetch()
            except Exception as exc:  # noqa: BLE001
                gate.record("failure", snapshot=snap.name, detail=f"before-fetch hook failed, nothing fetched: {exc}")
                log(f"Before-fetch hook failed; nothing was fetched: {exc}")
                store.write_manifest(snap, {"kind": kind, "season": season, "planned": len(jobs), "ok": 0,
                                            "status": "not started", "started_at": datetime.now(timezone.utc).isoformat(),
                                            "results": []})
                return EXIT_HOOK

        manifest = {
            "kind": kind, "season": season, "planned": len(jobs),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "running", "results": [],
        }
        results = manifest["results"]
        ok = consecutive = 0
        exit_code = EXIT_OK
        try:
            for i, job in enumerate(jobs):
                if i:
                    sleep(delay + random.uniform(0, jitter))
                log(f"[{i + 1}/{len(jobs)}] {job.url}")
                try:
                    rec = fetch_page(session, job.url, timeout)
                except FetchError as exc:
                    results.append({"page": job.page, "team": job.team, "url": job.url, "ok": False, "error": str(exc)})
                    log(f"    failed: {exc}")
                    consecutive += 1
                    if consecutive >= max_consecutive_failures:
                        log(f"    {consecutive} failures in a row; stopping this run.")
                        manifest["status"] = "aborted"
                        break
                    continue
                except requests.RequestException as exc:
                    results.append({"page": job.page, "team": job.team, "url": job.url, "ok": False, "error": repr(exc)})
                    log(f"    network error, stopping this run (no retries): {exc!r}")
                    manifest["status"] = "aborted"
                    break
                store.save_raw(snap, job.page, job.team, rec)
                results.append({"page": job.page, "team": job.team, "url": job.url, "ok": True})
                ok += 1
                consecutive = 0
        except BlockedError as exc:
            results.append({"page": job.page, "team": job.team, "url": job.url, "ok": False, "error": str(exc)})
            manifest["status"] = "blocked"
            gate.halt(str(exc))
            gate.record("blocked", snapshot=snap.name, ok=ok, detail=str(exc))
            log("\n" + "!" * 72)
            log("STOPPED: FanGraphs/Cloudflare appears to be blocking this client.")
            log(f"  {exc}")
            log("Nothing will be retried. All future refreshes are disabled until")
            log("you investigate and run `python -m rr clear-halt`.")
            log("!" * 72)
            exit_code = EXIT_BLOCKED
        finally:
            if manifest["status"] == "running":
                # Still "running" here means the loop ended normally, or an
                # unexpected exception / Ctrl-C is propagating.
                done = len(results) == len(jobs)
                manifest["status"] = ("complete" if ok == len(jobs) else "partial") if done else "interrupted"
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            manifest["ok"] = ok
            store.write_manifest(snap, manifest)

        if exit_code == EXIT_BLOCKED:
            return exit_code
        if ok == len(jobs):
            gate.record("success", snapshot=snap.name, ok=ok)
            log(f"Done: {ok}/{len(jobs)} pages saved to {snap}")
            return EXIT_OK
        gate.record("partial", snapshot=snap.name, ok=ok, failed=len(jobs) - ok, status=manifest["status"])
        log(f"Finished with problems: {ok}/{len(jobs)} pages saved to {snap}")
        return EXIT_PARTIAL
