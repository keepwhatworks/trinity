"""#246: an eval run must never persist a fabricated benchmark.

A live run was judged by the 'mlx' embedder backend, which returns empty output
→ every item defaulted to a neutral 0.5 → aggregate_score=0.5 was saved,
indistinguishable from a real score. Two guards: reject non-LLM judges up front,
and suppress the aggregate when scoring is degenerate (>50% empty/unparseable).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.evals import scorer
from trinity_local.evals.runner import EvalItemRun, EvalRunResult


def _run(n_items: int = 4) -> EvalRunResult:
    items = [
        EvalItemRun(
            eval_item_id=f"i{i}",
            rejection_type="REFRAME",
            prompt=f"prompt {i}",
            rejected_response="bad",
            user_substitute="good",
            rubric_signal="",
            basin_id="b0",
            target_response="a real target response of some length",
            target_error=None,
            elapsed_seconds=0.0,
        )
        for i in range(n_items)
    ]
    return EvalRunResult(
        eval_id="e1", target_provider="claude", target_model="claude-opus-4-8",
        started_at="2026-05-29T00:00:00", completed_at="", items_total=n_items,
        items_completed=n_items, items_failed=0, items=items,
    )


def test_rejects_non_llm_judge():
    # 'mlx' is in the configs but is the embedder backend, not an LLM judge.
    cfg = SimpleNamespace(name="mlx", model="mlx-community/Qwen", args=[])
    with pytest.raises(ValueError, match="not a valid LLM judge"):
        scorer.score_run(_run(), "lens text", "mlx", {"mlx": cfg})


def test_degenerate_scoring_suppresses_aggregate(monkeypatch):
    # A judge that returns empty for every item → all 0.5 defaults → the
    # aggregate must be None (not 0.5) and the run flagged degraded.
    class EmptyJudge:
        def run(self, prompt, cwd=None):
            return SimpleNamespace(stdout="")  # empty → 0.5 default
    monkeypatch.setattr(scorer, "make_provider", lambda cfg: EmptyJudge())
    cfg = SimpleNamespace(name="claude", model="claude-opus-4-8", args=[])
    result = scorer.score_run(_run(4), "lens text", "claude", {"claude": cfg})
    assert result.scoring_degraded is True
    assert result.aggregate_score is None, "must not persist a fabricated 0.5 benchmark"


def test_real_scoring_keeps_aggregate(monkeypatch):
    # A judge returning real scores → a genuine aggregate, not suppressed.
    class GoodJudge:
        def run(self, prompt, cwd=None):
            return SimpleNamespace(stdout='{"score": 0.8, "reason": "target is better"}')
    monkeypatch.setattr(scorer, "make_provider", lambda cfg: GoodJudge())
    cfg = SimpleNamespace(name="claude", model="claude-opus-4-8", args=[])
    result = scorer.score_run(_run(4), "lens text", "claude", {"claude": cfg})
    assert result.scoring_degraded is False
    assert result.aggregate_score == pytest.approx(0.8)


def test_judge_failures_excluded_from_mean_not_counted_as_half(monkeypatch):
    """Eval-hardening (2026-06-07): a PARTIAL-degenerate run (some judge failures,
    but < 50%) must not let the failure-0.5s pad the mean. The aggregate is the
    mean of GENUINE judgements only; failures are counted separately. A critic's
    'your judge-failures drag the number toward 0.5' attack is closed."""
    class MixedJudge:
        def __init__(self, real_scores):
            self.real = list(real_scores)
            self.i = 0

        def run(self, prompt, cwd=None):
            self.i += 1
            if self.i <= len(self.real):
                return SimpleNamespace(stdout=f'{{"score": {self.real[self.i-1]}, "reason": "ok"}}')
            return SimpleNamespace(stdout="")  # judge failure → would default to 0.5

    judge = MixedJudge([0.6, 0.7, 0.9, 1.0])  # 4 genuine, mean 0.8
    monkeypatch.setattr(scorer, "make_provider", lambda cfg: judge)
    cfg = SimpleNamespace(name="claude", model="claude-opus-4-8", args=[])
    result = scorer.score_run(_run(5), "lens text", "claude", {"claude": cfg})  # 5th item → failure

    assert result.scoring_degraded is False        # 1/5 failures, below the 50% floor
    assert result.judge_failures == 1
    assert result.n_scored == 4                     # only the genuine judgements
    # The mean is over the 4 real scores (0.8), NOT contaminated by the 0.5 failure.
    # Old behaviour: (0.6+0.7+0.9+1.0+0.5)/5 = 0.74 — the contamination this closes.
    assert result.aggregate_score == pytest.approx(0.8)
    assert result.aggregate_ci_half_width is not None and result.aggregate_ci_half_width > 0


def test_ci_half_width_none_for_single_score(monkeypatch):
    """n=1 → no spread to estimate → the ± must be None so the surface shows
    'n too small' rather than a fake-precise interval."""
    class GoodJudge:
        def run(self, prompt, cwd=None):
            return SimpleNamespace(stdout='{"score": 0.9, "reason": "ok"}')
    monkeypatch.setattr(scorer, "make_provider", lambda cfg: GoodJudge())
    cfg = SimpleNamespace(name="claude", model="claude-opus-4-8", args=[])
    result = scorer.score_run(_run(1), "lens text", "claude", {"claude": cfg})
    assert result.n_scored == 1
    assert result.aggregate_score == pytest.approx(0.9)
    assert result.aggregate_ci_half_width is None


def test_alignment_report_drives_judge_selection_and_recording():
    """Eval-hardening wiring (2026-06-08): eval-run prefers the MEASURED most-aligned
    judge from the report, never self-grades, and stamps the agreement onto the run."""
    from trinity_local.commands import eval as ev
    from trinity_local.evals.runner import EvalRunResult

    report = {
        "chosen_judge": "claude",
        "judges": {
            "claude": {"agreement": 0.87, "n_parsed": 18},
            "codex": {"agreement": 0.61, "n_parsed": 18},
        },
    }
    configs = {"claude": SimpleNamespace(enabled=True), "codex": SimpleNamespace(enabled=True)}

    # The aligned judge is chosen when it isn't the target.
    assert ev._alignment_chosen_judge("codex", configs, report) == "claude"
    # NEVER self-grade: if the aligned judge IS the target, fall back (None).
    assert ev._alignment_chosen_judge("claude", configs, report) is None
    # No report / disabled candidate → None (caller uses the heuristic).
    assert ev._alignment_chosen_judge("codex", configs, None) is None
    assert ev._alignment_chosen_judge("codex", {"claude": SimpleNamespace(enabled=False)}, report) is None

    # Recording stamps the chosen judge's measured agreement onto the result.
    r = EvalRunResult(
        eval_id="e", target_provider="codex", target_model=None, started_at="",
        completed_at="", items_total=0, items_completed=0, items_failed=0,
    )
    ev._record_judge_alignment(r, "claude", report)
    assert r.judge_agreement == 0.87
    assert r.judge_alignment_n == 18


def test_quota_failed_judge_is_named_and_suppressed(monkeypatch):
    """A rate-limited JUDGE (non-zero exit, empty stdout — the codex-quota outage
    on 2026-06-06) must (a) NOT fabricate a benchmark — #246 suppresses the
    all-0.5 aggregate — AND (b) carry a PRECISE reason naming the dispatch
    failure, so handle_eval_run's degraded-scoring diagnostic can tell 'the judge
    hit a rate limit' apart from a genuinely inconclusive 0.5."""
    class QuotaJudge:
        def run(self, prompt, cwd=None):
            return SimpleNamespace(
                stdout="", stderr="ERROR: You've hit your usage limit", returncode=1
            )
    monkeypatch.setattr(scorer, "make_provider", lambda cfg: QuotaJudge())
    cfg = SimpleNamespace(name="codex", model="gpt-5.5", args=[])
    result = scorer.score_run(_run(4), "lens text", "codex", {"codex": cfg})

    assert result.scoring_degraded is True
    assert result.aggregate_score is None
    reasons = [it.score_reason for it in result.items]
    # The reason now NAMES the cause (usage limit) — more precise than the old
    # "dispatch rc=1" — via describe_provider_failure, while keeping the
    # "judge returned empty output" _DEGENERATE_REASONS prefix so #246 suppresses.
    assert all((r or "").startswith("judge returned empty output") for r in reasons), reasons
    assert all("usage limit reached" in (r or "") for r in reasons), reasons
