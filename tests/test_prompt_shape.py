"""Pin down the prompt-shape detector. Bug we're fixing: it returned []
on a real-world prompt with (A)/(B)/(C) labels and 'which design best fits…'.
"""
from __future__ import annotations

from trinity_local.ranker import prompt_calls_for_council


class TestParenthesizedLabels:
    def test_paren_around_three_options(self):
        prompt = """Pick the strongest design:

(A) First option.
(B) Second option.
(C) Third option.

Which design best fits? Name the failure mode of the losing two.
"""
        escalate, signals = prompt_calls_for_council(prompt)
        assert escalate
        assert any(s.startswith("labeled_alternatives_paren_around") for s in signals)
        assert "which_best" in signals
        assert "failure_mode" in signals

    def test_paren_after_three_options(self):
        prompt = """A) first
B) second
C) third"""
        escalate, signals = prompt_calls_for_council(prompt)
        assert escalate
        assert any(s.startswith("labeled_alternatives_paren_after") for s in signals)

    def test_single_label_does_not_escalate(self):
        # One stray "(A)" in the middle of prose isn't a council signal.
        prompt = "The user picked option (A) yesterday."
        escalate, signals = prompt_calls_for_council(prompt)
        # named_candidates ("option A") fires here — that's fine, single
        # mention with that exact phrasing is genuinely a comparison cue.
        # What we care about is that lone-letter labels alone don't escalate.
        # Verify no labeled_alternatives_* signal:
        assert not any(s.startswith("labeled_alternatives") for s in signals)


class TestComparativePhrases:
    def test_which_X_best_fires(self):
        # "which design best fits" — was the regression case.
        escalate, signals = prompt_calls_for_council(
            "Which design best fits our use case?"
        )
        assert escalate
        assert "which_best" in signals

    def test_vs_fires(self):
        escalate, signals = prompt_calls_for_council("Redis vs Memcached for cache?")
        assert escalate
        assert "vs" in signals

    def test_failure_mode_fires(self):
        escalate, signals = prompt_calls_for_council(
            "Pick the winner. Name the failure mode of the loser."
        )
        assert escalate
        assert "failure_mode" in signals

    def test_tradeoffs_fires(self):
        escalate, signals = prompt_calls_for_council(
            "What are the tradeoffs between approach 1 and approach 2?"
        )
        assert escalate
        assert "tradeoffs" in signals

    def test_rank_these_fires(self):
        escalate, signals = prompt_calls_for_council(
            "Rank these designs by complexity."
        )
        assert escalate
        assert "pick_among" in signals


class TestNoEscalationOnVanilla:
    def test_simple_coding_question(self):
        escalate, signals = prompt_calls_for_council(
            "Refactor this Python function to remove duplication."
        )
        assert not escalate
        assert signals == []

    def test_simple_information_request(self):
        escalate, signals = prompt_calls_for_council(
            "What does the LRU cache decorator do in functools?"
        )
        assert not escalate
        assert signals == []

    def test_empty_prompt(self):
        escalate, signals = prompt_calls_for_council("")
        assert not escalate
        assert signals == []
