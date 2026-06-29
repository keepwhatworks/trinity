#!/usr/bin/env python3
"""scripts/embed.py — nomic-embed-text-v1.5 batch embedding.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).

Dual interface:
  - Shebang: `python3 scripts/embed.py < input.json`
    where input.json = `{"texts": ["hello", "world"], "dim": 768}`
    output = `{"vectors": [[...768 floats...], ...], "backend": "mlx",
                "cached_count": 0, "embedded_count": 2}`
  - Importable: `from scripts.embed import embed_batch`

The actual nomic-embed-text-v1.5 implementation lives in
`trinity_local.embeddings` (MLX backend + TF-IDF fallback + cache).
This script is the shebang-friendly entry point; v1.1 inverts the
dependency so the algorithm code moves here.

Default dim=768, batch_size=64. Cache-aware: vectors that exist in
`~/.trinity/cache/embeddings.jsonl` are returned without
re-embedding (10-50x speedup on repeated runs).

Tier-equivalence invariant (council verdict):
NOT bit-identical. Cosine similarity ≥ 0.9999 between this output
and a torch-CPU reproduction under pinned config (model hash
nomic-ai/nomic-embed-text-v1.5, tokenizer pinned, numpy ≥ 1.26).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

# Ensure repo root is importable when running as a script — the
# script lives at <repo>/scripts/embed.py and needs to import
# scripts._runtime + (optionally) trinity_local.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from scripts._runtime import (
    audit_log,
    bootstrap_or_continue,
    read_input_json,
    write_output_json,
)


SCRIPT_NAME = "embed"

# nomic-embed lives in sentence-transformers; MLX backend uses
# nomic-ai/nomic-embed-text-v1.5 directly. Pinned majors.
REQUIREMENTS = [
    "sentence-transformers>=2.7",
    "torch>=2.1",
    "numpy>=1.26",
]


def embed_batch(
    texts: list[str],
    *,
    dim: int = 768,
    batch_size: int = 64,
) -> tuple[list[list[float]], dict[str, Any]]:
    """Batch-embed texts. Returns (vectors, meta).

    meta carries: `backend` (mlx | tfidf), `cached_count` (legacy:
    always 0 since the persistent cache was retired 2026-05-17),
    `embedded_count`. The pip tier function only returns vectors;
    this wrapper adds the diagnostic meta so the shebang user can
    see which backend fired.
    """
    from trinity_local.embeddings import embed_batch as _embed

    cached_count = 0
    embedded_count = len(texts)

    vectors = _embed(texts, dim=dim, batch_size=batch_size)

    # The pip tier's embed_batch chooses backend internally. We can't
    # observe directly without re-architecting; report "mlx" when the
    # backend imported successfully, else "tfidf".
    backend = "tfidf"
    try:
        from trinity_local.embeddings import _mlx_backend
        if _mlx_backend is not None:
            backend = "mlx"
    except Exception:
        pass

    meta = {
        "backend": backend,
        "dim": dim,
        "cached_count": cached_count,
        "embedded_count": embedded_count,
        "total_count": len(texts),
    }
    return vectors, meta


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/embed.py",
        description=(
            "Batch-embed texts with nomic-embed-text-v1.5. "
            "Input JSON: {texts: [...], dim?: 768, batch_size?: 64}. "
            "Output JSON: {vectors: [[...], ...], backend, dim, "
            "cached_count, embedded_count, total_count}."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dependencies (auto-installed on first run):\n"
            f"  {chr(10).join('  ' + r for r in REQUIREMENTS)}\n\n"
            "Tier-equivalence invariant: cosine ≥ 0.9999 with torch-CPU "
            "reproduction under pinned config (model hash nomic-ai/nomic-embed-"
            "text-v1.5). NOT bit-identical."
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Input JSON file (or '-' for stdin; default: stdin)",
    )
    parser.add_argument(
        "--out", "-o",
        default="-",
        help="Output JSON file (or '-' for stdout; default: stdout)",
    )
    args = parser.parse_args(argv)

    started_at = time.monotonic()
    payload = read_input_json(args.input if args.input != "-" else None)
    if not isinstance(payload, dict) or "texts" not in payload:
        print("error: input must be a JSON object with a 'texts' field",
              file=sys.stderr)
        audit_log(
            script=SCRIPT_NAME, operation="embed_batch",
            outcome="bad_input", detail="missing 'texts' field",
        )
        return 2

    texts = payload["texts"]
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        print("error: 'texts' must be a list of strings", file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="embed_batch",
                  outcome="bad_input", detail="'texts' not list[str]")
        return 2

    dim = int(payload.get("dim", 768))
    batch_size = int(payload.get("batch_size", 64))

    try:
        vectors, meta = embed_batch(texts, dim=dim, batch_size=batch_size)
    except Exception as exc:
        audit_log(
            script=SCRIPT_NAME, operation="embed_batch",
            outcome="error",
            args={"n_texts": len(texts), "dim": dim, "batch_size": batch_size},
            detail=f"{type(exc).__name__}: {exc}",
        )
        print(f"error: embedding failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    audit_log(
        script=SCRIPT_NAME, operation="embed_batch",
        args={
            "n_texts": len(texts),
            "dim": dim,
            "batch_size": batch_size,
            "backend": meta["backend"],
            "cached_count": meta["cached_count"],
            "embedded_count": meta["embedded_count"],
            "elapsed_ms": elapsed_ms,
        },
    )

    result = {"vectors": vectors, **meta, "elapsed_ms": elapsed_ms}
    write_output_json(result, args.out if args.out != "-" else None)
    return 0


if __name__ == "__main__":
    bootstrap_or_continue(script_name=SCRIPT_NAME, requirements=REQUIREMENTS)
    sys.exit(_cli_main())
