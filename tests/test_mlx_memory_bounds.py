"""The MLX embedder must not let Metal memory grow without bound (founder report 2026-10-20: each
Claude session's trinity-local MCP server grew to 6-39 GB and the machine crashed).

Measured cause: MLX caches every freed Metal buffer and, by default, never shrinks the cache (ten
16-text batches left 8.2 GB cached with 0.6 GB in use); and a 128-text chunk of long texts needs ~20 GB
at once. MLX is faked here at the module boundary so the contract is checked without a GPU or a model.
"""
from __future__ import annotations

import sys
import types

import pytest

from trinity_local.embeddings import backend_mlx_native as B


@pytest.fixture
def fake_mlx(monkeypatch):
    calls = {"cache_limit": [], "clear": 0, "chunks": []}

    class Arr:
        def __init__(self, v):
            self.v = v

        def astype(self, _dtype):
            return self

        def tolist(self):
            return self.v

    core = types.ModuleType("mlx.core")
    core.float32 = "f32"
    core.array = Arr
    core.set_cache_limit = lambda n: calls["cache_limit"].append(n)

    def clear():
        calls["clear"] += 1
    core.clear_cache = clear
    mlx = types.ModuleType("mlx")
    mlx.core = core

    emb = types.ModuleType("mlx_embeddings")
    emb.load = lambda model_id: ("model", "tok")

    def generate(model, tok, texts):
        calls["chunks"].append(list(texts))
        # one vector per text, tagged with its length so order can be checked
        return types.SimpleNamespace(text_embeds=[[float(len(t))] + [1.0] * 3 for t in texts])
    emb.generate = generate
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(sys.modules, "mlx_embeddings", emb)
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: object())
    return calls


def test_the_cache_is_capped_when_the_model_loads(fake_mlx):
    e = B.MlxNativeEmbedder()
    e.embed_batch(["hello"], dim=4)
    assert fake_mlx["cache_limit"] == [B._MLX_CACHE_LIMIT], "MLX's buffer cache must be capped at load"
    assert 0 < B._MLX_CACHE_LIMIT <= 1024 ** 3


def test_the_cache_is_cleared_after_every_chunk(fake_mlx):
    e = B.MlxNativeEmbedder()
    e.embed_batch([f"text {i}" for i in range(300)], dim=4, batch_size=128)
    assert len(fake_mlx["chunks"]) == 3
    assert fake_mlx["clear"] == len(fake_mlx["chunks"]), "each chunk's buffers must leave the cache"


def test_a_chunk_is_bounded_by_characters_as_well_as_count(fake_mlx):
    e = B.MlxNativeEmbedder()
    long = ["x" * 20_000 for _ in range(10)]
    short = ["y" * 50 for _ in range(10)]
    vecs = e.embed_batch(long + short, dim=4, batch_size=128)
    for chunk in fake_mlx["chunks"]:
        assert len(chunk) <= 128
        assert sum(len(t) for t in chunk) <= B._CHUNK_CHAR_BUDGET + 64 or len(chunk) == 1
    assert len(fake_mlx["chunks"]) > 1, "ten 20k-character texts must not share one chunk"
    # order and count are kept: the first coordinate tags each input's (prefixed) length
    assert len(vecs) == 20
    assert all(v[0] > v_next[0] for v, v_next in [(vecs[9], vecs[10])])


def test_a_single_text_over_the_budget_goes_alone():
    out = list(B._chunks(["a" * 100_000, "b", "c"], batch_size=128, budget=1_000))
    assert out == [["a" * 100_000], ["b", "c"]]


def test_short_texts_still_share_a_chunk():
    out = list(B._chunks(["t"] * 200, batch_size=128, budget=B._CHUNK_CHAR_BUDGET))
    assert [len(c) for c in out] == [128, 72]


def test_a_malformed_limit_falls_back_instead_of_breaking_imports(monkeypatch):
    monkeypatch.setenv("TRINITY_MLX_CACHE_LIMIT_MB", "lots")
    assert B._cache_limit_bytes() == 256 * 1024 * 1024
    monkeypatch.setenv("TRINITY_MLX_CACHE_LIMIT_MB", "64")
    assert B._cache_limit_bytes() == 64 * 1024 * 1024
