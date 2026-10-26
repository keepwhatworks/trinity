"""scripts/security_gate.py: the mechanical trigger for the pre-push security pass
(council_0d0d398d463f5f7b, amd_0470). It must fire on the shapes that shipped real holes in build mode
(025bba74, ad4b4b6a) and stay quiet on prose, tests and removed lines."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("security_gate", REPO / "scripts" / "security_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _diff(path: str, *added: str, removed: str = "") -> str:
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", "@@ -1,1 +1,2 @@"]
    if removed:
        lines.append(f"-{removed}")
    lines += [f"+{a}" for a in added]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("line, trigger", [
    ('    rc = subprocess.run(["git", "apply", str(p)], cwd=work)', "patch application"),
    ('    tries = (["patch", "-p1", "-f", str(p)],)', "patch application"),
    ("        (work / name).symlink_to((cwd / name).resolve())", "symlinks and paths"),
    ("    proc = subprocess.Popen(cmd, shell=True)", "process execution"),
    ("    tf.extractall(tree)", "archive extraction"),
    ('    argv = ["sandbox-exec", "-p", profile]', "sandbox or permissions"),
    ("    data = pickle.loads(blob)", "deserialization"),
    ("    socket.create_connection(('127.0.0.1', port))", "network"),
    ("from subprocess import run as sh", "process execution"),
    ("    member = tf.extract(name, dest)", "archive extraction"),
    ("import socket as s", "network"),
])
def test_fires_on_the_shapes_that_shipped_holes(line, trigger):
    assert trigger in gate.triggers(_diff("src/trinity_local/build_mode.py", line))


def test_fires_on_a_permission_setting_in_config():
    assert "permission or sandbox config" in gate.triggers(
        _diff("config.json", '  "args": ["--sandbox", "workspace-write"]'))
    assert gate.triggers(_diff("config.json", '  "model": "gpt-5.6"')) == {}


@pytest.mark.parametrize("diff", [
    _diff("tests/test_build_mode.py", "    subprocess.run(['git', 'apply', 'x'])"),
    _diff("docs/plan.md", "We run subprocess.run and git apply in a sandbox."),
    _diff("src/trinity_local/build_mode.py", "x = 1", removed="subprocess.run(['git', 'apply'])"),
    _diff("scripts/security_gate.py", '    "process execution": r"\\bsubprocess\\b",'),
])
def test_quiet_on_tests_prose_removed_lines_and_its_own_patterns(diff):
    assert gate.triggers(diff) == {}


def test_the_build_mode_commits_would_have_required_the_pass():
    import subprocess
    for sha in ("025bba74", "ad4b4b6a"):
        diff = subprocess.run(["git", "show", sha], cwd=REPO, capture_output=True, text=True).stdout
        if not diff:
            pytest.skip("history not available (public export)")
        assert gate.triggers(diff), sha
