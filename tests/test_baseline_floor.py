"""Trust-gate tests for baseline_floor's judge-sanity check (#316).

The judge sanity must use the CONTRAST (echo_gold − echo_rejected) — "can the
judge tell the user's own correction from the answer they pushed back on?" — not
an absolute echo_gold ceiling. The absolute ceiling mis-fired on the real corpus:
Trinity's gold is a correction FRAGMENT that caps ~0.75 even fed back verbatim, so
echo_gold measured 0.747 (a demonstrably excellent judge, 0.0 echo_rejected, 0.61
discrimination) yet the 0.75 bar read "judge broken". These pin the contrast.
"""
from __future__ import annotations

import pytest

from trinity_local.evals import baseline_floor as bf
from trinity_local.evals.runner import EvalItemRun, EvalRunResult


def _run(n: int = 4) -> EvalRunResult:
    items = [
        EvalItemRun(
            eval_item_id=f"e{i}", rejection_type="REFRAME", prompt="q",
            rejected_response="bad", user_substitute="good", rubric_signal="r",
            basin_id=None, target_response="", target_error=None, elapsed_seconds=0.0,
        )
        for i in range(n)
    ]
    return EvalRunResult(
        eval_id="ev", target_provider="t", target_model=None,
        started_at="", completed_at="", items_total=n, items_completed=n,
        items_failed=0, items=items,
    )


def _patch_baselines(monkeypatch, scores: dict[str, float]) -> None:
    """Make score_baseline return a fixed aggregate per candidate — no LLM."""
    def fake(run, name, *a, **k):
        return bf.BaselineResult(name=name, aggregate=scores[name], n_scored=len(run.items))
    monkeypatch.setattr(bf, "score_baseline", fake)


def test_echo_gold_passes_contrast_even_below_old_absolute_ceiling(monkeypatch):
    """The #316 regression: echo_gold 0.747 (a correction fragment fed back) with a
    0.0 echo_rejected MUST pass judge sanity — the retired absolute 0.75 ceiling
    wrongly failed it. The contrast (0.747 ≥ 0.25) is the invariant.

    Mutation: revert judge_ok to `pos_score >= 0.75` → this fails (0.747 < 0.75).
    """
    _patch_baselines(monkeypatch, {
        "echo_gold": 0.747, "echo_rejected": 0.0, "echo_prompt": 0.14,
        "empty": 0.01, "constant": 0.08,
    })
    v = bf.evaluate_floor(_run(), real_aggregate=0.6, lens_text="",
                          judge_provider="claude", provider_configs={})
    assert v.recognition == 0.747
    assert v.judge_ok, "echo_gold ≫ echo_rejected ⇒ the judge recognizes the correction"
    assert v.discriminates  # real 0.6 vs worst dumb 0.14 → 0.46 ≥ 0.15
    assert v.trustworthy


def test_judge_that_cannot_tell_gold_from_rejected_is_refused(monkeypatch):
    """A judge that scores the user's correction ~the same as the answer they
    REJECTED can't read taste — judge_ok must be False however HIGH echo_gold is
    (the absolute ceiling would have passed this 0.9)."""
    _patch_baselines(monkeypatch, {
        "echo_gold": 0.9, "echo_rejected": 0.8, "echo_prompt": 0.1,
        "empty": 0.0, "constant": 0.05,
    })
    v = bf.evaluate_floor(_run(), real_aggregate=0.95, lens_text="",
                          judge_provider="claude", provider_configs={})
    assert v.recognition == pytest.approx(0.1)
    assert not v.judge_ok  # 0.1 < 0.25 recognition margin
    assert not v.trustworthy
    assert "JUDGE BROKEN" in v.reason
