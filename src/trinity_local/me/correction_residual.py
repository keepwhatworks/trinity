"""correction_residual.py — the lens as a SEEKER, not a confirmer.

Designed in a multi-agent residual/meter exchange (2026-06-02) and run on the
real founder corpus before adoption — the empirical step the design hinged on.
First read (n=112 model_miss, 5 basins ≥ _MIN_CORRECTIONS): basin residuals
0.95–0.997 and frontier compression-progress ≤0.007 on every candidate. That
LOOKED like "domain-local taste, no learnable fifth axis" — and that read was
RETRACTED the same day by a calibration pass (``axis_self_calibration()``):

  • The meter is CALIBRATED on short text — feed each named axis its own poles
    as a synthetic correction and all four round-trip to residual 0.000 /
    cos→own-axis 1.000. So the embed→difference→project chain does NOT destroy
    stance signal in-register; the strong "instrument is mute" reading is wrong.

A SECOND correction (2026-06-02, same exchange) overturned my own "register
mismatch" diagnosis — register is a near-zero lever; the flaw is the MEASURE +
the grouping. Projection is linear: ``(pri−sac)·a = pri·a − sac·a`` is the
signed stance-shift, and that NUMERATOR is correct at any register. The confound
is the DENOMINATOR — ``residual_fraction`` divides by ‖pri−sac‖, which is
content-dominated; long prototypes don't shrink it. Measured on the real corpus:

  • Register adds ~nothing — short vs long prototypes give magnitude 0.945→0.954
    and coherence 0.105→0.106 (no lift). My v1.7.236 "register confound" labeled
    the wrong lever.
  • The STANCE IS THERE — a content-norm-FREE readout recovers it: the signed
    scalar shift ``c·a`` judged by sign-consistency lands z = 2.6–4.1 on all four
    named axes (sign-consistency 0.14–0.28 vs ~0.08 null). The norm fraction
    buried exactly this.
  • Magnitude vs coherence is the real lever — ranking the off-span residual by
    COHERENCE (mean-resultant-length of UNIT residuals, 0.105 vs 0.076 null)
    sees structure where magnitude-ranking (resid ~0.95) sees "all off-axis".
    On planted heavy-tailed content, coherence recovers a hidden axis (cos 0.99)
    where magnitude can't (0.77).
  • Per-basin is STRUCTURALLY hopeless, not register-fixable: a basin is a topic
    cluster, so within-basin content is correlated → averaging REINFORCES the
    topic direction instead of cancelling it. ``correction_signature`` works
    GLOBALLY (cross-topic averaging cancels content, 5σ); the per-basin mean is
    the one place content survives. basin_residuals/frontier reintroduced exactly
    that content.

So basin_residuals()/frontier() (which rank by residual MAGNITUDE on per-basin
MEANS) are doubly confounded and stay ORPHANED. The validated path is
content-invariant SCORING — sign-consistency for known axes, coherence-ranking
for hidden-axis discovery — never the norm fraction, never per-basin means.
``axis_self_calibration()`` remains the in-register gate.

`correction_lens.py` projects the mean correction onto FOUR FIXED taste axes
(concrete↔abstract, terse↔verbose, decisive↔hedging, action↔description) and
reports the loadings. Read it honestly: every function in that file projects
ONTO the named span and reports how hard you lean on axes you already named.
By construction it cannot discover a fifth axis. It is a confirmation engine.

The only place a new axis of taste can live is the part of your steer that the
named axes CANNOT express — the component orthogonal to their span. Nothing in
the current lens ever looks there. This module does.

Two readouts:

  basin_residuals()  — per basin, the fraction of the basin's mean correction
                       that lies OUTSIDE the named-axis span. High residual =
                       you are steering in a direction the lens cannot name yet.
                       This is the gap. (The data-acquisition frontier — where
                       the next dream / eval-prompt / council should sample.)

  frontier()         — residual is NOT enough on its own. An idiosyncratic
                       one-off is ALSO off-axis, so it ALSO scores high residual
                       (on this corpus per-act coherence is ~0.14 — individual
                       corrections nearly orthogonal — so per-act residual is
                       almost all noise; that is why this module works on basin
                       MEANS, never single acts). The compression-progress test
                       separates signal from noise: take a high-residual basin's
                       remainder as a CANDIDATE new axis and measure how much
                       adding it reduces the residual of the OTHER basins. A real
                       new axis re-explains many basins (high progress); a
                       one-off explains only itself (~0 progress).
                       progress = residual × learnability — the noisy-TV guard.

Pure geometry. No LLM judge, no provider dispatch. Reuses the #257 primitives,
runs in the time it takes to embed your corrections once.
"""
from __future__ import annotations

