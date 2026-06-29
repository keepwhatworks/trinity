"""MLX backend for nomic-embed-text-v1.5.

Uses sentence-transformers with PyTorch for inference.
Apple Silicon acceleration via MPS when available.

Model: nomic-ai/nomic-embed-text-v1.5 (137M params, 8192 token context)
Supports Matryoshka dimensions: 768, 512, 256, 128, 64
"""
from __future__ import annotations

import math
from pathlib import Path


# #244: modernbert-embed-base (standard ModernBERT arch) — 8192 ctx, Matryoshka,
# nomic-trained, Apache-2.0. Replaces nomic-embed-text-v1.5, whose custom
# nomic_bert arch was unsupported by MLX and wedged torch-MPS. ModernBERT is a
# standard arch, so this torch path runs clean on CUDA/CPU (and MPS) — and the
# Apple-MLX path (backend_mlx_native.py) uses the SAME model, so a machine's
# vectors are model-consistent regardless of runtime. Override via TRINITY_EMBED_MODEL.
import os as _os
MODEL_ID = _os.environ.get("TRINITY_EMBED_MODEL", "nomic-ai/modernbert-embed-base")
MAX_TOKENS = 8192
DEFAULT_DIM = 768


def hf_cache_model_path() -> Path:
    """Where sentence-transformers actually caches the nomic model.

    HF cache dir name is derived from MODEL_ID with `/` → `--`. Returned
    by ``model_status()`` for diagnostics so a user inspecting the dict
    sees the real on-disk location (the previous ``~/.trinity/models/``
    path was misleading — that dir was created but unused).
    """
    safe = MODEL_ID.replace("/", "--")
    return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{safe}"

# Nomic task prefixes — if the caller already prepended one, don't double-prefix.
NOMIC_PREFIXES = ("search_document:", "search_query:", "clustering:", "classification:")


def _ensure_nomic_prefix(text: str) -> str:
    stripped = text.lstrip()
    for prefix in NOMIC_PREFIXES:
        if stripped.startswith(prefix):
            return text
    return f"search_document: {text}"

def download_model(*, force: bool = False) -> str:
    """Download/verify the model. Returns status message."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return "sentence-transformers not installed. Run `pip install -e '.[mlx]'` from your Trinity repo"

    try:
        # SentenceTransformer handles caching/downloading automatically
        _ = SentenceTransformer(MODEL_ID, trust_remote_code=True)
        return f"Model ready: {MODEL_ID}"
    except Exception as exc:
        return f"Download failed: {exc}"


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        return [v / norm for v in vector]
    return vector


class MlxEmbedder:
    """Embedder using sentence-transformers + nomic-embed-text-v1.5.

    Despite the module name, this uses PyTorch (with MPS on Apple Silicon)
    rather than raw MLX, because the nomic architecture requires trust_remote_code
    and einops for correct inference.
    """

    def __init__(self):
        self._model = None
        self._loaded = False
        self.model_path = hf_cache_model_path()

    def is_ready(self) -> bool:
        """Check if sentence-transformers is importable.

        Note: This is a simple check. The actual embed() call may still fail at runtime
        (network issues, missing model, permissions, etc.). The embeddings module handles
        these failures gracefully by falling back to TF-IDF.

        This function is kept for observability (model_status()) but is NOT used for
        critical decisions.
        """
        try:
            __import__("sentence_transformers")
            return True
        except ImportError:
            return False

    def _load(self) -> None:
        if self._loaded:
            return

        import os
        from sentence_transformers import SentenceTransformer

        try:
            # Pin CPU by default. This backend is sentence-transformers + torch
            # (NOT Apple MLX, despite the filename). nomic-embed-v1.5's
            # trust_remote_code custom ops are MPS-incompatible → per-op CPU
            # fallback thrash, and the Metal path can WEDGE on a GPU
            # command-buffer recovery: a real backfill dragged from ~56 nodes/s
            # to ~12 nodes/MIN after an "innocent victim" Metal error and never
            # recovered. Measured fresh on an M1 Ultra: CPU = 56 nodes/s + 3s
            # load; MPS = 97 nodes/s but 77s load + wedge-fragile. CPU wins for
            # both batch backfill (no wedge, ~10min for 33k) and interactive
            # single-embed (3s vs 77s load). Override with TRINITY_EMBED_DEVICE.
            # Device selection. nomic-embed stays the model — its Matryoshka
            # dims (we truncate via vector[:dim]) + 8k context are why it's
            # chosen; the only problem was the Apple-MPS RUNTIME. Prefer CUDA
            # (Linux/Windows NVIDIA — full PyTorch op coverage, fast + clean;
            # ROCm likewise reports as cuda), else CPU. NEVER auto-select MPS:
            # nomic's trust_remote_code custom ops fall back per-op on Metal
            # and the command queue can WEDGE (a backfill dragged to ~12
            # nodes/min). Apple users who want GPU set TRINITY_EMBED_DEVICE=mps
            # explicitly (at the wedge risk) or use the MLX accelerator (#241).
            # torch stays — it IS the cross-platform GPU path; MLX is Apple-only.
            device = os.environ.get("TRINITY_EMBED_DEVICE")
            if not device:
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:
                    device = "cpu"
            self._model = SentenceTransformer(MODEL_ID, trust_remote_code=True, device=device)
        except Exception as exc:
            # HF_HUB_OFFLINE=1 + missing cache surfaces as an obscure
            # OfflineModeIsEnabled / FileNotFoundError. Translate to an
            # actionable one-liner so the user knows the exact fix.
            offline = os.environ.get("HF_HUB_OFFLINE", "0") in {"1", "true", "True"}
            if offline:
                raise RuntimeError(
                    f"Trinity is in offline mode (HF_HUB_OFFLINE=1) but the model "
                    f"{MODEL_ID!r} isn't in the local HF cache. Run once with "
                    f"online access to pull it:\n"
                    f"  HF_HUB_OFFLINE=0 huggingface-cli download {MODEL_ID}\n"
                    f"After that, Trinity will load from cache without touching the Hub."
                ) from exc
            raise
        self._loaded = True

    def embed(self, text: str, *, dim: int = DEFAULT_DIM) -> list[float]:
        """Embed a single text. Uses Matryoshka truncation to `dim`.

        If the caller already prepended a Nomic task prefix (search_query:,
        search_document:, clustering:, classification:), it's preserved.
        Otherwise we default to search_document: for legacy callers.
        """
        self._load()
        prefixed = _ensure_nomic_prefix(text)
        vector = self._model.encode(prefixed).tolist()
        truncated = vector[:dim]
        return _l2_normalize(truncated)

    def embed_batch(self, texts: list[str], *, dim: int = DEFAULT_DIM, batch_size: int = 64) -> list[list[float]]:
        """Embed multiple texts in batches. ~10-50× faster than serial embed().

        Chunks inputs into groups of `batch_size` BEFORE calling model.encode
        — sentence-transformers tokenizes the entire input list upfront, so
        passing 1000 long texts in one call would balloon memory even with
        batch_size=64 honored for inference. We instead loop, calling encode
        with at most `batch_size` texts per call. This bounds peak memory at
        roughly batch_size * max_seq_len * hidden_dim regardless of total N.
        """
        self._load()
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            prefixed = [_ensure_nomic_prefix(t) for t in chunk]
            vectors = self._model.encode(prefixed, batch_size=batch_size)
            out.extend(_l2_normalize(v[:dim].tolist()) for v in vectors)
        return out
