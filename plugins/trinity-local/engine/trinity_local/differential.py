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

WHAT THIS IS NOT. A risk score. Its hunk-level sibling (internal/experiments/untested_hunks.py) was killed
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

from .scratch import _TEST_PATH, _apply, _rebase, _run, link_env_dirs, scratch_copy, tree_spellings

FLAG = "TRINITY_VERIFY_DIFFERENTIAL"       # "0" turns it off; on by default
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
        link_env_dirs(cwd, work)

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
        # POSITIVE CONTROL before believing "vacuous". A test that imports the code from OUTSIDE
        # the copy (an editable install's .pth points at the original tree) stays green whatever
        # the copy holds, and read as "green without the change too", for the most common Python
        # setup (coherence review 2026-10-15). Remove the changed source files outright: a test
        # that loads them from the copy must go red; one still green never loaded them.
        suspects = [(cid, c) for cid, c in live if status[cid] == "vacuous"]
        if suspects:
            for rel in _changed_paths(patch_text):
                # Code only: removing a changed config or data file could fail the test for an
                # unrelated reason and leave a false "vacuous" standing (Codex's verify read).
                if Path(rel).suffix not in _CODE_SUFFIXES:
                    continue
                target = work / rel
                if target.is_file() or target.is_symlink():
                    target.unlink()
            for cid, c in suspects:
                if _run([c], work, env2, timeout) == "green":
                    status[cid] = "not_loaded"
    measured = any(s in _MEASURED for s in status.values())
    return {"ran": measured, "basis": "measured" if measured else "declared",
            "status": status, "source_files": n_files, "seconds": round(time.time() - t0, 1),
            **({} if measured else {"reason": _why_unmeasured(status)})}


_MEASURED = ("detects", "no_load", "vacuous")


_CODE_SUFFIXES = {".py", ".pyx", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".go",
                  ".rs", ".java", ".kt", ".scala", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift",
                  ".m", ".mm", ".php", ".cs", ".ex", ".exs", ".erl", ".lua", ".pl", ".r", ".jl"}


def _changed_paths(patch_text: str) -> list[str]:
    """The files a source patch touches, relative to the tree."""
    paths = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+) b/(.+)$", line)
            if m:
                paths.append(m.group(2))
    return paths


def _why_unmeasured(status: dict[str, str]) -> str:
    n_rep = sum(s == "not_reproduced" for s in status.values())
    n_out = sum(s == "timeout" for s in status.values())
    n_nl = sum(s == "not_loaded" for s in status.values())
    # The copy carries what git carries (scratch.py): a test that needs an ignored file (a local
    # fixture, a built extension, .env) is green in the tree and red in the copy. Say so, since
    # the fix is the user's: track the file or build it in the test command.
    parts = ([f"{n_rep} not reproduced in the scratch copy, which carries only what git carries "
              "(a test that needs an ignored file cannot be measured)"] if n_rep else []) + \
            ([f"{n_out} timed out"] if n_out else []) + \
            ([f"{n_nl} load the code from outside the tree (an editable install?), so no revert "
              "can reach them"] if n_nl else [])
    return "no green test could be measured: " + (", ".join(parts) or "none ran")


def relevance(runs: list[dict], blocking: set[str], measured: dict | None) -> bool | None:
    """Kernel relevance from the measurement, or None to keep the declared value.
    Relevant when a blocking test is red (STOP stands) or a green blocking test is not
    vacuous."""
    if not measured or not measured.get("ran"):
        return None
    status = measured.get("status") or {}
    # Only blocking tests that RAN to a result: one that could not run (exit 126/127) has no
    # measurement, and counting it made the kernel "relevant" on its behalf.
    ids = [r["id"] for r in runs if r["id"] in blocking and r.get("green") is not None]
    if not ids:
        return None
    if any(r["green"] is False for r in runs if r["id"] in blocking):
        return True
    return any(status.get(i) != "vacuous" for i in ids)
