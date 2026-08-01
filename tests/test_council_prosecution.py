"""Slice (c) pure core: the cross-provider prosecution decomposition. No dispatch,
no LLM — the assignment logic and the refutation prompt, fully unit-testable.

The invariant that matters: a refuter is a provider that did NOT make the claim
(cross-provider adversary), and a claim with no opposing side is never
prosecuted. That is the moat — one model cross-examining itself is the
single-rich-persona agent Gauntlet beat on 96% of papers.
"""
from __future__ import annotations

from trinity_local.council_prosecution import (
    FLAG_ENV,
    plan_prosecution,
    prosecution_enabled,
    render_refutation_prompt,
)

MEMBERS = ["claude", "codex", "antigravity"]


def test_dormant_by_default(monkeypatch):
    monkeypatch.delenv(FLAG_ENV, raising=False)
    assert prosecution_enabled() is False
    for on in ("1", "true", "YES", "on"):
        monkeypatch.setenv(FLAG_ENV, on)
        assert prosecution_enabled() is True
    monkeypatch.setenv(FLAG_ENV, "0")
    assert prosecution_enabled() is False


def test_refuters_exclude_the_claim_makers():
    dc = [{"claim": "Cache at the edge.", "providers_for": ["claude"],
           "providers_against": ["codex"], "why_matters": "latency"}]
    plan = plan_prosecution(dc, MEMBERS)
    assert len(plan) == 1
    a = plan[0]
    assert a.makers == ["claude"]
    assert "claude" not in a.refuters  # the maker never cross-examines its own claim
    assert set(a.refuters) == {"codex", "antigravity"}
    assert a.why_matters == "latency"


def test_uncontested_claim_is_skipped():
    """Every member argued for it → no cross-provider adversary → not prosecuted."""
    dc = [{"claim": "Water is wet.", "providers_for": MEMBERS}]
    assert plan_prosecution(dc, MEMBERS) == []


def test_converged_council_has_nothing_to_prosecute():
    assert plan_prosecution([], MEMBERS) == []
    assert plan_prosecution(None, MEMBERS) == []


def test_maker_matching_is_case_insensitive():
    dc = [{"claim": "X", "providers_for": ["Claude"]}]  # capitalized in the label
    a = plan_prosecution(dc, MEMBERS)[0]
    assert "claude" not in a.refuters  # normalized, so the maker is still excluded


def test_malformed_entries_are_dropped_not_crashed():
    dc = [{"claim": ""}, "not a dict", {"providers_for": ["claude"]}, 42]
    assert plan_prosecution(dc, MEMBERS) == []


def test_refutation_prompt_is_adversarial_and_targeted():
    a = plan_prosecution([{"claim": "Origin caching is simpler.",
                           "providers_for": ["codex"]}], MEMBERS)[0]
    p = render_refutation_prompt("Edge or origin caching?", a, maker_evidence="Codex said origin.")
    assert "BREAK" in p                       # adversarial directive
    assert "Origin caching is simpler." in p  # the specific claim
    assert "codex" in p                        # names who to cross-examine
    assert "Codex said origin." in p           # the evidence is included
    assert "TRUE, PARTLY TRUE, or FALSE" in p  # forces a verdict, not a summary
    # small + in-distribution: one claim, not the whole council transcript
    assert len(p) < 1200
