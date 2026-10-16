"""verify must not STOP, or call a test vacuous, for reasons that are not the change
(coherence review 2026-10-15, second verify batch).

- Trinity pins HF_HUB_OFFLINE etc. for itself at startup; the user's tests inherited it, so a
  test that downloads a model went red only under verify: a false STOP.
- A command the shell could not run (exit 126/127, e.g. a GUI host's PATH without pytest)
  read as a red test: a false STOP.
- Over MCP without `cwd`, the tests ran in the server's own directory.
- With an editable install the reverted copy still imported the original tree, so a test that
  depends on the change read "vacuous" and the kernel "does NOT exercise the changed files".
- A kernel with no blocking result printed "tests: green".
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from trinity_local import runtime_env
from trinity_local import verify as V
from trinity_local.commands.verify import render_card


def _verify(cwd, criteria, **kw):
    kw.setdefault("run_panel", False)
    kw.setdefault("quality", False)
    kw.setdefault("differential", False)
    return V.verify(criteria, "diff --git a/x b/x\n", "ctx", cwd, **kw)


class TestTheUsersEnvironment:
    def test_trinitys_own_pins_do_not_reach_the_users_tests(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.setattr(runtime_env, "TRINITY_SET_ENV", set())
        runtime_env.set_default_env("HF_HUB_OFFLINE", "1")
        try:
            out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s",
                                      "command": '[ -z "$HF_HUB_OFFLINE" ]'}])
            assert out["kernel"]["green"] is True and out["triage"] != "STOP", out["kernel"]
        finally:
            os.environ.pop("HF_HUB_OFFLINE", None)

    def test_a_value_the_user_set_is_theirs_and_passes_through(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runtime_env, "TRINITY_SET_ENV", set())
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        runtime_env.set_default_env("HF_HUB_OFFLINE", "0")       # already set: not Trinity's
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s",
                                  "command": '[ "$HF_HUB_OFFLINE" = "1" ]'}])
        assert out["kernel"]["green"] is True


class TestACommandThatCouldNotRun:
    def test_command_not_found_is_no_result_not_a_red_test(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s",
                                  "command": "trinity-no-such-command-xyz --run"}])
        assert out["triage"] == "READ", out["reason"]
        run = out["kernel"]["runs"][0]
        assert run["green"] is None and run["could_not_run"]
        assert "t" in out["unverified"]
        card = render_card(out)
        assert "tests: no blocking result" in card and "could not run" in card
        assert "tests: green" not in card

    def test_a_real_red_test_still_stops(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "exit 1"}])
        assert out["triage"] == "STOP"


class TestTheMcpCwd:
    def _call(self, monkeypatch, cwd_dir, args):
        from trinity_local import mcp_server
        monkeypatch.chdir(cwd_dir)
        monkeypatch.setattr(V, "verify", lambda *a, **k: {"triage": "READ", "cwd": str(a[3])})
        return asyncio.run(mcp_server.handle_call_tool("verify", {
            "criteria": [{"id": "j", "kind": "judgment", "statement": "s"}], "diff": "d", **args}))

    def test_no_cwd_outside_a_repo_asks_for_one(self, monkeypatch, tmp_path, patch_trinity_home):
        from trinity_local.mcp_server import ToolError
        with pytest.raises(ToolError) as e:
            self._call(monkeypatch, tmp_path, {})
        assert "pass `cwd`" in str(e.value)

    def test_no_cwd_inside_a_repo_still_runs(self, monkeypatch, tmp_path, patch_trinity_home):
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        res = self._call(monkeypatch, tmp_path, {})
        body = json.loads(res[0]["text"] if isinstance(res[0], dict) else res[0].text)
        assert body["triage"] == "READ"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


def test_a_test_that_imports_the_original_tree_is_not_called_vacuous(tmp_path):
    """An editable install's .pth names the original tree by a path the copy's rewrite cannot
    reach. Simulated with a second path to the same tree. The test DOES depend on the change, so
    "vacuous" was wrong; the positive control marks it not_loaded."""
    from trinity_local import differential
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text("def f(x):\n    return x + 1\n")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "b")
    (root / "src" / "pkg" / "mod.py").write_text("def f(x):\n    return x + 2\n")
    (root / "tests" / "test_m.py").write_text("from pkg.mod import f\n\ndef test_f():\n    assert f(1) == 3\n")
    _git(root, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout
    alias = tmp_path / "installed"           # what an editable install's .pth points at
    alias.symlink_to(root / "src", target_is_directory=True)
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"
    m = differential.measure([("t", cmd)], diff, root,
                             env={"PYTHONPATH": str(alias), "PATH": "/usr/bin:/bin"})
    assert m["status"] == {"t": "not_loaded"}, m
    assert "outside the tree" in m.get("reason", "")
    assert m["status"]["t"] != "vacuous"


def test_a_test_that_could_not_run_does_not_make_the_kernel_relevant():
    from trinity_local.differential import relevance
    runs = [{"id": "ok", "green": True}, {"id": "gone", "green": None, "could_not_run": True}]
    measured = {"ran": True, "status": {"ok": "vacuous"}}
    assert relevance(runs, {"ok", "gone"}, measured) is False


def test_a_test_that_runs_a_missing_tool_is_still_red(tmp_path):
    """Exit 127 from INSIDE a test (it calls a tool the code needs) is a real red: only a
    command whose own program is missing is 'could not run' (Codex's verify read)."""
    out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s",
                              "command": "sh -c 'trinity-no-such-tool-xyz'"}])
    assert out["triage"] == "STOP" and out["kernel"]["runs"][0]["green"] is False


@pytest.mark.parametrize("command,resolves", [
    ("trinity-no-such-command-xyz --run", False),
    ("FOO=1 trinity-no-such-command-xyz", False),
    ("sh -c 'exit 127'", True),
    ("exit 127", True),
    ("/no/such/bin/pytest -q", False),
])
def test_program_resolution(command, resolves):
    assert V._program_resolves(command, {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}) is resolves


def test_the_positive_control_removes_code_not_config(tmp_path):
    """A changed config file stays: removing it could fail the test for an unrelated reason."""
    from trinity_local import differential
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text("def f(x):\n    return x + 1\n")
    (root / "settings.toml").write_text("mode = 'a'\n")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "b")
    (root / "src" / "pkg" / "mod.py").write_text("def f(x):\n    return x + 2\n")
    (root / "settings.toml").write_text("mode = 'b'\n")
    (root / "tests" / "test_m.py").write_text(
        "from pathlib import Path\nfrom pkg.mod import f\n\ndef test_f():\n"
        "    assert Path('settings.toml').exists()\n    assert f(1) == 3\n")
    _git(root, "add", "-A")
    diff = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout
    alias = tmp_path / "installed"
    alias.symlink_to(root / "src", target_is_directory=True)
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"
    m = differential.measure([("t", cmd)], diff, root,
                             env={"PYTHONPATH": str(alias), "PATH": "/usr/bin:/bin"})
    assert m["status"] == {"t": "not_loaded"}, m
