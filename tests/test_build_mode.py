"""Build mode: every lab answers with a patch; frozen tests decide which lands (build_mode.py).

The labs are faked at the provider boundary (`provider_factory`), so everything Trinity does around
them runs for real: the copy the labs read, the scratch copies, `git apply`, the acceptance command.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from trinity_local import build_mode
from trinity_local.config import AppConfig, ProviderConfig
from trinity_local.providers import ProviderResult

TEST = "tests/test_calc.py"
COMMAND = f"PYTHONPATH=. {sys.executable} -m pytest -q -p no:cacheprovider {TEST}"
TASK = "add() returns the difference; it must return the sum."


def _diff(new_body: str) -> str:
    return ("diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            f" def add(a, b):\n-    return a - b\n+    return {new_body}\n")


RIGHT = "Here you go.\n```diff\n" + _diff("a + b") + "```\nFixed the operator."
ALSO_RIGHT = "```diff\n" + _diff("b + a") + "```"
WRONG = "```diff\n" + _diff("a * b") + "```"
# Breaks the code and ships a test that always passes in place of the frozen one.
TAMPER = ("```diff\n" + _diff("a * b") +
          "diff --git a/tests/test_calc.py b/tests/test_calc.py\nnew file mode 100644\n"
          "--- /dev/null\n+++ b/tests/test_calc.py\n@@ -0,0 +1,2 @@\n+def test_add():\n+    assert True\n```")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "-c", "commit.gpgsign=false",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    # Where there is no OS sandbox (Linux CI), grade through build mode's own opt-in: these tests apply only their
    # own synthetic patches, and the sandbox itself has the needs_sandbox tests below. Without it every flow and
    # input-refusal test stopped at the missing-sandbox refusal before reaching what it checks (public CI's
    # Ubuntu jobs, 2026-10-26). On macOS the sandbox runs as before.
    if build_mode._sandbox_exec() is None:
        monkeypatch.setenv("TRINITY_BUILD_UNSANDBOXED", "1")
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (r / TEST).write_text("import calc\n\n\ndef test_add():\n    assert calc.add(2, 3) == 5\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _config() -> AppConfig:
    provs = {n: ProviderConfig(name=n, type="cli", enabled=True, label=n, command=[n], args=[],
                               task_types=set(), model=f"{n}-model")
             for n in ("claude", "codex", "antigravity")}
    return AppConfig(max_turns=1, notifications=False, providers=provs, task_preferences={})


def _factory(answers: dict[str, str], seen: list):
    class Fake:
        def __init__(self, cfg):
            self.name = cfg.name

        def run(self, prompt, cwd):
            cwd = Path(cwd)       # checked now: the copy is deleted when run_build returns
            seen.append({"lab": self.name, "prompt": prompt, "has_code": (cwd / "calc.py").is_file(),
                         "has_test": (cwd / TEST).exists(), "has_git": (cwd / ".git").exists()})
            return ProviderResult(provider=self.name, stdout=answers[self.name], stderr="", returncode=0,
                                  elapsed_seconds=1.0, usage={"total_tokens": 100})
    return Fake


def _build(repo: Path, answers: dict[str, str], seen: list | None = None, **kw) -> dict:
    return build_mode.run_build(TASK, COMMAND, [TEST], repo, members=list(answers), config=_config(),
                                provider_factory=_factory(answers, seen if seen is not None else []), **kw)


def test_the_only_passing_candidate_is_picked_and_recorded(repo, patch_trinity_home):
    out = _build(repo, {"claude": RIGHT, "codex": WRONG, "antigravity": "I would change the operator."})
    by = {c["lab"]: c for c in out["candidates"]}
    assert out["acceptance_at_head"] == "failed"
    assert by["claude"]["passed"] and not by["codex"]["passed"] and by["codex"]["grade"] == "failed"
    assert by["antigravity"]["has_diff"] is False and by["antigravity"]["passed"] is False
    assert out["picked"] == "claude" and out["chair"] is None
    assert "return a + b" in out["patch"] and Path(out["patch_path"]).is_file()
    # The user's tree is untouched: build mode hands back a patch, it never applies one.
    assert "a - b" in (repo / "calc.py").read_text()
    rows = (patch_trinity_home / "analytics" / "build_runs.jsonl").read_text().splitlines()
    row = json.loads(rows[-1])
    assert row["picked"] == "claude" and row["passed"] == ["claude"]
    assert TASK not in rows[-1] and "a + b" not in rows[-1], "the record holds no task text and no diff"


def test_the_labs_never_see_the_acceptance_test(repo, patch_trinity_home):
    seen: list = []
    _build(repo, {"claude": RIGHT, "codex": WRONG}, seen)
    assert len(seen) == 2
    for s in seen:
        assert s["has_code"] and not s["has_git"]
        assert not s["has_test"], "the frozen test must be absent from what the labs read"
        assert str(repo) not in s["prompt"], "the prompt never names the user's real tree"


def test_a_candidate_that_rewrites_the_test_gains_nothing(repo, patch_trinity_home):
    out = _build(repo, {"claude": TAMPER})
    (cand,) = out["candidates"]
    assert cand["applied"] is True and cand["passed"] is False, "the frozen test is put back before grading"
    assert out["picked"] is None


def test_two_passing_candidates_go_to_a_blinded_chairman(repo, patch_trinity_home, monkeypatch):
    from trinity_local import providers
    prompts: list[str] = []

    def chair(prompt, config, primary, cwd, **_kw):
        prompts.append(prompt)
        return ProviderResult(provider=primary, stdout='{"pick": "B", "why": "smaller"}', stderr="",
                              returncode=0), primary, None
    monkeypatch.setattr(providers, "run_with_chairman_fallback", chair)
    out = _build(repo, {"claude": RIGHT, "codex": ALSO_RIGHT})
    assert sorted(c["lab"] for c in out["candidates"] if c["passed"]) == ["claude", "codex"]
    assert out["chair"] == "claude" and out["why"] == "smaller" and out["picked"] in ("claude", "codex")
    assert "claude" not in prompts[0] and "codex" not in prompts[0], "candidates reach the chair blinded"


def test_an_unusable_chair_falls_back_to_the_smallest_passing_diff(repo, patch_trinity_home, monkeypatch):
    from trinity_local import providers
    monkeypatch.setattr(providers, "run_with_chairman_fallback",
                        lambda *a, **k: (None, None, "all chairs failed"))
    out = _build(repo, {"claude": RIGHT, "codex": ALSO_RIGHT})
    assert out["picked"] in ("claude", "codex") and out["chair"] is None
    assert "smallest passing diff" in out["why"]


def test_it_refuses_a_test_that_already_passes(repo, patch_trinity_home):
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    with pytest.raises(build_mode.BuildRefused, match="already passes"):
        _build(repo, {"claude": RIGHT})


@pytest.mark.parametrize("hidden, why", [([], "at least one"), (["../x.py"], "without '..'"),
                                         (["tests/missing.py"], "not found")])
def test_it_refuses_bad_hidden_tests(repo, patch_trinity_home, hidden, why):
    with pytest.raises(build_mode.BuildRefused, match=why):
        build_mode.run_build(TASK, COMMAND, hidden, repo, config=_config(),
                             provider_factory=_factory({}, []))


def test_it_refuses_a_hidden_test_that_links_outside_the_repo(repo, tmp_path, patch_trinity_home):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("def test_x():\n    assert False\n")
    (repo / "tests" / "test_link.py").symlink_to(outside)
    with pytest.raises(build_mode.BuildRefused, match="outside the repo"):
        build_mode.run_build(TASK, COMMAND, ["tests/test_link.py"], repo, config=_config(),
                             provider_factory=_factory({}, []))


def test_it_refuses_outside_a_git_work_tree(tmp_path, patch_trinity_home):
    (tmp_path / "plain").mkdir()
    with pytest.raises(build_mode.BuildRefused, match="git work tree"):
        build_mode.run_build(TASK, COMMAND, [TEST], tmp_path / "plain", config=_config(),
                             provider_factory=_factory({}, []))


@pytest.mark.parametrize("text, has", [(RIGHT, True), ("prose only", False), (_diff("a + b"), True),
                                       ("```python\nprint(1)\n```", False)])
def test_extract_diff(text, has):
    assert (build_mode.extract_diff(text) is not None) is has


def test_build_mode_grants_no_write_access():
    """Read-only by construction (amd_0465): the labs go through make_provider, the member path
    tests/test_council_dispatch_read_only.py pins, and this module spells no write grant.
    Mutation-proven: adding any of these strings to build_mode.py turns this red."""
    src = Path(build_mode.__file__).read_text()
    for grant in ("workspace-write", "danger-full-access", "acceptEdits", "bypassPermissions",
                  "dangerously-skip-permissions", "--full-auto", "--yolo"):
        assert grant not in src, grant
    assert "make_provider" in src and "provider_factory or make_provider" in src


def test_run_council_build_mode_refuses_without_hidden_tests(patch_trinity_home):
    from trinity_local.mcp_server import handle_call_tool
    res = asyncio.run(handle_call_tool("run_council", {"task": TASK, "mode": "build",
                                                       "acceptance_command": COMMAND}))
    payload = json.loads(res[0].text if hasattr(res[0], "text") else res[0]["text"])
    assert payload["ok"] is False and "hidden_tests" in payload["error"]


def test_run_council_build_mode_passes_through(repo, patch_trinity_home, monkeypatch):
    from trinity_local.mcp_server import handle_call_tool
    calls = []
    monkeypatch.setattr(build_mode, "run_build", lambda *a, **k: calls.append(a) or {"ok": True, "mode": "build"})
    res = asyncio.run(handle_call_tool("run_council", {
        "task": TASK, "mode": "build", "acceptance_command": COMMAND, "hidden_tests": [TEST],
        "cwd": str(repo), "members": ["chatgpt", "claude"]}))
    payload = json.loads(res[0].text if hasattr(res[0], "text") else res[0]["text"])
    assert payload == {"ok": True, "mode": "build"}
    assert calls[0][4] == ["codex", "claude"], "brand slugs fold to dispatch slugs, as for a council"


# --- Grading runs model-written code: sandboxed, and only safe patches apply (amd_0466) ---------

SYMLINK = ("```diff\ndiff --git a/out b/out\nnew file mode 120000\n--- /dev/null\n+++ b/out\n"
           "@@ -0,0 +1 @@\n+/etc\n\\ No newline at end of file\n```")


def _escaping(target: Path) -> str:
    """Correct arithmetic, plus an import-time write to `target`, outside the scratch copy."""
    return ("```diff\ndiff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,4 @@\n"
            f"+import pathlib\n+pathlib.Path({str(target)!r}).write_text('x')\n"
            " def add(a, b):\n-    return a - b\n+    return a + b\n```")


@pytest.mark.parametrize("patch, why", [
    ("diff --git a/x b/x\nnew file mode 120000\n", "symlink"),
    ("diff --git a/../evil b/../evil\n--- a/../evil\n+++ b/../evil\n", "outside the tree"),
    ("--- /dev/null\n+++ /etc/passwd\n", "outside the tree"),
    ("diff --git a/link b/link\nindex 1a2b3c4..5d6e7f8 120000\n", "symlink"),
    ('diff --git "a/x" "/etc/x"\n--- "a/x"\n+++ "/etc/x"\n', "outside the tree"),
])
def test_unsafe_patches_are_named(patch, why):
    assert why in (build_mode.unsafe_patch(patch) or "")


def test_a_safe_patch_is_not_flagged():
    assert build_mode.unsafe_patch(_diff("a + b")) is None


def test_a_patch_that_creates_a_symlink_is_never_applied(repo, patch_trinity_home):
    out = _build(repo, {"claude": SYMLINK})
    (cand,) = out["candidates"]
    assert cand["grade"] == "unsafe" and cand["applied"] is False and "symlink" in cand["error"]


@pytest.mark.skipif(build_mode._sandbox_exec() is None, reason="needs macOS sandbox-exec")
def test_model_code_run_at_grading_cannot_write_outside_its_copy(repo, patch_trinity_home):
    target = repo / "escaped.txt"
    out = _build(repo, {"claude": _escaping(target)})
    assert not target.exists(), "the candidate's import-time write escaped the grading sandbox"
    assert out["candidates"][0]["passed"] is False


def test_no_os_sandbox_refuses_unless_the_user_opts_in(repo, patch_trinity_home, monkeypatch):
    monkeypatch.setattr(build_mode, "_sandbox_exec", lambda: None)
    monkeypatch.delenv("TRINITY_BUILD_UNSANDBOXED", raising=False)
    with pytest.raises(build_mode.BuildRefused, match="OS sandbox"):
        _build(repo, {"claude": RIGHT})
    monkeypatch.setenv("TRINITY_BUILD_UNSANDBOXED", "1")
    assert _build(repo, {"claude": RIGHT})["picked"] == "claude"


def test_the_apply_step_itself_refuses_an_unsafe_patch(tmp_path):
    (tmp_path / "f.txt").write_text("x\n")
    p = tmp_path / "c.patch"
    p.write_text("diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+/etc\n")
    assert build_mode._apply_any(p, tmp_path, tmp_path) is False
    assert not (tmp_path / "link").exists() and not (tmp_path / "link").is_symlink()


def test_the_sandbox_wrapper_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(build_mode, "_sandbox_exec", lambda: None)
    monkeypatch.delenv("TRINITY_BUILD_UNSANDBOXED", raising=False)
    with pytest.raises(build_mode.BuildRefused, match="no OS sandbox"):
        build_mode._sandboxed("true", tmp_path)
    monkeypatch.setenv("TRINITY_BUILD_UNSANDBOXED", "1")
    assert build_mode._sandboxed("true", tmp_path) == "/bin/sh -c true"


# --- The sandbox is the boundary: no network, no credential reads, no secrets in env (amd_0467) ---

needs_sandbox = pytest.mark.skipif(build_mode._sandbox_exec() is None, reason="needs macOS sandbox-exec")


def _candidate(prelude: str) -> str:
    """Correct arithmetic only when `prelude` leaves LEAK false."""
    body = "".join(f"+{line}\n" for line in prelude.splitlines())
    n = len(prelude.splitlines())
    return ("```diff\ndiff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
            f"@@ -1,2 +1,{n + 2} @@\n{body} def add(a, b):\n-    return a - b\n"
            "+    return a - b if LEAK else a + b\n```")


@needs_sandbox
def test_model_code_cannot_reach_even_localhost(repo, patch_trinity_home):
    import socket
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(0.5)
    port = server.getsockname()[1]
    out = _build(repo, {"claude": _candidate(
        "import socket\ntry:\n    socket.create_connection(('127.0.0.1', %d), 2).sendall(b'x')\n"
        "    LEAK = True\nexcept OSError:\n    LEAK = False" % port)})
    try:
        server.accept()
        reached = True
    except OSError:
        reached = False
    finally:
        server.close()
    assert not reached, "model-written code reached a local socket: a relay out of the machine"
    assert out["candidates"][0]["passed"] is True


@needs_sandbox
def test_model_code_cannot_read_credential_stores(repo, tmp_path, patch_trinity_home, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_test").write_text("secret")
    monkeypatch.setenv("HOME", str(home))
    out = _build(repo, {"claude": _candidate(
        "try:\n    open(%r).read()\n    LEAK = True\nexcept OSError:\n    LEAK = False"
        % str(home / ".ssh" / "id_test"))})
    assert out["candidates"][0]["passed"] is True, "the credential file was readable at grading"


def test_grading_env_carries_no_secrets(repo, patch_trinity_home):
    import os
    env = dict(os.environ, MY_API_TOKEN="x", SSH_AUTH_SOCK="/tmp/agent.sock")
    out = _build(repo, {"claude": _candidate(
        "import os\nLEAK = 'MY_API_TOKEN' in os.environ or 'SSH_AUTH_SOCK' in os.environ")}, env=env)
    assert out["candidates"][0]["passed"] is True, "a secret-looking variable reached model-written code"


def test_the_apply_step_runs_inside_the_sandbox(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(build_mode, "_sandbox_exec", lambda: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(build_mode.subprocess, "run",
                        lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0))
    p = tmp_path / "c.patch"
    p.write_text(_diff("a + b"))
    assert build_mode._apply_any(p, tmp_path, tmp_path) is True
    assert seen and seen[0][0] == "/usr/bin/sandbox-exec" and "git" in seen[0]


def test_the_apply_advice_survives_a_home_a_shell_would_act_on(repo, tmp_path, monkeypatch):
    """`next` is pasted into a shell, and the patch it names lives under TRINITY_HOME: a home
    path with a space, $(...) or backticks must reach git apply as one path and run nothing."""
    import re

    home = tmp_path / "h $(touch PWNED) `touch PWNED2` x"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    out = _build(repo, {"claude": RIGHT})
    assert out["picked"] == "claude", out
    advice = re.search(r"with `(git apply .*)`, run the", out["next"]).group(1)
    subprocess.run(["sh", "-c", advice], cwd=repo, check=True, capture_output=True)
    assert "a + b" in (repo / "calc.py").read_text()
    assert not (repo / "PWNED").exists() and not (repo / "PWNED2").exists(), "the advice ran a command"
