"""Self-preference (self-enhancement bias) validation — the cross-provider-eval
trust check (#313).

The PURE analysis (`analyze_self_preference`) is the unit-tested core: given per-
response scores from each judge, does a judge inflate its OWN model family? These
tests pin the verdict logic, the degraded-column handling, and the per-model-change
staleness record — all without dispatching to a live model (the dispatch half,
`collect_scores`, rides `score_run` which is covered elsewhere).

Real-data validation 2026-06-09: judges measured NON-self-preferential (claude
−0.19 self-critical, antigravity +0.02, overall z=−2.62).
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _rows(family, own_judge, cross_judge, own_scores, cross_scores):
    """One row per (own, cross) pair for a family."""
    return [
        {"family": family, "scores": {own_judge: o, cross_judge: c}}
        for o, c in zip(own_scores, cross_scores)
    ]


class TestAnalyzeVerdict:
    def test_no_self_preference_when_own_judge_is_harsher(self):
        """The real-world case: a judge scores its OWN family LOWER than the cross
        judge does → negative delta → no self-preference (and self-critical)."""
        from trinity_local.evals.self_preference import (
            analyze_self_preference, VERDICT_NO_PREFERENCE,
        )
        rows = _rows("claude", "claude", "antigravity",
                     own_scores=[0.60, 0.62, 0.58, 0.61, 0.59, 0.63],
                     cross_scores=[0.85, 0.88, 0.84, 0.86, 0.87, 0.83])
        r = analyze_self_preference(rows, ["claude", "antigravity"])
        assert r.verdict == VERDICT_NO_PREFERENCE
        assert r.self_critical is True
        assert r.overall_delta is not None and r.overall_delta < 0

    def test_self_preference_detected_when_own_judge_inflates(self):
        """The bad case the experiment ruled out: a judge scores its OWN family
        HIGHER than the cross judge → positive, significant delta → SELF_PREFERENCE."""
        from trinity_local.evals.self_preference import (
            analyze_self_preference, VERDICT_SELF_PREFERENCE,
        )
        rows = _rows("claude", "claude", "antigravity",
                     own_scores=[0.90, 0.92, 0.88, 0.91, 0.89, 0.93],
                     cross_scores=[0.62, 0.60, 0.64, 0.61, 0.63, 0.59])
        r = analyze_self_preference(rows, ["claude", "antigravity"])
        assert r.verdict == VERDICT_SELF_PREFERENCE
        assert r.overall_delta is not None and r.overall_delta > 0
        assert r.overall_z is not None and r.overall_z > 1.96

    def test_no_preference_when_deltas_straddle_zero(self):
        from trinity_local.evals.self_preference import (
            analyze_self_preference, VERDICT_NO_PREFERENCE,
        )
        rows = _rows("antigravity", "antigravity", "claude",
                     own_scores=[0.80, 0.70, 0.85, 0.65, 0.78, 0.72],
                     cross_scores=[0.78, 0.72, 0.83, 0.67, 0.80, 0.70])
        r = analyze_self_preference(rows, ["antigravity", "claude"])
        assert r.verdict == VERDICT_NO_PREFERENCE
        assert r.self_critical is False


class TestDegradedColumns:
    def test_degraded_judge_column_dropped_and_partial(self):
        """A judge that fails (None) on >20% of rows is dropped; its family's
        self-cell becomes non-computable and the run is flagged partial — the
        codex-credit-outage shape."""
        from trinity_local.evals.self_preference import analyze_self_preference
        # claude + antigravity healthy; codex all-None (the dropped column).
        rows = []
        for o, c in zip([0.7, 0.72, 0.68, 0.71], [0.8, 0.82, 0.78, 0.81]):
            rows.append({"family": "claude", "scores": {"claude": o, "antigravity": c, "codex": None}})
        for _ in range(4):
            rows.append({"family": "codex", "scores": {"claude": 0.7, "antigravity": 0.8, "codex": None}})
        r = analyze_self_preference(rows, ["claude", "codex", "antigravity"])
        assert r.partial is True
        assert "codex" in r.dropped_judges
        codex_cell = next(d for d in r.family_deltas if d.family == "codex")
        assert codex_cell.computable is False  # its own-family judge was dropped
        claude_cell = next(d for d in r.family_deltas if d.family == "claude")
        assert claude_cell.computable is True  # claude judge healthy → still measurable

    def test_inconclusive_with_fewer_than_two_healthy_judges(self):
        from trinity_local.evals.self_preference import (
            analyze_self_preference, VERDICT_INCONCLUSIVE,
        )
        rows = [{"family": "claude", "scores": {"claude": 0.7, "codex": None}} for _ in range(5)]
        r = analyze_self_preference(rows, ["claude", "codex"])
        assert r.verdict == VERDICT_INCONCLUSIVE
        assert r.overall_delta is None


class TestRecordAndStaleness:
    def test_record_round_trips_and_flags_unvalidated_models(self, home):
        from trinity_local.evals.self_preference import (
            analyze_self_preference, save_self_preference_record,
            load_self_preference_record, unvalidated_models,
        )
        rows = _rows("claude", "claude", "antigravity",
                     own_scores=[0.6, 0.62, 0.58, 0.61], cross_scores=[0.8, 0.82, 0.78, 0.81])
        r = analyze_self_preference(rows, ["claude", "antigravity"])
        save_self_preference_record(r, ["claude-fable-5"])

        rec = load_self_preference_record()
        assert rec is not None
        assert rec["verdict"] == r.verdict
        assert "claude-fable-5" in rec["models_validated"]

        # A newly-shipped model not in the record is flagged unvalidated; a
        # validated one is not.
        assert unvalidated_models(["claude-fable-5", "gpt-6"]) == ["gpt-6"]
        assert unvalidated_models(["claude-fable-5"]) == []

    def test_unvalidated_when_no_record_returns_all(self, home):
        from trinity_local.evals.self_preference import unvalidated_models
        assert unvalidated_models(["a", "b"]) == ["a", "b"]


class TestVerbRegistered:
    def test_eval_selfpref_subcommand_registered(self):
        import argparse
        from trinity_local import main as main_module
        parser = main_module.build_parser()
        sub = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
        assert "eval-selfpref" in sub.choices

    def test_handler_imports_cleanly(self):
        # Confirms the ..evals.self_preference import chain resolves at runtime
        # (Pyright can lag on a new module; the import is what matters).
        from trinity_local.commands.eval import handle_eval_selfpref
        assert callable(handle_eval_selfpref)
