"""Pure-numerical helpers for the cortex consolidation pass.

Extracted from cortex.py to keep the routing/schema concerns there focused
on orchestration. Everything in this module is dependency-free Python math
(stdlib only) — no scipy, no numpy in production paths, no awareness of
RoutingPattern. The one impure function (`compute_basin_geometry`) is
included because it composes the math on top of evidence embeddings and
returns the structured geometric prior the flagship extraction prompt
conditions on; isolating it here makes the basin-shape contract a single
file's worth of code.

Public symbols:
- ``compute_basin_geometry(outcomes) -> dict``
- ``weiszfeld_median(points) -> list[float]``
- ``participation_ratio(points, center) -> float``
- ``project_onto_first_pc(points, center) -> list[float]``
- ``mean_cosine_to(center, points) -> float``
- ``excess_kurtosis(values) -> float``
- ``euclid(a, b) -> float``
- Constants: ``BIMODALITY_KURTOSIS_THRESHOLD``, ``MANIFOLD_DIM_SATURATION``

The underscore-prefixed aliases (``_weiszfeld_median``, etc.) are kept so
existing imports `from .cortex import _weiszfeld_median` continue to work
when cortex.py re-exports them.
"""
from __future__ import annotations

import math


# Calibration: -1.3 is just below uniform-distribution excess kurtosis
# (-1.2) so uniformly-along-an-axis basins (which are coherent, not
# bimodal) don't trip the flag. Truly twin-peaked distributions hit -1.4
# to -2.0+. Used by `compute_basin_geometry`.
BIMODALITY_KURTOSIS_THRESHOLD = -1.3

# Manifold-dimensionality cap. The participation-ratio metric saturates
# at this number of effective components. Kept here as a constant for
# future tuning; not currently used as a hard ceiling in the coherence
# score (which switched to mean-cosine-from-median, a cleaner metric).
MANIFOLD_DIM_SATURATION = 5.0


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


def _outcome_query_text(o: dict) -> str:
    """The DISCRIMINATIVE text for centroid placement: the user's actual
    question, NOT the chairman preamble.

    The synthesis_prompt prefixes a FIXED preamble + the user's core.md
    profile — both byte-identical across every council — and only later
    carries ``Original task: <question>`` and ``Goal: <goal>``. The previous
    code embedded ``synth.split("\\n\\n")[0]`` (the preamble), so every basin's
    centroid was the SAME template embedding and centroid routing could not
    discriminate between basins (the second root cause in #277, found
    2026-05-31: all basins scored an identical ~0.27 against any query). Pull
    the task + goal lines instead; fall back to the basin label for prompts
    that predate the labeled format.
    """
    synth = o.get("synthesis_prompt") or ""
    if synth:
        import re

        m_task = re.search(r"Original task:\s*(.+?)(?:\n\n|\Z)", synth, re.DOTALL)
        m_goal = re.search(r"(?:^|\n)Goal:\s*(.+?)(?:\n\n|\Z)", synth, re.DOTALL)
        parts = [m.group(1).strip() for m in (m_task, m_goal) if m]
        if parts:
            return " ".join(parts)[:500]
        # No labeled task → older/plain synthesis_prompt where the FIRST
        # paragraph IS the question (no fixed preamble). Use it directly.
        first = synth.split("\n\n")[0] if "\n\n" in synth else synth
        if first.strip():
            return first.strip()[:500]
    label = o.get("routing_label") or {}
    return (label.get("task_type") or "")[:500]


