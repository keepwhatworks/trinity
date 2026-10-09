"""verify's backed doubts: a reader's FAIL counts as evidence only when its test fails
(src/trinity_local/doubts.py, hq_124)."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from trinity_local import doubts as dz

BEFORE = "def covered(x):\n    return x + 1  # one more than the input\n"
AFTER = "def covered(x):\n    return x + 10  # ten more than the input\n"
QUOTE = "return x + 1  # one more than the input"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(BEFORE)
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "before")
    (root / "src" / "pkg" / "mod.py").write_text(AFTER)                  # the change, uncommitted
    return root


def _diff(root):
    return subprocess.run(["git", "diff"], cwd=root, capture_output=True, text=True).stdout


def _read(code, provider="codex", quote=None):
    return {"provider": provider, "lab": "openai", "votes": {"J1": False},
            "tests": {"J1": code} if code is not None else {},
            "evidence": {"J1": quote} if quote else {}}


OLD = "from pkg.mod import covered\n\ndef test_doubt():\n    assert covered(1) == 2\n"


@pytest.mark.parametrize("code, quote, expected", [
    (OLD, QUOTE, "backed"),                       # passes before, fails after, quote checks out
    (OLD, None, "backed_unquoted"),               # same behaviour, no pre-change quote
    (OLD, "a line that was never in the code at all", "backed_unquoted"),
    ("from pkg.mod import covered\n\ndef test_doubt():\n    assert covered(1) == 99\n", QUOTE, "spec_disagreement"),
    ("from pkg.mod import covered\n\ndef test_doubt():\n    assert covered(1) == 11\n", QUOTE, "not_backed"),
    ("def test_doubt(:\n    pass\n", QUOTE, "invalid"),
    (None, None, "no_test"),
])
def test_each_outcome(project, code, quote, expected):
    env = {"PYTHONPATH": str(project / "src"), "PATH": "/usr/bin:/bin"}
    rows = dz.run_doubt_tests([_read(code, quote=quote)], project, sys.executable, env=env, diff=_diff(project))
    assert [r["status"] for r in rows] == [expected], rows
    assert not list((project / "tests").glob("test_trinity_doubt_*")), "the user's tree is never written"


def test_only_fail_votes_are_doubts(project):
    passing = {"provider": "codex", "lab": "openai", "votes": {"J1": True}, "tests": {"J1": "x"}}
    assert dz.run_doubt_tests([passing], project, sys.executable) == []


def test_parse_tests_keeps_known_ids_and_refuses_ambiguity():
    one = '{"votes": {"J1": "FAIL"}, "why": "w", "tests": {"J1": "def test_a(): pass", "ZZ": "x"}}'
    assert dz.parse_tests(one, ["J1"]) == {"J1": "def test_a(): pass"}
    two = one + ' {"votes": {"J1": "FAIL"}, "tests": {"J1": "def test_b(): pass"}}'
    assert dz.parse_tests(two, ["J1"]) == {}


def test_python_for_uses_the_projects_interpreter():
    assert dz.python_for(["/opt/venv/bin/python -m pytest -q"]) == "/opt/venv/bin/python"
    assert dz.python_for(["npm test"]) == sys.executable


def test_verify_asks_for_tests_and_reports_backing_only_when_enabled(project, monkeypatch):
    from trinity_local import verify as v
    from trinity_local.commands.verify import render_card
    seen = []
    test_code = OLD

    def fake_dispatch(name, prompt, cwd, config, effort=None):
        seen.append(prompt)
        out = {"votes": {"J1": "FAIL"}, "why": "changed the contract", "tests": {"J1": test_code},
               "evidence": {"J1": QUOTE}}
        return SimpleNamespace(stdout=json.dumps(out), stderr="", returncode=0, usage={}), \
            SimpleNamespace(model="m", effort=None)
    monkeypatch.setattr(v, "_dispatch", fake_dispatch)
    config = SimpleNamespace(providers={"codex": SimpleNamespace(enabled=True)})
    crit = [{"id": "T1", "kind": "test", "statement": "s", "command": "true"},
            {"id": "J1", "kind": "judgment", "statement": "correct"}]
    env = {"PYTHONPATH": str(project / "src"), "PATH": "/usr/bin:/bin"}

    monkeypatch.delenv(dz.FLAG, raising=False)
    off = v.verify(crit, _diff(project), "", project, providers=("codex",), config=config, env=env)
    assert off["doubts"] is None and "pytest test file" not in seen[-1]

    monkeypatch.setenv(dz.FLAG, "1")
    on = v.verify(crit, _diff(project), "", project, providers=("codex",), config=config, env=env)
    assert "pytest test file" in seen[-1]
    assert [d["status"] for d in on["doubts"]] == ["backed"]
    assert on["triage"] == off["triage"]                    # report-only
    assert "BACKED: its test passes before this change and fails after" in render_card(on)


needs_sandbox = pytest.mark.skipif(not __import__("pathlib").Path(dz.SANDBOX_EXEC).exists(),
                                   reason="macOS sandbox-exec only")


@needs_sandbox
def test_model_written_code_cannot_read_home_or_see_secrets(project, monkeypatch):
    """Security review 2026-10-04: a doubt test is code a model wrote. It must not read the
    user's home, write outside scratch, reach the network, or see the caller's secrets."""
    monkeypatch.setenv("FAKE_API_KEY_FOR_TEST", "sk-should-not-leak")
    code = (
        "import os, pwd, socket, pytest\n"
        "def test_contained():\n"
        "    home = pwd.getpwuid(os.getuid()).pw_dir\n"
        "    assert 'FAKE_API_KEY_FOR_TEST' not in os.environ\n"
        "    with pytest.raises(PermissionError):\n"
        "        os.listdir(home)\n"
        "    with pytest.raises(PermissionError):\n"
        "        open(os.path.join(home, 'trinity_doubt_escape'), 'w')\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
    )
    env = dict(__import__("os").environ, PYTHONPATH=str(project / "src"))
    rows = dz.run_doubt_tests([_read(code)], project, sys.executable, env=env)
    assert [r["status"] for r in rows] == ["not_backed"], rows      # the containment test PASSED
    assert not (__import__("pathlib").Path.home() / "trinity_doubt_escape").exists()


def test_no_sandbox_means_no_execution(project, monkeypatch):
    monkeypatch.setattr(dz, "SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    rows = dz.run_doubt_tests([_read("def test_x():\n    assert False\n")], project, sys.executable, diff=_diff(project))
    assert [r["status"] for r in rows] == ["not_run_no_sandbox"]


def test_grounding_needs_a_pre_change_line_of_the_diff(project):
    d = _diff(project)
    assert dz.grounded(QUOTE, d)
    assert not dz.grounded("return x + 10  # ten more than the input", d)    # an ADDED line is not pre-change
    assert not dz.grounded("short", d)


def test_a_quote_padded_with_extra_text_is_not_verbatim(project):
    assert not dz.grounded(QUOTE + " and something the reader added", _diff(project))


def test_the_sandbox_also_denies_the_original_tree(tmp_path):
    prof = dz.sandbox_profile(tmp_path / "scratch", sys.executable, original=tmp_path / "user_repo")
    assert f'(subpath "{__import__("os").path.realpath(str(tmp_path / "user_repo"))}")' in prof.split("(allow file-read-data")[0]
