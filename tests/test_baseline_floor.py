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


def _scored_run(scores: list[float | None], reasons: list[str] | None = None) -> EvalRunResult:
    """A run whose items carry pre-judged scores — the shape run_floor_gate
    receives from handle_eval_run (after score_run, before the report)."""
    reasons = reasons or [""] * len(scores)
    items = [
        EvalItemRun(
            eval_item_id=f"e{i}", rejection_type="REFRAME", prompt="q",
            rejected_response="bad", user_substitute="good", rubric_signal="r",
            basin_id=None, target_response="ans", target_error=None,
            elapsed_seconds=0.0, score=s, score_reason=reasons[i],
            judge_provider="claude" if s is not None else None,
        )
        for i, s in enumerate(scores)
    ]
    return EvalRunResult(
        eval_id="ev", target_provider="t", target_model=None,
        started_at="2026-07-11T00:00:00+00:00", completed_at="2026-07-11T00:01:00+00:00",
        items_total=len(items), items_completed=len(items),
        items_failed=0, items=items,
    )


class TestRunFloorGate:
    """The eval-run pre-report gate (wired 2026-07-11 — the deferred #316
    follow-up). Cost cap, apples-to-apples subset mean, judge-failure
    exclusion, and the 3-candidate default are each load-bearing: every test
    here reds if its guard is deleted."""

    def _capture_baselines(self, monkeypatch, scores: dict[str, float]):
        calls: list[tuple[str, int]] = []

        def fake(run, name, *a, **k):
            calls.append((name, len(run.items)))
            return bf.BaselineResult(name=name, aggregate=scores.get(name, 0.0),
                                     n_scored=len(run.items))
        monkeypatch.setattr(bf, "score_baseline", fake)
        return calls

    def test_caps_items_and_compares_against_subset_mean(self, monkeypatch):
        """12 scored items → controls judged on FLOOR_GATE_MAX_ITEMS only, and
        the real aggregate defended is the SUBSET mean (0.8), not the full-run
        mean (0.53) — a capped gate must never mix an 8-item baseline mean with
        a 12-item real mean. Mutation: drop the [:max_items] slice → the capture
        sees 12; drop the subset re-mean → real_aggregate reads 0.5333."""
        calls = self._capture_baselines(monkeypatch, {
            "echo_gold": 0.9, "echo_rejected": 0.05, "echo_prompt": 0.1,
        })
        run = _scored_run([0.8] * 8 + [0.0] * 4)
        v = bf.run_floor_gate(run, "", "claude", {})
        assert v is not None
        assert all(n == bf.FLOOR_GATE_MAX_ITEMS for _, n in calls), calls
        assert v.real_aggregate == pytest.approx(0.8)
        assert v.trustworthy

    def test_default_candidates_are_the_three_gate_controls(self, monkeypatch):
        """The gate's judge bill is len(candidates) × items. The default is the
        pre-registered 3 (gold/rejected/prompt) — silently widening to all 5
        DEGENERATE_CANDIDATES is a 66% cost regression on every eval-run."""
        calls = self._capture_baselines(monkeypatch, {
            "echo_gold": 0.9, "echo_rejected": 0.0, "echo_prompt": 0.0,
        })
        bf.run_floor_gate(_scored_run([0.7, 0.7]), "", "claude", {})
        assert [n for n, _ in calls] == list(bf.FLOOR_GATE_CANDIDATES)
        assert set(bf.FLOOR_GATE_CANDIDATES) == {"echo_gold", "echo_rejected", "echo_prompt"}

    def test_excludes_judge_failure_items_like_the_headline_does(self, monkeypatch):
        """A judge-failure 0.5 (the scorer's _DEGENERATE_REASONS fallback) is
        'no score', excluded from the headline mean — the gate must mirror that
        predicate or its real_aggregate drifts from the number it defends."""
        self._capture_baselines(monkeypatch, {
            "echo_gold": 0.9, "echo_rejected": 0.0, "echo_prompt": 0.0,
        })
        run = _scored_run(
            [0.9, None, 0.5],
            reasons=["", "", "judge returned empty output — quota"],
        )
        v = bf.run_floor_gate(run, "", "claude", {})
        assert v is not None
        assert v.real_aggregate == pytest.approx(0.9)

    def test_returns_none_when_nothing_genuinely_scored(self, monkeypatch):
        self._capture_baselines(monkeypatch, {})
        run = _scored_run([None, 0.5], reasons=["", "judge output unparseable: x"])
        assert bf.run_floor_gate(run, "", "claude", {}) is None


