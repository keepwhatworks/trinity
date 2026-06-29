"""Eval runner. Given an eval set + a target provider, dispatch each
prompt and capture the candidate response. Persists results to
`~/.trinity/evals/results/eval_<eval_id>__model_<provider>__<ts>.json`.

Scoring (was the candidate better than what got rejected?) happens in
the sister `evals/scorer.py` module. Separating them keeps the
expensive dispatch isolated from the chairman-judge call so a partial
run can be re-scored cheaply, and a fresh scorer can re-grade existing
result files without re-dispatching.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import ProviderConfig
from ..providers import (
    _effective_effort,
    dispatched_model,
    make_provider,
    ProviderResult,
)
from .builder import EvalSet, results_dir


def _identity_effort(config: ProviderConfig, model: str | None) -> str | None:
    """The thinking-level half of the #239 identity triple.

    Returns the configured effort, EXCEPT when it's already baked into the
    model string (agy renders "Gemini 3.1 Pro (high)") — avoids "(high) · high"
    double-rendering on the card."""
    effort = _effective_effort(config)
    if not effort:
        return None
    if model and f"({effort})".lower() in model.lower():
        return None
    return effort


@dataclass
class EvalItemRun:
    eval_item_id: str
    rejection_type: str
    prompt: str
    rejected_response: str
    user_substitute: str
    rubric_signal: str
    basin_id: str | None
    target_response: str
    target_error: str | None
    elapsed_seconds: float
    score: float | None = None
    score_reason: str | None = None
    judge_provider: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvalRunResult:
    eval_id: str
    target_provider: str
    target_model: str | None
    started_at: str
    completed_at: str
    items_total: int
    items_completed: int
    items_failed: int
    items: list[EvalItemRun] = field(default_factory=list)
    aggregate_score: float | None = None
    by_rejection_type: dict[str, dict[str, float]] = field(default_factory=dict)
    # #239 — the model IDENTITY is a triple: slug (target_provider) + model
    # name/version (target_model) + thinking level (target_effort). The SAME
    # model at a different effort is a different contestant, so the effort must
    # be recorded for an honest cross-run comparison. None when not configured
    # (or, for agy, when the level is already baked into target_model).
    target_effort: str | None = None
    # True when scoring was degenerate (>50% of items hit the empty/unparseable
    # 0.5 default — e.g. a non-LLM judge that returns nothing). aggregate_score
    # is forced to None in that case so a fabricated benchmark never persists.
    scoring_degraded: bool = False
    # Eval-hardening (2026-06-07): n_scored is the count of GENUINE judgements the
    # aggregate is a mean over (judge-failure 0.5s are excluded, counted in
    # judge_failures instead — no "no score" pads the mean). aggregate_ci_half_width
    # is the 95% ± on the headline (None for n<2). These let every surface show n + ±
    # so a tiny-n score carries its own error bar instead of reading as fake-precise.
    n_scored: int = 0
    judge_failures: int = 0
    aggregate_ci_half_width: float | None = None
    # Eval-hardening (2026-06-08): the judge's measured trust — how often the judge
    # agreed with the user's OWN past corrections (judge_alignment.py), over how many
    # pairs. None when no alignment report exists yet. This is the "we measured the
    # judge, it agrees with you X%" claim that ships on the card next to the score.
    judge_agreement: float | None = None
    judge_alignment_n: int | None = None
    # Judge-policy transparency (2026-06-09): a self-judge run is one whose judge
    # is the SAME provider family as the target (judge-slug == target-slug; the
    # configured slug carries one model at a time, so this is the model grading its
    # own family). Originally assumed biased-high — but the self-preference
    # experiment (n=30 paired own-minus-cross deltas) MEASURED the opposite: judges
    # are NOT self-preferential (claude −0.19, self-critical; antigravity +0.02).
    # So this flag is NEUTRAL metadata, recorded for transparency (a same-family
    # judge can still read as a conflict of interest externally), NOT a penalty:
    # the aggregate is a valid ranking like any other judge's. Re-validate per
    # model change; codex/GPT family was untested (credit outage that run).
    self_judge: bool = False

    def to_dict(self) -> dict:
        return {
            "eval_id": self.eval_id,
            "target_provider": self.target_provider,
            "target_model": self.target_model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "items_total": self.items_total,
            "items_completed": self.items_completed,
            "items_failed": self.items_failed,
            "items": [it.to_dict() for it in self.items],
            "aggregate_score": self.aggregate_score,
            "by_rejection_type": self.by_rejection_type,
            "scoring_degraded": self.scoring_degraded,
            "target_effort": self.target_effort,
            "n_scored": self.n_scored,
            "judge_failures": self.judge_failures,
            "aggregate_ci_half_width": self.aggregate_ci_half_width,
            "judge_agreement": self.judge_agreement,
            "judge_alignment_n": self.judge_alignment_n,
            "self_judge": self.self_judge,
        }

    def result_path(self) -> Path:
        ts_safe = self.started_at.replace(":", "").replace("-", "")[:15]
        return results_dir() / f"eval_{self.eval_id}__model_{self.target_provider}__{ts_safe}.json"


def run_eval(
    eval_set: EvalSet,
    target_provider: str,
    provider_configs: dict[str, ProviderConfig],
    *,
    limit: int | None = None,
    cwd: Path | None = None,
    progress_callback=None,
) -> EvalRunResult:
    """Dispatch each eval item's prompt to the target provider.

    Returns a populated EvalRunResult with target_response per item.
    The caller is responsible for calling `scorer.score_run` if they
    want graded results.

    Raises KeyError if target_provider isn't in provider_configs.
    """
    if target_provider not in provider_configs:
        raise KeyError(
            f"Unknown provider '{target_provider}'. "
            f"Available: {sorted(provider_configs)}"
        )
    config = provider_configs[target_provider]
    provider = make_provider(config)
    # #270: eval items are ANSWERED, not EXECUTED — dispatch as clean completions
    # (no MCP, no tools, no agent loop) so an agentic item ("look at the live app
    # and trace…") doesn't make the model try to use the browser and hang.
    if hasattr(provider, "clean_completion"):
        provider.clean_completion = True
    cwd = cwd or Path.cwd()

    items_to_run = eval_set.items[:limit] if limit else eval_set.items
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    runs: list[EvalItemRun] = []
    failed = 0

    for idx, item in enumerate(items_to_run, start=1):
        item_start = time.monotonic()
        try:
            result: ProviderResult = provider.run(item.prompt, cwd=cwd)
            error = None
            if result.returncode != 0:
                error = (
                    f"{target_provider} returned exit {result.returncode}: "
                    f"{(result.stderr or 'no stderr').strip()[:200]}"
                )
                failed += 1
            response_text = result.stdout
            elapsed = result.elapsed_seconds
        except Exception as exc:
            error = f"dispatch raised: {exc!r}"
            response_text = ""
            elapsed = time.monotonic() - item_start
            failed += 1

        item_run = EvalItemRun(
            eval_item_id=item.eval_item_id,
            rejection_type=item.rejection_type,
            prompt=item.prompt,
            rejected_response=item.rejected_response,
            user_substitute=item.user_substitute,
            rubric_signal=item.rubric_signal,
            basin_id=item.basin_id,
            target_response=response_text,
            target_error=error,
            elapsed_seconds=elapsed,
        )
        runs.append(item_run)
        if progress_callback is not None:
            try:
                progress_callback(idx, len(items_to_run), item_run)
            except Exception:
                pass

    completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return EvalRunResult(
        eval_id=eval_set.eval_id,
        target_provider=target_provider,
        # recorded == dispatched: for antigravity this reads agy's
        # settings.json (config.model is ignored by the flagless agy CLI),
        # so the eval card attributes the model that actually ran.
        target_model=dispatched_model(config),
        # #239: record the thinking level so the same model at a different
        # effort is a distinguishable contestant. Suppressed when the level is
        # already in the model string (agy bakes "(high)" into target_model).
        target_effort=_identity_effort(config, dispatched_model(config)),
        started_at=started,
        completed_at=completed,
        items_total=len(items_to_run),
        items_completed=len(items_to_run) - failed,
        items_failed=failed,
        items=runs,
    )


def save_run_result(run_result: EvalRunResult) -> Path:
    path = run_result.result_path()
    path.write_text(
        json.dumps(run_result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_run_result(path: Path) -> EvalRunResult | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    items = []
    for it in raw.get("items", []):
        items.append(EvalItemRun(
            eval_item_id=it.get("eval_item_id", ""),
            rejection_type=it.get("rejection_type", ""),
            prompt=it.get("prompt", ""),
            rejected_response=it.get("rejected_response", ""),
            user_substitute=it.get("user_substitute", ""),
            rubric_signal=it.get("rubric_signal", ""),
            basin_id=it.get("basin_id"),
            target_response=it.get("target_response", ""),
            target_error=it.get("target_error"),
            elapsed_seconds=float(it.get("elapsed_seconds", 0.0)),
            score=it.get("score"),
            score_reason=it.get("score_reason"),
            judge_provider=it.get("judge_provider"),
        ))
    return EvalRunResult(
        eval_id=raw.get("eval_id", ""),
        target_provider=raw.get("target_provider", ""),
        target_model=raw.get("target_model"),
        started_at=raw.get("started_at", ""),
        completed_at=raw.get("completed_at", ""),
        items_total=int(raw.get("items_total", 0)),
        items_completed=int(raw.get("items_completed", 0)),
        items_failed=int(raw.get("items_failed", 0)),
        items=items,
        aggregate_score=raw.get("aggregate_score"),
        # Shape-guard (guard_shape_not_just_parse / #304) at the dataclass load
        # boundary: a valid-JSON-but-wrong-type `by_rejection_type` (a LIST/STRING
        # from a hand-edited / half-migrated result) would land RAW on the
        # dataclass, then `result.by_rejection_type.keys()` (eval_card) /
        # `.items()` (eval-show) crash with `'list' object has no attribute
        # 'keys'`. Coerce non-dict to {} here so EVERY consumer of the dataclass
        # sees the dict contract — the same boundary fix `load_council_outcome`
        # uses for wrong-type member_results / metadata.
        by_rejection_type=(
            raw["by_rejection_type"]
            if isinstance(raw.get("by_rejection_type"), dict)
            else {}
        ),
        scoring_degraded=bool(raw.get("scoring_degraded", False)),
        target_effort=raw.get("target_effort"),
        self_judge=bool(raw.get("self_judge", False)),
    )
