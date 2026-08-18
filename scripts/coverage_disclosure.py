"""What the local gate's green does NOT attest.

`launch-check.sh` ends with "All gates passed", and step 1 runs the suite where
browser-marked tests SKIP without Chrome rather than fail. So the green is honest about
every test it ran and silent about the ones it did not, which reads as full coverage.

That gap is measured, not suspected. Two disjoint stratified samples of this repo's own
fix-with-guard commits (n=192 and n=178, hq_099b/hq_099c) found that **30% and 29% of
them put their ENTIRE guard in the browser tier** -- guards that protect a real fix and
are silent on every default run. The tier itself is 498 of 4706 collected tests, so the
test-count share (11%) badly understates the FIX-GUARD share (~30%).

This does not change any verdict and must not: the browser tier has its own CI job, and
turning a missing local dependency into a red gate would make the common case fail for a
reason the developer cannot fix locally. It states the coverage instead, which is the
green-gate rule this repo already applies everywhere else -- a green attests what it
measured, and says what it did not.

Refuses rather than guessing: if the collection count cannot be read, it says so.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Measured 2026-08-17, hq_099b + hq_099c, two disjoint stratified draws.
FIX_GUARD_SHARE = "~30%"


def collect_counts(runner: list[str] | None = None) -> tuple[int, int] | None:
    """(browser_tests, total_tests), or None when the count cannot be established."""
    cmd = (runner or [sys.executable, "-m", "pytest"]) + [
        "tests/", "-q", "--collect-only", "-m", "browser", "-p", "no:cacheprovider"]
    try:
        out = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                             timeout=300).stdout
    except Exception:
        return None
    return parse_counts(out)


def parse_counts(text: str) -> tuple[int, int] | None:
    """Read pytest's `N/M tests collected` line.

    Kept separate from the subprocess call so the parsing is testable without running
    pytest -- the repo's own lesson that a guard exercising a reimplementation instead of
    the real function is decoration.
    """
    m = re.search(r"(\d+)\s*/\s*(\d+)\s+tests collected", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    # A bare "N tests collected" means nothing was deselected, i.e. the marker selected
    # everything -- not a number this disclosure can honestly use.
    return None


def render(counts: tuple[int, int] | None) -> str:
    if counts is None:
        return ("  coverage: browser-tier guard count UNAVAILABLE — this green does not "
                "attest them either way")
    browser, total = counts
    return (
        f"  coverage: this green does NOT attest {browser} browser-tier guards "
        f"({browser / total:.0%} of {total} collected tests, but the entire guard for "
        f"{FIX_GUARD_SHARE} of fix commits — measured, hq_099b/hq_099c).\n"
        f"  run them with:  .venv/bin/python -m pytest tests/ -m browser -q")


def main() -> int:
    print(render(collect_counts()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
