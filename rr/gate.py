"""The rate limit, enforced in code.

Rules implemented here:
  * At most one refresh per 48 hours. The floor is a module constant; config
    can raise the interval but never lower it.
  * The window is measured from the START of the last attempt, successful or
    not. So a scheduler that fires every hour, or a job that keeps failing,
    still produces at most one burst of requests per 48 hours.
  * Every attempt/success/failure is appended to data/state/pull_log.jsonl
    with a UTC timestamp, fsync'd before any request is made.
  * The check happens while holding an exclusive file lock, so two processes
    started at the same moment can't both pass it.
  * Fail closed: unreadable log, timestamps in the future, or a HALT file
    (written when FanGraphs appears to block us) all refuse to fetch.
"""
from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

HARD_FLOOR = timedelta(hours=48)
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

try:  # POSIX
    import fcntl

    def _try_lock(f) -> bool:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

except ImportError:  # Windows
    import msvcrt

    def _try_lock(f) -> bool:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


class GateClosed(RuntimeError):
    """A fetch is not allowed right now. The message says why."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return ts


def _fmt(ts: datetime | None) -> str:
    return ts.astimezone().strftime("%Y-%m-%d %H:%M %Z") if ts else "never"


class Gate:
    def __init__(self, state_dir: Path, interval_hours: float = 48):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.state_dir / "pull_log.jsonl"
        self.halt_path = self.state_dir / "HALT"
        self.lock_path = self.state_dir / "refresh.lock"
        self.interval = max(HARD_FLOOR, timedelta(hours=interval_hours))

    # -- log ------------------------------------------------------------------

    def read_log(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        entries = []
        for n, line in enumerate(self.log_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                entry["_ts"] = _parse_ts(entry["ts"])
                entry["event"]  # must exist
            except Exception as exc:  # noqa: BLE001 - fail closed on anything odd
                raise GateClosed(
                    f"Pull log {self.log_path} line {n} is unreadable ({exc}). "
                    "Refusing to fetch until it is fixed by hand."
                ) from exc
            entries.append(entry)
        return entries

    def record(self, event: str, **fields) -> dict:
        entry = {"ts": utcnow().isoformat(timespec="seconds"), "event": event, **fields}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    # -- status / check -------------------------------------------------------

    def status(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        entries = self.read_log()
        attempts = [e["_ts"] for e in entries if e["event"] == "attempt_start"]
        successes = [e["_ts"] for e in entries if e["event"] == "success"]
        last_attempt = max(attempts) if attempts else None
        last_success = max(successes) if successes else None
        next_allowed = (last_attempt + self.interval) if last_attempt else now
        return {
            "now": now,
            "last_attempt": last_attempt,
            "last_success": last_success,
            "next_allowed": next_allowed,
            "halted": self.halt_path.exists(),
            "halt_reason": self.halt_path.read_text(encoding="utf-8") if self.halt_path.exists() else None,
        }

    def check(self, now: datetime | None = None) -> None:
        """Raise GateClosed unless a refresh is allowed right now."""
        now = now or utcnow()
        if self.halt_path.exists():
            raise GateClosed(
                "HALTED after a suspected block:\n"
                + self.halt_path.read_text(encoding="utf-8").strip()
                + "\nInvestigate, then run `python -m rr clear-halt` to re-enable."
            )
        s = self.status(now)
        last = s["last_attempt"]
        if last and last > now + CLOCK_SKEW_TOLERANCE:
            raise GateClosed(
                f"Last pull is logged at {_fmt(last)}, which is in the future. "
                "The system clock may be wrong; refusing to fetch."
            )
        if last and now < last + self.interval:
            remaining = (last + self.interval) - now
            hours = remaining.total_seconds() / 3600
            raise GateClosed(
                f"Last pull started {_fmt(last)}. Next allowed after "
                f"{_fmt(last + self.interval)} ({hours:.1f} h from now)."
            )

    def reserve(self, **fields) -> None:
        """Check the gate and log attempt_start. Call inside exclusive()."""
        self.check()
        self.record("attempt_start", **fields)

    @property
    def halted(self) -> bool:
        return self.halt_path.exists()

    # -- halt -----------------------------------------------------------------

    def halt(self, reason: str) -> None:
        with open(self.halt_path, "w", encoding="utf-8") as f:
            f.write(f"{utcnow().isoformat(timespec='seconds')}  {reason}\n")
            f.flush()
            os.fsync(f.fileno())

    def clear_halt(self) -> bool:
        if self.halt_path.exists():
            self.halt_path.unlink()
            self.record("halt_cleared")
            return True
        return False

    # -- lock -----------------------------------------------------------------

    @contextlib.contextmanager
    def exclusive(self):
        f = open(self.lock_path, "a+")
        locked = _try_lock(f)
        try:
            if not locked:
                raise GateClosed("Another refresh is already running (lock held).")
            yield self
        finally:
            if locked:
                _unlock(f)
            f.close()
