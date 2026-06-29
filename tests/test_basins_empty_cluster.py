"""Robustness guard: k-means must not emit NaN centroids on an empty cluster.

The lens-build clusters thread embeddings into basins (me/basins._kmeans). When
k > 1 but the inputs are tightly clustered or duplicated (a cold-start user with
few prompts, TF-IDF near-collisions, repeated automation prompts), a cluster can
end up with NO assigned points mid-iteration. The classic k-means pitfall: updating
that centroid as `members.mean(axis=0)` over an empty slice yields NaN, which then
propagates through every downstream cosine — poisoning the WHOLE lens, the topology
centroids, and cortex routing (a silent, corpus-wide #277-shaped failure).

`_kmeans` guards this with `if len(members) > 0:` (keep the prior centroid for an
empty cluster). The existing kmeans tests cover the happy path, the n<=k degenerate
case, and empty input — but NOT the empty-cluster-mid-iteration path, so a
regression removing the guard stayed green (verified by mutation). This pins it.
"""
from __future__ import annotations

import numpy as np

import json

from trinity_local.me.basins import _kmeans, compute_basins, load_basins


def test_kmeans_identical_points_returns_finite_centroids():
    """General robustness: k>1 over IDENTICAL points returns finite centroids and
    labels every point (no crash, no NaN in this collapsed config)."""
    matrix = np.array([[1.0, 0.0, 0.0, 0.0]] * 5, dtype=np.float32)
    labels, centroids = _kmeans(matrix, k=3, seed=7)
    assert np.all(np.isfinite(centroids))
    assert labels.shape == (5,), "every point must still get a label"


def test_kmeans_empty_cluster_keeps_finite_centroid():
    """The load-bearing guard: a dense duplicate group plus a couple of distinct
    points with k LARGER than the number of well-separated groups forces clusters
    to go empty mid-iteration. Their centroids must stay FINITE (the prior init
    value), never mean([])=NaN. Mutation-proven: dropping the `if len(members) > 0`
    guard in _kmeans makes the empty clusters NaN → this fails."""
    pts = [[1.0, 0.0, 0.0]] * 6 + [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    matrix = np.array(pts, dtype=np.float32)
    _labels, centroids = _kmeans(matrix, k=5, seed=3)  # k=5 > 3 real groups
    assert np.all(np.isfinite(centroids)), (
        "an empty cluster produced a non-finite (NaN/Inf) centroid — the empty-"
        "cluster guard regressed; NaN basins poison every downstream cosine and "
        "corrupt the lens for a cold-start user"
    )


def test_compute_basins_no_nan_centroid_on_duplicate_embeddings(tmp_path, monkeypatch):
    """End-to-end through the real lens-build entrypoint: a corpus of distinct-text
    prompts that all carry the SAME embedding (a degenerate-but-real cold-start /
    TF-IDF case) must NOT yield a basin with a NaN centroid."""
    from types import SimpleNamespace

    nodes = [
        SimpleNamespace(
            id=f"p{i}", transcript_id=f"t{i}", text=f"a distinct prompt number {i}",
            embedding=[1.0, 0.0, 0.0, 0.0],
        )
        for i in range(4)
    ]
    monkeypatch.setattr(
        "trinity_local.me.basins.iter_prompt_nodes", lambda *a, **k: iter(nodes)
    )
    basins = compute_basins(k=3)  # k>1 on identical embeddings → empty clusters
    for b in basins:
        cen = getattr(b, "centroid", None)
        if cen is not None:
            assert np.all(np.isfinite(np.asarray(cen, dtype=float))), (
                f"basin {getattr(b, 'id', '?')} has a non-finite centroid — a NaN "
                "basin reached the lens output"
            )


def test_load_basins_tolerates_schema_drift(tmp_path, monkeypatch):
    """REGRESSION: `load_basins` must not raise on a topics.json whose basins carry
    an unknown key (a field a NEWER builder wrote, or the memory-viewer's slimmed
    `prompt_id_count` shape). The old `Basin(**b)` raised `TypeError: unexpected
    keyword argument`, which silently blinded every downstream consumer — most
    visibly lens-health's noise self-test, which went dark and leaked the raw
    exception repr into the user's trust report. A record missing a REQUIRED field
    is SKIPPED (not crash-the-whole-load), so one bad basin can't blind the rest."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    basins = [
        # healthy record + a drift key a newer builder/viewer added
        {"id": "b00", "size": 50, "top_terms": ["x"], "centroid": [0.1, 0.2],
         "prompt_id_count": 5, "future_field_v9": "z"},
        # a record missing the required `centroid` — must be skipped, not fatal
        {"id": "b01", "size": 50, "top_terms": ["y"]},
        {"id": "b02", "size": 50, "top_terms": ["w"], "centroid": [0.3, 0.4]},
    ]
    (tmp_path / "memories" / "topics.json").write_text(
        json.dumps({"basins": basins}), encoding="utf-8")

    loaded = load_basins()  # must NOT raise TypeError
    ids = {b.id for b in loaded}
    assert ids == {"b00", "b02"}, (
        f"load_basins should drop unknown keys and skip the centroid-less record, "
        f"keeping the two well-formed basins; got {ids!r}"
    )
    # the drift keys were dropped, the real fields survived
    b00 = next(b for b in loaded if b.id == "b00")
    assert b00.centroid == [0.1, 0.2] and not hasattr(b00, "prompt_id_count")
