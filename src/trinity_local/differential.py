"""Does each declared test actually test this change? Kernel relevance, measured.

verify's rule counts a green test only when it is RELEVANT: it exercises the
change. Until this module, relevance was DECLARED — the caller listed the test
for this change, so it counted. This measures it the way a reproduction test is
scored in 'Teaching Agents to Code Reliably' (arXiv 2610.03984): in a scratch
copy, revert the change's source (test files stay as they are), then rerun each
test criterion that was green on the change.

    detects    red without the change: the test depends on it
    no_load    the reverted tree does not load under the test (it imports what the
               change added): it depends on the change, though it never ran its
               behaviour without it
    vacuous    still green without the change: green whether or not the change
               exists, so its green says nothing about this change
    not_reproduced  the scratch copy did not reproduce the green (the control run), so
               the revert cannot be read; the test keeps its declared relevance
    timeout    a run in the copy timed out; the test keeps its declared relevance

A green blocking test counts toward relevance unless it is vacuous. Red tests are
not touched: a red blocking test is a STOP whatever its relevance. When nothing
can be measured (no source change, a diff that does not reverse-apply, every
test not reproduced or timed out) relevance stays declared and the reason is reported.

WHAT THIS IS NOT. A risk score. Its hunk-level sibling (untested.py) was killed
as a predictor of later fixes (hq_123: lift 0.81). This only makes the kernel's
own claim, "a relevant test is green", true by measurement instead of by
declaration. Measured before building (2026-10-09, four landed commits here):
every declared test command detected its change, and 23 of the 393 tests inside
those commands did; the rest were green either way.

Tests must import the tree under test (PYTHONPATH=src for a Python project, as
run_kernel requires). A test that imports an installed copy instead reads the
original code in both runs and comes back vacuous.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from .untested import _TEST_PATH, _apply, _rebase, _run, scratch_copy, tree_spellings

FLAG = "TRINITY_VERIFY_DIFFERENTIAL"       # "0" turns it off; on by default
_ENV_DIRS = (".venv", "venv", "node_modules")
_STATUS = {"green": "vacuous", "failed": "detects", "invalid": "no_load", "timeout": "timeout"}


def enabled() -> bool:
    return os.environ.get(FLAG, "1") != "0"


def _section_path(section: list[str]) -> str:
    """The file a `diff --git` section changes: the new path, or the old one when deleted."""
    new = old = ""
    for line in section:
        if line.startswith("+++ "):
            new = line[4:].strip()
        elif line.startswith("--- "):
            old = line[4:].strip()
        elif line.startswith("@@"):
            break
    for p in (new, old):
        if p and p != "/dev/null":
            return p[2:] if p[:2] in ("a/", "b/") else p
    m = re.match(r"diff --git a/(.+) b/(.+)$", section[0].rstrip("\n"))
    return m.group(2) if m else ""


def source_patch(diff: str) -> tuple[str, int]:
    """The diff with every test-file section removed, and how many files remain."""
    sections: list[list[str]] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
    keep = [s for s in sections if (p := _section_path(s)) and not _TEST_PATH.search(p)]
    text = "".join("".join(s) for s in keep)
    return (text if text.endswith("\n") or not text else text + "\n"), len(keep)


def measure(tests: list[tuple[str, str]], diff: str, cwd: Path, env: dict | None = None,
            timeout: int = 600) -> dict:
    """`tests`: (criterion id, command) for the test criteria that were GREEN on the change.
    `cwd` is the tree WITH the change applied. Returns {ran, basis, status, ...}."""
    t0 = time.time()
    base = {"ran": False, "basis": "declared"}
    if not tests:
        return dict(base, reason="no green test criterion to check")
    patch_text, n_files = source_patch(diff)
    if not n_files:
        return dict(base, reason="the change touches only test files")
    spellings = tree_spellings(cwd)
    cwd = Path(cwd).resolve()
    with tempfile.TemporaryDirectory(prefix="trinity-differential-") as tmp:
        work = Path(tmp) / "tree"
        scratch_copy(cwd, work)
        # The copy leaves out environments; a command such as `.venv/bin/python` needs them.
        for name in _ENV_DIRS:
            if (cwd / name).exists() and not (work / name).exists():
                (work / name).symlink_to((cwd / name).resolve())

        def rebase(value: str) -> str:
            for sp in spellings:
                value = _rebase(value, sp, str(work))
            return value
        env2 = {k: rebase(v) for k, v in (env or os.environ).items()}
        env2["PYTHONDONTWRITEBYTECODE"] = "1"      # a stale .pyc would hide the revert
        status: dict[str, str] = {}
        live = []
        for cid, cmd in tests:
            c = rebase(cmd)
            # Control: the copy must reproduce the green before the revert can mean anything.
            control = _run([c], work, env2, timeout)
            if control == "green":
                live.append((cid, c))
            else:
                status[cid] = "timeout" if control == "timeout" else "not_reproduced"
        if not live:
            return dict(base, reason=_why_unmeasured(status), status=status,
                        seconds=round(time.time() - t0, 1))
        p = Path(tmp) / "source.patch"
        p.write_text(patch_text)
        if not _apply(p, work, reverse=True):
            return dict(base, reason="the change's source does not reverse-apply to this tree",
                        status=status, seconds=round(time.time() - t0, 1))
        for cid, c in live:
            status[cid] = _STATUS[_run([c], work, env2, timeout)]
    measured = any(s in _MEASURED for s in status.values())
    return {"ran": measured, "basis": "measured" if measured else "declared",
            "status": status, "source_files": n_files, "seconds": round(time.time() - t0, 1),
            **({} if measured else {"reason": _why_unmeasured(status)})}


_MEASURED = ("detects", "no_load", "vacuous")


def _why_unmeasured(status: dict[str, str]) -> str:
    n_rep = sum(s == "not_reproduced" for s in status.values())
    n_out = sum(s == "timeout" for s in status.values())
    parts = ([f"{n_rep} not reproduced in the scratch copy"] if n_rep else []) + \
            ([f"{n_out} timed out"] if n_out else [])
    return "no green test could be measured: " + (", ".join(parts) or "none ran")


def relevance(runs: list[dict], blocking: set[str], measured: dict | None) -> bool | None:
    """Kernel relevance from the measurement, or None to keep the declared value.
    Relevant when a blocking test is red (STOP stands) or a green blocking test is not
    vacuous."""
    if not measured or not measured.get("ran"):
        return None
    status = measured.get("status") or {}
    ids = [r["id"] for r in runs if r["id"] in blocking]
    if not ids:
        return None
    if any(not r["green"] for r in runs if r["id"] in blocking):
        return True
    return any(status.get(i) != "vacuous" for i in ids)
