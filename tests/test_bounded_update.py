"""Bounded trust-region lens update (SkillOpt edit budget; generalizes #295 clobber).

Deterministic fake embedder: an item's leading token is its "concept" — same concept →
identical vector (cosine 1.0, matched as the same tension); different → orthogonal
(cosine 0.0, not matched). So "A first" and "A reworded" are the same tension; "B" is not.
"""
from __future__ import annotations

from trinity_local.me.bounded_update import bounded_update


_BASIS: dict[str, list[float]] = {}


def fake_embed(text: str) -> list[float]:
    concept = (text.split() or [""])[0]
    if concept not in _BASIS:
        v = [0.0] * 64
        v[len(_BASIS)] = 1.0
        _BASIS[concept] = v
    return _BASIS[concept]


def up(prior, cand, **kw):
    return bounded_update(prior, cand, key=lambda s: s, embed_fn=fake_embed, **kw)


def test_cold_start_adopts_candidate():
    merged, r = up([], ["A new", "B new"])
    assert merged == ["A new", "B new"] and r.reason == "cold-start" and r.added == 2


def test_stable_core_kept_as_prior_wording_no_churn():
    prior = ["A core tension", "B tension", "C tension"]
    cand = ["A reworded differently", "B said another way", "C restated"]  # same concepts
    merged, r = up(prior, cand)
    assert merged == prior, "matched tensions must keep PRIOR wording (no churn)"
    assert r.kept == 3 and r.added == 0 and r.removed == 0 and not r.refused


def test_bounded_add_admits_at_most_budget_new():
    prior = ["A x", "B x"]
    cand = ["A x", "B x", "N1 new", "N2 new", "N3 new"]  # 3 genuinely new
    merged, r = up(prior, cand, budget=2)
    assert merged == ["A x", "B x", "N1 new", "N2 new"], "only `budget` new admitted"
    assert r.added == 2 and r.removed == 0


def test_bounded_remove_drops_at_most_budget_per_cycle():
    prior = ["A x", "B x", "C x", "D x"]
    cand = ["A x"]                      # supports A; drops B,C,D (3 dropped)
    merged, r = up(prior, cand, budget=2)
    # at most 2 removed/cycle, from the WEAK (back) end → C,D removed, B survives
    assert merged == ["A x", "B x"], f"should drop only budget=2 from the weak end: {merged}"
    assert r.removed == 2 and r.kept == 1


def test_degenerate_empty_candidate_is_refused_keeps_prior():
    """The #295 clobber guard generalized: an empty build can't wipe the lens."""
    prior = ["A x", "B x", "C x"]
    merged, r = up(prior, [])
    assert merged == prior and r.refused and r.removed == 0


def test_total_rewrite_with_no_overlap_is_refused():
    prior = ["A x", "B x"]
    cand = ["X all new", "Y all new"]   # shares nothing with prior = wholesale replacement
    merged, r = up(prior, cand)
    assert merged == prior and r.refused, "a no-overlap rewrite must be refused, not applied"


def test_protected_tension_is_never_removed():
    prior = ["A x", "B x", "C x"]
    cand = ["B x", "C x"]               # drops A
    merged, r = up(prior, cand, budget=5, protected=lambda s: s.startswith("A"))
    assert "A x" in merged, "a protected (slow-update/core) tension must never be dropped"
    assert r.removed == 0


def test_budget_zero_freezes_adds_and_removes():
    prior = ["A x", "B x"]
    cand = ["A x", "B x", "N new"]
    merged, r = up(prior, cand, budget=0)
    assert merged == prior and r.added == 0 and r.removed == 0


def test_combined_add_and_remove_within_budget():
    prior = ["A x", "B x", "C x"]
    cand = ["A x", "N1 new", "N2 new", "N3 new"]   # keeps A; drops B,C; adds N1,N2,N3
    merged, r = up(prior, cand, budget=2)
    assert r.kept == 1 and r.added == 2 and r.removed == 2
    assert merged == ["A x", "N1 new", "N2 new"], merged
