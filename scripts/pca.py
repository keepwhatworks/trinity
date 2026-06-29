#!/usr/bin/env python3
"""scripts/pca.py — geometric median + PCA on embedding clusters.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).

Wraps the geometric primitives from trinity_local.cortex_geometry:
  - Weiszfeld geometric median (robust centroid alternative)
  - Participation ratio (effective manifold dimensionality)
  - Projection onto first principal component (kurtosis check)
  - Excess kurtosis (bimodality flag)

These power the cortex consolidation pipeline's geometric prior:
manifold-dim + bimodality fed to the chairman so rule-extraction
operates on cluster structure rather than language alone.

Dual interface:
  - Shebang: `python3 scripts/pca.py < input.json`
  - Importable: `from scripts.pca import basin_geometry`

CLI Input:
  {"vectors": [[...], ...], "operation": "geometry" | "median" | "pca"}

CLI Output (operation=geometry, the composite):
  {"center": [...], "manifold_dim": float, "bimodality_z": float,
   "mean_cosine_to_center": float, "n": int, "dim": int}
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


SCRIPT_NAME = "pca"
REQUIREMENTS = ["numpy>=1.26"]


def weiszfeld_median(
    points: list[list[float]],
    *,
    max_iter: int = 50,
    eps: float = 1e-6,
) -> list[float]:
    """Geometric median via Weiszfeld iteration. Robust to outliers.

    Defaults match trinity_local.cortex_geometry.weiszfeld_median
    EXACTLY so tier-equivalence holds when both are called with no
    explicit params. v1.1 inverts the dependency; defaults stay.
    """
    from trinity_local.cortex_geometry import weiszfeld_median as _impl
    return _impl(points, max_iter=max_iter, eps=eps)


def participation_ratio(
    points: list[list[float]],
    center: list[float] | None = None,
) -> float:
    """Effective manifold dimensionality via the participation ratio of
    the covariance eigenvalues. Returns a float in [1, dim]."""
    from trinity_local.cortex_geometry import (
        participation_ratio as _impl,
        weiszfeld_median,
    )
    if center is None:
        center = weiszfeld_median(points)
    return _impl(points, center)


def bimodality_z(
    points: list[list[float]],
    center: list[float] | None = None,
) -> float:
    """First-PC kurtosis as bimodality indicator. Negative excess
    kurtosis → bimodal. Returned as z-score; |z| > 1 is a flag."""
    from trinity_local.cortex_geometry import (
        project_onto_first_pc,
        excess_kurtosis,
        weiszfeld_median,
    )
    if center is None:
        center = weiszfeld_median(points)
    projections = project_onto_first_pc(points, center)
    k = excess_kurtosis(projections)
    # z = excess_kurtosis / sqrt(24/n). Normal dist → z=0; bimodal → z<0.
    n = len(projections)
    if n < 4:
        return 0.0
    se = (24.0 / n) ** 0.5
    return k / se if se > 0 else 0.0


def basin_geometry(points: list[list[float]]) -> dict:
    """Composite: returns center + manifold_dim + bimodality_z +
    mean_cosine_to_center + n + dim. The full geometric prior the
    cortex consolidation feeds the chairman."""
    from trinity_local.cortex_geometry import mean_cosine_to

    if not points:
        return {"center": [], "manifold_dim": 0.0, "bimodality_z": 0.0,
                "mean_cosine_to_center": 0.0, "n": 0, "dim": 0}

    center = weiszfeld_median(points)
    pr = participation_ratio(points, center)
    bz = bimodality_z(points, center)
    mc = mean_cosine_to(center, points)

    return {
        "center": center,
        "manifold_dim": pr,
        "bimodality_z": bz,
        "mean_cosine_to_center": mc,
        "n": len(points),
        "dim": len(points[0]) if points else 0,
    }


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/pca.py",
        description=(
            "Geometric primitives on embedding clusters: Weiszfeld median, "
            "manifold dimensionality (participation ratio), bimodality "
            "z-score. Operations: geometry (default), median, pca."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dependencies (auto-installed on first run):\n"
            f"  {chr(10).join('  ' + r for r in REQUIREMENTS)}"
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
        audit_log(script=SCRIPT_NAME, operation="basin_geometry",
                  outcome="bad_input", detail="missing 'vectors' field")
        return 2

    points = payload["vectors"]
    operation = payload.get("operation", "geometry")

    try:
        if operation == "geometry":
            result = basin_geometry(points)
        elif operation == "median":
            result = {"center": weiszfeld_median(points)}
        elif operation == "pca":
            from trinity_local.cortex_geometry import weiszfeld_median, project_onto_first_pc
            center = weiszfeld_median(points)
            projections = project_onto_first_pc(points, center)
            result = {"center": center, "projections": projections}
        else:
            print(f"error: unknown operation: {operation!r}", file=sys.stderr)
            audit_log(script=SCRIPT_NAME, operation="basin_geometry",
                      outcome="bad_input", detail=f"unknown op {operation!r}")
            return 2
    except Exception as exc:
        audit_log(
            script=SCRIPT_NAME, operation="basin_geometry", outcome="error",
            args={"n": len(points), "operation": operation},
            detail=f"{type(exc).__name__}: {exc}",
        )
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    audit_log(
        script=SCRIPT_NAME, operation=operation,
        args={"n": len(points), "elapsed_ms": elapsed_ms},
    )
    result["elapsed_ms"] = elapsed_ms
    write_output_json(result, args.out if args.out != "-" else None)
    return 0


if __name__ == "__main__":
    bootstrap_or_continue(script_name=SCRIPT_NAME, requirements=REQUIREMENTS)
    sys.exit(_cli_main())
