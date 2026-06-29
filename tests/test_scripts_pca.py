"""Tests for scripts/pca.py — geometric primitives.

Phase 2 (council_ff3da1fa84906791). Wraps cortex_geometry primitives;
tests verify dual-interface contract + tier-equivalence with the pip
tier (same in-process call → same output).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PCA_PY = REPO_ROOT / "scripts" / "pca.py"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_basin_geometry_returns_composite():
    """5 vectors in 3-d → composite has center (len 3), manifold_dim
    in [1, 3], bimodality_z float, mean_cosine in [-1, 1], n=5, dim=3."""
    from scripts.pca import basin_geometry

    points = [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0],
              [0.0, 1.0, 0.0], [0.1, 0.9, 0.0],
              [0.0, 0.0, 1.0]]
    result = basin_geometry(points)
    assert len(result["center"]) == 3
    assert 1.0 <= result["manifold_dim"] <= 3.0
    assert isinstance(result["bimodality_z"], float)
    assert -1.0 <= result["mean_cosine_to_center"] <= 1.0
    assert result["n"] == 5
    assert result["dim"] == 3


def test_basin_geometry_empty_input_returns_zeros():
    """Edge case: empty input returns all-zero composite without crashing."""
    from scripts.pca import basin_geometry
    result = basin_geometry([])
    assert result["n"] == 0
    assert result["center"] == []


def test_weiszfeld_median_collinear_points():
    """3 collinear points → median is the middle point. Tier-
    equivalence floor: our wrapper returns the same as the pip tier."""
    from scripts.pca import weiszfeld_median
    from trinity_local.cortex_geometry import weiszfeld_median as pip_weiszfeld

    points = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
    a = weiszfeld_median(points)
    b = pip_weiszfeld(points)
    assert a == b
    # Median of collinear points is the middle one.
    assert math.isclose(a[0], 1.0, abs_tol=1e-3)


def test_participation_ratio_in_valid_range():
    """For n points in d-dim, the participation ratio is in [1, d].
    A tight cluster → near 1; spread points → near d."""
    from scripts.pca import participation_ratio

    # Tight cluster around origin: PR ~ 1
    tight = [[0.01, 0.0, 0.0], [-0.01, 0.0, 0.0], [0.0, 0.01, 0.0]]
    pr_tight = participation_ratio(tight)
    assert 1.0 <= pr_tight <= 3.0

    # Spread across axes: higher PR
    spread = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    pr_spread = participation_ratio(spread)
    assert 1.0 <= pr_spread <= 3.0


def test_cli_geometry_operation_round_trip(isolated_home):
    payload = json.dumps({"vectors": [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]})
    result = subprocess.run(
        [sys.executable, str(PCA_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    out = json.loads(result.stdout)
    assert out["n"] == 3 and out["dim"] == 2
    assert len(out["center"]) == 2


def test_cli_unknown_operation_exits_2(isolated_home):
    payload = json.dumps({"vectors": [[1.0]], "operation": "nonsense"})
    result = subprocess.run(
        [sys.executable, str(PCA_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 2
    assert "unknown operation" in result.stderr
