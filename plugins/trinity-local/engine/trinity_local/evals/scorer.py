"""Eval scorer. Given a populated EvalRunResult (target_response per
item) + the user's lens.md, ask the chairman: "is the target_response
better than the rejected_response on the {rejection_type} axis?"

The chairman returns a structured judgment per item. Aggregates roll
up by rejection_type so the marketing-legible output is "model X scored
0.73 on YOUR COMPRESSION-prone prompts."

The judge is itself a model call; we deliberately let the caller pick
which provider plays judge (default: the user's chairman provider).
That avoids the obvious bias-trap of "the model being scored grades
itself" — score gemini using claude or codex as the judge, etc.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import ProviderConfig
from ..providers import make_provider, ProviderResult, result_hard_failed
from ._textnorm import strip_code_fences
from .runner import EvalRunResult


# One-liner per axis, surfaced in user-facing eval output (terminal +
# share card) so a reader knows what each axis measures without
# leaving the output. Long-form rubric below is for the chairman judge.
AXIS_ONELINER = {
    "REFRAME": "user wanted a different frame",
    "COMPRESSION": "user wanted shorter",
    "REDIRECT": "user wanted a different shape (spec vs narrative, etc.)",
    "SHARPENING": "user wanted more precision (numbers, identifiers)",
}


# Per-rejection-type rubric the chairman gets in the judge prompt.
# Each describes WHAT the user wanted that the rejected_response missed,
# so the chairman can grade on the right axis instead of generic quality.
REJECTION_AXIS_RUBRIC = {
    "REFRAME": (
        "The user substituted a different FRAME — the rejected response "
        "answered a different question than the user actually wanted asked. "
        "Score higher if the candidate response notices the user's likely "
        "frame and addresses THAT, not the literal question."
    ),
    "COMPRESSION": (
        "The user wanted SHORTER. The rejected response was a long lecture "
        "or multi-section essay. Score higher if the candidate response is "
        "concise and direct."
    ),
    "REDIRECT": (
        "The user wanted a structurally DIFFERENT output (e.g. spec vs "
        "narrative). The rejected response gave the wrong shape. Score "
        "higher if the candidate response delivers the shape the user "
        "implicitly wanted."
    ),
    "SHARPENING": (
        "The user wanted more PRECISION on the same topic. The rejected "
        "response was vague. Score higher if the candidate response "
        "names specifics (numbers, identifiers, concrete examples)."
    ),
}


JUDGE_PROMPT_TEMPLATE = """You are measuring how well a candidate answer honors what THIS user actually
wanted — judged by the user's own behavior, NOT by generic answer quality.

The user's taste (excerpted from their personal lens):
---
{lens_excerpt}
---

THE EXCHANGE THAT REVEALS THE PREFERENCE:

The user asked:
---
{question}
---

A model answered — and the user PUSHED BACK on it. This is the REJECTED answer:
---
{rejected_response}
---

