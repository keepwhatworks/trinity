"""Pure-numerical basin geometry, used by me/basin_split.py to split mega-basins.

Dependency-free Python math (stdlib only): no scipy, no numpy in production
paths.

Public symbols:
- ``weiszfeld_median(points) -> list[float]``
- ``participation_ratio(points, center) -> float``
- ``project_onto_first_pc(points, center) -> list[float]``
- ``mean_cosine_to(center, points) -> float``
- ``excess_kurtosis(values) -> float``
- ``euclid(a, b) -> float``
- Constant: ``BIMODALITY_KURTOSIS_THRESHOLD``

(Extracted from the deleted cortex engine. Retired 2026-10-26 along with
cortex.py: ``compute_basin_geometry``, which built the geometric prior for that
engine's flagship prompt, had no caller and an unresolvable embed import; its
underscore aliases; and the unused ``MANIFOLD_DIM_SATURATION``.)
"""
from __future__ import annotations

import math


# Calibration: -1.3 is just below uniform-distribution excess kurtosis
# (-1.2) so uniformly-along-an-axis basins (which are coherent, not
# bimodal) don't trip the flag. Truly twin-peaked distributions hit -1.4
# to -2.0+.
BIMODALITY_KURTOSIS_THRESHOLD = -1.3


def euclid(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def mean_cosine_to(center: list[float], points: list[list[float]]) -> float:
    """Mean cosine similarity from ``center`` to each row in ``points``.

    Used as the trust-relevant coherence signal — directly answers "if a
    new query's embedding lands near the median, how similar is that to
    the typical evidence?" Returns 0.0 when the center has zero norm
    (degenerate); otherwise in [-1.0, 1.0] which the caller clips.
    """
    if not points or not center:
        return 0.0
    center_norm = math.sqrt(sum(x * x for x in center))
    if center_norm < 1e-12:
        return 0.0
    sims: list[float] = []
    for p in points:
        p_norm = math.sqrt(sum(x * x for x in p))
        if p_norm < 1e-12:
            continue
        dot = sum(a * b for a, b in zip(p, center))
        sims.append(dot / (p_norm * center_norm))
    if not sims:
        return 0.0
    return sum(sims) / len(sims)


def weiszfeld_median(
    points: list[list[float]],
    max_iter: int = 50,
    eps: float = 1e-6,
) -> list[float]:
    """Geometric median via Weiszfeld iteration. Robust to outliers under
    L1 (a single far point can't drag the median the way it drags the mean).

    Single-pass O(I·N·D) where I ≤ 50. For typical basin sizes (≤50
    points × 768-dim embeddings) this completes in milliseconds.
    """
    if not points:
        return []
    dim = len(points[0])
    median = [sum(p[i] for p in points) / len(points) for i in range(dim)]

    for _ in range(max_iter):
        weights: list[float] = []
        total_weight = 0.0
        coincident_index: int | None = None
        for i, p in enumerate(points):
            d = euclid(median, p)
            if d < eps:
                coincident_index = i
                break
            w = 1.0 / d
            weights.append(w)
            total_weight += w

        if coincident_index is not None:
            # Per Vardi–Zhang, the L1 minimizer is the point itself unless
            # the net pull from the rest exceeds 1. For ≤50 high-dim
            # points collisions are vanishingly rare — snapping to the
            # colliding point is safe and stable.
            return list(points[coincident_index])

        if total_weight < eps:
            break

        new_median = [0.0] * dim
        for p, w in zip(points, weights):
            for j in range(dim):
                new_median[j] += p[j] * w
        for j in range(dim):
            new_median[j] /= total_weight

        step = euclid(new_median, median)
        median = new_median
        if step < eps:
            break

    return median


def participation_ratio(points: list[list[float]], center: list[float]) -> float:
    """Effective dimensionality via participation ratio of singular values.

    Define x_i = p_i - center. Build the covariance matrix
    C = (1/N) Xᵀ X. PR(C) = (tr C)² / tr(C²) only needs the trace and
    Frobenius norm of C, both computable from the N×N gram matrix
    (tr(XᵀX) = tr(XXᵀ); ‖XᵀX‖_F = ‖XXᵀ‖_F).

    So PR = (Σ d_i)² / Σ_{i,j} (⟨x_i, x_j⟩)² where d_i = ⟨x_i, x_i⟩.
    O(N²·D); fine for basins capped at 40 points.

    Result: 1.0 when all points coincide, → N as points spread isotropically.
    """
    n = len(points)
    if n < 2:
        return 0.0

    centered = [[p[i] - center[i] for i in range(len(p))] for p in points]

    gram_diag: list[float] = []
    for i in range(n):
        d_ii = sum(x * x for x in centered[i])
        gram_diag.append(d_ii)
    trace = sum(gram_diag)
    if trace < 1e-12:
        return 0.0

    frobenius_sq = 0.0
    for i in range(n):
        for j in range(n):
            inner = sum(centered[i][k] * centered[j][k] for k in range(len(centered[i])))
            frobenius_sq += inner * inner

    if frobenius_sq < 1e-12:
        return 0.0
    return (trace * trace) / frobenius_sq


def project_onto_first_pc(points: list[list[float]], center: list[float]) -> list[float]:
    """Project rows onto the leading singular direction via power iteration
    on the centered N×D matrix. Returns N scalars — the 1D distribution
    along the basin's primary axis of variation.

    Avoids a hard numpy/scipy dep: a 50-iteration power iteration on a
    small (≤40)×768 matrix runs in ms with no import cost.
    """
    n = len(points)
    if n < 2:
        return []
    dim = len(points[0])
    centered = [[p[i] - center[i] for i in range(dim)] for p in points]

    v = [1.0 / math.sqrt(dim)] * dim

    for _ in range(50):
        Xv = [sum(centered[i][j] * v[j] for j in range(dim)) for i in range(n)]
        new_v = [0.0] * dim
        for j in range(dim):
            new_v[j] = sum(centered[i][j] * Xv[i] for i in range(n))
        norm = math.sqrt(sum(x * x for x in new_v))
        if norm < 1e-12:
            return []
        new_v = [x / norm for x in new_v]
        delta = sum((new_v[j] - v[j]) ** 2 for j in range(dim))
        v = new_v
        if delta < 1e-8:
            break

    return [sum(centered[i][j] * v[j] for j in range(dim)) for i in range(n)]


def excess_kurtosis(values: list[float]) -> float:
    """Excess kurtosis = E[(x-μ)⁴] / σ⁴ - 3. Negative values flag
    distributions flatter than normal (uniform → -1.2, bimodal → more
    negative still). Positive values are peakier than normal."""
    n = len(values)
    if n < 4:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    if var < 1e-12:
        return 0.0
    fourth = sum((v - mean) ** 4 for v in values) / n
    return fourth / (var * var) - 3.0
