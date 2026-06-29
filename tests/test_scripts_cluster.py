"""Tests for scripts/cluster.py — k-means clustering.

Phase 2 (council_ff3da1fa84906791). Coverage:
  - kmeans() function — k-means++ init, iteration to convergence,
    deterministic given (vectors, k, seed)
  - degenerate case (n ≤ k) returns one-row-per-cluster
  - CLI round-trip via stdin/stdout
  - CLI rejects malformed input cleanly
  - audit-log entry per CLI invocation
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLUSTER_PY = REPO_ROOT / "scripts" / "cluster.py"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_kmeans_separates_three_obvious_clusters():
    """6 vectors in 3 corners of 3-d space → k=3 returns 3 clusters with
    2 members each."""
    from scripts.cluster import kmeans

    vectors = [
        [1.0, 0.0, 0.0], [0.9, 0.1, 0.0],
        [0.0, 1.0, 0.0], [0.1, 0.9, 0.0],
        [0.0, 0.0, 1.0], [0.0, 0.1, 0.9],
    ]
    result = kmeans(vectors, k=3, seed=42)
    assert result["n"] == 6
    assert result["k"] == 3
    assert result["dim"] == 3
    assert result["converged"]
    # Each pair should land in the same cluster.
    labels = result["labels"]
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[4] == labels[5]
    # And the three pairs should be in different clusters.
    assert len({labels[0], labels[2], labels[4]}) == 3


def test_kmeans_deterministic_given_same_seed():
    """Same input + same seed → bit-identical output. This is the
    floor for tier-equivalence (cosine ≥ 0.9999 across backends; same
    backend should be exact)."""
    from scripts.cluster import kmeans

    vectors = [[float(i), float(j), float(k)]
               for i in range(3) for j in range(3) for k in range(3)]
    r1 = kmeans(vectors, k=4, seed=7)
    r2 = kmeans(vectors, k=4, seed=7)
    assert r1["labels"] == r2["labels"]
    assert r1["centroids"] == r2["centroids"]


def test_kmeans_degenerate_n_le_k_one_per_cluster():
    """When n ≤ k, each vector is its own cluster (no useful k-means)."""
    from scripts.cluster import kmeans

    vectors = [[1.0, 0.0], [0.0, 1.0]]
    result = kmeans(vectors, k=5, seed=42)
    assert result["k"] == 2  # collapsed to n
    assert result["n"] == 2
    assert result["labels"] == [0, 1]
    assert result["centroids"] == vectors


def test_kmeans_empty_input_returns_empty_result():
    from scripts.cluster import kmeans
    result = kmeans([], k=3, seed=42)
    assert result == {"labels": [], "centroids": [], "k": 0, "n": 0,
                      "dim": 0, "iterations": 0, "converged": True}


def test_cli_round_trip_via_stdin_stdout(isolated_home):
    """Real subprocess: stdin JSON → stdout JSON, exit 0, audit appended."""
    payload = json.dumps({
        "vectors": [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]],
        "k": 2, "seed": 42,
    })
    result = subprocess.run(
        [sys.executable, str(CLUSTER_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    out = json.loads(result.stdout)
    assert out["n"] == 4 and out["k"] == 2
    assert out["converged"]
    assert "elapsed_ms" in out
    # Audit log written
    audit = (isolated_home / "audit.log").read_text().splitlines()
    last = json.loads(audit[-1])
    assert last["script"] == "cluster"
    assert last["operation"] == "kmeans"


def test_cli_bad_input_exits_2_and_audits(isolated_home):
    """Missing 'vectors' field → exit 2 with stderr message and audit
    entry tagged bad_input."""
    payload = json.dumps({"k": 3})
    result = subprocess.run(
        [sys.executable, str(CLUSTER_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 2
    assert "'vectors' field" in result.stderr
    audit = json.loads((isolated_home / "audit.log").read_text().splitlines()[-1])
    assert audit["outcome"] == "bad_input"