import math
from collections import defaultdict

# ---------------------------------------------------------------------------
# Pure geometry core (embedding-free — unit-testable on synthetic vectors).
# ---------------------------------------------------------------------------

def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def _unit(v):
    n = _norm(v)
    return [x / n for x in v] if n else list(v)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _sub_proj(v, e):
    """v minus its projection onto unit vector e."""
    d = _dot(v, e)
    return [x - d * ei for x, ei in zip(v, e)]


def gram_schmidt(vectors):
    """Orthonormalize a list of vectors. The 4 taste axes are NOT mutually
    orthogonal (concrete and action correlate), so the span must be
    orthonormalized before any residual is meaningful — projecting onto the
    raw, correlated axes double-counts the shared direction and understates
    the residual."""
    basis = []
    for v in vectors:
        w = list(v)
        for e in basis:
            w = _sub_proj(w, e)
        n = _norm(w)
        if n > 1e-9:                      # drop axes that are linearly dependent
            basis.append([x / n for x in w])
    return basis


def residual_fraction(v, ortho_basis):
    """Fraction of unit-ish v that lies outside span(ortho_basis), in [0,1].
    Returns (fraction, residual_unit_direction)."""
    w = list(v)
    for e in ortho_basis:
        w = _sub_proj(w, e)
    vn, wn = _norm(v), _norm(w)
    frac = (wn / vn) if vn else 0.0
    return frac, ([x / wn for x in w] if wn > 1e-9 else [0.0] * len(v))


def candidate_progress(basin_means, ortho_basis, candidate_basin):
    """Take `candidate_basin`'s orthogonal remainder as a proposed new axis,
    add it to the span, and measure the mean residual DROP across the OTHER
    basins. That drop is the candidate axis's compression progress.

    basin_means: {basin_id: mean_correction_vector (list[float])}
    Returns {progress, candidate_basin, per_basin: {bid: {before, after, drop}}}.
    """
    cand_v = basin_means[candidate_basin]
    _, cand_dir = residual_fraction(cand_v, ortho_basis)   # the unexplained direction
    extended = ortho_basis + [cand_dir]                    # cand_dir ⟂ span by construction
    per = {}
    drops = []
    for bid, mv in basin_means.items():
        if bid == candidate_basin:
            continue
        before, _ = residual_fraction(mv, ortho_basis)
        after, _ = residual_fraction(mv, extended)
        drop = before - after
        per[bid] = {"before": round(before, 3), "after": round(after, 3), "drop": round(drop, 3)}
        drops.append(drop)
    progress = (sum(drops) / len(drops)) if drops else 0.0
    return {
        "candidate_basin": candidate_basin,
        "progress": round(progress, 3),       # mean residual reduction on OTHER basins
        "n_other": len(drops),
        "per_basin": dict(sorted(per.items(), key=lambda kv: -kv[1]["drop"])),
    }


# ---------------------------------------------------------------------------
# Embedding-backed wrappers (bind to the real #257 corpus + axis primitives).
# ---------------------------------------------------------------------------

def _basin_means():
    """{basin_id: (label, n, mean_correction_unit)} over eligible basins.
    Mirrors correction_signature_by_basin's grouping + gates exactly."""
    from .correction_lens import _MIN_CORRECTIONS, _basin_labels, _mean_correction_unit
    from .preference_acts import iter_preference_acts

    by_basin = defaultdict(list)
    for a in iter_preference_acts():
        s, p = (a.sacrificed or "").strip(), (a.privileged or "").strip()
        if a.basin and len(s) > 4 and len(p) > 2:
            by_basin[a.basin].append((s, p))

    labels = _basin_labels()
    out = {}
    for basin, pairs in by_basin.items():
        if len(pairs) < _MIN_CORRECTIONS:
            continue
        try:
            out[basin] = ((labels.get(basin) or "")[:60], len(pairs), _mean_correction_unit(pairs))
        except Exception:
            continue
    return out


def basin_residuals(min_basins: int = 2) -> dict:
    """Per-basin fraction of the mean steer that the named axes cannot express.
    Highest residual first — the basins where your taste is going somewhere the
    lens has no word for. Best-effort {'ready': False}."""
    from .correction_lens import _axis_vectors
    try:
        from ..embeddings import mlx_actually_loaded
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    means = _basin_means()
    if len(means) < min_basins:
        return {"ready": False, "reason": "too few eligible basins", "n_basins": len(means)}

    ortho = gram_schmidt(list(_axis_vectors().values()))
    rows = {}
    for bid, (label, n, mc) in means.items():
        frac, _ = residual_fraction(mc, ortho)
        rows[bid] = {"label": label, "n": n, "residual": round(frac, 3)}
    return {
        "ready": True,
        "axis_rank": len(ortho),
        "basins": dict(sorted(rows.items(), key=lambda kv: -kv[1]["residual"])),
    }


