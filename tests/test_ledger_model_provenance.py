"""The tally must say how the model strings it keys on were obtained.

Founder's report, 2026-08-17: Trinity recorded `providers.<name>.model` from
config as the model that answered, and for a provider invoked without --model
that is a label nobody verified. The ask was explicit — a `model_source` field
"would at least let ledger consumers tell the difference rather than trusting
every row equally".

The fix took three attempts to reach that consumer, and each miss is instructive:

  1. written to RUN STATE metadata, which the ledger never reads;
  2. moved onto the member record, but `load_disagreements` extracted only
     `effort` and `model` and dropped the source on the floor;
  3. carried through onto the pattern and counted on the aggregate — here.

Measured the moment it worked: all 168 resolved claims report `unknown`, i.e.
NOT ONE per-model rate in this ledger rests on a verified model string.

Mutation-proven: dropping `model_sources=model_sources` from the pattern, or the
`model_provenance` key from the aggregate, REDs these.
"""
from __future__ import annotations

from trinity_local.disagreement_ledger import DisagreementPattern, aggregate_tally


def _pattern(claim_id, sources):
    return DisagreementPattern(
        claim_id=claim_id, council_id="c1", at="2026-08-17T00:00:00+00:00",
        claim="a claim", why_matters="", providers_for=["anthropic"],
        providers_against=["google"], chairman_winner="anthropic",
        models_for=["claude · opus · 5"], models_against=["google · pro · 3.1"],
        model_sources=sources,
    )


def test_the_aggregate_reports_how_each_model_string_was_obtained():
    pats = [_pattern("a#0", {"claude": "echoed"}), _pattern("a#1", {"claude": "pinned"})]
    agg = aggregate_tally(pats, {"a#0": "followed", "a#1": "contradicted"})
    assert agg["model_provenance"] == {"echoed": 1, "pinned": 1}


def test_a_council_recorded_before_capture_is_UNKNOWN_not_assumed():
    """The distinction is the whole point. `assumed` is a claim about how the
    model was obtained; these rows have no such claim, and inventing one would
    be the defect this fixes wearing a different hat."""
    agg = aggregate_tally([_pattern("a#0", {})], {"a#0": "followed"})
    assert agg["model_provenance"] == {"unknown": 1}
    assert "assumed" not in agg["model_provenance"]


def test_provenance_counts_only_the_claims_the_rates_rest_on():
    """Unresolved claims contribute nothing to a per-model rate, so they must
    contribute nothing to its provenance either."""
    pats = [_pattern("a#0", {"claude": "echoed"}), _pattern("a#1", {"claude": "echoed"})]
    agg = aggregate_tally(pats, {"a#0": "followed", "a#1": "unresolved"})
    assert agg["model_provenance"] == {"echoed": 1}


# A fourth test read the REAL ~/.trinity to assert the live ledger is entirely
# unverified. Removed: it mixed a real-home read with an isolated-home
# load_disagreements (so it failed for the wrong reason), and more importantly
# this repo's rule is that tests run ONLY under an isolated TRINITY_HOME — a
# test that touches the founder's real store is a defect whether or not it
# passes. The live number belongs in the residual ledger, where it is recorded:
# 168 of 168 resolved claims report `unknown`.


def test_load_disagreements_carries_the_source_off_the_member_record(tmp_path, monkeypatch):
    """The WIRING test, and the one that was missing.

    The tests above build a DisagreementPattern by hand, so deleting
    `model_sources=model_sources` at the construction site left them all green —
    decoration, the same shape caught an hour earlier on the member stamp. This
    one goes through the real reader against a real outcome-shaped file, which is
    also the test that would have caught the first fix writing to run state.
    """
    import json as _json

    from trinity_local.disagreement_ledger import load_disagreements

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    out = tmp_path / "council_outcomes"
    out.mkdir(parents=True)
    (out / "council_x.json").write_text(_json.dumps({
        "council_run_id": "council_x",
        "created_at": "2026-08-17T00:00:00+00:00",
        "metadata": {"task_text": "t"},
        "member_results": [
            {"provider": "codex", "model": "gpt-5.6-sol",
             "metadata": {"effort": "xhigh", "model_source": "echoed"}},
            {"provider": "claude", "model": "claude-opus-5",
             "metadata": {"effort": "high", "model_source": "assumed"}},
        ],
        "routing_label": {
            "winner": "codex",
            "disagreed_claims": [{"claim": "a contested claim",
                                  "providers_for": ["codex"],
                                  "providers_against": ["claude"]}],
        },
    }), encoding="utf-8")

    pats = [p for p in load_disagreements() if p.council_id == "council_x"]
    assert pats, "the synthetic council must load"
    assert pats[0].model_sources == {"codex": "echoed", "claude": "assumed"}, (
        "the source must survive the trip from member metadata to the pattern — "
        "dropping it is exactly how the first two versions of this fix failed")


def test_a_mixed_council_counts_toward_both_sources_and_says_the_denominator():
    """Caught while verifying end-to-end: with one echoed member and one assumed
    member, the counts sum to 2 for a SINGLE claim. Without the denominator a
    consumer reads that as two claims. The counts are per-distinct-source and
    must be compared against `resolved`, never against each other."""
    pat = _pattern("m#0", {"codex": "echoed", "claude": "assumed"})
    agg = aggregate_tally([pat], {"m#0": "followed"})
    assert agg["model_provenance"] == {"assumed": 1, "echoed": 1}
    assert agg["model_provenance_denominator"] == 1
    assert sum(agg["model_provenance"].values()) > agg["model_provenance_denominator"]
