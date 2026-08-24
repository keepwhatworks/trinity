"""The core gate must not replace a live incumbent on a length-confounded ruler.

MEASURED 2026-08-24 (res_079), 400 held-out prompts, neural ruler:

    no core at all ......... 11,732 bits   <- strictly the best "core" available
    a single space ......... 12,215
    a session-limit error .. 12,597
    an OAuth error ......... 13,061
    the REAL 1,187-char core 13,213

Every candidate prices held-out text WORSE than no core, and shorter junk beats
longer truth every time. At matched length nothing remains: the real core loses
to its own word-shuffle by 381 bits and beats character-level gibberish by 1.0
bit in 13,000.

That is how an OAuth error became `core.md` on 2026-08-18 and a session-limit
notice replaced it on 2026-08-24 — both ADMITTED by a fully-working gate with a
400-text corpus, because both were short. The chairman reads core.md first on
every council.
"""
from __future__ import annotations

import pytest

from trinity_local import core_gate


class TestProviderErrorsNeverReachTheRuler:
    @pytest.mark.parametrize("text", [
        "You've hit your session limit · resets 12am (America/New_York)",
        "Failed to authenticate: OAuth session expired and could not be refreshed",
        "Error: rate limit exceeded, please try again later",
    ])
    def test_the_two_real_incidents_and_a_sibling_are_refused(self, text):
        assert core_gate.looks_like_provider_error(text), text

    def test_a_real_core_is_not_mistaken_for_an_error(self):
        real = ("You commit to one named call with an expiry rather than curating a "
                "menu of options — a survey that decides nothing is a decision not "
                "yet made — but you never lock in before observing the load-bearing "
                "thing yourself, because a reported status is theater until the "
                "artifact is in your hands. You reason from the constraint that "
                "actually binds in the situation in front of you.")
        assert not core_gate.looks_like_provider_error(real)

    def test_a_long_text_merely_discussing_limits_is_not_an_error(self):
        """Shape alone must not condemn — it is shape AND brevity."""
        essay = ("You treat a rate limit as a design input rather than an "
                 "obstacle, and you would rather hold a runnable artifact than "
                 "an explanation of one. ") * 6
        assert len(essay) >= 400 and not core_gate.looks_like_provider_error(essay)

    def test_refusal_happens_before_any_scoring(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        core_gate.core_path().parent.mkdir(parents=True, exist_ok=True)
        core_gate.core_path().write_text("a real incumbent core, earned")

        def _boom(*a, **k):
            raise AssertionError("scored a provider error instead of refusing it")

        monkeypatch.setattr(core_gate, "score_bits", _boom)
        v = core_gate.propose_core("You've hit your session limit · resets 12am")
        assert not v.admitted and "PROVIDER ERROR" in v.reason


class TestTheGateFailsClosedWhileTheRulerIsConfounded:
    def test_a_shorter_candidate_cannot_evict_a_live_incumbent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        core_gate.core_path().parent.mkdir(parents=True, exist_ok=True)
        core_gate.core_path().write_text("the incumbent core, long and earned " * 20)
        # the ruler loves the short candidate — exactly the historical failure
        monkeypatch.setattr(core_gate, "score_bits",
                            lambda texts, art: ((10.0 if art == b"tiny" else 9000.0), "neural"))
        v = core_gate.propose_core("tiny", heldout=["a"] * core_gate.MIN_HELDOUT)
        assert not v.admitted, "a length-confounded ruler must not evict a live core"
        assert "LENGTH-CONFOUNDED" in v.reason
        assert core_gate.core_path().read_text().startswith("the incumbent")

    def test_the_numbers_are_still_computed_and_archived(self, tmp_path, monkeypatch):
        """Kept as evidence: the day a ruler passes a matched control this is data again."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        core_gate.core_path().parent.mkdir(parents=True, exist_ok=True)
        core_gate.core_path().write_text("incumbent")
        monkeypatch.setattr(core_gate, "score_bits",
                            lambda texts, art: ((10.0 if art == b"tiny" else 9000.0), "neural"))
        v = core_gate.propose_core("tiny", heldout=["a"] * core_gate.MIN_HELDOUT)
        assert v.candidate_bits == 10 and v.incumbent_bits == 9000
        assert v.archived, "refused candidates must remain recoverable"

    def test_a_genuine_first_write_still_succeeds(self, tmp_path, monkeypatch):
        """Fail-closed protects an INCUMBENT. With none, a cold install must work."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        core_gate.core_path().parent.mkdir(parents=True, exist_ok=True)
        v = core_gate.propose_core("a genuine first distillation of the founder")
        assert v.admitted and "first write" in v.reason


class TestTheRulerScoresTheSameTokensInEveryArm:
    """The no-artifact baseline must not score less text than the candidates.

    res_082: `lp` holds one loss per predicted token, so a slice of
    `max(len(ctx)-1, 0)` scores len(ids) tokens with a context and len(ids)-1
    without one. The baseline was scoring ONE FEWER TOKEN PER TEXT than every
    candidate it was compared against.

    Measured on 120 held-out prompts, that asymmetry was the dominant term and
    inverted the answer's sign: the real core priced 3,010 bits WORSE than
    no-core before the fix and 58 bits BETTER after it. It did not rescue the
    ruler — token-matched, the real core still loses to its own word-shuffle by
    334 bits — but a comparison between token counts is not a comparison
    between artifacts, whatever it concludes.
    """

    def _scored(self, ctx_len: int, ids_len: int) -> int:
        """Losses summed, mirroring the slice in _neural_bits."""
        lp_len = ctx_len + ids_len - 1
        start = ctx_len if ctx_len else 0
        return lp_len - start

    def test_every_arm_scores_the_same_number_of_tokens(self):
        ids = 40
        counts = {c: self._scored(c, ids) for c in (0, 1, 12, 241)}
        assert len(set(counts.values())) == 1, (
            f"arms score different token counts: {counts}. The baseline (ctx=0) "
            "scoring fewer tokens is exactly the res_082 defect."
        )
        assert set(counts.values()) == {ids - 1}

    def test_the_old_slice_is_what_produced_the_asymmetry(self):
        """Characterise the bug so a revert is recognisable, not just red."""
        old = lambda c, n: (c + n - 1) - max(c - 1, 0)
        assert old(0, 40) == 39 and old(241, 40) == 40, (
            "if this no longer holds, the historical diagnosis in res_082 needs "
            "re-deriving rather than trusting"
        )

    def test_the_source_uses_the_matched_slice(self):
        import inspect

        from trinity_local import core_gate

        src = inspect.getsource(core_gate._neural_bits)
        assert "lp[len(ctx) if ctx else 0:]" in src
        assert "max(len(ctx) - 1, 0)" not in src, "the asymmetric slice is back"
