#!/usr/bin/env python3
"""Real-data degeneracy sweep — "find more issues like the last fifteen".

Thin CLI wrapper over trinity_local.degeneracy.run_degeneracy_sweep() so the
same checks run here (cron/manual) AND inside `trinity-local status`
(_check_data_degeneracy). See the module docstring for the issue-class map.

    python scripts/degeneracy_sweep.py          # human report
    python scripts/degeneracy_sweep.py --json    # machine-readable

Exit code is non-zero when any check fires, so a cron can gate on it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trinity_local.degeneracy import run_degeneracy_sweep  # noqa: E402


def main() -> int:
    findings = run_degeneracy_sweep()
    if "--json" in sys.argv:
        print(json.dumps({"findings": findings, "clean": not findings}, indent=2))
    elif findings:
        print("DEGENERACY SWEEP — findings:")
        for f in findings:
            print(f"  ✘ {f}")
    else:
        print("DEGENERACY SWEEP — clean (all known degenerate classes absent on real data).")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