def _verdict(trustworthy: bool, reason: str = "probe") -> "bf.FloorVerdict":
    return bf.FloorVerdict(
        real_aggregate=0.8,
        baselines={"echo_gold": bf.BaselineResult("echo_gold", 0.9, 4),
                   "echo_rejected": bf.BaselineResult("echo_rejected", 0.1, 4)},
        margin=0.7 if trustworthy else 0.02,
        worst_negative="echo_rejected",
        recognition=0.8 if trustworthy else 0.05,
        judge_ok=trustworthy,
        discriminates=trustworthy,
        trustworthy=trustworthy,
        reason=reason,
    )


class TestFloorGateCliSurface:
    """The gate is only real where the number is READ (the surface-binding
    lesson): eval-run must print the refusal INSTEAD of the aggregate, stamp
    the verdict into the saved result, print the pass line on success, and
    honor --skip-floor with a disclosure."""

    def _run_cli(self, tmp_path, monkeypatch, capsys, *, gate, skip_floor=False):
        from types import SimpleNamespace
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.config import ProviderConfig
        import trinity_local.config as config_mod
        import trinity_local.evals.builder as builder
        import trinity_local.evals.runner as runner_mod
        import trinity_local.evals.scorer as scorer_mod
        from trinity_local.commands import eval as eval_cmd

        providers = {
            "claude": ProviderConfig(name="claude", type="cli", enabled=True,
                                     label="Claude", command=["claude", "-p"],
                                     args=[], task_types=set(),
                                     model="claude-opus-4-8", effort="high"),
            "codex": ProviderConfig(name="codex", type="codex", enabled=True,
                                    label="Codex", command=["codex", "exec"],
                                    args=[], task_types=set(),
                                    model="gpt-5.5", effort="high"),
        }
        monkeypatch.setattr(config_mod, "load_config",
                            lambda *a, **k: SimpleNamespace(providers=providers))
        monkeypatch.setattr(builder, "load_eval_set",
                            lambda eid: SimpleNamespace(eval_id=eid))
        monkeypatch.setattr(eval_cmd, "_record_judge_alignment", lambda *a, **k: None)

        def _fake_dispatch(eval_set, target, provider_configs, **kw):
            rr = _scored_run([None, None])
            rr.eval_id = eval_set.eval_id
            rr.target_provider = target
            return rr
        monkeypatch.setattr(runner_mod, "run_eval", _fake_dispatch)

        def _fake_score(run_result, lens_text, judge, provider_configs, **kw):
            for it in run_result.items:
                it.score, it.score_reason, it.judge_provider = 0.8, "ok", judge
            run_result.aggregate_score = 0.8
            run_result.n_scored = len(run_result.items)
            return run_result
        monkeypatch.setattr(scorer_mod, "score_run", _fake_score)
        monkeypatch.setattr(bf, "run_floor_gate", gate)

        args = SimpleNamespace(eval_id="eval_floorcli", target="claude",
                               judge="codex", skip_score=False, regrade=False,
                               limit=None, config=None, skip_floor=skip_floor)
        eval_cmd.handle_eval_run(args)
        return capsys.readouterr().out

    def test_refused_headline_not_printed_and_verdict_stamped(self, tmp_path, monkeypatch, capsys):
        """trustworthy=False → the refusal replaces the score line (a number a
        dumb control matched must not print as a benchmark), and the saved
        result carries baseline_floor so eval-show/launchpad exclude it.
        Mutation: delete the refusal branch in handle_eval_run → RED."""
        out = self._run_cli(tmp_path, monkeypatch, capsys,
                            gate=lambda *a, **k: _verdict(False, reason="EVAL DEGENERATE: probe"))
        assert "HEADLINE REFUSED" in out
        assert "Aggregate score:" not in out
        import json as _json
        from trinity_local.evals.builder import results_dir
        # result_path prepends "eval_" to the (already-prefixed) eval_id
        saved = list(results_dir().glob("eval_eval_floorcli__model_claude__*.json"))
        assert saved, "run result not saved"
        data = _json.loads(saved[0].read_text(encoding="utf-8"))
        assert data["baseline_floor"]["trustworthy"] is False

    def test_passing_floor_prints_pass_line_with_numbers(self, tmp_path, monkeypatch, capsys):
        out = self._run_cli(tmp_path, monkeypatch, capsys,
                            gate=lambda *a, **k: _verdict(True))
        assert "Aggregate score:" in out
        assert "Baseline floor: passed" in out
        assert "echo_rejected" in out  # names the strongest dumb rival

    def test_skip_floor_skips_gate_and_discloses(self, tmp_path, monkeypatch, capsys):
        def _must_not_run(*a, **k):
            raise AssertionError("run_floor_gate called despite --skip-floor")
        out = self._run_cli(tmp_path, monkeypatch, capsys,
                            gate=_must_not_run, skip_floor=True)
        assert "Aggregate score:" in out
        assert "SKIPPED (--skip-floor)" in out


