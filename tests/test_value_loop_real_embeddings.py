"""Lens-basin placement, proven on the REAL embedder: distinct domains land in
distinct basins at the production gates, with semantic separation that only
ModernBERT (not the TF-IDF fallback) produces.

`place_query` puts a question in its nearest lens basin; `scope_for_query` uses it
to hand a council the tensions of the question's own basin. The unit tests
(`test_lens_routing`) inject a stub embed_fn with orthogonal basins, so they prove
"GIVEN separable embeddings, placement is correct" — they canNOT prove the real
embedder separates distinct semantic clusters well enough for the production
gates (MATCH_FLOOR=0.36, MARGIN_FLOOR=0.02). An embedder swap, a normalization
regression or a threshold drift would keep every stub test green while real
placement collapsed into one basin or cleared none.

This seeds two clearly distinct domains (software architecture vs runtime
debugging), builds basin centroids from REAL text, and asserts that every seed
task AND two held-out queries (no salient tokens shared with the seeds, so this
is generalization, not string matching) land in their own basin.

Until 2026-10-26 this also ran the per-basin routing tally over seeded councils;
that tally left src/ with the router (removed 2026-08-11) and is pinned in
tests/test_frozen_basin_routing.py.

Gated on `mlx_actually_loaded()` — skips wherever the TF-IDF fallback is the
backend (it doesn't cluster semantically), the same gate test_semantic_filter uses.
"""
from __future__ import annotations

import pytest

_ARCH_SEEDS = [
    "How should I structure the service layer and its module boundaries?",
    "Design the component hierarchy for this dashboard architecture.",
    "What's the cleanest architecture for separating these concerns?",
    "Lay out the system design for the ingestion pipeline modules.",
]
_DEBUG_SEEDS = [
    "Why does this throw a null pointer at runtime and how do I fix it?",
    "Trace this stack and find the cause of the failing test.",
    "Debug the error in this function — the test crashes on edge input.",
    "This runtime exception is intermittent; help me find the root cause.",
]
_ARCH_HELDOUT = "Refactor the boundaries between these two services for cleaner separation"
_DEBUG_HELDOUT = "Figure out the reason this code blows up on that particular input"


def test_real_embedder_places_distinct_domains_in_distinct_basins():
    from trinity_local.embeddings import mlx_actually_loaded

    if not mlx_actually_loaded():
        pytest.skip("real semantic placement requires the [mlx] extras (TF-IDF doesn't cluster)")

    from trinity_local.embeddings import embed
    from trinity_local.lens_routing import place_query

    centroid_arch = embed("design the system architecture, module boundaries, and component structure")
    centroid_debug = embed("debug the runtime error, trace the stack, and fix the failing test")
    assert len(centroid_arch) == len(centroid_debug) >= 256, "embedder returned a degenerate vector"
    basins = [{"id": "b00", "centroid": centroid_arch}, {"id": "b01", "centroid": centroid_debug}]

    misplaced = [(t, place_query(t, basins, embed)) for t in _ARCH_SEEDS + [_ARCH_HELDOUT]
                 if place_query(t, basins, embed) != "b00"]
    misplaced += [(t, place_query(t, basins, embed)) for t in _DEBUG_SEEDS + [_DEBUG_HELDOUT]
                  if place_query(t, basins, embed) != "b01"]
    assert not misplaced, (
        "the real embedder did not separate architecture from debugging at the production "
        f"placement gates (task, placed basin): {misplaced}"
    )
