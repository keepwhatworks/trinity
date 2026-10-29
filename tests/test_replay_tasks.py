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
        rt._extract_data(tf, app)   # filter= does not exist before Python 3.10.12 (public CI's macOS 3.10)
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


def test_outside_a_git_repository_it_says_so_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    """The default --repo is the current directory; run anywhere else, git's "not a git repository"
    escaped as a traceback on every published wheel 1.7.402-1.7.407 (crash sweep, 2026-10-16)."""
    import argparse
    from trinity_local.commands.eval_replay import handle_replay
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    args = argparse.Namespace(repo=str(tmp_path), since="90 days ago", max=20, out=None, paths=None,
                              python=None, agent_network="allowlist")
    assert handle_replay(args) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_a_repository_without_commits_is_one_line_not_a_traceback(tmp_path, monkeypatch, capsys):
    """rev-parse passes in a fresh `git init`; the git log underneath then failed with a
    traceback (the 1.7.408 reader sweep)."""
    import argparse
    from trinity_local.commands.eval_replay import handle_replay
    subprocess.run(["git", "init", "-q", str(tmp_path / "fresh")], check=True)
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    args = argparse.Namespace(repo=str(tmp_path / "fresh"), since="90 days ago", max=20, out=None,
                              paths=None, python=None, agent_network="allowlist")
    assert handle_replay(args) == 1
    assert "eval-replay:" in capsys.readouterr().err


def test_without_git_on_path_it_says_so(tmp_path, monkeypatch, capsys):
    import argparse
    from trinity_local.commands.eval_replay import handle_replay
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    args = argparse.Namespace(repo=str(tmp_path), since="90 days ago", max=20, out=None, paths=None,
                              python=None, agent_network="allowlist")
    assert handle_replay(args) == 2
    assert "git is not installed" in capsys.readouterr().err


def _tar(*members: tuple[str, str, str]) -> tarfile.TarFile:
    import io
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind, extra in members:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(extra.encode())
                tf.addfile(ti, io.BytesIO(extra.encode()))
            elif kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, extra
                tf.addfile(ti)
            else:
                ti.type = tarfile.FIFOTYPE
                tf.addfile(ti)
    buf.seek(0)
    return tarfile.open(fileobj=buf)


@pytest.mark.parametrize("member", [("../x", "file", "a"), ("/abs/x", "file", "a"), ("link", "sym", "/etc/hosts"),
                                    ("d/link", "sym", "../../x"), ("fifo", "fifo", "")])
def test_without_the_data_filter_an_unsafe_member_is_refused(tmp_path, monkeypatch, member):
    """Python 3.10.0-3.10.11 has no tarfile `filter=` (public CI's macOS 3.10 raised TypeError, 2026-10-26). There
    the snapshot refuses what the "data" filter would, before writing anything."""
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    dest = tmp_path / "d"
    dest.mkdir()
    with pytest.raises(tarfile.TarError):
        rt._extract_data(_tar(member), dest)
    assert list(dest.iterdir()) == [] and not (tmp_path / "x").exists()


def test_without_the_data_filter_a_repo_snapshot_still_extracts(tmp_path, monkeypatch):
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    dest = tmp_path / "d"
    dest.mkdir()
    rt._extract_data(_tar(("pkg/a.py", "file", "x = 1\n"), ("pkg/b.py", "sym", "a.py")), dest)
    assert (dest / "pkg" / "a.py").read_text() == "x = 1\n"
    assert (dest / "pkg" / "b.py").is_symlink() and (dest / "pkg" / "b.py").read_text() == "x = 1\n"


def test_the_grading_script_quotes_test_paths_from_history(tmp_path):
    """A test path comes from the repository's history, so test.sh must treat it as a
    name: a space must not split it and $(...) must not run. Runs the real script with
    its container roots pointed at tmp_path."""
    import shlex

    name = "tests/a b/test_$(touch${IFS}PWNED).py"
    t = rt.ReplayTask(name="replay-x", fix="f", parent="p", date="d", test_files=[name],
                      failing=[], failure_tail="", command="")
    files, app, logs, cwd = (tmp_path / d for d in ("files", "app", "logs", "cwd"))
    for d in (files, app, cwd):
        d.mkdir()
    (files / name).parent.mkdir(parents=True)
    (files / name).write_text("def test_ok():\n    assert True\n")
    script = (rt._test_sh(t).replace("/tests/files/", f"{files}/").replace("/logs/", f"{logs}/")
              .replace("/app", str(app)).replace("python -m pytest", f"{shlex.quote(sys.executable)} -m pytest"))
    subprocess.run(["bash", "-c", script], cwd=cwd, check=True, capture_output=True)
    assert not (cwd / "PWNED").exists() and not (app / "PWNED").exists(), "a test path ran as a command"
    assert (app / name).exists()
    assert (logs / "verifier" / "reward.txt").read_text().strip() == "1"


def test_the_instruction_command_keeps_a_test_path_whole(tmp_path):
    """The instruction shows the agent a pytest command to run. A test path with a
    space or $(...) must come through it as one argument, unexpanded."""
    import shlex

    name = "tests/a b/test_$(touch${IFS}PWNED).py"
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=ENV)
    _commit(root, {"src/pkg/__init__.py": "", "src/pkg/mod.py": "def add(a, b):\n    return a - b\n"}, "initial")
    _commit(root, {"src/pkg/mod.py": "def add(a, b):\n    return a + b\n",
                   name: "from pkg.mod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}, "fix add")
    [t] = rt.find_tasks(root, since="2000-01-01", python=sys.executable)
    assert t.test_files == [name]
    assert shlex.split(t.command) == ["python", "-m", "pytest", "-q", name]
