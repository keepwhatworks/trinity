"""A scratch copy of the user's tree, and running their test commands in it.

verify's differential check reruns the change's own tests with the change's source
reverted, in a copy so the user's tree is never touched. The copy has to behave like
the original for the tests to mean anything; every rule below exists because a
difference made a test read wrong (the environment-fidelity class):

- Only what git would carry: tracked files plus untracked ones git does not ignore.
  Copying the whole directory took 524 MB from this repo (internal/ 375 MB, other
  worktrees under .claude/), and would copy a project's target/ or data/ in full.
- Regular files, directories and symlinks only: a socket (Rails tmp/sockets/puma.sock)
  or FIFO made copytree raise and crashed the whole verify.
- The copy gets its own throwaway git repo (see _throwaway_repo).
- A test command that times out takes its whole process group with it: killing only
  the shell left `sleep`, a server, or a test runner writing into the copy while it
  was deleted.

These helpers lived in untested.py beside the killed hunk check (hq_123); that check
moved to internal/experiments/untested_hunks.py (coherence review 2026-10-15).
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
from pathlib import Path

# Environments the copy leaves out; link_env_dirs links them back.
_ENV_DIRS = (".venv", "venv", "node_modules")
# Not code under test: caches and environments, when the tree is not a git checkout.
_IGNORE = shutil.ignore_patterns(".git", *_ENV_DIRS, "__pycache__",
                                 ".pytest_cache", ".mypy_cache", ".ruff_cache")
# Test files are where the evidence lives, not what is being checked.
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[^/]+$|\.(spec|test)\.[^/]+$")
# A revert can leave a program that does not even load. A test run that dies of that did
# not NOTICE the behaviour; it never ran it (council_1cef1a962d6c9a28).
_INVALID = re.compile(r"SyntaxError|IndentationError|TabError|ERROR collecting|errors during collection"
                      r"|ImportError while importing")


def _rebase(value: str, src: str, dst: str) -> str:
    return value.replace(src, dst) if src else value


def tree_spellings(cwd) -> list[str]:
    """Every ABSOLUTE spelling of the tree's path, longest first, for rewriting commands
    and env onto a scratch copy. On macOS /var is a symlink to /private/var, so both the
    given and the resolved spelling occur (hq_123 run 1). A relative spelling is never
    rewritten: verify's CLI passes --cwd ".", and replacing "." rewrote every dot in a
    command (`.venv/bin/python`, `test_x.py`), so no test ran in the copy (found
    dogfooding the differential check, 2026-10-09). Relative paths resolve inside the copy
    on their own."""
    p = Path(cwd)
    return sorted({str(p.absolute()), str(p.resolve())}, key=len, reverse=True)


def run_shell(command: str, cwd: Path, env: dict | None, timeout: float) -> tuple[int | None, str]:
    """Run one shell command in its own process group. (returncode, combined output);
    returncode None means it timed out and the whole group was killed.

    `subprocess.run(shell=True, timeout=...)` kills only the shell: `true && sleep 7` with a
    1 s timeout left the sleep running, and in the scratch copy an orphaned test kept
    writing while the copy was deleted (coherence review 2026-10-15)."""
    proc = subprocess.Popen(command, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, _ = proc.communicate()
        return None, out or ""


def classify(rc: int | None, out: str) -> str:
    """One run's outcome: 'green' | 'failed' (a test failed) | 'invalid' (the code did not load)
    | 'timeout'. pytest exits 1 when tests fail, 2 on an interrupt or collection error, 3/4 on an
    internal or usage error."""
    if rc is None:
        return "timeout"
    if rc == 0:
        return "green"
    return "invalid" if rc in (2, 3, 4) or _INVALID.search(out or "") else "failed"


def _run(commands: list[str], cwd: Path, env: dict | None, timeout: int) -> str:
    """The first non-green outcome of `commands`, else 'green' (see classify)."""
    for cmd in commands:
        status = classify(*run_shell(cmd, cwd, env, timeout))
        if status != "green":
            return status
    return "green"


def _apply(patch: Path, cwd: Path, reverse: bool) -> bool:
    args = ["git", "apply", "--whitespace=nowarn"] + (["-R"] if reverse else []) + [str(patch)]
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True).returncode == 0


def _source_objects(cwd: Path) -> Path | None:
    """The source checkout's object store, for a worktree too (its .git is a file pointing at
    the common dir)."""
    r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       cwd=cwd, capture_output=True, text=True)
    objects = Path(r.stdout.strip()) / "objects" if r.returncode == 0 and r.stdout.strip() else None
    return objects if objects is not None and objects.is_dir() else None


def _source_tracked(cwd: Path) -> list[str]:
    """Paths the source repo tracks (NUL-separated, so any filename survives)."""
    r = subprocess.run(["git", "ls-files", "-z"], cwd=cwd, capture_output=True, text=True)
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []


def _git_visible(cwd: Path) -> list[str] | None:
    """Tracked files plus untracked files git does not ignore, or None outside a work tree."""
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       cwd=cwd, capture_output=True, text=True)
    return sorted({p for p in r.stdout.split("\0") if p}) if r.returncode == 0 else None


def _copy_entry(src: Path, dst: Path) -> None:
    """Copy one entry git lists: a regular file, a symlink, or a directory (a submodule,
    copied whole). Anything else (socket, FIFO, device) is skipped."""
    try:
        mode = src.lstat().st_mode
    except OSError:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if stat.S_ISLNK(mode):
        dst.symlink_to(os.readlink(src))
    elif stat.S_ISREG(mode):
        shutil.copy2(src, dst)
    elif stat.S_ISDIR(mode) and not dst.exists():
        shutil.copytree(src, dst, ignore=_skip_special(), symlinks=True)


def _throwaway_repo(work: Path, borrow: Path | None = None, tracked: list[str] | None = None) -> None:
    """Give the scratch copy its OWN one-commit git repo. Tests that call git (e.g.
    `git rev-parse HEAD`) need a repo, and the copy leaves out .git on purpose: linking
    it to the user's repo would let a test write to the user's index. hq_123 run 1 read
    37% of commits as red for exactly this reason.
    The snapshot commit passes git's auto-gc threshold on a large tree, and git then repacks in
    a detached background process still writing into .git when the copy is deleted ("Directory
    not empty" killed verify, 2026-10-10). The throwaway repo never runs maintenance.
    `borrow` is the source's object store, added as a read-only git alternate: a test that
    resolves a historic commit (a doc-cited SHA) ran red in a history-less copy, so verify's
    differential reported it 'not checked' (res_186). Git never writes into an alternate, and the
    copy keeps its own index and refs.
    `tracked` is the source's tracked paths: `add -A` honours .gitignore, so a file the source
    force-tracks would read untracked in the copy and a test asserting it is tracked would go red
    there. Those paths are force-added; ignored files the source does NOT track stay out."""
    git = ["git", "-c", "user.name=trinity", "-c", "user.email=trinity@localhost",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
           "-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    subprocess.run(git + ["init", "-q"], cwd=work, capture_output=True, text=True)
    if borrow is not None:
        (work / ".git" / "objects" / "info" / "alternates").write_text(f"{borrow}\n")
    subprocess.run(git + ["add", "-A"], cwd=work, capture_output=True, text=True)
    present = [p for p in (tracked or []) if (work / p).exists() or (work / p).is_symlink()]
    if present:
        # --literal-pathspecs: a tracked name like "*.txt" is a filename, not a glob that would
        # force-add ignored files the source never tracked.
        subprocess.run(git + ["--literal-pathspecs", "add", "-f", "--pathspec-from-file=-",
                              "--pathspec-file-nul"], cwd=work,
                       input="\0".join(present), capture_output=True, text=True)
    subprocess.run(git + ["commit", "-q", "--no-verify", "-m", "scratch snapshot"], cwd=work,
                   capture_output=True, text=True)


def link_env_dirs(src: Path, work: Path) -> None:
    """Symlink into `work` the environments a scratch copy of `src` left out, so a
    command such as `.venv/bin/python` still runs in the copy. One already in `work`
    is kept."""
    for name in _ENV_DIRS:
        if (src / name).exists() and not (work / name).exists():
            (work / name).symlink_to((src / name).resolve())


def scratch_copy(cwd: Path, dest: Path) -> Path:
    """Copy the tree to `dest`. In a git work tree: the files git would carry (tracked, plus
    untracked and not ignored), and a throwaway repo of its own. Elsewhere: everything but
    caches, environments and non-regular files."""
    cwd = Path(cwd)
    visible = _git_visible(cwd)
    if visible is None:
        shutil.copytree(cwd, dest, ignore=_skip_special(), symlinks=True)
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    for rel in visible:
        _copy_entry(cwd / rel, dest / rel)
    _throwaway_repo(dest, _source_objects(cwd), _source_tracked(cwd))
    return dest


def _skip_special():
    """copytree ignore: the fixed cache/env list plus anything that is not a regular file,
    directory or symlink (a socket made copytree raise and crashed verify)."""
    def ignore(directory, names):
        skip = set(_IGNORE(directory, names))
        for n in names:
            try:
                mode = os.lstat(os.path.join(directory, n)).st_mode
            except OSError:
                skip.add(n)
                continue
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                skip.add(n)
        return skip
    return ignore
