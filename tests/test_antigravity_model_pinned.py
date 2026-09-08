"""antigravity's model must reach the argv, not float in agy's settings file.

Until 2026-09-04 the agy CLI genuinely had no --model flag, so Trinity injected
one only for claude. The consequence was quiet: `config.model` for antigravity
was decorative, dispatch ran `agy -p <prompt>` with no model flag, and whatever
~/.gemini/antigravity-cli/settings.json happened to say is what answered — while
every council recorded the config value as "the model that answered".

agy now has the flag (verified against the CLI on the exact argv this builds).
These tests pin the pinning.
"""
from __future__ import annotations

import subprocess

import pytest

from trinity_local import providers as P
from trinity_local.config import ProviderConfig


def _agy(model="gemini-3.8-flash-high", args=None):
    return ProviderConfig(
        name="antigravity", type="cli", enabled=True, label="Antigravity",
        command=["agy", "-p"], args=list(args or []), task_types=set(),
        model=model, effort="high",
    )


def _argv_for(config, monkeypatch, prompt="PROMPT"):
    seen = {}

    def spy(cmd, *a, **kw):
        seen.setdefault("argv", list(cmd))

        class R:
            returncode, stdout, stderr = 0, "ok", ""
        return R()

    monkeypatch.setattr(subprocess, "run", spy)
    P.CLIProvider(config).run(prompt, "/tmp")
    return seen["argv"]


class TestTheModelReachesTheCLI:
    def test_model_is_on_the_argv(self, monkeypatch):
        argv = _argv_for(_agy(), monkeypatch)
        assert "--model" in argv, (
            f"antigravity dispatched without --model: {argv}. agy would fall back "
            "to its own settings.json and the recorded model would be fiction.")
        assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"

    def test_prompt_stays_last_and_after_the_tail_flag(self, monkeypatch):
        """--model between -p and the prompt makes agy fail with
        'Not enough arguments following: p'."""
        argv = _argv_for(_agy(), monkeypatch)
        assert argv[-1] == "PROMPT"
        assert argv.index("--model") < argv.index("-p")

    def test_effort_is_not_also_passed(self, monkeypatch):
        """The SKU carries the level as a suffix, which agy treats as complete.
        Passing --effort too would be two mechanisms for one value."""
        argv = _argv_for(_agy(), monkeypatch)
        assert "--effort" not in argv

    def test_no_duplicate_when_args_already_carry_it(self, monkeypatch):
        argv = _argv_for(_agy(args=["--model", "gemini-3.8-flash-high"]), monkeypatch)
        assert argv.count("--model") == 1


class TestProvenanceMatchesTheInvocation:
    def test_antigravity_reads_pinned(self):
        assert P.injects_model_flag(_agy()) is True
        assert P.model_provenance(_agy(), echo=None) == "pinned"

    def test_a_modelless_config_is_not_claimed_as_pinned(self):
        """The refusal: nothing to inject means nothing is guaranteed."""
        assert P.injects_model_flag(_agy(model=None)) is False
        assert P.model_provenance(_agy(model=None), echo=None) != "pinned"

    @pytest.mark.parametrize("name", ["codex", "someone_elses_cli"])
    def test_injection_is_not_granted_to_other_providers(self, name):
        """codex re-adds its own flag in CodexProvider; a provider that does
        neither must not be stamped pinned."""
        cfg = _agy()
        cfg = ProviderConfig(
            name=name, type=cfg.type, enabled=True, label=name,
            command=cfg.command, args=[], task_types=set(),
            model="x", effort=None)
        assert P.injects_model_flag(cfg) is False
