"""Tests for the CLI-side embedder gate that surfaces the ~600 MB model
download as an upfront actionable message instead of a mid-command
surprise.

Pairs with the launchpad "Build deeper memory" card (test_embedder_
status_card.py): both surfaces use the same HF cache probe and both
present the same download command. The CLI gate fails fast — the
user sees the message in <100ms instead of after a multi-minute
CLI startup + an HF_HUB_OFFLINE error mid-Phase-1.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trinity_local.embeddings.backend_mlx import MODEL_ID

# HF cache dir name for the live embed model (modernbert-embed-base post-#244).
# Derived from MODEL_ID so the gate-probe tests track the real model, not a
# hardcoded nomic-v1.5 path that the gate no longer probes.
_MODEL_DIR_ID = MODEL_ID.replace("/", "--")


# ─── Gate primitive ─────────────────────────────────────────────────

class TestRequireEmbedderReady:
    def test_returns_silently_when_snapshot_present(
        self, tmp_path, monkeypatch
    ):
        """Happy path: model weights are in HF cache → return without
        raising. The CLI handler proceeds to do its actual work."""
        from trinity_local.embeddings import require_embedder_ready

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        snapshot = (
            tmp_path / ".cache" / "huggingface" / "hub"
            / f"models--{_MODEL_DIR_ID}"
            / "snapshots" / "abc123"
        )
        snapshot.mkdir(parents=True)
        # At least one weight file makes the "any iterdir" check pass.
        (snapshot / "model.safetensors").write_bytes(b"\x00")

        # Should not raise.
        require_embedder_ready()

    def test_raises_when_model_missing(self, tmp_path, monkeypatch):
        """The actual gating behavior: model isn't in HF cache → raise
        EmbedderNotReadyError with the Trinity download verb in the
        message. Updated 2026-05-19 to prefer the in-product
        `trinity-local download-embedder` verb over the raw
        `huggingface-cli download` command (still mentioned as fallback)."""
        from trinity_local.embeddings import (
            EmbedderNotReadyError, require_embedder_ready,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with pytest.raises(EmbedderNotReadyError) as exc_info:
            require_embedder_ready()

        msg = str(exc_info.value)
        # Preferred: the in-product Trinity verb.
        assert "trinity-local download-embedder" in msg, (
            "Error must surface the Trinity verb — agents in Claude Code "
            "can run it inline without the user needing to know HF cli syntax."
        )
        # Model id should still be discoverable (in the fallback command).
        assert MODEL_ID in msg or "huggingface-cli" in msg
        # And the why ("this command needs it for ..."):
        # Tolerant size check: gate message may mention any size string
        # (currently "~600 MB"; was "~700MB" pre-2026-05-20 re-measure).
        # The OR clauses catch the why ("lens"/"basins") even if the
        # size phrasing evolves.
        assert "600" in msg or "lens" in msg or "basins" in msg

    def test_raises_with_pip_install_when_libs_missing(
        self, tmp_path, monkeypatch
    ):
        """If sentence-transformers isn't installed, even running the
        download command won't help — the message must prepend the
        `[mlx]`-extra install (`pip install -e '.[mlx]'`, resolved from the
        Trinity clone — there is no PyPI package) so the user pastes ONE
        runnable line that chains both steps."""
        from trinity_local.embeddings import (
            EmbedderNotReadyError, require_embedder_ready,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Force the "libs missing" branch by making the import fail.
        import builtins
        real_import = builtins.__import__

        def _fail_st(name, *a, **k):
            if name == "sentence_transformers":
                raise ImportError("simulated")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fail_st)

        with pytest.raises(EmbedderNotReadyError) as exc_info:
            require_embedder_ready()
        msg = str(exc_info.value)
        assert "pip install" in msg, (
            "When MLX libs aren't installed, the error must mention "
            "pip install too — otherwise the user runs the download "
            "command and still can't use the embedder."
        )
        # The honest hint resolves the [mlx] extra from the local clone
        # (`pip install -e '.[mlx]'`), NOT the 404 PyPI form — there is no
        # `trinity-local` package on PyPI (install is git-clone + git-pull).
        assert "-e '.[mlx]'" in msg, msg
        assert "trinity-local[mlx]" not in msg, (
            f"must not hand the user the 404 PyPI form: {msg!r}"
        )
        # Even in the libs-missing case, the chained one-liner should
        # use the Trinity download verb (after pip install) so the
        # in-product narrative stays consistent.
        assert "trinity-local download-embedder" in msg

    def test_empty_snapshot_dir_does_not_count_as_ready(
        self, tmp_path, monkeypatch
    ):
        """A snapshot dir that exists but is empty means the download
        was interrupted. Treat as not-ready so the user re-downloads."""
        from trinity_local.embeddings import (
            EmbedderNotReadyError, require_embedder_ready,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        empty_snapshot = (
            tmp_path / ".cache" / "huggingface" / "hub"
            / f"models--{_MODEL_DIR_ID}"
            / "snapshots" / "abc123"
        )
        empty_snapshot.mkdir(parents=True)
        # No weight files written.

        with pytest.raises(EmbedderNotReadyError):
            require_embedder_ready()


# ─── CLI handler integration ───────────────────────────────────────

class TestCLIHandlersUseGate:
    """All three embedder-using commands must call require_embedder_ready
    before doing any heavy work. Drift here = the user discovers the
    ~600 MB requirement mid-command."""

    def test_dream_handler_imports_gate(self):
        """Source-level grep — handle_dream must import and call
        require_embedder_ready. Cheap regression guard that doesn't
        require wiring up the full handler test apparatus."""
        path = Path(__file__).resolve().parents[1] / "src" / "trinity_local" / "commands" / "dream.py"
        src = path.read_text()
        assert "require_embedder_ready" in src, (
            "dream handler must call require_embedder_ready — Phase 1 "
            "uses embeddings extensively, so the gate belongs at handler "
            "entry, not in the embeddings module's call sites."
        )
        assert "EmbedderNotReadyError" in src

    def test_me_build_handler_imports_gate(self):
        path = Path(__file__).resolve().parents[1] / "src" / "trinity_local" / "commands" / "me.py"
        src = path.read_text()
        assert "require_embedder_ready" in src, (
            "lens-build (handle_me_build) must call require_embedder_ready "
            "— embedder is used for assistant-text reranking + basin clustering."
        )

    def test_vocabulary_handler_imports_gate(self):
        path = Path(__file__).resolve().parents[1] / "src" / "trinity_local" / "commands" / "vocabulary.py"
        src = path.read_text()
        assert "require_embedder_ready" in src, (
            "vocabulary distillation must call require_embedder_ready — "
            "uses synonym embeddings to cluster anchor terms."
        )


class TestDreamHandlerGateBehavior:
    def test_dream_exits_cleanly_when_model_missing(
        self, tmp_path, monkeypatch, capsys
    ):
        """Functional test: run handle_dream with the model missing →
        exits with code 1, message on stderr (NOT silent download, NOT
        crash)."""
        from trinity_local.commands.dream import handle_dream

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        args = SimpleNamespace(
            similarity_threshold=0.9, max_clusters=None,
            min_overlap=2, skip_lens_build=False, skip_consolidate=False,
            sample_size=200, k_basins=12, budget_chars=10000,
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_dream(args)
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "huggingface-cli download" in err, (
            f"Dream gate must print the download command to stderr; got: {err!r}"
        )

    def test_me_build_exits_cleanly_when_model_missing(
        self, tmp_path, monkeypatch, capsys
    ):
        from trinity_local.commands.me import handle_me_build

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        args = SimpleNamespace(
            legacy=False, sample_size=200, k_basins=12,
            budget_chars=10000, dry_run=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_me_build(args)
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "huggingface-cli download" in err