The user's correction — what they steered toward instead. This is the REVEALED
PREFERENCE. It may be terse, blunt, informal, or a fragment ("yes", "just the
spec", "shorter") — that is FINE; it still defines the DIRECTION the user wanted:
---
{user_substitute}
---

Why this is a {rejection_type} correction ({axis_oneliner}):
{rubric}
Annotation: {rubric_signal}

NOW EVALUATE THIS CANDIDATE ANSWER to the same question:
---
{target_response}
---

The one question to answer: does the CANDIDATE give the user what they steered
toward (the DIRECTION of their correction) BETTER than the REJECTED answer did?

Judge ALIGNMENT WITH THE REVEALED PREFERENCE — not prose quality, not length, not
polish. A short, plain, or blunt answer that delivers what the user wanted is GOOD.
A fluent, well-written answer that repeats the rejected answer's mistake is BAD.

Output ONLY a JSON object on a single line. No prose, no markdown fences:
{{"score": <float in [0.0, 1.0]>, "reason": "<one-sentence rationale>"}}

1.0 = clearly delivers what the user steered toward; a decisive improvement over the rejected answer.
0.5 = no better than the rejected answer (repeats the mistake, or a sideways change).
0.0 = repeats or worsens the exact thing the user rejected.
"""


# Cap the lens excerpt sent to the judge. Full lens.md can be 6-10KB;
# 2000 chars of the most-relevant section is enough for a per-item
# rubric without dominating the judge's context window.
LENS_EXCERPT_BUDGET = 2000


def _lens_excerpt(lens_text: str, budget: int = LENS_EXCERPT_BUDGET) -> str:
    text = (lens_text or "").strip()
    if len(text) <= budget:
        return text
    head = budget // 2
    return f"{text[:head].rstrip()}\n[... excerpted ...]\n{text[-head:].lstrip()}"


# The canonical LLM judges (council providers). A judge must resolve to one of
# these — local/embedder backends fabricate 0.5s (#246).
_CANONICAL_JUDGES = {"claude", "codex", "antigravity"}

# score_reason prefixes that mark a 0.5 as a non-answer (judge failed), not a
# genuine "inconclusive" judgement — used to detect a degenerate scoring run.
_DEGENERATE_REASONS = (
    "judge returned empty output",
    "judge output unparseable",
    "judge dispatch raised",
)


def _mean_ci_half_width(scores: list[float], z: float = 1.96) -> float | None:
    """95% confidence half-width for the mean of per-item scores (the ± on the
    headline). Honest small-n disclosure: a 0.79 ±0.04 reads as trustworthy; a
    0.79 with no interval is the most quotable thing *against* the benchmark.

    Continuous scores in [0,1] → normal-approx CI on the mean: z * s / sqrt(n),
    sample stdev s. Returns None for n < 2 (no spread to estimate) — the caller
    must surface "n too small" rather than a fake-precise interval.
    """
    n = len(scores)
    if n < 2:
        return None
    mean = sum(scores) / n
    var = sum((s - mean) ** 2 for s in scores) / (n - 1)  # sample variance
    return z * (var ** 0.5) / (n ** 0.5)


def _parse_judge_response(raw: str) -> tuple[float, str]:
    """Extract {score, reason} from the judge's stdout. Falls back to
    a neutral 0.5 if parsing fails — better than crashing the whole
    score run on one judge that returned prose around the JSON."""
    if not raw:
        return 0.5, "judge returned empty output"
    # Strip code fences if the judge wrapped its JSON (shared stripper — the
    # old local `(?:json)?` form failed silently on a ```text fence).
    cleaned = strip_code_fences(raw)
    # First try direct JSON parse.
    try:
        parsed = json.loads(cleaned)
        score = float(parsed.get("score", 0.5))
        reason = str(parsed.get("reason", "")).strip()
        return max(0.0, min(1.0, score)), reason
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # Fall back: find the first JSON-looking object in the text.
    m = re.search(r"\{[^{}]*\"score\"\s*:\s*([0-9.]+)[^{}]*\}", cleaned)
    if m:
        try:
            score = float(m.group(1))
            return max(0.0, min(1.0, score)), "judge output partially parsed"
        except ValueError:
            pass
    return 0.5, f"judge output unparseable: {cleaned[:200]}"


def score_run(
    run_result: EvalRunResult,
    lens_text: str,
    judge_provider: str,
    provider_configs: dict[str, ProviderConfig],
    *,
    cwd: Path | None = None,
    progress_callback=None,
) -> EvalRunResult:
    """Score each item in the run by asking a judge provider whether
    the target_response is better than the rejected_response on the
    item's rejection axis.

    Mutates `run_result.items[*].score` + `.score_reason` +
    `.judge_provider` in place AND returns the same object for chaining.

    Aggregate score = mean of per-item scores, ignoring skipped items
    (where dispatch failed).

    Raises KeyError if `judge_provider` isn't in `provider_configs`.
    """
    if judge_provider not in provider_configs:
        raise KeyError(
            f"Unknown judge provider '{judge_provider}'. "
            f"Available: {sorted(provider_configs)}"
        )
    # A judge must be a real LLM provider. The local embedder backend ('mlx')
    # and other non-chat configs return empty output, which defaults every item
    # to a neutral 0.5 and fabricates a 0.5 aggregate indistinguishable from a
    # real benchmark (#246 — a live run shipped exactly this). Restrict to the
    # canonical council LLMs (aliases resolved).
    from ..council_schema import normalize_provider_slug
    if normalize_provider_slug(judge_provider) not in _CANONICAL_JUDGES:
        raise ValueError(
            f"Judge provider '{judge_provider}' is not a valid LLM judge — it "
            f"must resolve to one of {sorted(_CANONICAL_JUDGES)}. A non-chat "
            f"backend (e.g. the 'mlx' embedder) returns empty output and "
            f"fabricates a neutral 0.5 score for every item."
        )
    config = provider_configs[judge_provider]
    judge = make_provider(config)
    cwd = cwd or Path.cwd()
    lens_excerpt = _lens_excerpt(lens_text)

    scored_count = 0
    real_scores: list[float] = []  # genuine judgements only (excludes judge-failure 0.5s)
    degenerate_count = 0
    per_type: dict[str, list[float]] = {}

    for idx, item in enumerate(run_result.items, start=1):
        if item.target_error:
            # Failed dispatch — can't score. Leave score=None.
            continue

        rubric = REJECTION_AXIS_RUBRIC.get(
            item.rejection_type,
            "Score on overall quality and alignment with the user's taste rubric above.",
        )
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            lens_excerpt=lens_excerpt,
            question=(item.prompt or "(question unavailable)")[:2000],
            rejection_type=item.rejection_type,
            axis_oneliner=AXIS_ONELINER.get(
                item.rejection_type, "the user wanted something different"
            ),
            rubric=rubric,
            rejected_response=(item.rejected_response or "")[:2000],
            user_substitute=(item.user_substitute or "")[:1000],
            rubric_signal=(item.rubric_signal or "(none)")[:500],
            target_response=(item.target_response or "")[:2000],
        )
        try:
            result: ProviderResult = judge.run(prompt, cwd=cwd)
            # Shared hard-failure predicate (rc != 0 AND no usable stdout) — the
            # same one the council member dispatch uses. Its getattr defaults treat
            # a thin SimpleNamespace judge double (no `returncode`) as success, so
            # this only fires on a genuine non-zero dispatch, never on a mock.
            if result_hard_failed(result):
                # Judge CLI failed (rate-limited / token-exhausted — the quota
                # error lands on stderr, stdout is empty). Parsing the empty
                # stdout would label it the generic "empty output"; name the
                # dispatch failure so the degraded-scoring diagnostic in
                # handle_eval_run is precise about WHY (a rc!=0 judge, not a
                # genuinely inconclusive 0.5). Still a _DEGENERATE_REASON prefix
                # so #246 suppresses the fabricated aggregate.
                # Keep the "judge returned empty output" prefix so #246's
                # _DEGENERATE_REASONS suppression still fires, but APPEND the real
                # cause (usage limit + reset time, auth, …) so the degraded-scoring
                # diagnostic says WHY, not just "rc=1".
                from ..providers import describe_provider_failure
                why = describe_provider_failure(
                    result.stdout, result.stderr, result.returncode, provider=judge_provider
                )
                score, reason = 0.5, f"judge returned empty output — {why}"
            else:
                score, reason = _parse_judge_response(result.stdout)
        except Exception as exc:
            score, reason = 0.5, f"judge dispatch raised: {exc!r}"

        item.score = score
        item.score_reason = reason
        item.judge_provider = judge_provider

        scored_count += 1
        if score == 0.5 and reason.startswith(_DEGENERATE_REASONS):
            # A judge-FAILURE 0.5 is "no score", not a real 0.5. Counting it in
            # the mean silently drags the aggregate toward 0.5 — the partial-
            # degenerate contamination a critic pokes ("your failures pad the
            # number"). Exclude it from BOTH the aggregate and the per-axis means;
            # surface the count separately as judge_failures.
            degenerate_count += 1
        else:
            real_scores.append(score)
            per_type.setdefault(item.rejection_type, []).append(score)

        if progress_callback is not None:
            try:
                progress_callback(idx, len(run_result.items), item)
            except Exception:
                pass

    # Suppress a fabricated benchmark: when most scored items hit the
    # empty/unparseable 0.5 default, the run says nothing about the model —
    # don't persist a real-looking aggregate (#246, the confidence-honesty rule
    # applied to evals). aggregate_score=None + a degraded flag so every surface
    # treats it as "no score", not "scored 0.5".
    run_result.scoring_degraded = bool(
        scored_count and degenerate_count / scored_count > 0.5
    )
    run_result.judge_failures = degenerate_count
    run_result.n_scored = len(real_scores)
    # Self-judge detection (2026-06-09): judge-slug == target-slug means the judge
    # is grading its own provider family. Measured NON-self-preferential (the
    # self-preference experiment: own-minus-cross −0.19 claude / +0.02 antigravity),
    # so this is NEUTRAL transparency metadata, not a bias penalty — surfaces note
    # the same-family relationship; the score ranks like any other judge's.
    run_result.self_judge = (
        normalize_provider_slug(judge_provider)
        == normalize_provider_slug(run_result.target_provider)
    )
    if run_result.scoring_degraded or not real_scores:
        run_result.aggregate_score = None
        run_result.aggregate_ci_half_width = None
    else:
        # Mean of GENUINE judgements only; ± is the 95% interval on that mean.
        run_result.aggregate_score = sum(real_scores) / len(real_scores)
        run_result.aggregate_ci_half_width = _mean_ci_half_width(real_scores)
    run_result.by_rejection_type = {
        rtype: {
            "count": len(scores),
            "mean_score": sum(scores) / len(scores) if scores else 0.0,
            "min_score": min(scores) if scores else 0.0,
            "max_score": max(scores) if scores else 0.0,
        }
        for rtype, scores in per_type.items()
    }
    return run_result
