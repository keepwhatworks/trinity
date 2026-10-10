"""verify measures kernel relevance: each green test reruns with the change's source
reverted (differential.py). A test still green without the change is vacuous and
does not make the kernel relevant; red tests keep STOP.

Built on a real throwaway git repo so the scratch copy, the reverse-apply and the
reruns are the production path, not mocks.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from trinity_local import differential
from trinity_local.verify import verify

PY = sys.executable


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                           *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    """calc.py before the change: add() only. The change: add() handles None, and a new
    double(). Tests: one pins the new behaviour, one never touches the change, one imports
    the new symbol."""
    r = tmp_path / "proj"
    (r / "tests").mkdir(parents=True)
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (r / "tests" / "test_old.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "before")
    (r / "calc.py").write_text("def add(a, b):\n    return (a or 0) + (b or 0)\n\n\ndef double(x):\n    return 2 * x\n")
    (r / "tests" / "test_none.py").write_text("from calc import add\n\ndef test_none():\n    assert add(None, 3) == 3\n")
    (r / "tests" / "test_double.py").write_text("from calc import double\n\ndef test_double():\n    assert double(4) == 8\n")
    _git(r, "add", "-A")
    return r, _git(r, "diff", "--cached")


def _crit(cid: str, test: str) -> dict:
    return {"id": cid, "kind": "test", "statement": cid,
            "command": f"PYTHONPATH=. {PY} -m pytest -q -p no:cacheprovider tests/{test}"}


def test_each_test_is_classified_against_the_reverted_change(repo):
    cwd, diff = repo
    out = verify([_crit("t_none", "test_none.py"), _crit("t_old", "test_old.py"),
                  _crit("t_double", "test_double.py")], diff, "", cwd, run_panel=False)
    m = out["differential"]
    assert m["ran"] and m["basis"] == "measured" and m["source_files"] == 1
    assert m["status"] == {"t_none": "detects", "t_old": "vacuous", "t_double": "no_load"}
    assert out["kernel"]["relevant"] is True and out["kernel"]["relevance_basis"] == "measured"
    assert out["unverified"] == ["t_old"]


def test_a_kernel_of_only_vacuous_tests_is_not_relevant(repo):
    """MUTATION: let relevance() count a vacuous test and the kernel turns relevant here."""
    cwd, diff = repo
    out = verify([_crit("t_old", "test_old.py")], diff, "", cwd, run_panel=False)
    assert out["kernel"]["green"] is True and out["kernel"]["relevant"] is False
    assert out["triage"] == "READ" and "does not exercise" in out["reason"]


def test_a_red_test_still_stops_whatever_the_measurement(repo):
    cwd, diff = repo
    (cwd / "tests" / "test_red.py").write_text("def test_red():\n    assert False\n")
    out = verify([_crit("t_old", "test_old.py"), _crit("t_red", "test_red.py")], diff, "", cwd,
                 run_panel=False)
    assert out["triage"] == "STOP" and out["kernel"]["relevant"] is True


def test_relevance_stays_declared_when_nothing_can_be_measured(repo):
    cwd, diff = repo
    tests_only = "".join("diff --git " + s for s in diff.split("diff --git ")[1:] if s.startswith("a/tests/"))
    out = verify([_crit("t_old", "test_old.py")], tests_only, "", cwd, run_panel=False)
    assert out["differential"]["ran"] is False and "only test files" in out["differential"]["reason"]
    assert out["kernel"]["relevant"] is True and out["kernel"]["relevance_basis"] == "declared"
    stale = diff.replace("return (a or 0) + (b or 0)", "return (a or 0) + (b or 9)")
    out = verify([_crit("t_none", "test_none.py")], stale, "", cwd, run_panel=False)
    assert out["differential"]["ran"] is False and "reverse-apply" in out["differential"]["reason"]
    assert out["kernel"]["relevance_basis"] == "declared"


def test_switched_off_it_does_not_run(repo):
    cwd, diff = repo
    out = verify([_crit("t_old", "test_old.py")], diff, "", cwd, run_panel=False, differential=False)
    assert out["differential"] is None and out["kernel"]["relevant"] is True


def test_the_original_tree_is_never_touched(repo):
    cwd, diff = repo
    before = (cwd / "calc.py").read_text()
    verify([_crit("t_none", "test_none.py")], diff, "", cwd, run_panel=False)
    assert (cwd / "calc.py").read_text() == before
    changed = [ln for ln in _git(cwd, "status", "--porcelain").splitlines() if "__pycache__" not in ln]
    assert changed == ["M  calc.py", "A  tests/test_double.py", "A  tests/test_none.py"]   # nothing reverted here


def test_source_patch_drops_test_sections_and_keeps_new_and_deleted_files():
    diff = ("diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+n\n"
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-g\n")
    text, n = differential.source_patch(diff)
    assert n == 3 and "tests/test_x.py" not in text and "new.py" in text and "gone.py" in text


def test_card_says_which_tests_test_the_change(repo):
    from trinity_local.commands.verify import render_card

    cwd, diff = repo
    out = verify([_crit("t_none", "test_none.py"), _crit("t_old", "test_old.py")], diff, "", cwd,
                 run_panel=False)
    card = render_card(out)
    assert "red without the change: it tests it" in card
    assert "VACUOUS: green without the change too" in card
    assert "[t_old]" in card.split("unverified:")[1]


def test_a_test_the_copy_cannot_reproduce_is_not_read_as_detecting(repo):
    """A test that is green only in the real tree (here: it needs a cache file the scratch
    copy leaves out) would read red after the revert for reasons that have nothing to do
    with the change. The control run marks it not_reproduced instead. MUTATION: drop the control
    run and it is reported as detecting the change."""
    cwd, diff = repo
    (cwd / ".pytest_cache").mkdir()
    (cwd / ".pytest_cache" / "marker").write_text("x")
    (cwd / "tests" / "test_env.py").write_text(
        "from pathlib import Path\n\ndef test_env():\n    assert Path('.pytest_cache/marker').exists()\n")
    out = verify([_crit("t_env", "test_env.py")], diff, "", cwd, run_panel=False)
    assert out["differential"]["status"] == {"t_env": "not_reproduced"}
    assert "1 not reproduced" in out["differential"]["reason"]
    assert out["differential"]["ran"] is False and out["kernel"]["relevance_basis"] == "declared"


def test_a_relative_cwd_measures_like_an_absolute_one(repo, monkeypatch):
    """verify's CLI passes --cwd ".". Rewriting "." onto the scratch copy replaced every dot
    in the command, so nothing reproduced and relevance silently stayed declared (found
    dogfooding this check). MUTATION: put the relative spelling back in tree_spellings."""
    cwd, diff = repo
    monkeypatch.chdir(cwd)
    out = verify([_crit("t_none", "test_none.py"), _crit("t_old", "test_old.py")], diff, "", Path("."),
                 run_panel=False)
    assert out["differential"]["ran"] is True
    assert out["differential"]["status"] == {"t_none": "detects", "t_old": "vacuous"}