def compute_basin_geometry(outcomes: list[dict]) -> dict:
    """Return the structured geometric prior for one basin's evidence
    embeddings. The flagship extraction prompt conditions on this prior
    to do rule-extraction-on-structure instead of geometry-in-language.

    Returns dict with:
      centroid:        geometric median (Weiszfeld iteration); robust to
                       outliers via L1 minimization. ``[]`` if embedding fails.
      manifold_dim:    effective dim via participation ratio (descriptive
                       only — surfaced to the LLM prompt, not the trust score).
      coherence_score: mean cosine similarity to the median, clipped 0..1.
                       Direct trust component (catches "noisy basin" rules).
      bimodal_flag:    excess-kurtosis(first PC) < threshold ⇒ basin
                       plausibly splits into two modes; surfaced so flagship
                       can emit two subroutes.
      ordered_indices: outcomes sorted by L2 distance from the median
                       (typical first, outliers last) so the LLM prompt
                       lists representative episodes at the top.

    Best-effort: any failure returns an empty geometry (centroid=[],
    coherence=0.5 neutral, flag=False, ordered=[]). The caller then falls
    through to label-only paths.
    """
    empty = {
        "centroid": [],
        "manifold_dim": 0.0,
        "coherence_score": 0.5,
        "bimodal_flag": False,
        "ordered_indices": [],
    }

    try:
        from .embeddings import embed
    except ImportError:
        return empty

    indexed_prompts: list[tuple[int, str]] = []
    for idx, o in enumerate(outcomes[:40]):
        text = _outcome_query_text(o)
        if text.strip():
            indexed_prompts.append((idx, text.strip()))

    if not indexed_prompts:
        return empty

    try:
        vectors_raw = [(idx, embed(p)) for idx, p in indexed_prompts]
    except Exception:
        return empty
    vectors_with_idx = [(idx, v) for idx, v in vectors_raw if v]
    if not vectors_with_idx:
        return empty

    dim = len(vectors_with_idx[0][1])
    vectors_with_idx = [(idx, v) for idx, v in vectors_with_idx if len(v) == dim]
    if not vectors_with_idx:
        return empty

    indices = [idx for idx, _ in vectors_with_idx]
    matrix = [list(v) for _, v in vectors_with_idx]
    n = len(matrix)

    median = weiszfeld_median(matrix)

    distances = [(idx, euclid(matrix[i], median)) for i, idx in enumerate(indices)]
    # Distance ASC, original index ASC as a stable tie-break so ordered_indices
    # is deterministic when two points are equidistant from the median (else the
    # enumerate order, which is input-order-derived, picks).
    distances.sort(key=lambda t: (t[1], t[0]))
    ordered_indices = [idx for idx, _ in distances]

    manifold_dim = participation_ratio(matrix, median)
    coherence = max(0.0, min(1.0, mean_cosine_to(median, matrix)))

    # Floor N at 10 — kurtosis is too noisy below that to call bimodality
    # with any confidence. False positives cost more than false negatives
    # here (a wrongly-split basin produces two weak rules instead of one
    # decent rule).
    bimodal_flag = False
    if n >= 10:
        first_pc_scalars = project_onto_first_pc(matrix, median)
        if first_pc_scalars:
            bimodal_flag = excess_kurtosis(first_pc_scalars) < BIMODALITY_KURTOSIS_THRESHOLD

    return {
        "centroid": median,
        "manifold_dim": manifold_dim,
        "coherence_score": coherence,
        "bimodal_flag": bimodal_flag,
        "ordered_indices": ordered_indices,
    }


# Underscore aliases so existing `from .cortex import _xxx` calls keep
# working after cortex.py re-exports these. Drop in v1.6 once the rest of
# the tree has migrated to the no-underscore names.
_euclid = euclid
_mean_cosine_to = mean_cosine_to
_weiszfeld_median = weiszfeld_median
_participation_ratio = participation_ratio
_project_onto_first_pc = project_onto_first_pc
_excess_kurtosis = excess_kurtosis
_compute_basin_geometry = compute_basin_geometry
_BIMODALITY_KURTOSIS_THRESHOLD = BIMODALITY_KURTOSIS_THRESHOLD
_MANIFOLD_DIM_SATURATION = MANIFOLD_DIM_SATURATION
