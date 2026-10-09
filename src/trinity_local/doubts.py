"""A reader's doubt counts only if it comes with a test that fails.

verify's readers vote FAIL from the diff alone, and on agent-written commits those
votes did not pick out the later-fixed ones (hq_122: lift 1.07). Evidence-backed
verification (arXiv 2610.00972) resolves a disputed claim by running the check that
separates the candidates, not by counting votes. Here: every FAIL must come with a
self-contained pytest test that should fail on the changed code if the doubt is right.
The kernel runs it in a scratch copy with the network denied. The reader proposes;
execution decides; nothing the reader writes runs against the user's tree.

  backed       the test PASSES on the code before the change, FAILS by assertion after it,
               and the reader quoted, verbatim, a pre-change line stating that behaviour
  backed_unquoted  the same before/after behaviour, but no verifiable pre-change quote
  spec_disagreement  the test fails before the change too: it asserts something that
               never held, a preference rather than a regression (council_39db12c038cf80ca)
  not_backed   the test passes on the change
  invalid      the test does not load (syntax, import, collection error)
  unchecked    the test hangs
  no_test      the reader doubted but gave no test
  not_run_no_sandbox  no macOS sandbox-exec here, so model-written code is not run

With the flag on, the reader prompt also asks for a test, so readers' votes (and so
STOP/READ) can differ from a run with the flag off; the doubt rows themselves never
change the triage.

MEASURED, KILLED at the positive control (hq_124, res_154): backed doubts fired on 80%
of diffs that reintroduce a known bug, but also on 52.5% of clean commits (bar: 2x). A
test that passes before and fails after detects that behaviour CHANGED, which most commits
intend; the pre-change quote did not separate intent from regression (93/99 doubts had
one). Only the author's declared tests encode intent. OFF (TRINITY_VERIFY_BACKED=1).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from .untested import _run, scratch_copy

FLAG = "TRINITY_VERIFY_BACKED"
TIMEOUT = 120

PROMPT_ADDENDUM = """
For EVERY criterion you vote FAIL, also write ONE self-contained pytest test file that
FAILS on the changed code if your concern is right, and would pass once it is fixed. It
must import the project's own code (never re-implement it), use no network, and run with
pytest from the repository root. It must PASS on the code before this change. Also quote,
verbatim, one existing line from BEFORE the change (a '-' or unchanged line of the diff)
that states the behaviour your test checks. Add both to your JSON as
"tests": {{"<criterion id>": "<the whole python file>"}} and
"evidence": {{"<criterion id>": "<the verbatim pre-change line>"}}. A FAIL without a test is
still reported, but only a test that passes before and fails after counts as evidence.
"""
MIN_QUOTE = 20

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# Environment keys a model-written test may see. Nothing else is inherited: an API key
# or token in the caller's environment must never reach code a model wrote.
_ENV_KEEP = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "VIRTUAL_ENV", "TRINITY_DISABLE_MLX",
             "TRINITY_AUTOSCAN_DISABLED")


def _python_prefixes(python: str) -> list[str]:
    import subprocess as sp
    r = sp.run([python, "-c", "import sys,os,json; print(json.dumps(sorted({os.path.realpath(p) for p in "
                "(sys.prefix, sys.base_prefix, os.path.dirname(os.path.realpath(sys.executable)))})))"],
               capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return []


def sandbox_profile(scratch: Path, python: str, original: Path | None = None) -> str:
    """macOS sandbox for code a model wrote (security review of this module, 2026-10-04):
    no network; no writes outside the scratch dir; no reads of file CONTENTS in the user's
    home or in the user's original tree (wherever it lives) except the Python install the
    tests need and the scratch copy itself. Metadata reads (names, sizes) stay allowed: the
    interpreter cannot resolve its own path without them."""
    home = os.path.realpath(os.path.expanduser("~"))
    scratch = os.path.realpath(str(scratch))
    denied = f'(subpath "{home}")' + (f' (subpath "{os.path.realpath(str(original))}")' if original else "")
    allow_read = " ".join(f'(subpath "{p}")' for p in [scratch, *_python_prefixes(python)])
    return ("(version 1)(allow default)(deny network*)"
            f'(deny file-write*)(allow file-write* (subpath "{scratch}") (literal "/dev/null") (regex #"^/dev/(tty|fd/)"))'
            f"(deny file-read-data {denied})(allow file-read-data {allow_read})")


def enabled() -> bool:
    return os.environ.get(FLAG) == "1"


_JSON_START = re.compile(r"\{")


def parse_tests(text: str, ids: list[str], key: str = "tests") -> dict[str, str]:
    """criterion id -> string under `key` ("tests" or "evidence") from the reader's JSON.
    Two readable but different objects is ambiguity, and ambiguity returns nothing."""
    found: list[dict] = []
    decoder = json.JSONDecoder()
    for m in _JSON_START.finditer(text or ""):
        try:
            d, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and isinstance(d.get(key), dict):
            found.append({k: v for k, v in d[key].items()
                          if k in ids and isinstance(v, str) and v.strip()})
    if not found or any(f != found[0] for f in found[1:]):
        return {}
    return found[0]


def python_for(commands: list[str]) -> str:
    """The interpreter the project's own tests use, when the first test command names one."""
    for cmd in commands:
        first = (cmd.split() or [""])[0]
        if os.path.basename(first).startswith("python") and os.path.isabs(first):
            return first
    return sys.executable


