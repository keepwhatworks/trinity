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


# ─── Model-churn honesty (council_39e25084ea339099, 2026-07-04) ────────────
# Design B + effective-n floor: stale-model episodes decay ×0.5; a basin whose
# margin survives on dead evidence must refuse to route.

def _cm(cid, task, winner, model, when="2026-06-01T00:00:00+00:00"):
    d = _c(cid, task, winner, when=when)
    d["winner_model"] = model
    return d


def test_stale_model_episodes_are_half_weighted():
    """3 stale codex wins (gpt-5.3 era) vs 2 fresh claude wins: raw count says
    codex, churn-decayed weight says claude — the new model's live evidence
    outranks the old model's ghost."""
    from trinity_local.lens_routing import compute_basin_routing
    councils = [
        _cm("c1", "api question one", "codex", "gpt-5.3-codex"),
        _cm("c2", "api question two", "codex", "gpt-5.3-codex"),
        _cm("c3", "api question three", "codex", "gpt-5.3-codex"),
        _cm("c4", "api question four", "claude", "claude-opus-4-8"),
        _cm("c5", "api question five", "claude", "claude-opus-4-8"),
    ]
    current = {"codex": "gpt-5.5", "claude": "claude-opus-4-8"}
    r = compute_basin_routing(councils, BASINS, _embed, current_models=current)
    b = r["b00"]
    assert b["winner"] == "claude", b   # 2.0 fresh > 1.5 decayed
    assert b["fresh_n"] == 2 and b["stale_n"] == 3
    assert b["models"] == {"gpt-5.3-codex": 3, "claude-opus-4-8": 2}
    assert abs(b["effective_n"] - 3.5) < 1e-6


def test_all_stale_basin_keeps_margin_but_loses_effective_n():
    """THE council's key failure mode: uniform stale wins preserve a perfect
    margin — margin-only gating would route confidently on a dead model.
    effective_n must expose it and pick_routes must refuse it."""
    from trinity_local.lens_routing import compute_basin_routing, pick_routes
    councils = [
        _cm(f"c{i}", f"api question {i}", "codex", "gpt-5.3-codex")
        for i in range(5)
    ]
    r = compute_basin_routing(councils, BASINS, _embed,
                              current_models={"codex": "gpt-5.5"})
    b = r["b00"]
    assert b["margin"] == 1.0            # margin survives…
    assert b["effective_n"] == 2.5       # …but the evidence mass halved
    assert not pick_routes(b), (
        "a basin whose wins are ALL from a superseded model routed anyway — "
        "the confidently-stale pick the council's effective-n floor exists for"
    )


def test_none_winner_model_treated_as_stale():
    from trinity_local.lens_routing import compute_basin_routing
    councils = [
        _cm("c1", "api question", "claude", None),
        _cm("c2", "api question again", "claude", "claude-opus-4-8"),
        _cm("c3", "api question thrice", "claude", "claude-opus-4-8"),
    ]
    r = compute_basin_routing(councils, BASINS, _embed,
                              current_models={"claude": "claude-opus-4-8"})
    b = r["b00"]
    assert b["stale_n"] == 1 and b["fresh_n"] == 2
    assert abs(b["effective_n"] - 2.5) < 1e-6
    assert b["models"].get("unknown") == 1


def test_unknown_current_model_applies_no_decay():
    """A provider absent from current_models can't be staleness-assessed —
    pre-churn behavior (no decay), documented."""
    from trinity_local.lens_routing import compute_basin_routing
    councils = [
        _cm(f"c{i}", f"api question {i}", "antigravity", "Gemini 2 Pro")
        for i in range(3)
    ]
    r = compute_basin_routing(councils, BASINS, _embed, current_models={})
    assert abs(r["b00"]["effective_n"] - 3.0) < 1e-6


def test_pick_routes_legacy_entry_falls_back_to_margin_gate():
    from trinity_local.lens_routing import pick_routes
    assert pick_routes({"winner": "claude", "margin": 0.4})          # legacy: no effective_n
    assert not pick_routes({"winner": "claude", "margin": 0.05})     # margin floor still bites
    assert not pick_routes({"winner": "claude", "margin": 0.4, "effective_n": 1.0})
    assert pick_routes({"winner": "claude", "margin": 0.4, "effective_n": 3.0})


def test_load_council_records_reads_created_at_from_outcome_not_routing_label(monkeypatch):
    """Surface-binding guard (2026-07-17, workflow finding): recency decay was
    silently DEAD in production (measured 634/634 councils) because
    _load_council_records read created_at from `routing_label`, which
    CouncilRoutingLabel.to_dict() never emits — so every age was 0 and
    HALF_LIFE_DAYS was inert. The real date lives on the outcome (oc.created_at).
    Tests passed only because compute_basin_routing's fixtures inject created_at
    directly, never exercising this disk binding. MUTATION: revert the source to
    `(r.get("routing_label") or {}).get("created_at")` and this reds (created_at
    comes back '')."""
    from types import SimpleNamespace
    from trinity_local import lens_routing

    real_date = "2026-05-01T12:00:00+00:00"
    # _scan_outcomes yields the record WITHOUT created_at (routing_label lacks it,
    # exactly like production); the outcome object carries the true date.
    monkeypatch.setattr(
        "trinity_local.personal_routing._scan_outcomes",
        lambda: ([{
            "council_run_id": "council_x",
            "chairman_winner": "claude",
            "routing_label": {},  # no created_at — the production shape
            "substantive_members": 2,
            "distinct_substantive_providers": 2,
        }], {}),
    )
    monkeypatch.setattr(
        "trinity_local.council_runtime.load_council_outcome",
        lambda cid: SimpleNamespace(
            created_at=real_date, metadata={"task_text": "t"},
            winner_model="claude-x", member_results=[]),
    )
    recs = lens_routing._load_council_records()
    assert len(recs) == 1
    assert recs[0]["created_at"] == real_date, (
        "created_at must come from the outcome, not the routing_label that "
        f"never carries it (got {recs[0]['created_at']!r})"
    )
