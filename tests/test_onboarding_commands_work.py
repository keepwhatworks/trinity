"""The commands a new user is shown work (coherence review 2026-10-15).

Reproduced on the shipped v1.7.402 wheel in a clean venv: `status` printed none of its
failing checks; it showed every user without the optional Chrome extension a failing check;
the first-run hint `trinity-local council "<question>"` exited 2; `download-embedder` could
not download because every verb pinned HuggingFace offline first. The launchpad's "Refresh
memory" and "Repair extension" died at the host's 120 s timeout. `update --deps`, the fix
status names for a broken `mcp`, ran pip inside a uv tool environment.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_status_names_every_failing_check(monkeypatch, capsys, patch_trinity_home):
    from trinity_local import health_checks
    from trinity_local.commands import status as S
    real = health_checks.run_doctor

    def doctored():
        r = real()
        r.checks.append(health_checks.CheckResult(name="provider:codex", ok=False,
                                                  detail="codex is signed out",
                                                  fix="codex login"))
        return r
    monkeypatch.setattr(S, "run_doctor", doctored)
    S.handle_status(SimpleNamespace(json=False, as_json=False))
    out = capsys.readouterr().out
    assert "✗ codex is signed out" in out and "codex login" in out


def test_the_optional_extension_is_not_a_failure(monkeypatch):
    from trinity_local import health_checks, launchpad_data
    monkeypatch.setattr(launchpad_data, "dispatch_readiness",
                        lambda: {"ready": False, "recommended_action": "install the extension"})
    r = health_checks._check_dispatch_ready()
    assert r.ok is True and r.fix == "trinity-local install-extension"


def test_the_first_run_hint_is_a_command_that_parses(patch_trinity_home):
    from trinity_local.commands.status import _first_run_rung
    health = SimpleNamespace(checks=[
        SimpleNamespace(name="lens_built", ok=True, fix="trinity-local lens"),
        SimpleNamespace(name="provider:claude", ok=True, fix=None),
        SimpleNamespace(name="provider:codex", ok=True, fix=None)])
    for transcripts in (0, 24901):
        rung = _first_run_rung(health, 0, transcripts)
        cmd = rung["fix"].split("# or:", 1)[1].strip()
        argv = shlex.split(cmd)
        assert argv[:2] == ["trinity-local", "plan"], cmd
        from trinity_local.main import build_parser
        ns = build_parser().parse_args(argv[1:])
        assert ns.task_positional, "the question must reach the parser"


def test_plan_takes_the_question_positionally():
    from trinity_local.main import build_parser
    ns = build_parser().parse_args(["plan", "Ship now or after the migration?"])
    assert ns.task_positional == "Ship now or after the migration?" and ns.task is None
    ns = build_parser().parse_args(["plan", "--task", "x"])
    assert ns.task == "x"


def test_plan_without_a_question_says_how(capsys):
    from trinity_local.commands.council import handle_council_launch
    assert handle_council_launch(SimpleNamespace(task=None, task_positional=None)) == 2
    assert 'trinity-local plan "' in capsys.readouterr().err


def _pin_after(argv, tmp_path):
    probe = (f"import os, sys; sys.argv={argv!r}\n"
             "from trinity_local import main as M\n"
             "M._run_schema_migrations=lambda: None\n"
             "M.build_parser=lambda: (_ for _ in ()).throw(SystemExit(0))\n"
             "try:\n    M.main()\nexcept SystemExit:\n    pass\n"
             "print('PIN', os.environ.get('HF_HUB_OFFLINE'))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_"))}
    env.update(TRINITY_HOME=str(tmp_path), PYTHONPATH=str(REPO / "src"))
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=120)
    return r.stdout


@pytest.mark.parametrize("argv", [["trinity-local", "plan", "download-embedder"],
                                  ["trinity-local", "plan", "--task", "download-embedder"]])
def test_an_argument_equal_to_the_verb_stays_offline(argv, tmp_path):
    """Only the verb unpins: an element-membership check skipped the pin for any argument
    that equalled the verb's name (Gemini's verify read)."""
    assert "PIN 1" in _pin_after(argv, tmp_path)


def test_download_embedder_is_not_pinned_offline(tmp_path):
    probe = ("import os, sys; sys.argv=['trinity-local','download-embedder','--help']\n"
             "from trinity_local import main as M\n"
             "M._run_schema_migrations=lambda: None\n"
             "try:\n    M.main()\nexcept SystemExit:\n    pass\n"
             "print('PIN', os.environ.get('HF_HUB_OFFLINE'))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_"))}
    env.update(TRINITY_HOME=str(tmp_path), PYTHONPATH=str(REPO / "src"))
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=120)
    assert "PIN None" in r.stdout, r.stdout[-300:] + r.stderr[-300:]


@pytest.mark.parametrize("kind", ["dream", "extension-repair-auto"])
def test_long_host_actions_are_detached(kind):
    from trinity_local import capture_host
    assert kind in capture_host._DETACHED_ACTIONS


def test_update_deps_on_a_uv_install_reruns_the_installer(monkeypatch):
    from trinity_local.commands import update as U
    ran = []
    monkeypatch.setattr(U, "_uv_managed", lambda: True)
    monkeypatch.setattr(U, "_refresh_pip_deps", lambda j: ran.append("pip") or 1)
    monkeypatch.setattr(U.subprocess, "run", lambda argv, check=False: ran.append(argv[-1]) or
                        SimpleNamespace(returncode=0))
    assert U.handle_update(SimpleNamespace(deps=True, json=False, check=False, skill_dir=None)) == 0
    assert "pip" not in ran and ran, ran


def test_uninstall_removes_the_reflex_blocks_and_nothing_else(tmp_path, monkeypatch, capsys):
    from trinity_local.commands import install as I
    monkeypatch.setattr(I.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.write_text("# my rules\nkeep me\n", encoding="utf-8")
    I._reflex_one_file(claude_md, remove=False)
    assert I.REFLEX_BEGIN in claude_md.read_text()
    monkeypatch.chdir(tmp_path)
    I.handle_uninstall(SimpleNamespace(yes=True, include_data=False, include_hf_cache=False))
    text = claude_md.read_text()
    assert I.REFLEX_BEGIN not in text and "keep me" in text


def test_plan_prints_the_decision_not_just_paths():
    from trinity_local.commands.council import format_verdict
    outcome = SimpleNamespace(
        routing_label=SimpleNamespace(decision="Ship after the migration.",
                                      flip_condition="If the migration slips past Friday.",
                                      disagreed_claims=[{"claim": "The cache is safe to ship now."}]),
        metadata={"member_failures": [{"provider": "codex", "reason": "quota_exhausted"}]})
    text = format_verdict(outcome)
    for want in ("Ship after the migration.", "If the migration slips", "safe to ship now",
                 "codex (quota_exhausted)"):
        assert want in text, want


def test_the_positional_question_reaches_the_council(monkeypatch, patch_trinity_home):
    from trinity_local.commands import council as C
    seen = {}
    monkeypatch.setattr(C, "handle_council_start", lambda a: seen.setdefault("bundle", a.bundle))
    C.handle_council_launch(SimpleNamespace(
        task=None, task_positional="Ship now or after the migration?", goal="g", instructions="i",
        context_file=None, project_hint="", members=["claude", "codex"], primary_provider=None,
        cwd=".", config=None, open_browser=False, status_token=None))
    from trinity_local.council_runtime import load_prompt_bundle
    assert load_prompt_bundle(seen["bundle"]).task_text == "Ship now or after the migration?"