def frontier(top_k: int = 3) -> dict:
    """The data-acquisition signal: rank basins by compression progress, not by
    raw residual. For each of the top-residual basins, test whether its
    remainder is a SHARED new axis (re-explains other basins → high progress) or
    an idiosyncratic one-off (explains only itself → ~0 progress). Sample where
    progress is highest — that is residual × learnability."""
    from .correction_lens import _axis_vectors
    try:
        from ..embeddings import mlx_actually_loaded
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    means = _basin_means()
    if len(means) < 2:
        return {"ready": False, "reason": "need >=2 eligible basins", "n_basins": len(means)}

    ortho = gram_schmidt(list(_axis_vectors().values()))
    vecs = {bid: mc for bid, (_, _, mc) in means.items()}
    labels = {bid: lbl for bid, (lbl, _, _) in means.items()}

    # Candidates = the highest-residual basins (most off-axis steer).
    resid = {bid: residual_fraction(mc, ortho)[0] for bid, mc in vecs.items()}
    candidates = sorted(resid, key=lambda b: -resid[b])[:top_k]

    scored = []
    for bid in candidates:
        prog = candidate_progress(vecs, ortho, bid)
        scored.append({
            "basin": bid,
            "label": labels.get(bid, ""),
            "residual": round(resid[bid], 3),    # how off-axis it is
            "progress": prog["progress"],        # how much it re-explains others
            "verdict": "new axis" if prog["progress"] > 0.05 else "idiosyncratic / noise",
            "spillover": prog["per_basin"],
        })
    # Sample where progress (not residual) is highest. Tie-break on the basin
    # id so the rendered frontier order is deterministic when two basins tie on
    # progress (else the candidates scan order picks).
    scored.sort(key=lambda s: (-s["progress"], s["basin"]))
    return {"ready": True, "axis_rank": len(ortho), "frontier": scored}


def axis_self_calibration() -> dict:
    """Calibrate the meter BEFORE trusting any residual it reports.

    Feed each named taste axis its OWN poles as a synthetic correction — built
    by the SAME ``_mean_correction_unit`` that processes real corrections — and
    project onto the orthonormalized span. A correction made from an axis's own
    poles MUST land in-span (residual ~0). If it returns the 1/√n chance floor
    instead, the embed→difference→project chain is destroying stance signal and
    every ``basin_residuals()`` number is mute — the residual would say nothing
    about taste, only about an uncalibrated instrument.

    Empirical (2026-06-02, real founder embedder — nomic-modernbert 768d): all 4
    axes round-trip to residual 0.000 / cos→own-axis 1.000 → ``calibrated: True``.
    The instrument is sound ON SHORT TEXT. The confound is register, not the
    chain: real corrections are long, and the same stance written long projects
    at residual ~0.82 (content-dominated), so ``basin_residuals()`` on long-text
    corrections is register-confounded — see the module docstring.

    Pole sets are unbalanced (e.g. terse↔verbose has 3 positive, 1 negative); we
    cycle the smaller set so the synthetic reconstructs the multi-phrase centroid
    the axis is built from — otherwise the pole imbalance, not the instrument,
    depresses the round-trip.
    """
    from .correction_lens import TASTE_AXES, _axis_vectors, _mean_correction_unit
    try:
        from ..embeddings import mlx_actually_loaded
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    axis_vecs = _axis_vectors()
    ortho = gram_schmidt(list(axis_vecs.values()))
    rows = {}
    for name, (pos, neg) in TASTE_AXES.items():
        n = max(len(pos), len(neg))
        pairs = [(neg[i % len(neg)], pos[i % len(pos)]) for i in range(n)]
        syn = _mean_correction_unit(pairs)
        frac, _ = residual_fraction(syn, ortho)
        rows[name] = {
            "residual": round(frac, 3),
            "cos_own_axis": round(_dot(syn, axis_vecs[name]), 3),
        }
    worst = max((r["residual"] for r in rows.values()), default=1.0)
    return {
        "ready": True,
        "calibrated": worst < 0.1,          # every named axis round-trips in-span
        "worst_residual": round(worst, 3),
        "axes": rows,
    }
