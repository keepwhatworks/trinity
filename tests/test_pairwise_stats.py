"""Host-owned tests for the luna-written pairwise leaf module (the council's
delegation rule: the model that writes a computation never writes its test).
Values below are textbook/hand-computed, not derived from the implementation."""
from __future__ import annotations

import pytest

from trinity_local.evals.pairwise_stats import (
    PAIRWISE_JUDGE_PROMPT,
    kendall_tau,
    parse_pairwise_verdict,
    wilson_ci,
    win_rate,
)


class TestWilsonCI:
    def test_textbook_value(self):
        lo, hi = wilson_ci(8, 10)
        assert lo == pytest.approx(0.4901, abs=0.01)
        assert hi == pytest.approx(0.9433, abs=0.01)

    def test_zero_n_is_maximally_uncertain(self):
        assert wilson_ci(0, 0) == (0.0, 1.0)

    def test_ci_contains_the_point_and_stays_in_unit(self):
        for wins, n in ((0, 5), (2.5, 5), (5, 5), (9.5, 19)):
            lo, hi = wilson_ci(wins, n)
            assert 0.0 <= lo <= wins / n <= hi <= 1.0

    def test_fifty_percent_ci_includes_half(self):
        lo, hi = wilson_ci(9.5, 19)
        assert lo < 0.5 < hi  # the structural no-claim condition


class TestKendallTau:
    def test_identical_is_one(self):
        assert kendall_tau(list("abcde"), list("abcde")) == pytest.approx(1.0)

    def test_reversed_is_minus_one(self):
        assert kendall_tau(list("abcde"), list("edcba")) == pytest.approx(-1.0)

    def test_single_adjacent_swap_on_five(self):
        # 1 discordant of 10 pairs -> (9-1)/10 = 0.8 — exactly the PASS floor
        assert kendall_tau(list("abcde"), list("bacde")) == pytest.approx(0.8)

    def test_label_mismatch_raises(self):
        with pytest.raises(ValueError):
            kendall_tau(["a", "b"], ["a", "c"])


class TestWinRate:
    def test_ties_count_half(self):
        assert win_rate(3, 2, 5) == pytest.approx(0.4)

    def test_empty_is_zero(self):
        assert win_rate(0, 0, 0) == 0.0


class TestJudgePromptNeutrality:
    def test_placeholders_present(self):
        for ph in ("{prompt}", "{context_fragment}", "{answer_1}", "{answer_2}"):
            assert ph in PAIRWISE_JUDGE_PROMPT

    def test_no_provenance_leak(self):
        """The codex council finding: the judge must not be told which answer
        was rejected — the label itself would bias the verdict independent of
        position. Mutation: add 'rejected' to the template → RED."""
        low = PAIRWISE_JUDGE_PROMPT.lower()
        for leak in ("reject", "original answer", "gold", "correction",
                     "model-generated", "preferred", "better answer was"):
            assert leak not in low, f"provenance leak in judge prompt: {leak!r}"

    def test_renders_without_keyerror(self):
        PAIRWISE_JUDGE_PROMPT.format(prompt="p", context_fragment="c",
                                     answer_1="a", answer_2="b")


class TestParseVerdict:
    def test_clean_json(self):
        assert parse_pairwise_verdict('{"winner": "1", "reason": "x"}') == ("1", "x")

    def test_fenced_and_noisy(self):
        raw = 'Sure. Here is my verdict:\n```json\n{"winner": "2", "reason": "b wins"}\n```\nthanks'
        w, r = parse_pairwise_verdict(raw)
        assert w == "2"

    def test_last_block_wins(self):
        raw = '{"winner": "1", "reason": "draft"} final: {"winner": "tie", "reason": "close"}'
        assert parse_pairwise_verdict(raw)[0] == "tie"

    def test_garbage_degrades_to_tie(self):
        w, r = parse_pairwise_verdict("no json here at all")
        assert w == "tie" and "unparseable" in r

    def test_unknown_winner_normalizes_to_tie(self):
        w, _ = parse_pairwise_verdict('{"winner": "answer_1", "reason": "x"}')
        assert w in ("1", "tie")  # normalization contract: never a raw label
