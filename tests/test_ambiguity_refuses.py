"""When the evidence is ambiguous, refuse. Do not pick a winner.

Four defects found by an Astra audit on 2026-09-16, all in the shipped gate,
all the same shape: code faced with two possible readings picked one by an
incidental property — first in the document, biggest counter, whatever the slug
happened to say — and returned it as fact.

  verify.py       the FIRST object carrying a `votes` key won, so a reviewer
                  that illustrates the format before answering had its verdict
                  INVERTED and the real reasoning attached to the wrong vote.
  providers.py    the model with the most output tokens was "the one that
                  answered" — a terse answer beside a chatty helper recorded
                  the helper. This was itself a fix, shipped hours earlier, for
                  first-in-dict. A different guess, same shape.
  verify_rule.py  any unrecognised provider slug became its own lab, so three
                  aliases for one lab satisfied "three distinct labs" — which
                  inverts the single claim the product rests on.
  verify.py       two criteria could share an id, so one vote answered both and
                  a non-blocking opinion could decide a blocking gate.

The unifying repair is not a better heuristic. It is that ambiguity has no
answer to recover, and manufacturing one is how this codebase produces
plausible wrong results. Refusing costs a stamp or a re-run. Guessing costs a
verdict nobody can audit.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.providers import parse_claude_json
from trinity_local.verify import _parse_votes, _reject_duplicate_ids, Criterion
from trinity_local.verify_rule import Panel


class TestTwoVerdictsIsNoVerdict:
    def test_an_example_before_the_answer_no_longer_wins(self):
        t = 'Example: {"votes":{"c":"PASS"}} Final verdict: {"votes":{"c":"FAIL"},"why":"wrong"}'
        assert _parse_votes(t, ["c"]) is None, (
            "two disagreeing vote objects have no recoverable answer")

    def test_agreeing_duplicates_are_fine(self):
        """Refusal is for CONFLICT, not for repetition — they say the same thing."""
        t = '{"votes":{"c":"FAIL"}} and again {"votes":{"c":"FAIL"},"why":"x"}'
        votes, why = _parse_votes(t, ["c"])
        assert votes == {"c": False} and why == "x"

    def test_a_single_object_is_unaffected(self):
        votes, why = _parse_votes('{"votes":{"c":"FAIL"},"why":"b unguarded"}', ["c"])
        assert votes == {"c": False} and why == "b unguarded"

    def test_a_preamble_before_one_object_is_unaffected(self):
        assert _parse_votes('Here: {"votes":{"c":"PASS"},"why":""}', ["c"])[0] == {"c": True}


class TestAnAmbiguousAnsweringModelIsNotGuessed:
    def _mu(self, mu):
        return json.dumps({"result": "ok", "usage": {}, "modelUsage": mu})

    def test_two_producers_report_nothing(self):
        """A missing echo means "nothing to compare", and the substitution guard
        leaves the member alone. Uncertainty costs a stamp, not a member."""
        mu = {"claude-haiku-4-5": {"outputTokens": 100, "canonicalModel": "claude-haiku-4-5"},
              "claude-fable-5-1": {"outputTokens": 20, "canonicalModel": "claude-fable-5-1"}}
        assert parse_claude_json(self._mu(mu))["model"] is None

    def test_one_producer_beside_an_idle_helper_is_unambiguous(self):
        mu = {"claude-haiku-4-5": {"outputTokens": 0, "canonicalModel": "claude-haiku-4-5"},
              "claude-fable-5-1": {"outputTokens": 20, "canonicalModel": "claude-fable-5-1"}}
        assert parse_claude_json(self._mu(mu))["model"] == "claude-fable-5-1"

    def test_a_lone_model_without_counts_still_reports(self):
        assert parse_claude_json(self._mu({"claude-opus-5": {"canonicalModel": "claude-opus-5"}}))["model"] \
            == "claude-opus-5"

    def test_an_ambiguous_echo_does_not_drop_a_member(self):
        from trinity_local.providers import is_model_substitution
        mu = {"claude-haiku-4-5": {"outputTokens": 100, "canonicalModel": "claude-haiku-4-5"},
              "claude-fable-5-1": {"outputTokens": 20, "canonicalModel": "claude-fable-5-1"}}
        echo = parse_claude_json(self._mu(mu))["model"]
        assert is_model_substitution("claude-fable-5-1", echo) is False


class TestAliasesCannotManufactureIndependence:
    def test_three_aliases_for_one_lab_are_one_lab(self):
        p = Panel(ran=True, votes={"claude_a": True, "claude_b": True, "claude_c": True})
        assert len(p.labs()) == 1
        assert p.consensus_pass() is False, (
            "three seats pointed at one lab are not three independent readers")

    def test_the_real_trio_still_reaches_consensus(self):
        p = Panel(ran=True, votes={"claude": True, "codex": True, "antigravity": True})
        assert len(p.labs()) == 3 and p.consensus_pass() is True

    def test_one_unknown_alongside_two_known_labs_still_counts(self):
        """An unknown provider is a real vote from an undeclared lab, not a
        non-vote. It just cannot be numerous."""
        p = Panel(ran=True, votes={"claude": True, "codex": True, "mystery": True})
        assert len(p.labs()) == 3 and p.consensus_pass() is True

    def test_two_unknowns_do_not_add_up_to_two_labs(self):
        p = Panel(ran=True, votes={"claude": True, "m1": True, "m2": True})
        assert len(p.labs()) == 2 and p.consensus_pass() is False


class TestOneVotePerCriterion:
    def test_duplicate_ids_are_refused(self):
        with pytest.raises(ValueError, match="duplicate criterion id"):
            _reject_duplicate_ids([
                Criterion.from_dict({"id": "c", "kind": "judgment", "statement": "correctness",
                                     "blocking": True}),
                Criterion.from_dict({"id": "c", "kind": "judgment", "statement": "formatting",
                                     "blocking": False})])

    def test_distinct_ids_pass(self):
        _reject_duplicate_ids([
            Criterion.from_dict({"id": "a", "kind": "judgment", "statement": "x", "blocking": True}),
            Criterion.from_dict({"id": "b", "kind": "judgment", "statement": "y", "blocking": False})])

    def test_verify_itself_refuses_a_duplicated_block(self):
        import inspect
        from trinity_local import verify as V
        assert "_reject_duplicate_ids(criteria)" in inspect.getsource(V.verify), (
            "the check must run on the real entry point, not only as a helper")
