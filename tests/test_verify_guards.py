"""verify runs the repository's own guard tests on every call, once the user trusts them
(council_37bf5aba6a201a3f, amd_0578).

8 repo-guard breaks landed red on main in 110 audited commits, three of them shipping the plugin with a
stale bundled engine, each caught by a test that already existed but was not among the tests the
agent chose. A guard checks an invariant, not the change: a red guard STOPs; a green guard never
vouches for the change and is never rerun by the differential.

A guard file is shell commands the repository chose, so it runs only after the user has trusted
that exact content (`trinity-local verify --allow-guards`, direnv's model): any edit revokes it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trinity_local.verify import GUARDS_FILE, GUARDS_FIX, allow_guards, load_guards, read_guards, verify

PY = sys.executable


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Trust lives in TRINITY_HOME: each test starts with none."""
    h = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(h))
    return h


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """add() learns to treat None as 0. test_none pins the new behaviour; test_old never touches it."""
    r = tmp_path / "proj"
    (r / "tests").mkdir(parents=True)
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (r / "tests" / "test_old.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "before")
    (r / "calc.py").write_text("def add(a, b):\n    return (a or 0) + (b or 0)\n")
    (r / "tests" / "test_none.py").write_text("from calc import add\n\ndef test_none():\n    assert add(None, 3) == 3\n")
    _git(r, "add", "-A")
    return r, _git(r, "diff", "--cached")


def _crit(cid: str, test: str) -> dict:
    return {"id": cid, "kind": "test", "statement": cid,
            "command": f"PYTHONPATH=. {PY} -m pytest -q -p no:cacheprovider tests/{test}"}


def _cli(*argv: str) -> int:
    """The command a user types, parsed by the real CLI parser and run by its handler."""
    from trinity_local.main import build_parser
    args = build_parser().parse_args(list(argv))
    return args.handler(args)


def _guard(r: Path, ok: bool, trust: bool = True) -> None:
    (r / "tests" / "test_invariant.py").write_text(f"def test_invariant():\n    assert {ok}\n")
    (r / ".trinity").mkdir(exist_ok=True)
    (r / GUARDS_FILE).write_text(json.dumps([
        {"id": "invariant", "statement": "the invariant holds",
         "command": f"PYTHONPATH=. {PY} -m pytest -q -p no:cacheprovider tests/test_invariant.py"}]))
    if trust:
        allow_guards(r)


def test_a_red_guard_stops_and_names_itself(repo):
    cwd, diff = repo
    _guard(cwd, ok=False)
    out = verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    assert out["triage"] == "STOP"
    assert (out["guards"]["ids"], out["guards"]["red"], out["guards"]["trusted"]) == (
        ["guard:invariant"], ["guard:invariant"], True)
    assert "repository guard red: guard:invariant" in out["reason"]


def test_a_red_guard_stops_even_when_the_caller_passed_no_test(repo):
    cwd, diff = repo
    _guard(cwd, ok=False)
    out = verify([], diff, "", cwd, run_panel=False)
    assert out["triage"] == "STOP" and out["kernel"]["relevant"] is True


def test_a_green_guard_never_vouches_for_the_change(repo):
    cwd, diff = repo
    _guard(cwd, ok=True)
    only_guards = verify([], diff, "", cwd, run_panel=False)
    assert only_guards["kernel"]["relevant"] is False and only_guards["triage"] == "READ"
    vacuous = verify([_crit("t_old", "test_old.py")], diff, "", cwd, run_panel=False)
    assert vacuous["kernel"]["relevant"] is False                 # the guard does not rescue a vacuous kernel
    assert "guard:invariant" not in vacuous["differential"]["status"]     # never rerun as a detector
    assert vacuous["unverified"] == ["t_old"]                     # and never listed as unverified


def test_a_detecting_test_beside_a_green_guard_reads_as_before(repo):
    cwd, diff = repo
    _guard(cwd, ok=True)
    out = verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    assert out["kernel"]["relevant"] is True and out["kernel"]["green"] is True
    assert out["differential"]["status"] == {"t_none": "detects"}
    assert out["guards"]["red"] == []


def test_an_untrusted_guard_file_never_runs_and_says_how_to_trust_it(repo):
    """The repository chose these commands; until the user trusts them, verify only hashes the file."""
    cwd, diff = repo
    _guard(cwd, ok=False, trust=False)
    out = verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    assert out["triage"] != "STOP" and out["guards"]["ids"] == []
    assert (out["guards"]["declared"], out["guards"]["trusted"], out["guards"]["fix"]) == (True, False, GUARDS_FIX)
    assert "not trusted" in out["reason"] and GUARDS_FIX in out["reason"]
    (cwd / GUARDS_FILE).write_text("{not json")       # untrusted content is never parsed
    assert load_guards(cwd) == ([], {"declared": True, "trusted": False})


def test_the_advice_clears_the_warning(repo):
    """Advice closure: the command verify names, run as the user would run it, makes the guards run."""
    cwd, diff = repo
    _guard(cwd, ok=False, trust=False)
    assert _cli(*GUARDS_FIX.split()[1:], "--cwd", str(cwd)) == 0
    out = verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    assert out["guards"]["trusted"] is True and "fix" not in out["guards"] and out["triage"] == "STOP"


def test_trusting_says_the_test_code_itself_is_not_pinned(repo, capsys):
    """Trust pins the commands, not the test files they run, which change with the repository
    (security review of the guard commit, 2026-10-25): the user reads that at trust time."""
    cwd, _ = repo
    _guard(cwd, ok=True, trust=False)
    assert _cli("verify", "--allow-guards", "--cwd", str(cwd)) == 0
    assert "Trust covers these commands, not the code they run" in capsys.readouterr().out


def test_any_edit_to_the_guard_file_revokes_trust(repo):
    cwd, diff = repo
    _guard(cwd, ok=False)
    path = cwd / GUARDS_FILE
    path.write_text(path.read_text().replace("the invariant holds", "the invariant still holds"))
    out = verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    assert out["guards"]["trusted"] is False and out["triage"] != "STOP"


def test_trust_belongs_to_one_repository_and_its_worktrees(repo, tmp_path):
    """Trust is filed under the repository: a worktree of it shares the trust, and another clone
    holding byte-identical guards does not, since there the same commands run that clone's tests."""
    cwd, _ = repo
    _guard(cwd, ok=True)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", "guards")
    _git(cwd, "worktree", "add", "-q", str(tmp_path / "wt"))
    assert load_guards(tmp_path / "wt")[1] == {"declared": True, "trusted": True}
    other = tmp_path / "other"
    (other / ".trinity").mkdir(parents=True)
    (other / GUARDS_FILE).write_bytes((cwd / GUARDS_FILE).read_bytes())
    _git(other, "init", "-q")
    assert load_guards(other) == ([], {"declared": True, "trusted": False})


def test_each_call_reads_the_guard_file_once(repo, monkeypatch):
    """A second read could see an edit made between the hash and the parse, and so run, or show the
    user, content nobody trusted (codex verify read, 2026-10-25)."""
    cwd, _ = repo
    _guard(cwd, ok=True, trust=False)
    reads = []
    for name in ("read_bytes", "read_text"):
        real = getattr(Path, name)

        def spy(self, *a, _real=real, **k):
            if self.name == "guards.json":
                reads.append(self)
            return _real(self, *a, **k)
        monkeypatch.setattr(Path, name, spy)
    allow_guards(cwd)
    assert len(reads) == 1
    reads.clear()
    assert load_guards(cwd)[1]["trusted"] is True and len(reads) == 1


def test_guards_switch_off_only_on_purpose(repo, monkeypatch):
    cwd, diff = repo
    _guard(cwd, ok=False)
    assert verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False, guards=False)["triage"] != "STOP"
    monkeypatch.setenv("TRINITY_VERIFY_GUARDS", "0")
    assert verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)["triage"] != "STOP"


