"""Judge-validity gate (architecture council 2026-07-04, item 3).

The eval leaderboard was "the loudest green with the weakest invariant": it
ranked models on a judge measured at 50-65% agreement with the user's own
corrections — coin-flip territory — with no floor anywhere. The gate: a
pre-registered JUDGE_VALIDITY_FLOOR; below it (or unmeasured) every ranking
surface stamps the caveat. Degenerate-data tests assert the stamp FIRES —
the green-gate checklist rule that a refusal must be proven, not assumed."""
from __future__ import annotations

def _result(agreement):
    from trinity_local.evals.runner import EvalRunResult
    rr = EvalRunResult(
        eval_id="e1", target_provider="codex", target_model="gpt-5.5",
        started_at="2026-07-06T00:00:00+00:00", completed_at="2026-07-06T00:01:00+00:00",
        items_total=3, items_completed=3, items_failed=0, items=[],
    )
    rr.judge_agreement = agreement
    return rr


class TestGateDerivation:
    def _apply(self, rr, agreement):
        from trinity_local.commands.eval import _record_judge_alignment
        report = {"judges": {"claude": {"agreement": agreement, "n_parsed": 20}}}
        _record_judge_alignment(rr, "claude", report)
        return rr

    def test_below_floor_marks_not_validated(self):
        rr = self._apply(_result(None), 0.50)
        assert rr.judge_validated is False

    def test_at_floor_validates(self):
        rr = self._apply(_result(None), 0.70)
        assert rr.judge_validated is True

    def test_no_report_leaves_unmeasured(self):
        from trinity_local.commands.eval import _record_judge_alignment
        rr = _result(None)
        _record_judge_alignment(rr, "claude", None)
        assert rr.judge_validated is None

    def test_floor_is_preregistered_not_cleared_by_todays_judges(self):
        """The floor must sit ABOVE the best measured judge (0.65,
        length-confounded) — lowering it to make today's leaderboard green
        is the #35 failure this gate exists to prevent."""
        from trinity_local.evals.runner import JUDGE_VALIDITY_FLOOR
        assert JUDGE_VALIDITY_FLOOR >= 0.70


class TestLeaderboardStamp:
    def test_unvalidated_rows_print_the_caveat(self, capsys):
        from trinity_local.commands.eval import _print_judge_validity_note
        rows = [
            {"target": "codex", "judge_validated": False},
            {"target": "claude", "judge_validated": None},
        ]
        _print_judge_validity_note(rows)
        out = capsys.readouterr().out
        assert "judge validity" in out and "codex" in out and "claude" in out
        assert "directional" in out and "eval-judge-check" in out

    def test_fully_validated_leaderboard_stays_clean(self, capsys):
        from trinity_local.commands.eval import _print_judge_validity_note
        _print_judge_validity_note([{"target": "codex", "judge_validated": True}])
        assert capsys.readouterr().out == ""

    def test_legacy_rows_without_field_get_the_caveat(self, capsys):
        """A result written before the gate existed carries no judge_validated
        key — it must read as unmeasured (caveat), not silently validated."""
        from trinity_local.commands.eval import _print_judge_validity_note
        _print_judge_validity_note([{"target": "antigravity"}])
        out = capsys.readouterr().out
        assert "antigravity" in out and "judge validity" in out
