"""verify records each run as counts only (analytics/verify_runs.jsonl), so the rate of
tests that never touch their change (res_173) becomes measurable across callers."""
from __future__ import annotations

import json
import subprocess
import sys

SECRET = "zz-private-task-name"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, capture_output=True, text=True, check=True)


def test_one_line_per_run_with_counts_and_no_text(tmp_path, monkeypatch):
    """MUTATION: drop the record_run call, or put a criterion id/statement/command in the row,
    and this reds."""
    home = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(home))
    from trinity_local.verify import verify

    r = tmp_path / "proj"
    (r / "tests").mkdir(parents=True)
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (r / "tests" / "test_old.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "before")
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef unused():\n    return 1\n")
    _git(r, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=r, capture_output=True, text=True).stdout
    crit = [{"id": SECRET, "kind": "test", "statement": f"{SECRET} passes",
             "command": f"PYTHONPATH=. {sys.executable} -m pytest -q -p no:cacheprovider tests/test_old.py"}]
    verify(crit, diff, f"context {SECRET}", r, run_panel=False)

    lines = (home / "analytics" / "verify_runs.jsonl").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert SECRET not in lines[0] and "pytest" not in lines[0] and str(r) not in lines[0]
    assert row["tests"] == 1 and row["tests_red"] == 0 and row["judgments"] == 0
    assert row["differential"] == {"vacuous": 1} and row["kernel_relevant"] is False
    assert row["triage"] == "READ" and row["relevance_basis"] == "measured"


def test_logging_never_fails_a_verify(tmp_path, monkeypatch):
    from trinity_local import verify as V

    monkeypatch.setattr("trinity_local.state_paths.analytics_dir",
                        lambda: (_ for _ in ()).throw(OSError("read-only home")))
    V.record_run({"triage": "READ"})          # must not raise
