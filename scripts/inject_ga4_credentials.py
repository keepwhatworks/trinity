#!/usr/bin/env python3
"""Write the GA4 ingestion pair into the package immediately before a build.

WHY THIS EXISTS. `pip install trinity-local` gives a user no GA4 env vars, so an
env-only lookup meant every shipped install reported healthy and transmitted
nothing — counting activations would have read zero whether or not anyone
installed. The pair therefore has to travel inside the wheel.

WHY IT IS NOT COMMITTED. This repo mirrors to a public repository. A committed
api_secret is a published one. So the module is generated here, is gitignored,
and exists only in the build tree.

WHAT THIS IS NOT. A GA4 Measurement Protocol api_secret is a WRITE-ONLY
ingestion key: it can add events to the property and cannot read anything out.
The realistic abuse is junk events, which GA4 filters handle. It is not a
password, not an account credential, and must never be stored or treated as one.

Usage, from the maintainer's shell only:
    export TRINITY_GA4_MEASUREMENT_ID=G-XXXXXXXXXX
    export TRINITY_GA4_API_SECRET=...
    python scripts/inject_ga4_credentials.py && python -m build

    # and to be sure it never lands in git:
    python scripts/inject_ga4_credentials.py --clean
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

TARGET = pathlib.Path(__file__).resolve().parent.parent / "src" / "trinity_local" / "_ga4_bundled.py"

TEMPLATE = '''"""GENERATED AT PUBLISH TIME — do not commit, do not edit.

Written by scripts/inject_ga4_credentials.py. Gitignored on purpose: this repo
mirrors publicly. Holds a write-only GA4 ingestion pair, nothing readable.
"""

MEASUREMENT_ID = {mid!r}
API_SECRET = {sec!r}
'''


def _tracked_by_git() -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(TARGET)],
                       capture_output=True, text=True,
                       cwd=str(TARGET.parent.parent.parent))
    return r.returncode == 0


def main() -> int:
    if "--clean" in sys.argv:
        if TARGET.exists():
            TARGET.unlink()
            print(f"removed {TARGET.name}")
        else:
            print("nothing to remove")
        return 0

    mid = os.environ.get("TRINITY_GA4_MEASUREMENT_ID", "").strip()
    sec = os.environ.get("TRINITY_GA4_API_SECRET", "").strip()
    if not mid or not sec:
        print("REFUSED: set TRINITY_GA4_MEASUREMENT_ID and TRINITY_GA4_API_SECRET.\n"
              "Writing a half-filled module would ship an install that still "
              "silently discards every event, which is the defect this exists to "
              "fix.", file=sys.stderr)
        return 1
    if not mid.startswith("G-"):
        print(f"REFUSED: measurement id {mid!r} does not look like G-XXXXXXXXXX. "
              "That field is often confused with the numeric property id, which "
              "the Measurement Protocol silently ignores — the exact shape of "
              "failure this whole change exists to prevent.", file=sys.stderr)
        return 1

    TARGET.write_text(TEMPLATE.format(mid=mid, sec=sec), encoding="utf-8")
    print(f"wrote {TARGET.name} ({len(sec)}-char secret withheld from this output)")

    if _tracked_by_git():
        print("FATAL: the generated module is TRACKED BY GIT. It would be "
              "published on the next sync. Run `git rm --cached "
              "src/trinity_local/_ga4_bundled.py` and confirm .gitignore covers "
              "it before building.", file=sys.stderr)
        return 2
    print("confirmed untracked — safe to build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