def test_a_missing_file_is_no_guards_and_a_malformed_one_is_refused_at_trust(repo, tmp_path):
    cwd, _ = repo
    assert load_guards(cwd) == ([], {"declared": False, "trusted": False}) and read_guards(cwd) == []
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert load_guards(outside)[0] == []
    (cwd / ".trinity").mkdir()
    for bad in ("{not json", json.dumps({"id": "x"}), json.dumps([{"id": "x"}]), json.dumps([{"command": "true"}])):
        (cwd / GUARDS_FILE).write_text(bad)
        with pytest.raises(ValueError, match=r"\.trinity/guards\.json"):
            allow_guards(cwd)
        assert _cli("verify", "--allow-guards", "--cwd", str(cwd)) == 1


def test_a_guard_file_that_is_a_link_is_ignored(repo, tmp_path):
    cwd, _ = repo
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps([{"id": "x", "command": "true"}]))
    (cwd / ".trinity").mkdir()
    (cwd / GUARDS_FILE).symlink_to(elsewhere)
    assert load_guards(cwd) == ([], {"declared": False, "trusted": False})


def test_the_run_log_counts_guards_and_red_guards(repo, home):
    cwd, diff = repo
    _guard(cwd, ok=False)
    verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    (row,) = [json.loads(l) for l in (home / "analytics" / "verify_runs.jsonl").read_text().splitlines()]
    assert (row["guards"], row["guards_red"]) == (1, 1)
    assert "invariant" not in json.dumps(row)                     # counts only, never ids
