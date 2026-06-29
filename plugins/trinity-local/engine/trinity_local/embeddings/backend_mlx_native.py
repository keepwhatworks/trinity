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


def _l2_truncate(vec: list[float], dim: int) -> list[float]:
    """Matryoshka truncate to `dim`, then L2-normalize."""
    v = vec[:dim]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


class MlxNativeEmbedder:
    """Embeds via Apple MLX. Import-time probe raises if MLX isn't present."""

    def __init__(self) -> None:
        # Probe up front so an unavailable MLX surfaces as ImportError at
        # construction — the package selector treats that as "fall through".
        import mlx.core  # noqa: F401
        from mlx_embeddings import generate, load

        self._load = load
        self._generate = generate
        self._model = None
        self._tok = None
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
            chunk = [_DOC_PREFIX + t for t in texts[start : start + batch_size]]
            res = self._generate(self._model, self._tok, texts=chunk)
            # bf16 → float32 in MLX before crossing to Python (avoids the
            # PEP-3118 buffer dtype error a direct numpy cast hits).
            vectors = mx.array(res.text_embeds).astype(mx.float32).tolist()
            out.extend(_l2_truncate(v, dim) for v in vectors)
        return out