def _pre_change_lines(diff: str) -> set[str]:
    return {line[1:].strip() for line in (diff or "").splitlines()
            if line[:1] in ("-", " ") and not line.startswith("---") and len(line[1:].strip()) >= MIN_QUOTE}


def grounded(quote: str | None, diff: str) -> bool:
    """The quote must be verbatim text from ONE pre-change line of the diff (removed or
    unchanged context): the whole line or a part of it, never a line plus extra text."""
    q = (quote or "").strip()
    if len(q) < MIN_QUOTE:
        return False
    return any(q in line for line in _pre_change_lines(diff))


def run_doubt_tests(reads: list[dict], cwd: Path, python: str, env: dict | None = None,
                    timeout: int = TIMEOUT, diff: str = "") -> list[dict]:
    """Run every doubt's test on a scratch copy of the code AFTER the change (`cwd`) and on
    one BEFORE it (the copy with `diff` reversed). Returns one row per doubt."""
    rows: list[dict] = []
    doubts = [(r, cid) for r in reads for cid, ok in (r.get("votes") or {}).items() if ok is False]
    if not doubts:
        return rows
    if not Path(SANDBOX_EXEC).exists():
        # Model-written code never runs unsandboxed: no sandbox, no execution.
        return [{"provider": r.get("provider"), "lab": r.get("lab"), "criterion": cid,
                 "status": "not_run_no_sandbox"} for r, cid in doubts]
    given = str(Path(cwd))
    cwd = Path(cwd).resolve()
    with tempfile.TemporaryDirectory(prefix="trinity-doubt-") as tmp:
        work = scratch_copy(cwd, Path(tmp) / "tree")
        home = Path(tmp) / "home"
        (home / "tmp").mkdir(parents=True)
        src = env or os.environ
        env2 = {}
        for k in _ENV_KEEP:
            if k in src:
                v = src[k]
                for spelling in sorted({given, str(cwd)}, key=len, reverse=True):
                    v = v.replace(spelling, str(work))
                env2[k] = v
        env2.update(HOME=str(home), TRINITY_HOME=str(home / ".trinity"), TMPDIR=str(home / "tmp"))
        profile = sandbox_profile(Path(tmp), python, original=cwd)
        # The code before the change: the same copy with the diff reversed.
        before = None
        if diff.strip():
            from .untested import _apply
            cand = scratch_copy(cwd, Path(tmp) / "before")
            (Path(tmp) / "change.patch").write_text(diff if diff.endswith("\n") else diff + "\n")
            if _apply(Path(tmp) / "change.patch", cand, reverse=True):
                before = cand
                (before / "tests").mkdir(exist_ok=True)
        env_before = {k: v.replace(str(work), str(before)) for k, v in env2.items()} if before else None
        (work / "tests").mkdir(exist_ok=True)
        for i, (r, cid) in enumerate(doubts):
            code = (r.get("tests") or {}).get(cid)
            row = {"provider": r.get("provider"), "lab": r.get("lab"), "criterion": cid}
            if not code:
                rows.append(dict(row, status="no_test"))
                continue
            import shlex
            rel = f"tests/test_trinity_doubt_{i}.py"

            def run_on(tree, env_):
                (tree / rel).write_text(code)
                cmd = (f"{SANDBOX_EXEC} -p {shlex.quote(profile)} "
                       f"{shlex.quote(python)} -m pytest -q -x -p no:cacheprovider {shlex.quote(rel)}")
                try:
                    return _run([cmd], tree, env_, timeout)
                finally:
                    (tree / rel).unlink(missing_ok=True)
            after = run_on(work, env2)
            if after == "green":
                status = "not_backed"
            elif after in ("invalid", "timeout"):
                status = {"invalid": "invalid", "timeout": "unchecked"}[after]
            elif before is None:
                status = "backed_unchecked_before"          # no reversible diff: cannot run the before side
            else:
                prior = run_on(before, env_before)
                if prior == "green":
                    quote = (r.get("evidence") or {}).get(cid)
                    status = "backed" if grounded(quote, diff) else "backed_unquoted"
                elif prior == "failed":
                    status = "spec_disagreement"
                else:
                    status = "before_" + prior               # the test does not load or hangs on the old code
            rows.append(dict(row, status=status, test_lines=len(code.splitlines())))
    return rows
