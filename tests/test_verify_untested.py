"""verify's untested-change check: revert one hunk at a time, rerun the declared tests,
report the hunks no test notices (src/trinity_local/untested.py, hq_123)."""
from __future__ import annotations

import subprocess
import sys
import pytest

from trinity_local import untested as ut

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


def test_parse_splits_files_into_hunks(project):
    _, diff, _ = project
    paths = sorted({h.path for h in ut.parse_hunks(diff)})
    assert paths == ["pkg/mod.py", "pkg/notes.py", "tests/test_mod.py"]


def test_flags_only_the_hunk_no_test_notices(project):
    root, diff, cmd = project
    r = ut.untested_hunks(diff, [cmd], root, env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
    assert r["ran"], r
    flagged = [h["hunk"] for h in r["untested"]]
    statuses = {h["hunk"]: h["status"] for h in r["hunks"]}
    assert len(flagged) == 1 and statuses
    hunk_text = next(h for h in ut.parse_hunks(diff) if h.header == flagged[0])
    assert any("x * 3" in line for line in hunk_text.body), "the flagged hunk must be the untested function"
    assert r["trivial_skipped"] == 1                      # the comment-only file is not a candidate
    assert (root / "pkg" / "mod.py").read_text() == AFTER   # the real tree is never touched


def test_does_not_run_on_red_tests(project):
    root, diff, _ = project
    r = ut.untested_hunks(diff, ["false"], root)
    assert not r["ran"] and "red" in r["reason"]


def test_does_not_run_without_test_commands(project):
    root, diff, _ = project
    assert ut.untested_hunks(diff, [], root)["reason"] == "no declared test commands"


def test_verify_reports_it_only_when_enabled(project, monkeypatch):
    from trinity_local import verify as v
    root, diff, cmd = project
    crit = [{"id": "T1", "kind": "test", "statement": "s", "command": cmd}]
    env = {"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"}
    monkeypatch.delenv(ut.FLAG, raising=False)
    off = v.verify(crit, diff, "", root, run_panel=False, env=env)
    assert off["untested"] is None
    monkeypatch.setenv(ut.FLAG, "1")
    on = v.verify(crit, diff, "", root, run_panel=False, env=env)
    assert on["untested"]["ran"] and len(on["untested"]["untested"]) == 1
    assert on["triage"] == off["triage"]                   # report-only: never changes STOP/READ
    from trinity_local.commands.verify import render_card
    assert "untested: 1 of" in render_card(on)


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


def test_a_symlinked_tree_path_still_points_the_tests_at_the_copy(tmp_path):
    """hq_123 run 1: the tree was passed as /var/... while the code compared against the
    resolved /private/var/..., so PYTHONPATH kept pointing at the untouched original and
    every revert looked untested. Reproduce with an explicit symlink and a src/ layout, where
    the package is reachable ONLY through PYTHONPATH (as in this repo)."""
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(BEFORE)
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "before")
    (root / "src" / "pkg" / "mod.py").write_text(AFTER)
    (root / "tests" / "test_mod.py").write_text(TEST)
    _git(root, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"
    r = ut.untested_hunks(diff, [cmd], link, env={"PYTHONPATH": str(link / "src"), "PATH": "/usr/bin:/bin"})
    assert r["ran"], r
    assert r["counts"]["tested"] == 1 and r["counts"]["untested"] == 1, r["hunks"]


def test_tests_that_call_git_work_in_the_scratch_copy(project):
    """The copy has no .git of the user's; it gets its own throwaway repo so a test that
    runs git does not read as red (hq_123 run 1: 37% of commits)."""
    root, diff, cmd = project
    r = ut.untested_hunks(diff, ["git rev-parse HEAD", cmd], root,
                          env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    assert r["ran"], r
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout
    assert before.strip(), "the user's repo is untouched and still has its own HEAD"


def test_a_same_size_revert_is_still_seen(tmp_path):
    """x + 1 -> x + 2 keeps the file size; within one second a cached .pyc would hide the
    revert and the hunk would read 'untested'. It must read 'tested'."""
    root = tmp_path / "s"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text("def f(x):\n    return x + 1\n")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "b")
    (root / "src" / "pkg" / "mod.py").write_text("def f(x):\n    return x + 2\n")
    (root / "tests" / "test_m.py").write_text("from pkg.mod import f\n\ndef test_f():\n    assert f(1) == 3\n")
    _git(root, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"
    for _ in range(3):
        r = ut.untested_hunks(diff, [cmd], root, env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"})
        assert r["counts"]["tested"] == 1 and r["counts"]["untested"] == 0, r["hunks"]
