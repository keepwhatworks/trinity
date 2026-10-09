"""Replay tasks from your own bugs, Harbor-shaped (src/trinity_local/replay_tasks.py)."""
from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from trinity_local import replay_tasks as rt

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}


def _commit(root, files, msg):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=ENV)
    subprocess.run(["git", "commit", "-qm", msg], cwd=root, check=True, env=ENV)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=ENV)
    _commit(root, {"src/pkg/__init__.py": "", "src/pkg/mod.py": "def add(a, b):\n    return a - b\n",
                   "src/pkg/other.py": "def name():\n    return 'x'\n"}, "initial: add is wrong")
    _commit(root, {"src/pkg/mod.py": "def add(a, b):\n    return a + b\n",
                   "tests/test_mod.py": "from pkg.mod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"},
            "SECRET-MESSAGE fix the add bug by switching minus to plus")
    _commit(root, {"src/pkg/other.py": "def name():\n    return 'x'  # same behaviour\n",
                   "tests/test_other.py": "from pkg.other import name\n\ndef test_name():\n    assert name() == 'x'\n"},
            "refactor: comment only, its test already passes before")
    return root


def test_only_a_red_then_green_fix_becomes_a_task(repo):
    tasks = rt.find_tasks(repo, since="2000-01-01", python=sys.executable)
    assert [t.test_files for t in tasks] == [["tests/test_mod.py"]]
    assert tasks[0].failing == ["tests/test_mod.py::test_add"]


def test_written_task_has_harbor_layout_and_no_commit_message(repo, tmp_path):
    t = rt.find_tasks(repo, since="2000-01-01", python=sys.executable)[0]
    d = rt.write_task(repo, t, tmp_path / "out")
    for rel in ("instruction.md", "task.toml", "environment/Dockerfile", "environment/repo.tar.gz",
                "tests/test.sh", "tests/files/tests/test_mod.py", "solution/solve.sh", "solution/fix.patch"):
        assert (d / rel).exists(), rel
    every_text = "".join(p.read_text(errors="ignore") for p in d.rglob("*") if p.is_file() and p.suffix != ".gz")
    assert "SECRET-MESSAGE" not in every_text
    toml = (d / "task.toml").read_text()
    assert 'schema_version = "1.3"' in toml and 'network_mode = "no-network"' in toml and "allowlist" in toml
    assert "pytest -q tests/test_mod.py" in (d / "instruction.md").read_text()


def test_the_task_is_red_without_the_fix_and_green_with_it(repo, tmp_path):
    """Validates the exported task locally the way a runner would: extract, run, apply, run."""
    t = rt.find_tasks(repo, since="2000-01-01", python=sys.executable)[0]
    d = rt.write_task(repo, t, tmp_path / "out")
    app = tmp_path / "app"
    app.mkdir()
    with tarfile.open(d / "environment" / "repo.tar.gz") as tf:
        tf.extractall(app, filter="data")
    assert not (app / ".git").exists()
    assert rt._pytest(sys.executable, app, t.test_files, 60)[0] == "failed"
    assert subprocess.run(["git", "apply", str(d / "solution" / "fix.patch")], cwd=app).returncode == 0
    assert rt._pytest(sys.executable, app, t.test_files, 60)[0] == "green"


def test_cli_exports_into_trinity_home(repo, tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    from trinity_local.commands.eval_replay import handle_replay
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    assert handle_replay(SimpleNamespace(repo=str(repo), since="2000-01-01", max=5, out=None, paths=None,
                                         python=sys.executable, agent_network="allowlist")) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["tasks"] == 1 and Path(res["dir"]).is_relative_to(tmp_path / "home")


def test_failure_output_never_carries_local_paths():
    raw = f"repo = PosixPath('/private/tmp/pytest-of-someone/x')\n{Path.home()}/projects/p/file.py:3"
    out = rt._scrub(raw)
    assert "/private/tmp" not in out and str(Path.home()) not in out and out.count("<path>") == 2


def test_network_policy_is_strict_by_default_and_relaxable_for_local_checks(repo, tmp_path):
    t = rt.find_tasks(repo, since="2000-01-01", python=sys.executable)[0]
    strict = (rt.write_task(repo, t, tmp_path / "a") / "task.toml").read_text()
    assert 'network_mode = "allowlist"' in strict and 'network_mode = "no-network"' in strict
    loose = (rt.write_task(repo, t, tmp_path / "b", agent_network="public") / "task.toml").read_text()
    assert "allowlist" not in loose and loose.count('network_mode = "public"') == 2
