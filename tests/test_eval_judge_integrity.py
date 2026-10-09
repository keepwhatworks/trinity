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


def test_alignment_report_records_agreement_onto_the_run():
    """The run carries its judge's measured agreement with the extracted corrections
    (a meter the card shows), whichever judge scored it."""
    from trinity_local.commands import eval as ev

    report = {"chosen_judge": None, "judges": {"claude": {"agreement": 0.87, "n_parsed": 18}}}
    r = EvalRunResult(
        eval_id="e", target_provider="codex", target_model=None, started_at="",
        completed_at="", items_total=0, items_completed=0, items_failed=0,
    )
    ev._record_judge_alignment(r, "claude", report)
    assert r.judge_agreement == 0.87
    assert r.judge_alignment_n == 18


def test_corrections_report_never_chooses_the_eval_judge(tmp_path, monkeypatch, capsys):
    """res_169/res_170: the corrections a judge is checked against are machine-extracted
    and no two extractors agree on them beyond kappa 0.26, so they cannot choose the
    judge. A report naming a strongly 'aligned' judge must not move eval-run off its
    fixed default (claude target -> codex). MUTATION: read chosen_judge (or a
    floor-clearing tier) back into the selection and the judge becomes antigravity."""
    import json as _json

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    import trinity_local.config as config_mod
    import trinity_local.evals.builder as builder
    import trinity_local.evals.runner as runner_mod
    from trinity_local.commands import eval as eval_cmd

    providers = {n: SimpleNamespace(name=n, enabled=True, model=None, args=[])
                 for n in ("claude", "codex", "antigravity")}
    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: SimpleNamespace(providers=providers))
    monkeypatch.setattr(builder, "load_eval_set", lambda eid: SimpleNamespace(eval_id=eid))
    monkeypatch.setattr(runner_mod, "run_eval", lambda eval_set, target, cfgs, **kw: _run(2))
    used = []

    def _fake_score(run_result, lens_text, judge, provider_configs, **kw):
        used.append(judge)
        return run_result
    monkeypatch.setattr(scorer, "score_run", _fake_score)
    report = eval_cmd._alignment_report_path()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_json.dumps({"chosen_judge": "antigravity", "judges": {
        "antigravity": {"agreement": 0.95, "n_parsed": 40},
        "codex": {"agreement": 0.55, "n_parsed": 40}}}), encoding="utf-8")

    eval_cmd.handle_eval_run(SimpleNamespace(
        eval_id="eval_x", target="claude", judge=None, skip_score=False, regrade=False,
        limit=None, config=None, skip_floor=True))
    assert used == ["codex"], used
    assert "a meter only" in capsys.readouterr().out


def test_corrections_judge_check_records_no_choice(tmp_path, monkeypatch, capsys):
    """On the extracted corrections, eval-judge-check reports the leader as a meter and
    records chosen_judge=None even when one judge agrees 100%. MUTATION: save `chosen`
    unconditionally and this reds."""
    import json as _json
    from argparse import Namespace

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.commands import eval as eval_cmd
    from trinity_local.evals import judge_alignment as ja

    pairs = [ja.PreferencePair(pair_id=f"p{i}", axis="REFRAME",
                               option_a="GOOD a" if i % 2 else "bad a",
                               option_b="bad b" if i % 2 else "GOOD b",
                               human_side="A" if i % 2 else "B", source_id=f"s{i}")
             for i in range(20)]
    monkeypatch.setattr(ja, "build_preference_pairs", lambda limit=None: pairs)

    class _GoodJudge:
        def run(self, prompt, cwd=None):
            a = prompt[prompt.find("Response A:"):prompt.find("Response B:")]
            return SimpleNamespace(stdout="A" if "GOOD" in a else "B", returncode=0, stderr="")
    monkeypatch.setattr("trinity_local.providers.make_provider", lambda cfg: _GoodJudge())
    monkeypatch.setattr("trinity_local.config.load_config",
                        lambda *a, **k: SimpleNamespace(providers={"claude": SimpleNamespace(enabled=True)}))

    assert eval_cmd.handle_eval_judge_check(Namespace(dataset=None, limit=20, config=None)) == 0
    saved = _json.loads(eval_cmd._alignment_report_path().read_text(encoding="utf-8"))
    assert saved["chosen_judge"] is None
    assert saved["judges"]["claude"]["agreement"] == 1.0
    assert "A meter, not a choice" in capsys.readouterr().out


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


