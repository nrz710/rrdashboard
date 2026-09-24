"""Stdlib-only gate check, so CI can skip installing dependencies when the gate is closed.

    python -m rr.gatecheck      exit 0 = a refresh may run now
                                exit 3 = too soon (reason printed)
                                exit 4 = halted after a suspected block (reason printed)

This is only an optimization. The authoritative check still happens inside
`python -m rr refresh`, under the lock, immediately before any request.
"""
import sys

from . import config
from .gate import Gate, GateClosed


def main() -> int:
    gate = Gate(config.STATE_DIR, config.MIN_REFRESH_INTERVAL_HOURS)
    if gate.halt_path.exists():
        print("HALTED: " + gate.halt_path.read_text(encoding="utf-8").strip())
        return 4
    try:
        gate.check()
    except GateClosed as exc:
        print(f"Gate closed: {exc}")
        return 3
    print("Gate open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