class TestFloorPersistenceAndLeaderboard:
    def test_baseline_floor_roundtrips_and_shape_guards(self, tmp_path, monkeypatch):
        import json as _json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.runner import load_run_result, save_run_result
        rr = _scored_run([0.8])
        rr.aggregate_score = 0.8
        rr.baseline_floor = _verdict(False).to_dict()
        path = save_run_result(rr)
        loaded = load_run_result(path)
        assert loaded.baseline_floor["trustworthy"] is False
        # wrong-TYPE baseline_floor (hand-edit) degrades to None, not a crash
        raw = _json.loads(path.read_text(encoding="utf-8"))
        raw["baseline_floor"] = "garbled"
        path.write_text(_json.dumps(raw), encoding="utf-8")
        assert load_run_result(path).baseline_floor is None

    def test_leaderboard_excludes_refused_run_and_reports_it(self, tmp_path, monkeypatch):
        """A floor-refused run must not rank; the exclusion is DISCLOSED (a
        silent absence reads as 'never benchmarked'). The newest-wins
        fallthrough matches the null-score placeholder rule: an older passing
        run of the same target fills the slot."""
        import os
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands.eval import (
            _collect_leaderboard_rows,
            _print_floor_refusal_note,
        )
        from trinity_local.evals.runner import save_run_result

        clean = _scored_run([0.8])
        clean.aggregate_score = 0.8
        clean.eval_id = "evb"
        clean.target_provider = "codex"
        clean.started_at = "2026-07-10T00:00:00+00:00"
        p_old = save_run_result(clean)

        refused = _scored_run([0.9])
        refused.aggregate_score = 0.9
        refused.eval_id = "evb"
        refused.target_provider = "codex"
        refused.started_at = "2026-07-11T00:00:00+00:00"
        refused.baseline_floor = _verdict(False).to_dict()
        p_new = save_run_result(refused)
        os.utime(p_old, (1_000_000, 1_000_000))
        os.utime(p_new, (2_000_000, 2_000_000))

        rows, _, floor_refused = _collect_leaderboard_rows(None)
        assert floor_refused == ["codex"]
        assert [r["target"] for r in rows] == ["codex"]
        # the OLDER passing run stands in — its 0.8, not the refused 0.9
        assert rows[0]["aggregate_score"] == pytest.approx(0.8)

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_floor_refusal_note(rows, floor_refused)
        note = buf.getvalue()
        assert "headline refused" in note and "codex" in note
        assert "older passing run shown" in note