def test_judge_validated_requires_the_alignment_pair_floor():
    """#green-gate (2026-07-17, workflow finding): judge_validated is the trust
    green that gates the 'directional, not decisive' caveat — it only prints
    when validated is not True. It was set on agreement alone with NO n floor,
    so a fallback judge that abstained on noise (chosen_judge=None) still got
    stamped True off a sub-15-pair 1.0 agreement, silencing the caveat on a
    coin flip. The n floor is the SAME MIN_ALIGNMENT_PAIRS the two selection
    paths already enforce. MUTATION: drop the `n < MIN_ALIGNMENT_PAIRS` branch
    and the first assert reds (thin agreement stamps True again)."""
    from types import SimpleNamespace
    from trinity_local.commands.eval import _record_judge_alignment
    from trinity_local.evals.judge_alignment import MIN_ALIGNMENT_PAIRS

    def validated(ag, n):
        rr = SimpleNamespace(judge_agreement=None, judge_alignment_n=None, judge_validated=None)
        _record_judge_alignment(rr, "claude", {"judges": {"claude": {"agreement": ag, "n_parsed": n}}})
        return rr.judge_validated

    # thin measurement -> None (unmeasured), NOT True — the caveat must still fire
    assert validated(1.0, MIN_ALIGNMENT_PAIRS - 1) is None
    # at/above the floor the real gate applies
    assert validated(0.8, MIN_ALIGNMENT_PAIRS) is None   # never True: no reference can validate (res_169)
    assert validated(0.5, MIN_ALIGNMENT_PAIRS + 5) is False
    # never False on a thin sample (that would read as 'invalid judge', wrong)
    assert validated(0.2, 2) is None


def test_thin_axis_shows_no_reportable_score_not_a_mean():
    """green-over-degenerate (2026-07-18, workflow finding): the per-axis eval
    breakdown (eval-run + eval-show, via _axis_breakdown_lines) printed a
    per-axis mean (+ 25-char bar) for EVERY axis with no minimum-sample gate, so
    a n=1 COMPRESSION axis read as a real 1.000 score with the same visual weight
    as a n=30 axis — the live #281 shape. Wire the pre-registered MIN_AXIS_N
    floor: a thin axis shows 'not reportable', never a mean. MUTATION: drop the
    `n < MIN_AXIS_N` branch and the thin axis prints a mean → this reds."""
    from trinity_local.commands.eval import _axis_breakdown_lines
    from trinity_local.evals.composition_floor import MIN_AXIS_N

    by_axis = {
        "COMPRESSION": {"count": MIN_AXIS_N - 1, "mean_score": 1.0, "min_score": 1.0, "max_score": 1.0},
        "REFRAME": {"count": MIN_AXIS_N + 5, "mean_score": 0.42, "min_score": 0.0, "max_score": 1.0},
    }
    for bar in (False, True):
        lines = _axis_breakdown_lines(by_axis, bar=bar)
        comp = [ln for ln in lines if ln.strip().startswith("COMPRESSION")]
        refr = [ln for ln in lines if ln.strip().startswith("REFRAME")]
        assert comp and "no reportable" in comp[0] and "mean=" not in comp[0], (
            f"thin COMPRESSION axis must show 'no reportable', not a mean (bar={bar}): {comp}"
        )
        # the well-sampled axis still shows its real score
        assert refr and "mean=0.420" in refr[0], (
            f"well-sampled REFRAME axis must still print its mean (bar={bar}): {refr}"
        )
