"""Framing enters the trust tally only as a real contrast, never as a lone cell.

§2 of the compression-turn plan, schema half. The recording half shipped as
res_117; this is the key change it gated.

The rule effort taught. Four effort sub-cells clear MIN_TALLY_N in the live
ledger and NOT ONE has a sibling — no model x version has a second effort level
on file, because rotation was never switched on. Each is therefore a lone number
wearing the shape of a contrast: it tells you nothing the model's overall rate
does not already say. Framing gets the sibling requirement from the start, so a
single framing is withheld rather than shown.

Measured 2026-09-03: 0 of 1,286 councils on disk carry framing, because every one
predates the recording commit. A breakdown built today must therefore be EMPTY,
and that emptiness is the gate working, not a bug.
"""
from __future__ import annotations

from trinity_local.disagreement_ledger import (
    MIN_TALLY_N,
    DisagreementPattern,
    aggregate_tally,
)

MODEL = "anthropic·opus·5"


def _claims(n, *, framing, followed, cid_offset=0):
    """n resolved claims where MODEL argued FOR, with the given framing."""
    return [
        DisagreementPattern(
            claim_id=f"c{cid_offset + i}", council_id=f"council_{cid_offset + i}",
            at="2026-09-03T00:00:00", claim=f"claim {i}", why_matters="",
            providers_for=["anthropic"], providers_against=["openai"],
            chairman_winner="anthropic", task_excerpt="",
            framing=framing, models_for=[MODEL], models_against=["openai·gpt·5.6"],
        )
        for i in range(n)
    ]


def _res(pats, verdict):
    return {p.claim_id: verdict for p in pats}


class TestItRefusesWhenThereIsNoContrast:
    def test_a_pre_framing_corpus_yields_nothing(self):
        pats = _claims(30, framing="", followed=True)
        out = aggregate_tally(pats, _res(pats, "followed"))
        assert out["framing_breakdown"] == {}, (
            "every council on disk today predates framing recording; a breakdown "
            "here would be n=0 cells presented as a finding"
        )

    def test_one_framing_alone_is_withheld_however_large(self):
        pats = _claims(MIN_TALLY_N * 5, framing="goal", followed=True)
        out = aggregate_tally(pats, _res(pats, "followed"))
        assert out["framing_breakdown"] == {}, (
            "a lone framing cell is the exact shape of the four effort sub-cells "
            "that clear MIN_TALLY_N with no sibling: a number that cannot be a "
            "contrast because there is nothing to contrast it with"
        )

    def test_a_sibling_below_the_floor_does_not_rescue_a_lone_cell(self):
        big = _claims(MIN_TALLY_N * 3, framing="goal", followed=True)
        tiny = _claims(MIN_TALLY_N - 1, framing="goal+context_excerpt",
                       followed=True, cid_offset=900)
        pats = big + tiny
        out = aggregate_tally(pats, _res(pats, "followed"))
        assert out["framing_breakdown"] == {}, (
            "the sibling must itself clear the floor; otherwise one solid cell and "
            "one thin one read as a comparison that the thin cell cannot support"
        )


class TestItProducesWhenTheContrastIsReal:
    def test_two_framings_over_the_floor_surface_together(self):
        a = _claims(MIN_TALLY_N + 2, framing="goal", followed=True)
        b = _claims(MIN_TALLY_N + 2, framing="goal+context_excerpt",
                    followed=False, cid_offset=500)  # resolved "contradicted": MODEL argued for and lost
        pats = a + b
        res = {**_res(a, "followed"), **_res(b, "contradicted")}
        out = aggregate_tally(pats, res)
        rows = out["framing_breakdown"].get(MODEL)
        assert rows is not None, "two framings over the floor is a real contrast"
        assert set(rows) == {"goal", "goal+context_excerpt"}
        assert rows["goal"]["w"] == MIN_TALLY_N + 2
        assert rows["goal+context_excerpt"]["l"] == MIN_TALLY_N + 2
        for cell in rows.values():
            assert "ci" in cell and "ci_excludes_half" in cell, (
                "each cell carries its own interval, like the effort rows"
            )

    def test_effort_breakdown_is_untouched_by_any_of_this(self):
        pats = _claims(MIN_TALLY_N + 2, framing="goal", followed=True)
        out = aggregate_tally(pats, _res(pats, "followed"))
        assert "effort_breakdown" in out and "framing_breakdown" in out
