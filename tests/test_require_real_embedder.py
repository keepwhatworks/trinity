"""require_real_embedder — the production gate that refuses to run semantic
flows on the SHA-1 TF-IDF stub (the "remove tf-idf" work, founder 2026-07-18).

The gate is the #35 green-over-degenerate guard for embeddings: a flow that
computes on the wire-compatible TF-IDF projection and presents the result as a
real semantic space (routing, lens-build, the disagreement / meta-pattern
retrieval tier) must refuse LOUDLY with an install prompt, not silently compute
garbage. TF-IDF stays legal ONLY under the explicit TRINITY_DISABLE_MLX=1
test/CI escape hatch.

Mutation proof: delete the `if not mlx_actually_loaded()` refusal in
require_real_embedder → test_refuses_loudly_in_prod_without_real_backend RED.
"""
from __future__ import annotations

import pathlib

import pytest

from trinity_local import embeddings
from trinity_local.embeddings import EmbedderNotReadyError, require_real_embedder


def test_allows_under_test_hatch(monkeypatch):
    """TRINITY_DISABLE_MLX=1 is the sanctioned stub — the gate must allow it
    even with no real backend loaded (CI has no model; tests need speed)."""
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: False)
    require_real_embedder()  # must not raise


def test_allows_when_real_backend_active(monkeypatch):
    monkeypatch.delenv("TRINITY_DISABLE_MLX", raising=False)
    monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True)
    require_real_embedder()  # must not raise


def test_refuses_loudly_in_prod_without_real_backend(monkeypatch):
    """No hatch + no real backend = a production run with only the TF-IDF stub.
    The gate must raise an actionable EmbedderNotReadyError, never return and
    let the caller compute on fake vectors."""
    monkeypatch.delenv("TRINITY_DISABLE_MLX", raising=False)
    monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: False)
    with pytest.raises(EmbedderNotReadyError) as exc:
        require_real_embedder()
    msg = str(exc.value).lower()
    # loud + actionable: names an install/download path, not a bare failure
    assert ("download" in msg) or ("pip install" in msg) or ("trinity-local download-embedder" in msg)


def test_semantic_flows_gate_on_the_real_backend():
    """Wiring ratchet: the semantic-result flows must gate on the active-backend
    check (require_real_embedder), not only the model-file probe
    (require_embedder_ready) which passes even when the backend can't embed and
    embed() silently falls back to TF-IDF. Reverting any one reds this test."""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "trinity_local"
    for rel in ("commands/vocabulary.py", "commands/dream.py", "commands/me.py", "stale_pass.py"):
        src = (root / rel).read_text()
        assert "require_real_embedder" in src, f"{rel} must gate on require_real_embedder"
