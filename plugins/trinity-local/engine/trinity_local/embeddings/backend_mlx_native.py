"""Real Apple-MLX embedding backend — `mlx-embeddings` + modernbert-embed-base.

This is the ACTUAL Apple-MLX path. (`backend_mlx.py` is the
torch/sentence-transformers fallback — a historical misnomer kept for the
non-Apple path; see #244.) It activates on Apple Silicon when `mlx` +
`mlx-embeddings` import, and is preferred over the torch backend there because:

  - nomic-embed-text-v1.5's custom `nomic_bert` arch is unsupported by MLX and
    wedges torch-MPS; **nomic-ai/modernbert-embed-base** is the standard
    ModernBERT arch — 8192 native context, Matryoshka (truncate via `[:dim]`),
    Apache-2.0, nomic-trained — and runs ~6,300 nodes/s on an M-series GPU
    (vs torch CPU 56/s, MPS 97/s + 77s load). Measured + chosen over Qwen3-0.6B
    (200x slower), gte-modernbert (70x slower), EmbeddingGemma (license-gated),
    bge-m3 (won't load) — see #243/#244.

`__init__` raises ImportError when MLX isn't available so the package selector
falls through to the torch backend, then TF-IDF.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def _hf_cache_dir(model_id: str) -> Path:
    """Where HuggingFace caches the model (mirrors backend_mlx.hf_cache_model_path)."""
    return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_id.replace('/', '--')}"

# The canonical embedding model — shared with the torch fallback so a machine's
# vectors are model-consistent regardless of which runtime produced them
# (modernbert is a STANDARD arch: MLX on Apple, torch-CUDA/CPU elsewhere).
MLX_MODEL_ID = "nomic-ai/modernbert-embed-base"
DEFAULT_DIM = 768

# nomic/ModernBERT embedders use task prefixes; documents/clustering use this.
_DOC_PREFIX = "search_document: "

# The SAME prefix set backend_mlx._ensure_nomic_prefix guards on. Kept in sync
# deliberately: the two backends must agree on what "already prefixed" means, or
# the same text embeds to two different vectors depending on which one is live.
_NOMIC_PREFIXES = ("search_document:", "search_query:", "clustering:", "classification:")


def _ensure_doc_prefix(text: str) -> str:
    """Prefix ONCE. Never double-prefix.

    THE BUG THIS FIXES (measured 2026-08-01). This module used to do a bare
    `_DOC_PREFIX + t`, with no already-prefixed check — while `backend_mlx.py`
    HAS had that check all along. Several production callers pre-prefix before
    calling:

        ingest_helpers.py:117   f"search_document: {turn.text}"   (CLI ingest)
        stale_pass.py:238       f"search_document: {node.text}"   (embed heal)
        me_builder.py:276       f"search_document: {t[:600]}"

    so their text arrived here and became
    `search_document: search_document: ...`, which embeds ~0.974 away from the
    correctly-prefixed vector for the SAME string.

    Measured consequence on the live corpus: of 300 sampled live nodes, 24.7%
    were double-prefixed — roughly 10k of 40,236 — and the split runs WITHIN
    providers (claude 50 double / 35 single), because it tracks the code path,
    not the source. Web captures (gemini, chatgpt) were 100% clean; the CLI
    transcript path was not.

    Why it stayed invisible: everything downstream compares vectors to OTHER
    vectors from the same store, so a uniformly shifted subpopulation still
    clusters, still ranks, still returns plausible neighbours. It only shows up
    when you ask a question with a known answer — "do two byte-identical texts
    embed to the same vector?" — which nothing did until a near-duplicate census
    reported LESS near-duplicate mass (35.9% at cos>=0.995) than exact-duplicate
    mass (38.0%), an arithmetic impossibility that could only mean the vectors
    were lying.
    """
    stripped = text.lstrip()
    for prefix in _NOMIC_PREFIXES:
        if stripped.startswith(prefix):
            return text
    return _DOC_PREFIX + text


def _l2_truncate(vec: list[float], dim: int) -> list[float]:
    """Matryoshka truncate to `dim`, then L2-normalize."""
    v = vec[:dim]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


class MlxNativeEmbedder:
    """Embeds via Apple MLX. Construction PROBES for MLX without importing it.

    LAZY BY CONTRACT (2026-07-25) — do not re-add an import to `__init__`.
    This constructor used to `import mlx.core` + `mlx_embeddings` up front so an
    unavailable MLX surfaced as ImportError and the selector in
    `embeddings/__init__` fell through to the torch backend. That probe was
    right about SELECTION and catastrophic about COST: `embeddings/__init__`
    constructs this class at MODULE level, and `me/basins.py` imports that
    package for the pure helper `is_finite_embedding`, which schema migrations
    pull in on EVERY `trinity-local` startup. So every process — including MCP
    servers that never embed anything — paid the full backend import.

    Measured before the fix, on an isolated EMPTY home with zero tool calls:
    366 MB idle (498 MB with autoscan) against a documented ~62 MB, with
    mlx/Metal mapped. Eight concurrent MCP servers held 4,047 MB.

    `importlib.util.find_spec` answers the same selection question for a
    top-level package WITHOUT executing it (measured: +0.0 MB, and `mlx` never
    enters `sys.modules`), so construction stays a probe and the real import
    moves to `_ensure()` beside the model load. The torch backend
    (`backend_mlx.MlxEmbedder`) has always been lazy this way; this class was
    the asymmetry, not the pattern.

    Behaviour note: a package that is present-but-broken now degrades at first
    embed (embeddings.embed catches and falls back to TF-IDF) rather than
    falling through to the torch backend at construction. That trade is
    deliberate — a broken install is rare, and it does not justify a Metal
    context in every process.
    """

    def __init__(self) -> None:
        # Probe WITHOUT importing. find_spec does not execute a top-level
        # package, so no Metal context is created here. Raising ImportError on
        # a miss preserves the selector contract in embeddings/__init__.
        import importlib.util

        for _mod in ("mlx", "mlx_embeddings"):
            if importlib.util.find_spec(_mod) is None:
                raise ImportError(f"{_mod} is not installed")
        # Typed Any: these hold third-party callables/handles that only exist
        # after `_ensure()` runs the deferred import. Annotating them keeps the
        # deferred-init pattern from widening every downstream call site to
        # Optional (which reads as a bug in the embed path when it is not one).
        self._load: Any = None
        self._generate: Any = None
        self._model: Any = None
        self._tok: Any = None
        self._loaded = False
        # Interface parity with the torch backend (embeddings/__init__'s
        # model_status() reads .model_path + .is_ready()).
        self.model_path = _hf_cache_dir(MLX_MODEL_ID)

    def is_ready(self) -> bool:
        """True if the model is loaded or present in the local HF cache."""
        return self._loaded or self.model_path.exists()

    def _ensure(self) -> None:
        if self._loaded:
            return
        # THE REAL IMPORT LANDS HERE, on first embed — this is what creates the
        # Metal context and costs the memory the constructor no longer spends.
        if self._load is None:
            from mlx_embeddings import generate, load

            self._load, self._generate = load, generate
        # Loads from the local HF cache (one-time download). No network at
        # steady state — honours the HF_HUB_OFFLINE pin once cached.
        self._model, self._tok = self._load(MLX_MODEL_ID)
        self._loaded = True

    def embed(self, text: str, *, dim: int = DEFAULT_DIM) -> list[float]:
        return self.embed_batch([text], dim=dim)[0]

    def embed_batch(
        self, texts: list[str], *, dim: int = DEFAULT_DIM, batch_size: int = 128
    ) -> list[list[float]]:
        if not texts:
            return []
        self._ensure()
        import mlx.core as mx

        out: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = [_ensure_doc_prefix(t) for t in texts[start : start + batch_size]]
            res = self._generate(self._model, self._tok, texts=chunk)
            # bf16 → float32 in MLX before crossing to Python (avoids the
            # PEP-3118 buffer dtype error a direct numpy cast hits).
            vectors = mx.array(res.text_embeds).astype(mx.float32).tolist()
            out.extend(_l2_truncate(v, dim) for v in vectors)
        return out
