"""Guards for the dissent-outcome instrument (`trust --dissent`).

Founder framing 2026-07-25: "if a model continuously differs and is overruled that
means it's not good. however, if it differs and it is merged, that means it has
something smart to say." Two rates per model — upheld, and grafted-when-it-lost.

Every guard below pins a decision that was got WRONG first and corrected:
  * `resolution` names WHICH SIDE survives, not whether the claim is true. Reading it
    as claim-polarity made a whole probe measure nothing.
  * a raw `==` on provider slugs counts chatgpt-vs-codex (one lab) as disagreement —
    that produced a 54% "model effect" that was 38 points aliasing, reported to the
    founder and retracted.
  * disputes the chairman left 'unresolved' are not evidence about either side.
  * a rate over a handful of disputes is not a finding.
"""
from __future__ import annotations

from trinity_local.dissent_outcome import (
    MIN_DISPUTES,
    DissentRecord,
    _side_that_survived,
    compute,
    wilson,
)


def _claim(fors, against, resolution):
    return {"claim": "c", "providers_for": fors, "providers_against": against,
            "resolution": resolution}


def _row(claims, grafts=()):
    return {"routing_label": {"disagreed_claims": list(claims), "grafts": list(grafts)}}


class TestSideResolution:
    def test_winner_is_read_from_the_named_side_not_the_prose(self):
        """`resolution` names a PROVIDER. If the named provider argued FOR, the FOR
        side won — regardless of words like 'survives' appearing anywhere."""
        w, l = _side_that_survived(_claim(["claude"], ["codex"], "claude survives, partially"))
        assert w == ["claude"] and l == ["codex"]
        w, l = _side_that_survived(_claim(["claude"], ["codex"], "codex survives — claude conceded"))
        assert w == ["codex"] and l == ["claude"], (
            "the side that WINS is the one the resolution names, even when the other "
            "side's slug appears later in the sentence"
        )

    def test_brand_names_resolve_to_their_slug(self):
        """The chairman writes 'GPT' and 'Gemini' in prose, not the dispatch slug."""
        w, _ = _side_that_survived(_claim(["codex"], ["antigravity"], "gpt survives on the evidence"))
        assert w == ["codex"]
        w, _ = _side_that_survived(_claim(["codex"], ["antigravity"], "gemini survives here"))
        assert w == ["antigravity"]

    def test_capture_slugs_are_canonicalized(self):
        """Web-capture councils record chatgpt/claude_ai/gemini; CLI records
        codex/claude/antigravity. Same labs — a raw compare invented a 54% effect."""
        w, l = _side_that_survived(_claim(["claude_ai"], ["chatgpt"], "claude_ai survives"))
        assert w == ["claude"] and l == ["codex"]

    def test_unresolved_disputes_are_skipped_even_when_a_provider_is_NAMED(self):
        """An undecided dispute is not evidence about either side.

        The fixture MUST name a provider inside the unresolved text. The first version
        of this guard used "unresolved — neither settles it", which names nobody, so
        the abstain-when-unidentifiable path returned None on its own and the guard
        passed with the unresolved check DELETED — vacuous. Mutation-proven with this
        fixture: removing the check resolves this to claude and reds."""
        entry = _claim(["claude"], ["codex"],
                       "unresolved — claude and codex each hold part of it")
        assert _side_that_survived(entry) is None
        assert _side_that_survived(_claim(["claude"], [], "claude survives unopposed")) is None
        assert _side_that_survived(_claim(["claude"], ["codex"], "")) is None

    def test_unidentifiable_side_abstains_rather_than_guessing(self):
        w = _side_that_survived(_claim(["claude"], ["codex"], "the stronger argument prevails"))
        assert w is None, "naming no provider must abstain, not default to a side"


