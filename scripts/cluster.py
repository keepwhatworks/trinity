#!/usr/bin/env python3
"""scripts/cluster.py — k-means clustering on embedding matrices.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).

The same k-means + k-means++ init the basins pipeline uses to discover
topic clusters from thread-mean embeddings. Pure numpy; no torch
needed. Tier-equivalence: deterministic given (vectors, k, seed).

Dual interface:
  - Shebang: `python3 scripts/cluster.py < input.json`
    where input.json = {"vectors": [[...], ...], "k": 12, "seed": 42}
    output = {"labels": [...], "centroids": [[...], ...], "k": K,
              "n": N, "iterations": I, "converged": bool}
  - Importable: `from scripts.cluster import kmeans`

When deps are pinned (numpy >= 1.26) and seed is fixed, this script's
output is reproducible byte-for-byte across runs.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

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


SCRIPT_NAME = "cluster"
REQUIREMENTS = ["numpy>=1.26"]

DEFAULT_K = 12
DEFAULT_SEED = 42
DEFAULT_MAX_ITER = 50


def kmeans(
    vectors: list[list[float]],
    *,
    k: int = DEFAULT_K,
    seed: int = DEFAULT_SEED,
    max_iter: int = DEFAULT_MAX_ITER,
) -> dict:
    """k-means with k-means++ init. Deterministic given (vectors, k, seed).

    Returns dict: labels (list[int], len=N), centroids (list[list[float]],
    shape k×D), k (int), n (int), dim (int), iterations (int),
    converged (bool).

    Degenerate case (n ≤ k): each row is its own cluster (no clustering
    needed). The pip tier's compute_basins() handles this; we mirror.
    """
    import numpy as np

    if not vectors:
        return {"labels": [], "centroids": [], "k": 0, "n": 0, "dim": 0,
                "iterations": 0, "converged": True}

    matrix = np.asarray(vectors, dtype=np.float64)
    n, dim = matrix.shape

    if n <= k:
        # Degenerate: each row is its own cluster.
        labels = list(range(n))
        return {
            "labels": labels,
            "centroids": matrix.tolist(),
            "k": n,
            "n": n,
            "dim": dim,
            "iterations": 0,
            "converged": True,
        }

    # k-means++ init (mirrors src/trinity_local/me/basins._kmeans_pp_init).
    rng = np.random.default_rng(seed)
    centroids_idx = [int(rng.integers(0, n))]
    for _ in range(k - 1):
        chosen = matrix[centroids_idx]
        diffs = matrix[:, None, :] - chosen[None, :, :]
        sq = np.sum(diffs * diffs, axis=2)
        min_sq = np.min(sq, axis=1)
        # Probability proportional to squared distance.
        if min_sq.sum() <= 0:
            centroids_idx.append(int(rng.integers(0, n)))
            continue
        probs = min_sq / min_sq.sum()
        next_idx = int(rng.choice(n, p=probs))
        centroids_idx.append(next_idx)
    centroids = matrix[centroids_idx].copy()

    labels = np.zeros(n, dtype=int)
    iterations = 0
    converged = False
    for it in range(max_iter):
        iterations = it + 1
        diffs = matrix[:, None, :] - centroids[None, :, :]
        sq_dists = np.sum(diffs * diffs, axis=2)
        new_labels = np.argmin(sq_dists, axis=1)
        if (new_labels == labels).all() and it > 0:
            converged = True
            break
        labels = new_labels
        for c in range(k):
            members = matrix[labels == c]
            if len(members) > 0:
                centroids[c] = members.mean(axis=0)

    return {
        "labels": labels.tolist(),
        "centroids": centroids.tolist(),
        "k": k,
        "n": n,
        "dim": dim,
        "iterations": iterations,
        "converged": converged,
    }


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/cluster.py",
        description=(
            "k-means clustering on embedding matrices. "
            "Input JSON: {vectors: [[...], ...], k?: 12, seed?: 42, "
            "max_iter?: 50}. Output JSON: {labels, centroids, k, n, "
            "dim, iterations, converged}."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dependencies (auto-installed on first run):\n"
            f"  {chr(10).join('  ' + r for r in REQUIREMENTS)}\n\n"
            "Tier-equivalence: deterministic given (vectors, k, seed) under "
            "pinned numpy. Same output across runs + same output between "
            "this and the pip tier's compute_basins() inner loop."
        ),
    )
    parser.add_argument("input", nargs="?", default="-")
    parser.add_argument("--out", "-o", default="-")
    args = parser.parse_args(argv)

    started_at = time.monotonic()
    payload = read_input_json(args.input if args.input != "-" else None)
    if not isinstance(payload, dict) or "vectors" not in payload:
        print("error: input must be a JSON object with a 'vectors' field",
              file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="kmeans",
                  outcome="bad_input", detail="missing 'vectors' field")
        return 2

    vectors = payload["vectors"]
    if not isinstance(vectors, list) or not all(
        isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)
        for v in vectors
    ):
        print("error: 'vectors' must be a list of list[float]", file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="kmeans",
                  outcome="bad_input", detail="'vectors' not list[list[float]]")
        return 2

    k = int(payload.get("k", DEFAULT_K))
    seed = int(payload.get("seed", DEFAULT_SEED))
    max_iter = int(payload.get("max_iter", DEFAULT_MAX_ITER))

    try:
        result = kmeans(vectors, k=k, seed=seed, max_iter=max_iter)
    except Exception as exc:
        audit_log(
            script=SCRIPT_NAME, operation="kmeans", outcome="error",
            args={"n": len(vectors), "k": k, "seed": seed},
            detail=f"{type(exc).__name__}: {exc}",
        )
        print(f"error: clustering failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    audit_log(
        script=SCRIPT_NAME, operation="kmeans",
        args={
            "n": result["n"], "k": result["k"], "dim": result["dim"],
            "seed": seed, "iterations": result["iterations"],
            "converged": result["converged"], "elapsed_ms": elapsed_ms,
        },
    )
    result["elapsed_ms"] = elapsed_ms
    write_output_json(result, args.out if args.out != "-" else None)
    return 0


if __name__ == "__main__":
    bootstrap_or_continue(script_name=SCRIPT_NAME, requirements=REQUIREMENTS)
    sys.exit(_cli_main())