class TestFloorMirrorsOnOtherClaimSurfaces:
    """The CLI leaderboard, the launchpad hero, and the share-card PNG all
    read the same result files — a refused headline must be withdrawn on ALL
    of them or the exclusion just moves the laundering surface."""

    def test_launchpad_eval_summary_skips_refused_run(self, tmp_path, monkeypatch):
        """Discriminating fixture: the refused run is NEWER and HIGHER (0.9 vs
        0.5) — if the launchpad skip is deleted, the hero headline becomes the
        withdrawn 0.9 and this reds."""
        import os
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.runner import save_run_result
        from trinity_local.launchpad_data import _eval_summary

        clean = _scored_run([0.5])
        clean.aggregate_score = 0.5
        clean.by_rejection_type = {"REFRAME": {"count": 1, "mean_score": 0.5,
                                               "min_score": 0.5, "max_score": 0.5}}
        clean.eval_id = "evlp"
        clean.target_provider = "codex"
        clean.started_at = "2026-07-10T00:00:00+00:00"
        p_clean = save_run_result(clean)

        refused = _scored_run([0.9])
        refused.aggregate_score = 0.9
        refused.by_rejection_type = {"REFRAME": {"count": 1, "mean_score": 0.9,
                                                 "min_score": 0.9, "max_score": 0.9}}
        refused.eval_id = "evlp"
        refused.target_provider = "claude"
        refused.started_at = "2026-07-11T00:00:00+00:00"
        refused.baseline_floor = _verdict(False).to_dict()
        p_refused = save_run_result(refused)
        os.utime(p_clean, (1_000_000, 1_000_000))
        os.utime(p_refused, (2_000_000, 2_000_000))

        summary = _eval_summary()
        assert summary["has_results"] is True
        assert summary["target"] == "codex"
        assert summary["aggregate_score"] == pytest.approx(0.5)

    def test_share_card_refuses_to_render_refused_headline(self):
        """floor_refused runs KEEP their aggregate in the JSON by design, so
        the card's own gate is the only thing between a withdrawn headline and
        a shareable PNG. The flag must (a) be derived from the stamped verdict
        and (b) actually change the paint. Mutation: drop floor_refused from
        the render gate → the two renders are identical → RED."""
        import dataclasses as _dc
        from trinity_local.eval_card import (
            collect_card_data_from_result,
            render_eval_card,
        )

        rr = _scored_run([0.9])
        rr.aggregate_score = 0.9
        rr.by_rejection_type = {"REFRAME": {"count": 1, "mean_score": 0.9,
                                            "min_score": 0.9, "max_score": 0.9}}
        rr.baseline_floor = _verdict(False).to_dict()
        data = collect_card_data_from_result(rr)
        assert data.floor_refused is True

        png_refused = render_eval_card(data)
        png_scored = render_eval_card(_dc.replace(data, floor_refused=False))
        assert png_refused != png_scored, (
            "floor_refused did not change the rendered card — the withdrawn "
            "headline would ship on the PNG"
        )


class TestFloorInnerFieldCorruption:
    def test_pass_line_survives_wrong_typed_baselines(self, tmp_path, monkeypatch, capsys):
        """#304 vein, found by the hour-6 corrupt-state audit: a verdict dict
        whose `baselines` is a STRING (hand-edit / drift) crashed the pass-line
        (`'str'.values()`) — killing eval-run AT THE REPORT, after the full
        judge spend. The guard degrades to 0 items; the line still prints.
        Mutation: drop the isinstance guard on _floor_baselines → RED."""
        cli = TestFloorGateCliSurface()
        bad = _verdict(True)
        bad.baselines = "not a dict"  # wrong-typed inner field
        out = cli._run_cli(tmp_path, monkeypatch, capsys, gate=lambda *a, **k: bad)
        assert "Baseline floor: passed" in out
        assert "controls on 0 items" in out
