"""Unit tests for the cortex-collapse routing tally (lens_routing.compute_basin_routing).

The collapse (#298) derives routing from the lens basins instead of the cortex
trust/centroid engine. This pins the pure tally: real-contest filtering, lens-
centroid placement (match + margin gates), recency weighting, and the min-count
omission that hands weak basins back to kNN. Fully synthetic (injected embed_fn)
— no real embedder, deterministic.
"""
from __future__ import annotations

from trinity_local.lens_routing import compute_basin_routing, place_query


# Three orthogonal synthetic basins; the embed_fn maps a keyword to that basis
# vector so we control exactly which basin each council lands in.
BASINS = [
    {"id": "b00", "centroid": [1.0, 0.0, 0.0]},  # "api"
    {"id": "b01", "centroid": [0.0, 1.0, 0.0]},  # "refactor"
    {"id": "b02", "centroid": [0.0, 0.0, 1.0]},  # "naming"
]


def _embed(text: str) -> list[float]:
    t = text.lower()
    if "api" in t:
        return [1.0, 0.0, 0.0]
    if "refactor" in t:
        return [0.0, 1.0, 0.0]
    if "naming" in t:
        return [0.0, 0.0, 1.0]
    return [0.0, 0.0, 0.0]  # out-of-domain → no basin


def _c(cid, task, winner, members=3, when="2026-06-01T00:00:00+00:00"):
    return {"council_id": cid, "task_text": task, "winner": winner,
            "substantive_members": members, "created_at": when}


def test_basin_winner_tally_over_real_contests():
    councils = [
        _c("c1", "design the api surface", "claude"),
        _c("c2", "the api shape question", "claude"),
        _c("c3", "another api decision", "codex"),  # b00 gets claude,claude,codex
    ]
    routing = compute_basin_routing(councils, BASINS, _embed)
    assert "b00" in routing
    assert routing["b00"]["winner"] == "claude"     # 2 claude vs 1 codex
    assert routing["b00"]["count"] == 3
    assert routing["b00"]["margin"] > 0


def test_min_count_basin_is_omitted():
    # b01 has only ONE real-contest council → below MIN_COUNT (2) → omitted →
    # ask falls through to kNN for refactor queries.
    councils = [
        _c("c1", "design the api", "claude"),
        _c("c2", "the api again", "claude"),
        _c("c3", "refactor the module", "codex"),  # lone refactor council
    ]
    routing = compute_basin_routing(councils, BASINS, _embed)
    assert "b00" in routing
    assert "b01" not in routing


def test_walkover_councils_excluded():
    # A 1-member "council" is a walkover, not a real contest (stands in for the
    # absent batch flag). It must NOT contribute to the tally.
    councils = [
        _c("c1", "design the api", "claude", members=3),
        _c("c2", "the api shape", "claude", members=3),
        _c("c3", "api walkover", "antigravity", members=1),  # excluded
    ]
    routing = compute_basin_routing(councils, BASINS, _embed)
    assert routing["b00"]["count"] == 2  # c3 excluded
    assert routing["b00"]["winner"] == "claude"
    assert "antigravity" not in str(routing["b00"]["evidence"])


def test_out_of_domain_query_assigned_no_basin():
    # A task that embeds to the zero vector matches no basin (sim 0 < floor) →
    # not placed anywhere. With only OOD councils, routing is empty.
    councils = [
        _c("c1", "translate this paragraph", "claude"),
        _c("c2", "summarize that essay", "codex"),
    ]
    routing = compute_basin_routing(councils, BASINS, _embed)
    assert routing == {}


def test_ambiguous_placement_abstains():
    # A task equidistant from two basins (top1 - top2 < margin_floor) is an
    # ambiguous placement → abstain (don't misroute). Embed to the b00/b01
    # bisector so top1≈top2.
    def embed_ambiguous(text: str) -> list[float]:
        return [0.707, 0.707, 0.0]  # 45° between b00 and b01 → equal sims
    councils = [_c("c1", "x", "claude"), _c("c2", "y", "claude")]
    routing = compute_basin_routing(councils, BASINS, embed_ambiguous)
    assert routing == {}, "equidistant placement must abstain, not pick a basin"


def test_place_query_routes_to_nearest_basin():
    assert place_query("design the api surface", BASINS, _embed) == "b00"
    assert place_query("refactor the auth module", BASINS, _embed) == "b01"
    assert place_query("naming the product", BASINS, _embed) == "b02"


