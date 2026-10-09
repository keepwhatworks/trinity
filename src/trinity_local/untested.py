"""Which parts of a change does no test notice?

For each non-trivial source hunk of the diff (largest first, at most MAX_HUNKS),
revert just that hunk in a scratch copy of the tree and rerun the change's own
declared test commands. A hunk whose reversal leaves every test green is
UNTESTED: nothing in the declared tests depends on it. A revert that leaves code
that does not load (syntax, import or collection error) is INVALID and one that
hangs is UNCHECKED; neither counts as a test noticing the change.

Deterministic, no model call. It sharpens what a person reads; it never decides
on its own (verify still returns STOP only for a red relevant test). The idea is
the 'omissions' check from evidence-backed verification (arXiv 2610.00972); the
substrate is hq_099's finding that 92% of defect-fix commits carry a test that
goes red when the fix is reverted.

MEASURED, KILLED (hq_123, res_152): on 103 agent-written commits, commits with an
untested hunk were later fixed LESS often (lift 0.81; prevalence-matched hunk-count
and churn baselines scored 1.42 and 1.52), and untested hunks were later fixed 11
points less often than tested ones (95% CI -19 to -4). Untested marks quiet code,
not risky code. It stays OFF (TRINITY_VERIFY_UNTESTED=1) as a descriptive aid only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_HUNKS = 8
FLAG = "TRINITY_VERIFY_UNTESTED"

_IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "node_modules", "__pycache__",
                                 ".pytest_cache", ".mypy_cache", ".ruff_cache")
# Test files are where the evidence lives, not what is being checked.
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[^/]+$|\.(spec|test)\.[^/]+$")
# A hunk that only touches blank lines or comments cannot be noticed by a test.
_TRIVIAL_LINE = re.compile(r"^\s*($|#|//)")


def enabled() -> bool:
    return os.environ.get(FLAG) == "1"


@dataclass
class Hunk:
    path: str
    file_header: list[str]
    header: str
    body: list[str] = field(default_factory=list)

    def changed(self) -> list[str]:
        return [line[1:] for line in self.body if line[:1] in ("+", "-")]

    def size(self) -> int:
        return len(self.changed())

    def trivial(self) -> bool:
        return all(_TRIVIAL_LINE.match(line) for line in self.changed())

    def patch(self) -> str:
        return "\n".join(self.file_header + [self.header] + self.body) + "\n"


def parse_hunks(diff: str) -> list[Hunk]:
    """Split a unified diff (git format) into single-hunk units."""
    hunks: list[Hunk] = []
    file_header: list[str] = []
    path = ""
    cur: Hunk | None = None
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur, file_header, path, in_header = None, [line], "", True
            continue
        if in_header and not line.startswith("@@"):
            file_header.append(line)
            if line.startswith("+++ "):
                target = line[4:].strip()
                path = target[2:] if target.startswith("b/") else target
            elif line.startswith("--- ") and not path:
                source = line[4:].strip()
                path = source[2:] if source.startswith("a/") else source
            continue
        if line.startswith("@@"):
            in_header = False
            if path in ("", "/dev/null") or not file_header:
                cur = None
                continue
            cur = Hunk(path=path, file_header=list(file_header), header=line)
            hunks.append(cur)
            continue
        if cur is not None and line[:1] in (" ", "+", "-", "\\"):
            cur.body.append(line)
    return hunks


def _rebase(value: str, src: str, dst: str) -> str:
    return value.replace(src, dst) if src else value


# A reverted hunk can leave a program that does not even load. A test run that
# dies of that did not NOTICE the behaviour; it never ran it (council_1cef1a962d6c9a28).
_INVALID = re.compile(r"SyntaxError|IndentationError|TabError|ERROR collecting|errors during collection"
                      r"|ImportError while importing")


def _run(commands: list[str], cwd: Path, env: dict | None, timeout: int) -> str:
    """'green' | 'failed' (a test failed) | 'invalid' (the code did not load) | 'timeout'."""
    for cmd in commands:
        try:
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return "timeout"
        if r.returncode == 0:
            continue
        out = r.stdout + r.stderr
        # pytest: 1 = tests failed; 2 = interrupted or collection error; 3/4 = internal or usage
        if r.returncode in (2, 3, 4) or _INVALID.search(out):
            return "invalid"
        return "failed"
    return "green"


def _apply(patch: Path, cwd: Path, reverse: bool) -> bool:
    args = ["git", "apply", "--whitespace=nowarn"] + (["-R"] if reverse else []) + [str(patch)]
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True).returncode == 0


def _throwaway_repo(work: Path) -> None:
    """Give the scratch copy its OWN one-commit git repo. Tests that call git (e.g.
    `git rev-parse HEAD`) need a repo, and the copy leaves out .git on purpose: linking
    it to the user's repo would let a test write to the user's index. hq_123 run 1 read
    37% of commits as red for exactly this reason."""
    git = ["git", "-c", "user.name=trinity", "-c", "user.email=trinity@localhost",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "scratch snapshot"]):
        subprocess.run(git + args, cwd=work, capture_output=True, text=True)


def scratch_copy(cwd: Path, dest: Path) -> Path:
    """Copy the tree to `dest` (no .git, caches or venvs). If the source is a git checkout,
    the copy gets its own throwaway repo. Shared by the untested-change and doubt checks."""
    shutil.copytree(cwd, dest, ignore=_IGNORE, symlinks=True)
    if (Path(cwd) / ".git").exists():
        _throwaway_repo(dest)
    return dest


def untested_hunks(diff: str, commands: list[str], cwd: Path, env: dict | None = None,
                   timeout: int = 600, max_hunks: int = MAX_HUNKS) -> dict:
    """Revert each chosen hunk in a scratch copy of `cwd` and rerun `commands`.

    `cwd` is the tree WITH the change applied (what verify's kernel tests). Any
    occurrence of the original path in `commands` or `env` (e.g. PYTHONPATH=<cwd>/src)
    is rewritten to the scratch copy, so the tests import the copy being mutated.
    """
    t0 = time.time()
    given = str(Path(cwd))
    cwd = Path(cwd).resolve()
    # Rewrite BOTH spellings of the tree's path. On macOS /var is a symlink to
    # /private/var, so a PYTHONPATH built from mkdtemp() never contains the
    # resolved path; rewriting only that one left the tests importing the
    # untouched original, and every revert read as "untested" (hq_123 run 1).
    spellings = sorted({given, str(cwd)}, key=len, reverse=True)
    all_hunks = parse_hunks(diff)
    source = [h for h in all_hunks if not _TEST_PATH.search(h.path)]
    candidates = [h for h in source if not h.trivial()]
    base = {"ran": False, "hunks_total": len(all_hunks), "source_hunks": len(source),
            "trivial_skipped": len(source) - len(candidates)}
    if not commands:
        return dict(base, reason="no declared test commands")
    if not candidates:
        return dict(base, reason="no non-trivial source hunks")
    chosen = sorted(candidates, key=lambda h: -h.size())[:max_hunks]
    with tempfile.TemporaryDirectory(prefix="trinity-untested-") as tmp:
        work = Path(tmp) / "tree"
        scratch_copy(cwd, work)
        def rebase(value: str) -> str:
            for sp in spellings:
                value = _rebase(value, sp, str(work))
            return value
        cmds = [rebase(c) for c in commands]
        import os as _os
        env2 = {k: rebase(v) for k, v in (env or _os.environ).items()}
        # A revert can leave a file the same size within the same second (x + 1 -> x + 2);
        # Python's mtime+size bytecode cache would then run the unreverted code and the
        # revert would look "untested". No bytecode, no stale reads.
        env2["PYTHONDONTWRITEBYTECODE"] = "1"
        if _run(cmds, work, env2, timeout) != "green":
            return dict(base, reason="the declared tests are red on the change itself")
        rows = []
        for h in chosen:
            p = Path(tmp) / "hunk.patch"
            p.write_text(h.patch())
            if not _apply(p, work, reverse=True):
                rows.append({"path": h.path, "hunk": h.header, "lines": h.size(), "status": "not_revertible"})
                continue
            outcome = _run(cmds, work, env2, timeout)
            restored = _apply(p, work, reverse=False)
            status = {"green": "untested", "failed": "tested", "invalid": "invalid",
                      "timeout": "unchecked"}[outcome]
            rows.append({"path": h.path, "hunk": h.header, "lines": h.size(), "status": status})
            if not restored:                   # the copy no longer matches the change: stop here
                rows[-1]["status"] += "_unrestored"
                break
    untested = [r for r in rows if r["status"].startswith("untested")]
    counts = {k: sum(r["status"].startswith(k) for r in rows)
              for k in ("tested", "untested", "invalid", "unchecked", "not_revertible")}
    return dict(base, ran=True, assessed=len(rows), capped=max(0, len(candidates) - len(chosen)),
                untested=untested, counts=counts, hunks=rows, seconds=round(time.time() - t0, 1))
