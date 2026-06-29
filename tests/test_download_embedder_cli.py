"""Tests for the trinity-local download-embedder admin command.

Wraps the underlying setup_model() helper as a first-class Trinity
verb so the embedder gate's error message can point at it instead
of a raw huggingface-cli line. Companion to e6d1d44 (CLI gate).
"""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest


class TestCommandRegistration:
    def test_download_embedder_in_subparser_surface(self):
        """The verb must register on argparse so users discover it via
        --help. Drift here = the gate's error message points at a
        command that doesn't exist."""
        from trinity_local.commands.download_embedder import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        args = parser.parse_args(["download-embedder"])
        assert getattr(args, "handler", None) is not None
        # --force flag exposed.
        args = parser.parse_args(["download-embedder", "--force"])
        assert args.force is True

    def test_registered_in_main_command_modules(self):
        """The command must be in main.CORE_COMMAND_MODULES so
        `trinity-local download-embedder` actually dispatches. Adding
        the module but forgetting to register it is silent failure."""
        from trinity_local import main

        assert "download_embedder" in main.CORE_COMMAND_MODULES, (
            "download_embedder must be in CORE_COMMAND_MODULES — "
            "otherwise the verb is unreachable via the CLI."
        )


class TestHandlerBehavior:
    def test_success_returns_zero(self, monkeypatch, capsys):
        """Happy path: setup_model returns a 'Model ready: …' string →
        handler exits 0 + prints success."""
        from trinity_local.commands.download_embedder import (
            handle_download_embedder,
        )

        monkeypatch.setattr(
            "trinity_local.embeddings.setup_model",
            lambda force=False: "Model ready: nomic-ai/modernbert-embed-base",
        )
        args = SimpleNamespace(force=False, json=False)
        rc = handle_download_embedder(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Model ready" in out

    def test_failure_returns_one(self, monkeypatch, capsys):
        """When setup_model returns a non-'Model ready' message (e.g.,
        network failed, deps missing), the handler returns 1 and prints
        to stderr."""
        from trinity_local.commands.download_embedder import (
            handle_download_embedder,
        )

        monkeypatch.setattr(
            "trinity_local.embeddings.setup_model",
            lambda force=False: "Download failed: connection refused",
        )
        args = SimpleNamespace(force=False, json=False)
        rc = handle_download_embedder(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "Download failed" in err

    def test_force_flag_passed_through(self, monkeypatch):
        """--force must thread through to setup_model so the user can
        re-download a partial / corrupt cache entry."""
        from trinity_local.commands.download_embedder import (
            handle_download_embedder,
        )

        captured = {}

        def _fake_setup(force=False):
            captured["force"] = force
            return "Model ready: ..."

        monkeypatch.setattr("trinity_local.embeddings.setup_model", _fake_setup)
        args = SimpleNamespace(force=True, json=False)
        handle_download_embedder(args)
        assert captured.get("force") is True

    def test_json_mode_emits_parseable_output(self, monkeypatch, capsys):
        """--json emits structured output for agent consumption.
        Claude Code can pluck the result inline."""
        from trinity_local.commands.download_embedder import (
            handle_download_embedder,
        )

        monkeypatch.setattr(
            "trinity_local.embeddings.setup_model",
            lambda force=False: "Model ready: nomic-ai/modernbert-embed-base",
        )
        args = SimpleNamespace(force=False, json=True)
        rc = handle_download_embedder(args)
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["ok"] is True
        assert "Model ready" in parsed["message"]
        assert parsed["force"] is False


class TestErrorMessageReferencesVerb:
    """The embedder gate's error message must point at the new verb,
    not the raw huggingface-cli command. Closes the loop: user hits
    a missing-model error → sees the verb → runs it → continues."""

    def test_gate_error_mentions_new_verb(self, tmp_path, monkeypatch):
        from pathlib import Path
        from trinity_local.embeddings import (
            EmbedderNotReadyError, require_embedder_ready,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with pytest.raises(EmbedderNotReadyError) as exc_info:
            require_embedder_ready()
        msg = str(exc_info.value)
        assert "trinity-local download-embedder" in msg, (
            "Embedder gate error must surface the Trinity verb — "
            "raw huggingface-cli alone is too low-level for the "
            "agent-paste-into install path the audience-expansion "
            "claim rides on."
        )