def test_non_dict_basins_filtered_not_crashed():
    """Shape guard (#304 sibling): a corrupt/clobbered topics.json can hand
    compute_basin_routing / place_query a `basins` list whose ENTRIES are
    non-dicts. Every access is `b.get(...)`, so without a filter this crashed
    the `consolidate` CLI verb (which, unlike `ask`, does NOT wrap the call in
    try/except) with `AttributeError: 'str' object has no attribute 'get'`. Both
    must filter to dicts at the entry and degrade — and a valid dict basin mixed
    into the junk must still work. Mutation: drop the
    `[b for b in basins if isinstance(b, dict)]` filter → these raise."""
    corrupt = ["b00", 123, None, *BASINS]  # non-dict junk + the valid basins
    councils = [_c("c1", "design the api surface", "claude"),
                _c("c2", "the api shape question", "claude")]
    # Mixed list: no crash, and the valid basins still tally / place.
    routing = compute_basin_routing(councils, corrupt, _embed)
    assert isinstance(routing, dict) and routing.get("b00", {}).get("winner") == "claude"
    assert place_query("design the api surface", corrupt, _embed) == "b00"
    # All-junk basins → graceful empty/None, never a crash.
    assert compute_basin_routing(councils, ["x", 1, None], _embed) == {}
    assert place_query("anything", ["x", 1, None], _embed) is None


def test_place_query_out_of_domain_returns_none():
    # Embeds to the zero vector → no basin clears the match floor → kNN handles it.
    assert place_query("translate this paragraph", BASINS, _embed) is None


def test_place_query_ambiguous_returns_none():
    # Equidistant from b00 and b01 (45°) → top1−top2 below margin → abstain.
    assert place_query("x", BASINS, lambda t: [0.707, 0.707, 0.0]) is None


def test_place_query_agrees_with_tally_placement():
    # The ask-side placement MUST use the same gates as the tally builder, else
    # a query routes to a basin the consolidation never tallied. A council and a
    # later query with the same text must land in the same basin.
    councils = [_c("c1", "the api decision", "claude"), _c("c2", "an api question", "claude")]
    routing = compute_basin_routing(councils, BASINS, _embed)
    placed = place_query("a fresh api question", BASINS, _embed)
    assert placed == "b00"
    assert placed in routing  # the basin the query lands in HAS a winner tally
    assert routing[placed]["winner"] == "claude"


def test_recency_weighting_favors_newer_winner():
    # Same basin, a recent codex flip outweighs older claude wins via the
    # recency half-life. Old claude (far past) is heavily decayed; the recent
    # codex councils dominate.
    councils = [
        _c("c1", "api one", "claude", when="2026-01-01T00:00:00+00:00"),  # ~5 months old
        _c("c2", "api two", "claude", when="2026-01-02T00:00:00+00:00"),
        _c("c3", "api three", "codex", when="2026-06-01T00:00:00+00:00"),  # newest = weight 1.0
        _c("c4", "api four", "codex", when="2026-06-01T00:00:00+00:00"),
    ]
    routing = compute_basin_routing(councils, BASINS, _embed)
    # By raw count it's 2-2; recency (30d half-life over ~5 months) decays the
    # old claude wins to near-zero, so the recent codex pair wins.
    assert routing["b00"]["winner"] == "codex"
    assert routing["b00"]["count"] == 4


# ── load_topics_basins: the single shape-guarded topics.json reader ──────────
# Unifies what ask._try_cortex_route / consolidate_via_lens_basins /
# launchpad_data._load_topics_basins / milestones._count_basins each open-coded
# (one with no top-level shape guard). A valid-JSON-wrong-shape topics.json must
# degrade to [] for EVERY reader, not crash one and mis-route another.
import json as _json

import pytest as _pytest

from trinity_local.lens_routing import load_topics_basins as _load_topics_basins


@_pytest.fixture
def _topics(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
    import trinity_local.lens_routing as lr

    monkeypatch.setattr(lr, "_TOPICS_BASINS_CACHE", None)  # isolate the module cache

    def _write(obj):
        from trinity_local.state_paths import topics_path

        p = topics_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")
        return p

    return _write


def test_load_topics_basins_happy_path(_topics):
    _topics({"basins": [{"id": "b00"}, {"id": "b01"}]})
    assert _load_topics_basins() == [{"id": "b00"}, {"id": "b01"}]


def test_load_topics_basins_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "nope"))
    import trinity_local.lens_routing as lr

    monkeypatch.setattr(lr, "_TOPICS_BASINS_CACHE", None)
    assert _load_topics_basins() == []


def test_load_topics_basins_guards_wrong_shapes(_topics):
    # The exact failure ask.py lacked: valid JSON, wrong type → [] not a crash.
    for bad in ('{"basins": "a string"}', '{"basins": 123}', "[1,2,3]", '"just a string"', "not json"):
        _topics(bad)
        assert _load_topics_basins() == [], f"wrong-shape {bad!r} must degrade to []"


def test_load_topics_basins_drops_non_dict_entries(_topics):
    _topics({"basins": [{"id": "b00"}, "b01", 42, None, {"id": "b02"}]})
    assert _load_topics_basins() == [{"id": "b00"}, {"id": "b02"}]
