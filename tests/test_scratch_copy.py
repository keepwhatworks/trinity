"""The scratch copy verify's differential check runs the user's tests in (scratch.py).

It used to live in untested.py beside the hunk check killed at hq_123; that check moved to
internal/experiments (2026-10-15). Properties the copy must keep are tested here, the ones
only visible end to end through the differential check, its live consumer."""
from __future__ import annotations

import subprocess
import sys
import pytest

from trinity_local import differential
from trinity_local import scratch as ut

SPACER = "\n".join(f"# spacer {i}" for i in range(8)) + "\n"
BEFORE = '''def covered(x):
    return x + 1

''' + SPACER + '''
def uncovered(x):
    return x * 2
'''
AFTER = '''def covered(x):
    return x + 10

''' + SPACER + '''
def uncovered(x):
    return x * 3
'''
TEST = '''from pkg.mod import covered


def test_covered():
    assert covered(1) == 11
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


@pytest.fixture
def project(tmp_path):
    """A repo whose change edits two functions; only one is covered by the test."""
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "mod.py").write_text(BEFORE)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "before")
    (root / "pkg" / "mod.py").write_text(AFTER)
    (root / "tests" / "test_mod.py").write_text(TEST)
    (root / "pkg" / "notes.py").write_text("# only a comment\n")
    _git(root, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"
    return root, diff, cmd


@pytest.mark.parametrize("cmd, expected", [
    ("true", "green"),
    ("exit 1", "failed"),                                                  # a test failed
    ("echo 'E   SyntaxError: invalid syntax'; exit 1", "invalid"),         # the code did not load
    ("echo 'ERROR collecting tests/test_x.py'; exit 1", "invalid"),
    ("exit 2", "invalid"),                                                 # pytest: collection error
    ("sleep 5", "timeout"),
])
def test_outcomes_that_are_not_a_test_noticing_are_kept_apart(tmp_path, cmd, expected):
    """Only a real test failure counts as 'tested' (council_1cef1a962d6c9a28)."""
    assert ut._run([cmd], tmp_path, None, timeout=1) == expected


def test_the_scratch_repo_starts_no_git_maintenance(tmp_path, monkeypatch):
    """The throwaway commit snapshots the whole tree. On a large tree that passes git's auto-gc
    threshold, and git 2.54 then repacks in a DETACHED background process that is still writing
    into .git when the copy is deleted: verify died with "Directory not empty" (2026-10-10).
    Force auto maintenance on, in the foreground, through a throwaway global config: the copy
    must still hold no pack, i.e. no maintenance ran."""
    root = tmp_path / "big"
    root.mkdir()
    for i in range(2000):
        (root / f"f{i}.txt").write_text(f"{i}\n")
    _git(root, "init", "-q")
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[gc]\n\tauto = 1\n\tautoDetach = false\n"
                   "[maintenance]\n\tauto = true\n\tautoDetach = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    copy = ut.scratch_copy(root, tmp_path / "copy")
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=copy, capture_output=True).returncode == 0
    packs = list((copy / ".git" / "objects" / "pack").glob("*.pack"))
    assert not packs, f"git maintenance ran in the scratch repo: {packs}"


def _two_commit_repo(root):
    root.mkdir()
    (root / "a.txt").write_text("one\n")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "first")
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    (root / "a.txt").write_text("two\n")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "second")
    return first


@pytest.mark.parametrize("via_worktree", [False, True])
def test_the_scratch_repo_resolves_the_source_history(tmp_path, via_worktree):
    """Tests that check a doc-cited commit still exists ran red in the history-less scratch copy,
    so verify's differential reported them 'not checked' (res_186). The copy borrows the source
    repo's objects read-only, also when the source is a git worktree (a .git FILE)."""
    repo = tmp_path / "repo"
    first = _two_commit_repo(repo)
    src = repo
    if via_worktree:
        src = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", "--detach", str(src), "HEAD")
    refs_before = subprocess.run(["git", "for-each-ref"], cwd=repo, capture_output=True, text=True).stdout
    copy = ut.scratch_copy(src, tmp_path / "copy")
    assert subprocess.run(["git", "cat-file", "-e", first], cwd=copy).returncode == 0, \
        "a commit from the source's history must resolve in the scratch copy"
    # A test that commits inside the copy writes to the copy, never to the source.
    (copy / "b.txt").write_text("scratch\n")
    _git(copy, "add", "-A"); _git(copy, "commit", "-qm", "scratch")
    assert subprocess.run(["git", "for-each-ref"], cwd=repo, capture_output=True, text=True).stdout == refs_before
    assert subprocess.run(["git", "status", "--porcelain"], cwd=src, capture_output=True, text=True).stdout == ""


def test_the_scratch_repo_tracks_what_the_source_tracks(tmp_path):
    """A file the source force-tracks despite .gitignore (a skill file, say) must be tracked in the
    copy too, or a test asserting it is tracked reads red there and verify calls it 'not
    checked'. An ignored file the source does NOT track stays out of the copy's index."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("kept/\n.env\n")
    (repo / "kept").mkdir()
    (repo / "kept" / "skill.md").write_text("tracked on purpose\n")
    (repo / ".env").write_text("TOKEN=not-a-real-one\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".gitignore")
    _git(repo, "add", "-f", "kept/skill.md")
    _git(repo, "commit", "-qm", "first")
    copy = ut.scratch_copy(repo, tmp_path / "copy")
    tracked = subprocess.run(["git", "ls-files"], cwd=copy, capture_output=True, text=True).stdout.split()
    assert "kept/skill.md" in tracked
    assert ".env" not in tracked


def test_tracked_names_are_literal_not_pathspec_patterns(tmp_path):
    """A tracked file literally named '*.txt' must not act as a glob that force-adds an ignored
    file the source never tracked (verify's Codex reader)."""
    repo = tmp_path / "repo"
    (repo / "star").mkdir(parents=True)
    (repo / ".gitignore").write_text("star/\n")
    (repo / "star" / "*.txt").write_text("literal name\n")
    (repo / "star" / "other.txt").write_text("ignored, never tracked\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".gitignore")
    _git(repo, "--literal-pathspecs", "add", "-f", "star/*.txt")
    _git(repo, "commit", "-qm", "first")
    copy = ut.scratch_copy(repo, tmp_path / "copy")
    tracked = subprocess.run(["git", "ls-files"], cwd=copy, capture_output=True, text=True).stdout.splitlines()
    assert "star/other.txt" not in tracked
    assert any(t.endswith("*.txt") for t in tracked)


# ── Ported from the hunk check onto the differential check, its live consumer ──

def _src_layout(tmp_path, before, after, test):
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(before)
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "before")
    (root / "src" / "pkg" / "mod.py").write_text(after)
    (root / "tests" / "test_mod.py").write_text(test)
    _git(root, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout
    return root, diff


CMD = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"


def test_a_symlinked_tree_path_still_points_the_tests_at_the_copy(tmp_path):
    """hq_123 run 1: the tree was passed as /var/... while the code compared against the
    resolved /private/var/..., so PYTHONPATH kept pointing at the untouched original and
    every revert looked untested."""
    root, diff = _src_layout(tmp_path, BEFORE, AFTER, TEST.replace("pkg.mod", "pkg.mod"))
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    m = differential.measure([("t", CMD)], diff, link,
                             env={"PYTHONPATH": str(link / "src"), "PATH": "/usr/bin:/bin"})
    assert m["status"] == {"t": "detects"}, m


def test_tests_that_call_git_work_in_the_scratch_copy(project):
    """The copy has no .git of the user's; it gets its own throwaway repo so a test that
    runs git does not read as red (hq_123 run 1: 37% of commits)."""
    root, diff, cmd = project
    m = differential.measure([("t", f"git rev-parse HEAD && {cmd}")], diff, root,
                             env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    assert m["status"] == {"t": "detects"}, m
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True).stdout.strip(), "the user's repo keeps its own HEAD"


def test_a_same_size_revert_is_still_seen(tmp_path):
    """x + 1 -> x + 2 keeps the file size; within one second a cached .pyc would hide the
    revert and the test would read vacuous."""
    root, diff = _src_layout(tmp_path, "def f(x):\n    return x + 1\n", "def f(x):\n    return x + 2\n",
                             "from pkg.mod import f\n\ndef test_f():\n    assert f(1) == 3\n")
    for _ in range(3):
        m = differential.measure([("t", CMD)], diff, root,
                                 env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"})
        assert m["status"] == {"t": "detects"}, m


# ── The copy carries what git carries, and nothing that crashes it (2026-10-15) ──

def test_the_copy_leaves_out_what_git_ignores(tmp_path):
    """Copying the whole directory took 524 MB from this repo."""
    repo = tmp_path / "r"
    (repo / "big").mkdir(parents=True)
    (repo / "big" / "blob.bin").write_bytes(b"x" * 1000)
    (repo / ".gitignore").write_text("big/\n")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "init", "-q"); _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "c")
    (repo / "new_untracked.py").write_text("y = 2\n")
    copy = ut.scratch_copy(repo, tmp_path / "copy")
    assert not (copy / "big").exists()
    assert (copy / "a.py").exists() and (copy / "new_untracked.py").exists()


@pytest.mark.parametrize("in_git", [True, False])
def test_a_socket_in_the_tree_does_not_crash_the_copy(tmp_path, in_git, monkeypatch):
    """A socket (Rails tmp/sockets/puma.sock) made copytree raise and crashed verify."""
    import socket
    repo = tmp_path / "r"
    (repo / "tmp").mkdir(parents=True)
    (repo / "a.py").write_text("x = 1\n")
    if in_git:
        _git(repo, "init", "-q"); _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "c")
    monkeypatch.chdir(repo / "tmp")           # AF_UNIX paths are capped near 104 bytes
    s = socket.socket(socket.AF_UNIX)
    try:
        s.bind("s.sock")
        copy = ut.scratch_copy(repo, tmp_path / "copy")
        assert (copy / "a.py").exists() and not (copy / "tmp" / "s.sock").exists()
    finally:
        s.close()


def test_a_timed_out_command_takes_its_children_with_it(tmp_path):
    """Killing only the shell left the child running after verify reported."""
    import os
    import time
    t0 = time.monotonic()
    rc, _ = ut.run_shell("sleep 30 & echo $! > child.pid; wait", tmp_path, None, timeout=1)
    # A deadline, not a wait: with only the shell killed, reading its output blocked until the
    # orphan let go of the pipe, 30 s later (the first version of this test passed that way).
    assert rc is None and time.monotonic() - t0 < 10
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError(f"child {pid} survived the timeout")
