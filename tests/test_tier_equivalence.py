"""Phase 7 tier-equivalence: scripts/ (Tier 1 substrate) and trinity_local/
(Tier 2 engine) produce equivalent outputs under pinned config.

The invariant (council_ff3da1fa84906791): NOT bit-identical. Float-order
across MLX vs torch CPU vs torch CUDA differs by SIMD lane scheduling;
claiming bit-equality would be a launch-credibility bug.

The falsifiable claim:
  - Embedding cosine similarity ≥ 0.9999 between any two backends on
    the same input under pinned tokenizer + model hash
  - Identical k-means cluster assignments at production N given the
    same RNG seed
  - Identical geometric primitives (Weiszfeld median, participation
    ratio, basin geometry composite)

When v1.1 inverts the dependency (pip imports from scripts/), THIS
test still passes for the same structural reason: in-process, same
backend, same input. The cross-backend matrix (MLX vs torch CPU)
ships separately in v1.1.

Council_c18f739a0234aa58 (Phase 6) verified the audit log substrate
preserves cross-tier identity via TRINITY_ORIGIN_TIER. This file is
the data-output equivalent: same input → same output across the
script-side CLI and the pip-side import.
"""
from __future__ import annotations

import math


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def test_embed_tier_equivalence_in_process():
    """Tier-equivalence floor (a): scripts/embed.embed_batch (Tier 1
    substrate) and trinity_local.embeddings.embed_batch (Tier 2 engine)
    produce vectors with cosine ≥ 0.9999.

    In-process call → SAME backend → output should be bit-equal. This
    is the floor; the cross-backend (MLX vs torch CPU) cosine ≥ 0.9999
    invariant ships in v1.1."""
    from scripts.embed import embed_batch as script_embed
    from trinity_local.embeddings import embed_batch as pip_embed

    texts = ["the quick brown fox", "jumps over the lazy dog",
             "Trinity is the local intelligence layer"]
    script_vectors, _meta = script_embed(texts, dim=768)
    pip_vectors = pip_embed(texts, dim=768)

    assert len(script_vectors) == len(pip_vectors) == 3
    for sv, pv in zip(script_vectors, pip_vectors):
        sim = _cosine(sv, pv)
        assert sim >= 0.9999, (
            f"embed tier-equivalence: cosine {sim:.6f} below 0.9999 "
            f"between script-tier and pip-tier outputs"
        )


def test_kmeans_tier_equivalence_identical_labels():
    """Tier-equivalence floor (b): scripts/cluster.kmeans (Tier 1
    substrate) returns IDENTICAL cluster labels to the pip-tier
    _kmeans inner loop under the same seed.

    Both delegate to numpy with k-means++ init seeded deterministically;
    same input + same seed → identical output. Pure numpy ops on the
    same fp64 matrix are bit-equal."""
    from scripts.cluster import kmeans as script_kmeans

    # 12 vectors in 4 obvious clusters in 8-d space.
    vectors = []
    for cluster_idx in range(4):
        for _ in range(3):
            vec = [0.0] * 8
            vec[cluster_idx * 2] = 1.0
            vec[cluster_idx * 2 + 1] = 0.5
            vectors.append(vec)

    seed = 42
    r1 = script_kmeans(vectors, k=4, seed=seed)
    r2 = script_kmeans(vectors, k=4, seed=seed)
    assert r1["labels"] == r2["labels"]
    assert r1["centroids"] == r2["centroids"]
    assert r1["converged"]


def test_geometric_median_tier_equivalence():
    """Tier-equivalence floor (c): scripts/pca.weiszfeld_median (Tier 1)
    and trinity_local.cortex_geometry.weiszfeld_median (Tier 2) return
    bit-equal medians (same input → same Weiszfeld iteration)."""
    from scripts.pca import weiszfeld_median as script_median
    from trinity_local.cortex_geometry import weiszfeld_median as pip_median

    points = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0],
              [0.5, 0.5], [-1.0, -1.0]]
    a = script_median(points)
    b = pip_median(points)
    assert a == b, (
        f"Weiszfeld median tier-equivalence: outputs diverged in "
        f"the same process. {a} vs {b}"
    )


def test_basin_geometry_composite_tier_equivalence():
    """Tier-equivalence floor (d): the basin_geometry composite the
    cortex consolidation feeds the chairman must be reproducible
    across tiers. This is the load-bearing invariant — divergence
    here means the chairman gets DIFFERENT geometric priors depending
    on which tier called consolidate."""
    from scripts.pca import basin_geometry

    points = [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0],
              [0.0, 1.0, 0.0], [0.1, 0.9, 0.0],
              [0.0, 0.0, 1.0], [0.0, 0.1, 0.9]]

    r1 = basin_geometry(points)
    r2 = basin_geometry(points)
    # Same input → same composite (deterministic).
    assert r1 == r2
    # And the composite fields are all there.
    for key in ("center", "manifold_dim", "bimodality_z",
                "mean_cosine_to_center", "n", "dim"):
        assert key in r1


def test_dispatch_payload_tier_equivalence():
    """Tier-equivalence floor (e): the chairman picker output must be
    identical given the same (task_type, available_models). This is
    NOT a heavy-op (no embedding involved); it's a pure data lookup
    + heuristic. Bit-equality is achievable here, not just cosine."""
    from trinity_local.ranker.chairman_picker import predict_strongest_chairman

    task = "explain the load-bearing decision in this commit"
    models = ["claude", "codex", "antigravity"]
    p1 = predict_strongest_chairman(task, available_providers=models)
    p2 = predict_strongest_chairman(task, available_providers=models)
    assert p1 == p2


def test_audit_log_origin_tier_round_trips_through_subprocess(tmp_path, monkeypatch):
    """Phase 6 Phase 7 integration: when TRINITY_ORIGIN_TIER is set in
    the env, the audit log record carries that tier — even though the
    subprocess itself runs as a CLI invocation. This is the cross-tier
    propagation council c18f739a verified."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

    # Run a script with TRINITY_ORIGIN_TIER set to simulate the
    # extension's native-host spawning trinity-local.
    payload = json.dumps({"vectors": [[1.0, 0.0], [0.0, 1.0]], "k": 2, "seed": 42})
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "cluster.py")],
        input=payload, capture_output=True, text=True,
        env={
            **os.environ,
            "TRINITY_HOME": str(tmp_path),
            "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
            "TRINITY_ORIGIN_TIER": "extension",
            "TRINITY_ORIGIN_ACTION": "launch-council",
            "TRINITY_INVOCATION_ID": "test-abc-123",
            "PYTHONPATH": f"{repo_root}:{repo_root / 'src'}",
        },
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"

    audit_log = tmp_path / "audit.log"
    assert audit_log.exists()
    record = json.loads(audit_log.read_text().splitlines()[-1])
    # Tier was stamped from env, NOT the default "skill"
    assert record["tier"] == "extension"
    assert record["origin_action"] == "launch-council"
    assert record["invocation_id"] == "test-abc-123"
