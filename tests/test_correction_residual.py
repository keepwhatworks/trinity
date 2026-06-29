"""The residual-seeker geometry (correction_residual.py): on planted structure,
frontier's compression-progress must SEPARATE a shared hidden axis (re-explains
its siblings → high progress) from an idiosyncratic one-off (explains only itself
→ ~0 progress). Pure geometry, no embeddings — deterministic + fast.

This is the noisy-TV guard the residual meter rests on: residual alone flags
every off-axis basin (signal AND noise both look high-residual), so the policy
must rank by progress, not residual. Pin that the separation actually works."""
from __future__ import annotations

import math

from trinity_local.me.correction_residual import (
    candidate_progress,
    gram_schmidt,
    residual_fraction,
)


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def test_gram_schmidt_orthonormalizes_and_drops_dependents():
    e0 = [1, 0, 0, 0]
    # second vector correlated with e0 (concrete↔action overlap analog), third dependent
    basis = gram_schmidt([e0, [0.7, 0.7, 0, 0], [2, 0, 0, 0]])
    assert len(basis) == 2, "a linearly-dependent axis must be dropped"
    # orthonormal: unit length + mutually orthogonal
    for b in basis:
        assert abs(math.sqrt(sum(x * x for x in b)) - 1.0) < 1e-9
    assert abs(sum(a * b for a, b in zip(basis[0], basis[1]))) < 1e-9


def test_residual_fraction_is_zero_in_span_one_outside():
    span = gram_schmidt([[1, 0, 0, 0], [0, 1, 0, 0]])
    frac_in, _ = residual_fraction([3, 4, 0, 0], span)     # fully inside span
    frac_out, _ = residual_fraction([0, 0, 1, 0], span)    # fully outside span
    assert frac_in < 1e-9
    assert abs(frac_out - 1.0) < 1e-9


def _fake_unit_embedder(dim: int = 24):
    """Deterministic per-text unit vectors — same text always maps to the same
    vector, so _axis_vectors and _mean_correction_unit see a consistent space.
    With unit embeds the synthetic correction built from an axis's poles equals
    that axis's vector by construction, so a correctly-wired calibration must
    round-trip to residual 0 — this guards the plumbing without a real embedder."""
    import hashlib

    cache: dict[str, list[float]] = {}

    def fake(texts):
        out = []
        for t in texts:
            if t not in cache:
                h = hashlib.sha256(t.encode("utf-8")).digest()
                v = [(h[i % len(h)] / 255.0) - 0.5 for i in range(dim)]
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                cache[t] = [x / n for x in v]
            out.append(cache[t])
        return out

    return fake


def test_axis_self_calibration_round_trips_with_unit_embedder(monkeypatch):
    # embed_batch / mlx_actually_loaded are imported INSIDE the functions from
    # trinity_local.embeddings, so patch them at the source module.
    import trinity_local.embeddings as emb
    import trinity_local.me.correction_lens as cl
    monkeypatch.setattr(emb, "embed_batch", _fake_unit_embedder())
    monkeypatch.setattr(emb, "mlx_actually_loaded", lambda: True)

    from trinity_local.me.correction_residual import axis_self_calibration

    r = axis_self_calibration()
    assert r["ready"] and r["calibrated"], r
    assert r["worst_residual"] < 1e-6, r
    assert set(r["axes"]) == set(cl.TASTE_AXES)
    for name, v in r["axes"].items():
        # a correction built from axis N's own poles lands in-span and aligns
        # with axis N — the calibration invariant the residual readout rests on.
        assert v["residual"] < 1e-6, (name, v)
        assert abs(v["cos_own_axis"] - 1.0) < 1e-6, (name, v)


def test_axis_self_calibration_abstains_without_real_embeddings(monkeypatch):
    import trinity_local.embeddings as emb
    monkeypatch.setattr(emb, "mlx_actually_loaded", lambda: False)
    from trinity_local.me.correction_residual import axis_self_calibration

    r = axis_self_calibration()
    assert r["ready"] is False
    assert "calibrated" not in r  # never claims calibration under the TF-IDF fallback


def test_frontier_progress_separates_shared_axis_from_one_off():
    # Named-axis span = e0, e1. A hidden SHARED off-axis direction = e2: three
    # basins steer mostly along e2 (+ tiny per-basin idiosyncrasy). One basin is
    # a one-off along e3, orthogonal to everything.
    span = gram_schmidt([[1, 0, 0, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0, 0, 0]])
    basin_means = {
        "shared_a": _unit([0.1, 0.0, 1.0, 0.0, 0.05, 0.0, 0.0, 0.0]),
        "shared_b": _unit([0.0, 0.1, 1.0, 0.0, 0.0, 0.05, 0.0, 0.0]),
        "shared_c": _unit([-0.1, 0.0, 1.0, 0.0, 0.0, 0.0, 0.05, 0.0]),
        "one_off":  _unit([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
    }
    # Every basin is off the named span (high residual) — residual alone can't tell them apart.
    for bid, mv in basin_means.items():
        assert residual_fraction(mv, span)[0] > 0.9, bid

    shared = candidate_progress(basin_means, span, "shared_a")
    oneoff = candidate_progress(basin_means, span, "one_off")

    # The shared remainder re-explains its siblings; the one-off moves nothing else.
    assert shared["progress"] > 0.3, f"shared axis should generalize: {shared['progress']}"
    assert oneoff["progress"] < 0.05, f"one-off should not generalize: {oneoff['progress']}"
    assert shared["progress"] > oneoff["progress"]
    # Spillover: adding shared_a's axis drops shared_b / shared_c residuals to ~0.
    sib = shared["per_basin"]
    assert sib["shared_b"]["after"] < 0.2 and sib["shared_b"]["drop"] > 0.5
    assert sib["shared_c"]["after"] < 0.2 and sib["shared_c"]["drop"] > 0.5
    # ...but leaves the one-off untouched.
    assert sib["one_off"]["drop"] < 0.05
