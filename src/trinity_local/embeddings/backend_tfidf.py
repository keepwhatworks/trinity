"""TF-IDF fallback backend for embeddings.

Always available (zero dependencies). Produces a sparse-ish vector
from word frequencies. Used when MLX is not installed or model not
downloaded. Not competitive with real embeddings but keeps the API
functional on any machine.
"""
from __future__ import annotations

import math
import re
from collections import Counter


# Global document frequency table (built from observed texts)
_doc_freq: Counter[str] = Counter()
_n_docs: int = 0


def _tokenize(text: str) -> list[str]:
    """Simple word tokenizer."""
    return re.findall(r'[a-z0-9_]+', text.lower())


def _stable_dim(token: str, dim: int) -> int:
    """Stable, cross-process hash → dimension index.

    Python's built-in hash() is randomized via PYTHONHASHSEED, which
    means subprocess and in-process calls would map the SAME token to
    DIFFERENT dimensions — silent tier-equivalence drift (council
    `council_37eca30b6e7010df` flagged this as the highest-priority
    pre-empt before launch).

    SHA-1 is stable, fast, and overkill-safe here. We only need the
    low bits modulo `dim`; collision resistance is irrelevant for a
    hash-projection embedding.
    """
    import hashlib
    digest = hashlib.sha1(token.encode("utf-8"), usedforsecurity=False).digest()
    # Low 8 bytes → integer → modulo dim. Stable across processes.
    return int.from_bytes(digest[:8], "big") % dim


def embed_tfidf(text: str, *, dim: int = 256) -> list[float]:
    """Produce a fixed-dim hash-projected TF-IDF vector.

    Since we don't have a corpus at single-text embed time, this uses
    a hash-projection approach: each token maps to a dimension via
    a stable SHA-1 hash projection (NOT Python's hash() — that's
    PYTHONHASHSEED-randomized and would silently break tier-equivalence
    across subprocess boundaries; council `37eca30b6e7010df` pre-empt).
    TF is used as the weight.
    """
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * dim

    tf = Counter(tokens)
    vector = [0.0] * dim

    for token, count in tf.items():
        idx = _stable_dim(token, dim)
        weight = 1.0 + math.log(count) if count > 0 else 0.0
        vector[idx] += weight

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]

    return vector


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
