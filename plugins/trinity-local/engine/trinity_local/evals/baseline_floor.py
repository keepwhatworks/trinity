"""Degenerate-baseline floor — the automatable version of "look at the raw rows".

A benchmark number is only meaningful if a STUPID candidate scores badly on it.
If "echo the user's question back" scores as well as a frontier model, the eval
can't discriminate good from bad — so the headline number is noise, and we must
refuse to ship it (no matter how plausible it looks).

This is the reliable defense against Trinity's #1 recurring bug: a green aggregate
sitting on degenerate data (principle #35, the green-gate). Unit tests can't catch
it — they assert the code runs, not that the number means anything — because their
fixtures are clean by construction (prompt != gold). The corpus is not. So we probe
the metric itself with candidates whose score we already KNOW the sign of:

  - POSITIVE control (ceiling): `echo_gold` feeds the candidate the user's own
    preferred answer. A working judge MUST score this high; if it doesn't, the
    JUDGE is broken, not the models.
  - NEGATIVE controls (floor): `echo_prompt` (echo the question), `empty`, and
    `constant` (a generic non-answer) carry no taste-matching signal. A working
    eval MUST score these low. If `echo_prompt` scores high, the eval is degenerate
    — typically because prompt ≈ gold (exactly the reaction-as-prompt bug, #316).

The discrimination margin = real_model_score − max(negative_baseline_scores).
If it's below DISCRIMINATION_FLOOR, the eval does not separate signal from noise
and the headline is REFUSED. This would have caught the 0.82/0.90 bug for free:
on that eval, echo_prompt scores ~as high as Opus because the gold IS the prompt.

Reuses the production scorer (`score_run`) so the baseline is judged by the exact
same judge, prompt, and rubric as the real models — apples to apples.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .runner import EvalItemRun, EvalRunResult
from .scorer import score_run

# A real model must beat the best dumb floor-baseline by at least this margin for
# the eval to count as discriminating. 0.15 mirrors the council WINNER_MARGIN_FLOOR
# (a sub-0.15 edge over "echo the question" is within judge noise — a coin flip).
DISCRIMINATION_FLOOR = 0.15

# CONTRAST-based judge sanity (#316). A working judge must score the POSITIVE
# control (echo_gold — the user's own correction, fed back) clearly ABOVE the
# answer the user REJECTED (echo_rejected), by at least this margin. The invariant
# is "does the judge recognize the user's correction over the thing they pushed
# back on?".
#
# This REPLACED an absolute `echo_gold >= 0.75` ceiling. That ceiling assumed the
# gold is a polished answer — but Trinity's gold is a correction FRAGMENT ("let me
# zoom in, what do you think?") that structurally caps around 0.75 even when fed
# back verbatim. On the real corpus echo_gold measured 0.747 (n=16) against a 0.0
# echo_rejected and a 0.61 discrimination — a demonstrably excellent judge — yet
# the absolute bar mis-fired "judge broken" on a 0.003 shortfall. The contrast is
# gold-structure-agnostic: it tests the judge's ability to tell the user's steer
# from the rejected answer, not an arbitrary absolute level. 0.25 ≫ the 0.15
# discrimination noise floor (a clear gap, not a coin flip).
JUDGE_RECOGNITION_MARGIN = 0.25

# Candidate-response generators: item -> synthetic target_response. The score's
# expected SIGN is in the comment; that's what makes each one a control.
DEGENERATE_CANDIDATES: dict[str, Callable[[EvalItemRun], str]] = {
    "echo_gold": lambda it: it.user_substitute or "",   # POSITIVE control → expect HIGH
    # The answer the user REJECTED. Under the pairwise frame ("does C honor the
    # correction better than the rejected answer?") feeding the rejected answer
    # back as C must score LOW — it IS the thing that got corrected. The single
    # most meaningful negative control.
    "echo_rejected": lambda it: it.rejected_response or "",  # NEGATIVE control → expect LOW
    "echo_prompt": lambda it: it.prompt or "",           # NEGATIVE control → expect LOW
    "empty": lambda it: "",                               # NEGATIVE control → expect LOW
    "constant": lambda it: "It depends on your situation; there are pros and cons either way.",  # NEGATIVE → LOW
}

# Which candidates are floor controls (must score LOW) vs the ceiling control.
_NEGATIVE_BASELINES = ("echo_rejected", "echo_prompt", "empty", "constant")
_POSITIVE_BASELINE = "echo_gold"


@dataclass
class BaselineResult:
    name: str
    aggregate: float | None
    n_scored: int


@dataclass
class FloorVerdict:
    real_aggregate: float | None
    baselines: dict[str, BaselineResult]
    margin: float | None            # real − worst (highest-scoring) negative baseline
    worst_negative: str | None      # which dumb baseline scored highest (the threat)
    recognition: float | None       # echo_gold − echo_rejected (the judge-sanity contrast)
    judge_ok: bool                  # recognition >= JUDGE_RECOGNITION_MARGIN
    discriminates: bool             # margin >= DISCRIMINATION_FLOOR
    trustworthy: bool               # judge_ok AND discriminates
    reason: str

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


def _clone_with_response(run: EvalRunResult, responder: Callable[[EvalItemRun], str]) -> EvalRunResult:
    """A copy of `run` whose every item's target_response is replaced by the
    candidate, score reset — ready to re-judge under identical conditions."""
    items = [
        dataclasses.replace(
            it,
            target_response=responder(it),
            target_error=None,
            score=None,
            score_reason=None,
            judge_provider=None,
        )
        for it in run.items
    ]
    return dataclasses.replace(run, items=items, aggregate_score=None, n_scored=0)


def score_baseline(
    run: EvalRunResult,
    name: str,
    lens_text: str,
    judge_provider: str,
    provider_configs: dict,
    *,
    cwd: Path | None = None,
) -> BaselineResult:
    """Score one degenerate candidate through the production judge path."""
    responder = DEGENERATE_CANDIDATES[name]
    clone = _clone_with_response(run, responder)
    scored = score_run(clone, lens_text, judge_provider, provider_configs, cwd=cwd)
    return BaselineResult(name=name, aggregate=scored.aggregate_score, n_scored=scored.n_scored)


def evaluate_floor(
    run: EvalRunResult,
    real_aggregate: float | None,
    lens_text: str,
    judge_provider: str,
    provider_configs: dict,
    *,
    cwd: Path | None = None,
    candidates: tuple[str, ...] | None = None,
) -> FloorVerdict:
    """Run the baselines and decide whether `real_aggregate` is defensible.

    `run` supplies the eval items (its own target_response is ignored — we swap in
    each candidate). `real_aggregate` is the headline you want to defend.
    """
    names = candidates or tuple(DEGENERATE_CANDIDATES)
    results: dict[str, BaselineResult] = {}
    for name in names:
        results[name] = score_baseline(run, name, lens_text, judge_provider, provider_configs, cwd=cwd)

    pos = results.get(_POSITIVE_BASELINE)
    pos_score = pos.aggregate if pos else None
    # CONTRAST judge sanity (#316): echo_gold (the user's own correction) must beat
    # echo_rejected (the answer the user rejected) by the recognition margin. This
    # is the invariant — the judge can tell the user's steer from the thing they
    # pushed back on — and is agnostic to whether the gold is a polished answer or a
    # terse fragment (the absolute-ceiling mis-fire that read a 0.747 echo_gold as
    # "broken"). echo_rejected is a NEGATIVE control, so it's also tallied below.
    rej = results.get("echo_rejected")
    rej_score = rej.aggregate if rej else None
    recognition = (
        round(pos_score - rej_score, 4)
        if (pos_score is not None and rej_score is not None)
        else None
    )
    judge_ok = recognition is not None and recognition >= JUDGE_RECOGNITION_MARGIN

    # name -> aggregate, only floor controls that actually scored
    neg: dict[str, float] = {
        n: r.aggregate
        for n, r in results.items()
        if n in _NEGATIVE_BASELINES and r.aggregate is not None
    }
    # Baseline name as a stable tie-break so the named worst-negative is
    # deterministic on a score tie (`min` over (-score, name) = highest score /
    # smallest name; `worst_score` is unaffected by the tie-break).
    worst_negative = min(neg, key=lambda n: (-neg[n], n)) if neg else None
    worst_score = neg[worst_negative] if worst_negative else None

    margin = (
        round(real_aggregate - worst_score, 4)
        if (real_aggregate is not None and worst_score is not None)
        else None
    )
    discriminates = margin is not None and margin >= DISCRIMINATION_FLOOR
    trustworthy = judge_ok and discriminates

    if not judge_ok:
        reason = (
            f"JUDGE BROKEN: echo_gold (the user's OWN correction) scored {pos_score} "
            f"vs echo_rejected (the answer they pushed back on) {rej_score} — a "
            f"{recognition} gap, under the {JUDGE_RECOGNITION_MARGIN} recognition "
            f"margin. The judge can't tell the user's steer from the rejected answer, "
            f"so no score from it means anything."
        )
    elif not discriminates:
        reason = (
            f"EVAL DEGENERATE: a dumb baseline ('{worst_negative}') scored {worst_score}, "
            f"only {margin} below the real model ({real_aggregate}). Under the "
            f"{DISCRIMINATION_FLOOR} floor → the eval can't separate signal from noise. "
            f"REFUSE the headline."
        )
    else:
        reason = (
            f"OK: real {real_aggregate} beats the best dumb baseline "
            f"('{worst_negative}' {worst_score}) by {margin} ≥ {DISCRIMINATION_FLOOR}; "
            f"judge sanity passed (echo_gold {pos_score} beats echo_rejected "
            f"{rej_score} by {recognition} ≥ {JUDGE_RECOGNITION_MARGIN})."
        )

    return FloorVerdict(
        real_aggregate=real_aggregate,
        baselines=results,
        margin=margin,
        worst_negative=worst_negative,
        recognition=recognition,
        judge_ok=judge_ok,
        discriminates=discriminates,
        trustworthy=trustworthy,
        reason=reason,
    )