class TestCompute:
    def test_upheld_and_overruled_are_counted_per_side(self):
        recs = compute([_row([_claim(["claude"], ["codex"], "claude survives")])])
        assert recs["claude"].upheld == 1 and recs["claude"].overruled == 0
        assert recs["codex"].overruled == 1 and recs["codex"].upheld == 0

    def test_graft_is_attributed_PER_CLAIM_not_per_council(self):
        """The bug this replaces: `grafted_from` was a council-wide set applied inside
        the per-claim loop, so ONE graft credited EVERY claim that provider lost in the
        same council. Two losses and one graft scored two grafts, inflating every
        published rate. Found 2026-07-28 by an independent GPT-5.6 review.

        Fixture is discriminating: codex loses TWO claims and is grafted on ONE."""
        row = _row([_claim(["claude"], ["codex"], "claude survives"),
                    _claim(["claude"], ["codex"], "claude survives")],
                   grafts=[{"claim": "A", "from": "codex"}])
        # The disputed claims here are both "c" (from _claim), so the graft on "A"
        # matches neither — the council-wide bug would have scored 2.
        rec = compute([row])["codex"]
        assert rec.overruled == 2
        assert rec.grafted_when_overruled == 0, (
            "a graft whose claim text matches no disputed claim must credit NOTHING; "
            "the old council-wide join would have credited both losses"
        )

    def test_graft_matching_the_claim_text_does_credit_it(self):
        """The positive half — otherwise the fix above could be 'never credit anything'."""
        entry = {"claim": "the router must enable IPv6", "providers_for": ["claude"],
                 "providers_against": ["codex"], "resolution": "claude survives"}
        row = _row([entry], grafts=[{"claim": "the router must enable IPv6", "from": "codex"}])
        assert compute([row])["codex"].grafted_when_overruled == 1

    def test_graft_source_is_canonicalized(self):
        """A web-capture graft (chatgpt) must credit the CLI slug (codex)."""
        entry = {"claim": "the router must enable IPv6", "providers_for": ["claude"],
                 "providers_against": ["codex"], "resolution": "claude survives"}
        row = _row([entry], grafts=[{"claim": "the router must enable IPv6", "from": "chatgpt"}])
        assert compute([row])["codex"].grafted_when_overruled == 1

    def test_the_published_rate_is_WITHDRAWN_not_zero(self):
        """Both attributions are wrong (council-wide overcounts; per-claim matches 0.4%
        of real grafts), and the behavioural test was already net +0. A withdrawn metric
        must emit None with a reason, never a 0% that reads as a measurement."""
        from trinity_local.dissent_outcome import GRAFT_WHEN_LOST_IS_NOT_COMPUTABLE
        d = DissentRecord("codex", upheld=30, overruled=10, grafted_when_overruled=4).to_dict()
        assert d["graft_when_lost_rate"] is None
        assert d["graft_when_lost_uncomputable"] == GRAFT_WHEN_LOST_IS_NOT_COMPUTABLE
        assert "withdrawn" in d["graft_when_lost_uncomputable"]

    def test_a_model_with_zero_losses_does_not_crash_the_readout(self):
        """`trust --dissent` raised TypeError formatting None as a percentage for any
        trustworthy model that never lost. Found 2026-07-28 by the same review."""
        d = DissentRecord("codex", upheld=25, overruled=0).to_dict()
        assert d["trustworthy"] is True and d["graft_when_lost_rate"] is None

    def test_one_sided_claims_are_not_disputes(self):
        """A claim nobody opposed is not evidence that its holder won an argument."""
        assert _side_that_survived(_claim(["claude"], [], "claude survives unopposed")) is None

    def test_grafted_never_exceeds_overruled(self):
        """An invariant: you cannot be 'grafted when overruled' more often than you
        were overruled. A rate above 1.0 would be a silent double-count."""
        rows = [_row([_claim(["claude"], ["codex"], "claude survives")],
                     grafts=[{"claim": "x", "from": "codex"}]) for _ in range(5)]
        rec = compute(rows)["codex"]
        assert rec.grafted_when_overruled <= rec.overruled


class TestAbstention:
    def test_thin_models_get_no_verdict(self):
        rec = DissentRecord("qwen27", upheld=2, overruled=2)
        assert rec.disputes < MIN_DISPUTES and not rec.trustworthy

    def test_floor_is_the_publish_gate(self):
        rec = DissentRecord("codex", upheld=MIN_DISPUTES, overruled=0)
        assert rec.trustworthy

    def test_ci_widens_as_n_shrinks(self):
        """A guard against reporting a bare rate: 3/4 and 300/400 are both 75% and
        only one of them means anything."""
        lo_small, hi_small = wilson(3, 4)
        lo_big, hi_big = wilson(300, 400)
        assert (hi_small - lo_small) > (hi_big - lo_big) * 3


class TestNegationAwareSideResolution:
    """`resolution` names which side survives, in free-form prose. The original reader
    took the FIRST provider mentioned, which is right only when the survivor is named
    first. Found 2026-07-28 by an independent GPT-5.6 review of this module; the
    original guard missed it because its fixture put the survivor first, so
    first-mention and truth happened to coincide."""

    def test_the_negated_provider_does_not_win(self):
        w, l = _side_that_survived(_claim(
            ["codex"], ["claude"],
            "codex's claim does not survive; claude's side survives because the evidence holds"))
        assert w == ["claude"] and l == ["codex"], (
            "first-mention-wins returns codex here, which is the shipped bug"
        )

    def test_the_original_survivor_first_case_still_works(self):
        w, l = _side_that_survived(_claim(["claude"], ["codex"], "codex survives — claude conceded"))
        assert w == ["codex"] and l == ["claude"]

    def test_both_sides_claimed_to_survive_abstains(self):
        """Unreadable prose gets the same treatment as 'unresolved': no verdict."""
        assert _side_that_survived(_claim(
            ["claude"], ["codex"], "claude survives and codex survives too")) is None

    def test_a_mention_with_no_verdict_verb_is_not_a_vote(self):
        """Naming a provider is not deciding for it."""
        assert _side_that_survived(_claim(
            ["claude"], ["codex"], "the stronger argument prevails on balance")) is None

    def test_only_negations_resolve_to_the_unnegated_side(self):
        w, l = _side_that_survived(_claim(
            ["claude"], ["codex"], "codex does not survive the evidence"))
        assert w == ["claude"] and l == ["codex"]
