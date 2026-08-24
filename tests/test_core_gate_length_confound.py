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
