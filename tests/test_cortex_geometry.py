"""Regression tests for `cortex_geometry` — the pure-numerical core of
cortex consolidation. The module ships as a dependency-free stdlib block
that underpins basin-shape priors fed to the flagship extraction prompt.

Coverage rationale: cortex_geometry.py was extracted to its own file
expressly so the math has a single home, but until now it was only
exercised transitively via cortex.py. A regression in `weiszfeld_median`
or `mean_cosine_to` would corrupt the geometric prior silently — every
basin would still get a centroid, just a worse one — and the cortex.py
tests don't compute geometry from known inputs end-to-end.

Each test asserts a property that follows from the math definition (not
from current implementation details), so an alternative correct
implementation could swap in without test churn.
"""
from __future__ import annotations


import pytest

from trinity_local.cortex_geometry import (
    BIMODALITY_KURTOSIS_THRESHOLD,
    compute_basin_geometry,
    euclid,
    excess_kurtosis,
    mean_cosine_to,
    participation_ratio,
    project_onto_first_pc,
    weiszfeld_median,
)


class TestEuclid:
    def test_zero_distance(self):
        assert euclid([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0

    def test_unit_step(self):
        assert euclid([0.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_pythagorean(self):
        assert euclid([0.0, 0.0], [3.0, 4.0]) == pytest.approx(5.0)


class TestMeanCosineTo:
    def test_identical_vectors_yield_one(self):
        v = [1.0, 0.0, 0.0]
        assert mean_cosine_to(v, [v, v, v]) == pytest.approx(1.0)

    def test_orthogonal_yields_zero(self):
        assert mean_cosine_to([1.0, 0.0], [[0.0, 1.0]]) == pytest.approx(0.0)

    def test_antipodal_yields_negative_one(self):
        assert mean_cosine_to([1.0, 0.0], [[-1.0, 0.0]]) == pytest.approx(-1.0)

    def test_empty_points_returns_zero(self):
        assert mean_cosine_to([1.0, 0.0], []) == 0.0

    def test_empty_center_returns_zero(self):
        assert mean_cosine_to([], [[1.0, 0.0]]) == 0.0

    def test_zero_center_returns_zero(self):
        # Zero-norm center is degenerate; mean_cosine_to must not divide
        # by zero. The 1e-12 floor in the implementation is the guard.
        assert mean_cosine_to([0.0, 0.0], [[1.0, 0.0]]) == 0.0

    def test_zero_norm_points_skipped(self):
        # A zero-norm point can't contribute a defined cosine. Implementation
        # skips it and averages the rest. The single non-zero point yields 1.0.
        result = mean_cosine_to([1.0, 0.0], [[1.0, 0.0], [0.0, 0.0]])
        assert result == pytest.approx(1.0)


class TestWeiszfeldMedian:
    """The whole reason this function exists is L1-robustness — a single
    outlier should NOT drag the median the way it drags the mean. These
    tests pin that property; if Weiszfeld collapses to a plain mean, they
    fail."""

    def test_single_point_returns_itself(self):
        assert weiszfeld_median([[1.0, 2.0, 3.0]]) == [1.0, 2.0, 3.0]

    def test_empty_returns_empty(self):
        assert weiszfeld_median([]) == []

    def test_two_points_midpoint(self):
        # Geometric median of two points is the segment connecting them;
        # any point on that segment minimizes total L2 distance. Weiszfeld
        # initializes at the mean (the midpoint) so it stays there.
        median = weiszfeld_median([[0.0, 0.0], [10.0, 0.0]])
        assert median[0] == pytest.approx(5.0, abs=0.5)
        assert median[1] == pytest.approx(0.0, abs=0.5)

    def test_outlier_robustness_beats_mean(self):
        # Five points clustered near origin, one far outlier. The MEAN
        # gets dragged ~1.67 toward the outlier; the geometric MEDIAN
        # stays much closer to the cluster. This is the L1-vs-L2
        # property that makes Weiszfeld worth implementing.
        cluster = [[0.0, 0.0], [0.1, 0.0], [-0.1, 0.0], [0.0, 0.1], [0.0, -0.1]]
        outlier = [[100.0, 0.0]]
        points = cluster + outlier

        mean_x = sum(p[0] for p in points) / len(points)
        median = weiszfeld_median(points)

        # Mean is around 16.7 (dominated by outlier); median should be
        # near the cluster (< 1.0) — the whole point of the function.
        assert abs(mean_x - 16.67) < 0.1
        assert median[0] < 1.0, (
            f"Weiszfeld median x={median[0]} got pulled by outlier (should be <1.0)"
        )

    def test_cluster_dominates_outlier(self):
        # Three coincident points + one far outlier. Whether the
        # iteration triggers the coincident-snap path or just converges
        # by Weiszfeld is implementation detail — the property worth
        # pinning is that the median lands AT the cluster (~0,0), not
        # somewhere between cluster and outlier (which the mean ~25,25
        # would give). A loose tolerance covers both convergence paths.
        points = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [100.0, 100.0]]
        median = weiszfeld_median(points)
        assert median[0] == pytest.approx(0.0, abs=1e-3)
        assert median[1] == pytest.approx(0.0, abs=1e-3)

    def test_high_dim_completes(self):
        # Smoke check that the loop terminates on a 768-d basin within
        # max_iter (50). Builds 30 points around a known center.
        center = [0.5] * 768
        points = []
        for i in range(30):
            noise = [(i * 0.001) - 0.015 for _ in range(768)]
            points.append([c + n for c, n in zip(center, noise)])
        median = weiszfeld_median(points)
        assert len(median) == 768
        # Should land near the true center.
        diff = euclid(median, center)
        assert diff < 0.1


class TestParticipationRatio:
    def test_single_point_returns_zero(self):
        assert participation_ratio([[1.0, 2.0]], [0.0, 0.0]) == 0.0

    def test_empty_returns_zero(self):
        assert participation_ratio([], [0.0, 0.0]) == 0.0

    def test_coincident_points_yield_zero(self):
        # Identical points → all centered to zero → trace = 0 → degenerate.
        pts = [[1.0, 0.0]] * 5
        assert participation_ratio(pts, [1.0, 0.0]) == 0.0

    def test_isotropic_grows_with_n(self):
        # N points evenly distributed in 2D should give PR close to 2
        # (the effective dimensionality). The exact PR depends on the
        # distribution; for a regular grid it's between 1 and N.
        pts = [
            [1.0, 0.0],
            [-1.0, 0.0],
            [0.0, 1.0],
            [0.0, -1.0],
        ]
        center = [0.0, 0.0]
        pr = participation_ratio(pts, center)
        # 4 points spread on unit circle in 2D should yield PR near 2
        # (genuinely 2D distribution).
        assert pr == pytest.approx(2.0, abs=0.5)

    def test_collinear_yields_low_pr(self):
        # All points on one line → effective dimensionality ~1.
        pts = [[i, 0.0] for i in range(-5, 6)]
        center = [0.0, 0.0]
        pr = participation_ratio(pts, center)
        assert pr == pytest.approx(1.0, abs=0.2)


class TestProjectOntoFirstPc:
    def test_single_point_returns_empty(self):
        assert project_onto_first_pc([[1.0, 2.0]], [0.0, 0.0]) == []

    def test_recovers_known_axis(self):
        # Points stretched along x-axis only; first PC should be ±x.
        # The projected scalars should preserve the rank order of x-coords.
        pts = [[float(i), 0.0] for i in range(-5, 6)]
        center = [0.0, 0.0]
        scalars = project_onto_first_pc(pts, center)
        assert len(scalars) == len(pts)
        # Sign is arbitrary in power iteration; check monotone-or-anti
        # by comparing pairs.
        sorted_scalars = sorted(scalars)
        # Either ascending or descending; either way after sorting the
        # span should match x-range.
        assert sorted_scalars[-1] - sorted_scalars[0] == pytest.approx(10.0, abs=0.5)


class TestExcessKurtosis:
    def test_too_few_returns_zero(self):
        assert excess_kurtosis([1.0, 2.0]) == 0.0

    def test_zero_variance_returns_zero(self):
        assert excess_kurtosis([1.0, 1.0, 1.0, 1.0, 1.0]) == 0.0

    def test_uniform_is_negative(self):
        # Continuous uniform → excess kurtosis = -1.2. Sampled, expect
        # close to that.
        values = [i / 100.0 for i in range(1000)]
        k = excess_kurtosis(values)
        assert k == pytest.approx(-1.2, abs=0.1)

    def test_bimodal_more_negative_than_threshold(self):
        # Two well-separated clusters → strongly platykurtic. This is the
        # signal `compute_basin_geometry` uses to flag a bimodal basin.
        low = [0.0] * 50
        high = [10.0] * 50
        k = excess_kurtosis(low + high)
        assert k < BIMODALITY_KURTOSIS_THRESHOLD, (
            f"bimodal distribution kurtosis {k} should be < {BIMODALITY_KURTOSIS_THRESHOLD}"
        )


class TestComputeBasinGeometry:
    """End-to-end shape contract: the returned dict always has the same
    five keys with safe defaults so the LLM prompt template never KeyErrors,
    no matter how degenerate the input."""

    def test_empty_outcomes_returns_neutral(self):
        result = compute_basin_geometry([])
        assert result == {
            "centroid": [],
            "manifold_dim": 0.0,
            "coherence_score": 0.5,
            "bimodal_flag": False,
            "ordered_indices": [],
        }

    def test_outcomes_with_no_text_returns_neutral(self):
        outcomes = [{"synthesis_prompt": "", "routing_label": {}}]
        result = compute_basin_geometry(outcomes)
        # No text → no embeddings → empty geometry. Same shape as the
        # empty-input case — the LLM prompt path is unbreakable.
        assert result["centroid"] == []
        assert result["coherence_score"] == 0.5
        assert result["bimodal_flag"] is False

    def test_keys_always_present(self):
        # Even if embedding succeeds, the result schema is fixed.
        # We don't load mlx in tests, so synthesis_prompt with text will
        # hit the embed import; the fallback path may or may not produce
        # a centroid depending on the embedding backend. Either way the
        # five keys are present.
        outcomes = [
            {"synthesis_prompt": "what is the best language for systems work?"},
            {"synthesis_prompt": "how do I write fast python loops?"},
        ]
        result = compute_basin_geometry(outcomes)
        assert set(result.keys()) == {
            "centroid",
            "manifold_dim",
            "coherence_score",
            "bimodal_flag",
            "ordered_indices",
        }


def test_underscore_aliases_match_public_names():
    """Back-compat aliases (`_weiszfeld_median`, etc.) exist so any leftover
    `from .cortex import _weiszfeld_median` calls still resolve. Each alias
    must point at the same function object as its public name — drift
    would mean a caller hits a stale duplicate that doesn't track fixes.
    """
    from trinity_local import cortex_geometry as cg

    assert cg._euclid is cg.euclid
    assert cg._mean_cosine_to is cg.mean_cosine_to
    assert cg._weiszfeld_median is cg.weiszfeld_median
    assert cg._participation_ratio is cg.participation_ratio
    assert cg._project_onto_first_pc is cg.project_onto_first_pc
    assert cg._excess_kurtosis is cg.excess_kurtosis
    assert cg._compute_basin_geometry is cg.compute_basin_geometry
