"""#241: embedder device selection — prefer CUDA, never auto-select MPS.

nomic-embed-v1.5 stays the model (Matryoshka dims + 8k context are why it's
chosen). The "MLX" backend is actually sentence-transformers + torch. nomic's
trust_remote_code custom ops have gaps in Apple-Metal's op coverage → per-op CPU
fallback, and the Metal path can WEDGE (a real backfill dragged from ~56 nodes/s
to ~12 nodes/MIN). But CUDA has full op coverage, so torch IS the fast
cross-platform GPU path. So the device rule:

  - explicit TRINITY_EMBED_DEVICE wins (incl. opt-in "mps" at the wedge risk),
  - else CUDA when available (Linux/Windows NVIDIA — fast + clean),
  - else CPU (Apple-safe default; never auto-MPS for nomic).

These guards stop a refactor from (a) dropping back to unconditional CPU (which
penalises CUDA boxes) or (b) auto-selecting MPS (which reopens the wedge).
"""
from __future__ import annotations

import sys
import types

import pytest


def _install_fake_sentence_transformers(monkeypatch, recorder):
    fake = types.ModuleType("sentence_transformers")

    class _FakeST:
        def __init__(self, model_id, **kw):
            recorder["model_id"] = model_id
            recorder["device"] = kw.get("device")
            recorder["trust_remote_code"] = kw.get("trust_remote_code")

    fake.SentenceTransformer = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)


def test_prefers_cuda_when_available(monkeypatch):
    monkeypatch.delenv("TRINITY_EMBED_DEVICE", raising=False)
    torch = pytest.importorskip("torch")  # CI has no [mlx] extras

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    rec: dict = {}
    _install_fake_sentence_transformers(monkeypatch, rec)
    from trinity_local.embeddings.backend_mlx import MlxEmbedder

    MlxEmbedder()._load()
    assert rec["device"] == "cuda", f"must prefer cuda when available; got {rec.get('device')!r}"
    assert rec["trust_remote_code"] is True


def test_cpu_when_no_cuda_never_auto_mps(monkeypatch):
    monkeypatch.delenv("TRINITY_EMBED_DEVICE", raising=False)
    torch = pytest.importorskip("torch")  # CI has no [mlx] extras

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    rec: dict = {}
    _install_fake_sentence_transformers(monkeypatch, rec)
    from trinity_local.embeddings.backend_mlx import MlxEmbedder

    MlxEmbedder()._load()
    # Apple/CPU-only default is cpu — NEVER auto-mps (that reopens the wedge).
    assert rec["device"] == "cpu", f"no-cuda default must be cpu (never auto-mps); got {rec.get('device')!r}"


def test_env_override_honored(monkeypatch):
    monkeypatch.setenv("TRINITY_EMBED_DEVICE", "mps")
    rec: dict = {}
    _install_fake_sentence_transformers(monkeypatch, rec)
    from trinity_local.embeddings.backend_mlx import MlxEmbedder

    MlxEmbedder()._load()
    assert rec["device"] == "mps", (
        f"TRINITY_EMBED_DEVICE must override the default (opt-in mps at the wedge risk); got {rec.get('device')!r}"
    )
