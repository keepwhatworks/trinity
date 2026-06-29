"""eval-show --compare: cross-provider leaderboard CLI parity.

Launchpad copy promises `trinity-local eval-show renders the same` as
the leaderboard. This pins the CLI behavior: --compare aggregates
across targets, sorts by aggregate_score desc, scopes by --eval-id,
warns when rows span multiple eval sets.
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _write_run(
    home: Path,
    *,
    eval_id: str,
    target: str,
    aggregate: float | None,
    items_completed: int = 10,
    judge: str = "claude",
    by_axis: dict | None = None,
    items_failed: int = 0,
) -> Path:
    """Drop a synthetic eval result JSON in the canonical location.

    Filename follows runner.result_path():
      eval_<eval_id>__model_<target>__<ts>.json
    """
    from trinity_local.evals.builder import results_dir
    results_dir().mkdir(parents=True, exist_ok=True)
    fname = f"eval_{eval_id}__model_{target}__20260101T000000.json"
    path = results_dir() / fname
    payload = {
        "eval_id": eval_id,
        "target_provider": target,
        "target_model": f"{target}-model",
        "started_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:10:00+00:00",
        "items_total": items_completed + items_failed,
        "items_completed": items_completed,
        "items_failed": items_failed,
        "items": [
            {"judge_provider": judge, "score": 0.5, "rejection_type": "REFRAME"}
        ],
        "aggregate_score": aggregate,
        "by_rejection_type": {
            axis: {"mean_score": score, "count": 5, "min_score": score, "max_score": score}
            for axis, score in (by_axis or {}).items()
        },
    }
    path.write_text(json.dumps(payload))
    return path


def _compare_args(eval_id: str | None = None, by_axis: bool = False) -> Namespace:
    return Namespace(
        target=None,
        eval_id=eval_id,
        limit_samples=0,
        compare=True,
        by_axis=by_axis,
    )


class TestCompareEmptyState:
    def test_no_runs_exits_nonzero_with_hint(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        with pytest.raises(SystemExit) as exc:
            handle_eval_show(_compare_args())
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "No eval results found" in out
        assert "eval-run --target" in out

    def test_filter_to_unknown_eval_id_surfaces_filter(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.8)
        with pytest.raises(SystemExit) as exc:
            handle_eval_show(_compare_args(eval_id="set_DOES_NOT_EXIST"))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "set_DOES_NOT_EXIST" in out


class TestCompareLeaderboard:
    def test_orders_by_aggregate_score_desc(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.78)
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76)
        _write_run(home, eval_id="set_a", target="antigravity", aggregate=0.61)
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        # The ordering check: claude before codex before antigravity.
        i_claude = out.find("claude")
        i_codex = out.find("codex")
        i_antigravity = out.find("antigravity")
        assert 0 <= i_claude < i_codex < i_antigravity, (
            f"Leaderboard order wrong:\n{out}"
        )
        # The leader-margin summary surfaces below the table.
        assert "claude leads codex" in out
        assert "+0.020" in out  # 0.78 - 0.76

    def test_all_tie_does_not_print_false_leader(self, home, capsys):
        """When the top two providers TIE on aggregate, the leader-margin
        line must not claim 'X leads Y by +0.000' — that names a winner of
        a contest that ended even (green-gate #35, the routing cheat-sheet's
        'tied' shape). The CLI is the surface that RANKS providers head-to-
        head, so a false +0.000 lead here propagates the overclaim."""
        from trinity_local.commands.eval import handle_eval_show
        from trinity_local.evals.composition_floor import top_two_tied
        from trinity_local.commands.eval import _collect_leaderboard_rows
        # Discriminating fixture: two providers, identical aggregate.
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.750)
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.750)
        # PRECONDITION B (render-independent): assert the seeded state IS a tie
        # at display precision BEFORE trusting the printed output — so the
        # symptom assertion below is non-vacuous.
        rows, _ = _collect_leaderboard_rows(None)
        assert top_two_tied(rows), "fixture must be a real aggregate tie"
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        # PRECONDITION A: the leaderboard actually rendered (both rows + score).
        assert "0.750" in out and "claude" in out and "codex" in out
        # THE BITE — the founder symptom: a TIED leaderboard printed
        # "X leads Y by +0.000".
        assert "leads" not in out, (
            "FALSE TIE-LEADER in eval-show --compare: claude and codex are TIED "
            "at 0.750 but the CLI printed a '…leads…' margin line (green-gate "
            f"#35 — a winner of a contest that ended even). Output:\n{out}"
        )
        assert "+0.000" not in out, (
            "FALSE +0.000 MARGIN in eval-show --compare: a tie cannot have a "
            f"lead. Output:\n{out}"
        )
        # Must POSITIVELY acknowledge the tie, not silently drop the line.
        assert "tied" in out, (
            f"the tied leaderboard must state the tie honestly. Output:\n{out}"
        )

    def test_judge_provider_surfaces_in_table(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.8, judge="codex")
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.7, judge="claude")
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        # Both judges visible — a model never grading itself is the
        # core claim and worth surfacing.
        assert "codex" in out
        assert "claude" in out

    def test_mixed_eval_sets_warning_fires_when_unscoped(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.8)
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.7)
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        assert "rows span 2 different eval sets" in out
        assert "--eval-id" in out

    @pytest.mark.parametrize("bad_axis", [
        [{"name": "REFRAME", "mean_score": 0.8, "count": 6}],  # LIST (half-migrated)
        "garbled",                                              # STRING (hand-edit)
    ])
    def test_wrong_type_by_rejection_type_does_not_crash_compare(self, home, capsys, bad_axis):
        """A valid-JSON-but-WRONG-TYPE `by_rejection_type` (a list/string where the
        per-axis map is expected) on ONE result file must NOT crash
        `eval-show --compare --by-axis`: the leaderboard reader did
        `(data.get("by_rejection_type") or {}).items()` — a truthy non-dict slips
        past `or {}` and `.items()` raised `'list' object has no attribute 'items'`,
        dumping a raw traceback to the terminal (the corrupt-state-file class). The
        guard coerces non-dict to {} at the read boundary (mirroring the launchpad
        leaderboard fix). The valid sibling row still renders; the corrupt file's
        per-axis breakdown is just dropped. MUTATION: revert the isinstance coercion
        in commands/eval._collect_leaderboard_rows → this reds with a traceback.
        """
        from trinity_local.commands.eval import handle_eval_show
        from trinity_local.evals.builder import results_dir

        # A valid run (renders normally) + a corrupt one (wrong-type axis map).
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.80,
                   by_axis={"REFRAME": 0.8})
        results_dir().mkdir(parents=True, exist_ok=True)
        corrupt = {
            "eval_id": "set_a",
            "target_provider": "codex",
            "target_model": "codex-model",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:10:00+00:00",
            "items_total": 10, "items_completed": 10, "items_failed": 0,
            "items": [{"judge_provider": "claude", "score": 0.5, "rejection_type": "REFRAME"}],
            "aggregate_score": 0.70,
            "by_rejection_type": bad_axis,  # <-- wrong type
        }
        (results_dir() / "eval_set_a__model_codex__20260101T000001.json").write_text(
            json.dumps(corrupt)
        )

        # Must not raise — before the fix this dumped an AttributeError traceback.
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        # The valid sibling row still renders (the corrupt file degraded, not the run).
        assert "claude" in out
        assert "0.80" in out

    def test_eval_id_scope_suppresses_warning_and_filters(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.8)
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.7)
        handle_eval_show(_compare_args(eval_id="set_a"))
        out = capsys.readouterr().out
        assert "rows span" not in out
        assert "eval set: set_a" in out
        # Filtered out: set_b's codex row shouldn't render.
        assert "codex" not in out

    def test_per_target_dedup_keeps_most_recent(self, home, capsys):
        """If a target has multiple runs against the same eval set,
        keep the newest (matches launchpad policy)."""
        from trinity_local.commands.eval import handle_eval_show
        from trinity_local.evals.builder import results_dir
        results_dir().mkdir(parents=True, exist_ok=True)
        older = results_dir() / "eval_set_a__model_claude__20260101T000000.json"
        older.write_text(json.dumps({
            "eval_id": "set_a", "target_provider": "claude",
            "items": [{"judge_provider": "codex"}],
            "items_completed": 10, "aggregate_score": 0.50,
        }))
        newer = results_dir() / "eval_set_a__model_claude__20260201T000000.json"
        newer.write_text(json.dumps({
            "eval_id": "set_a", "target_provider": "claude",
            "items": [{"judge_provider": "codex"}],
            "items_completed": 10, "aggregate_score": 0.95,
        }))
        # Touch newer to ensure mtime ordering.
        import os, time
        os.utime(newer, (time.time(), time.time()))
        handle_eval_show(_compare_args(eval_id="set_a"))
        out = capsys.readouterr().out
        assert "0.950" in out
        assert "0.500" not in out

    def test_web_era_gemini_slug_merges_into_one_antigravity_row(self, home, capsys):
        """A Gemini run stored under the web-era CAPTURE slug `gemini` and one
        under the CLI DISPATCH slug `antigravity` are the SAME provider — they
        must dedup to ONE leaderboard row (newest wins), not render as two.
        Found 2026-06-01 on real data: eval-show --compare listed both
        `antigravity` (0.496) and `gemini` (0.442) as separate rows. Symmetric
        to the launchpad leaderboard canon (#292), which this CLI path missed."""
        import os
        from trinity_local.commands.eval import handle_eval_show
        from trinity_local.evals.builder import results_dir
        results_dir().mkdir(parents=True, exist_ok=True)
        old = results_dir() / "eval_set_a__model_gemini__20260101T000000.json"
        old.write_text(json.dumps({
            "eval_id": "set_a", "target_provider": "gemini",
            "target_model": "Gemini 3.1 Pro",
            "items": [{"judge_provider": "claude"}],
            "items_completed": 18, "aggregate_score": 0.442,
        }))
        new = results_dir() / "eval_set_a__model_antigravity__20260201T000000.json"
        new.write_text(json.dumps({
            "eval_id": "set_a", "target_provider": "antigravity",
            "target_model": "Gemini 3.1 Pro (High)",
            "items": [{"judge_provider": "claude"}],
            "items_completed": 41, "aggregate_score": 0.496,
        }))
        # antigravity is the newest → it wins the merged slot.
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        # A second provider so the board is realistic + the margin line renders.
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.80)
        handle_eval_show(_compare_args(eval_id="set_a"))
        out = capsys.readouterr().out
        assert "antigravity" in out, "the merged Gemini row should use the dispatch slug"
        assert "0.496" in out, "the newest (antigravity) Gemini run should win the slot"
        assert "gemini" not in out, "web-era 'gemini' slug leaked as a SECOND leaderboard row"
        assert "0.442" not in out, "the older, superseded gemini run still shows"

    def test_rescore_nudge_targets_fold_web_era_slugs(self, home):
        """The eval-build re-score nudge lists prior-scored providers AND emits
        `eval-run --target {target}` commands from `_targets_with_results`. A
        web-era `gemini` result must fold to `antigravity` — else the nudge
        double-counts Gemini and prints `--target gemini`, a slug eval-run can't
        dispatch."""
        from trinity_local.commands.eval import _targets_with_results
        _write_run(home, eval_id="set_a", target="gemini", aggregate=0.44)
        _write_run(home, eval_id="set_a", target="antigravity", aggregate=0.50)
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.80)
        targets = _targets_with_results()
        assert "gemini" not in targets, (
            "web-era slug not folded — the nudge would emit `--target gemini`"
        )
        assert targets == {"antigravity", "claude"}, (
            f"Gemini should fold to one antigravity target, got {targets}"
        )


class TestCompareFlagRegistered:
    def test_compare_arg_present(self):
        """Argparse smoke: the flag is registered on the eval-show
        subparser so doc invocations don't fail."""
        from trinity_local.commands.eval import register
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        args = parser.parse_args(["eval-show", "--compare"])
        assert getattr(args, "compare", False) is True

    def test_by_axis_arg_present(self):
        from trinity_local.commands.eval import register
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        args = parser.parse_args(["eval-show", "--compare", "--by-axis"])
        assert getattr(args, "by_axis", False) is True


class TestByAxisMatrix:
    """--by-axis: per-rejection-type cross-provider matrix view."""

    def test_matrix_renders_axes_as_columns(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        # Header carries both axes
        assert "REFRAME" in out
        assert "COMPRESSION" in out
        # Both providers' axis scores render
        assert "0.810" in out  # claude REFRAME
        assert "0.480" in out  # claude COMPRESSION
        assert "0.770" in out  # codex COMPRESSION

    def test_per_axis_leader_callout(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        # The wedge claim: name the right leader per axis
        assert "REFRAME → claude" in out
        assert "COMPRESSION → codex" in out

    def test_missing_axis_for_a_provider_renders_dash(self, home, capsys):
        """If a provider's run didn't cover an axis (older run, partial
        eval), the matrix cell should show '—', not crash."""
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74})  # no COMPRESSION axis
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        # codex row should have a — for COMPRESSION
        codex_line = next(l for l in out.splitlines() if "codex" in l and "leader" not in l)
        # Two possible positions depending on header ordering — just
        # check there's a — somewhere in the codex row.
        assert "—" in codex_line

    def test_no_runs_have_per_axis_falls_back_gracefully(self, home, capsys):
        """Pre-by_rejection_type runs (no axis breakdown) should print a
        helpful hint instead of an empty matrix."""
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.8)  # no by_axis
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        assert "no per-axis breakdown" in out
        assert "eval-run" in out

    def test_aggregate_leader_margin_suppressed_when_mixed_eval_sets(self, home, capsys):
        """Same consistency rule for the AGGREGATE compare path:
        'X leads Y by ±Z' is a head-to-head subtraction. When sets
        mix, the warning says don't compare — so don't print the
        margin line either."""
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79)
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.76)
        handle_eval_show(_compare_args())  # aggregate mode, not by_axis
        out = capsys.readouterr().out
        assert "rows span 2 different eval sets" in out
        assert "leads" not in out  # no head-to-head line

    def test_aggregate_leader_margin_renders_when_sets_agree(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79)
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76)
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        assert "rows span" not in out
        # The leader-margin line surfaces in the agreed-sets case
        assert "claude leads codex" in out
        assert "+0.030" in out

    def test_per_axis_leader_callout_suppressed_when_mixed_eval_sets(self, home, capsys):
        """Same fix shipped to launchpad chips + PNG matrix card —
        the per-axis leader callout synthesizes a head-to-head across
        providers. When those providers scored on DIFFERENT eval sets,
        the comparison is exactly what the mixed-set warning forbids."""
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        # Unscoped so mixed-set warning fires
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        # Warning fires
        assert "rows span 2 different eval sets" in out
        # But the leader callout is suppressed (would name a misleading
        # winner-per-axis across mismatched sets)
        assert "Per-axis leader:" not in out

    def test_per_axis_leader_callout_renders_when_sets_agree(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        # No mixed warning, leader callout IS present
        assert "rows span" not in out
        assert "Per-axis leader:" in out
        assert "REFRAME → claude" in out
        assert "COMPRESSION → codex" in out

    def test_by_axis_without_compare_exits_2(self, home, capsys):
        """--by-axis is only valid inside --compare; lone --by-axis exits
        with a hint not a crash."""
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.8)
        args = Namespace(
            target=None, eval_id=None, limit_samples=0,
            compare=False, by_axis=True,
        )
        with pytest.raises(SystemExit) as exc:
            handle_eval_show(args)
        assert exc.value.code == 2
        out = capsys.readouterr().out
        assert "--by-axis only applies to the leaderboard view" in out


class TestExclusionDisclosure:
    """The leaderboard aggregate is a mean over COMPLETED items only — timeouts
    and dispatch failures are excluded, not scored 0. Providers can fail
    DIFFERENT items, so two aggregates can span different subsets of the eval
    set. The leaderboard (the ranking surface) must disclose this, matching the
    single-provider detail view's "N/M dispatched, K failed". Regression guard
    for the gap found 2026-05-31 (claude 0.818 over 39/42 ranked above
    antigravity 0.496 over 41/42 with no on-surface hint that 3 vs 1 items were
    dropped — and claude's excluded timeouts were a larger swing than its lead)."""

    def test_leaderboard_discloses_per_provider_exclusions(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82, items_failed=3)
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.78, items_failed=0)
        _write_run(home, eval_id="set_a", target="antigravity", aggregate=0.50, items_failed=1)
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        assert "excluded from aggregate" in out.lower(), (
            "leaderboard must disclose dropped items — the ranking spans "
            "different item subsets otherwise, silently."
        )
        # Per-provider counts, only for providers that actually dropped items.
        assert "claude: 3" in out
        assert "antigravity: 1" in out
        assert "codex: 0" not in out, "a provider with 0 failures shouldn't be listed"

    def test_no_disclosure_when_every_provider_scored_full_set(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82, items_failed=0)
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.78, items_failed=0)
        handle_eval_show(_compare_args())
        out = capsys.readouterr().out
        assert "excluded from aggregate" not in out.lower(), (
            "no exclusions ⇒ no disclosure noise — the note must hide itself."
        )

    def test_by_axis_view_also_discloses_exclusions(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82,
                   items_failed=2, by_axis={"REFRAME": 0.84})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.78,
                   items_failed=0, by_axis={"REFRAME": 0.80})
        handle_eval_show(_compare_args(by_axis=True))
        out = capsys.readouterr().out
        assert "excluded from aggregate" in out.lower()
        assert "claude: 2" in out


class TestLaunchpadEvalExclusion:
    """The launchpad's eval card mirrors the CLI leaderboard, so it must carry
    the same exclusion disclosure in its payload (excluded_runs)."""

    def test_eval_summary_payload_exposes_excluded_runs(self, home):
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82, items_failed=3)
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.78, items_failed=0)
        es = _eval_summary()
        excluded = es.get("excluded_runs") or []
        # Only the provider with failures appears; codex (0 failures) is absent.
        targets = {e["items_failed"] for e in excluded}
        assert 3 in targets, f"claude's 3 excluded items missing from payload: {excluded}"
        assert all(e["items_failed"] > 0 for e in excluded), (
            "excluded_runs must list only providers that actually dropped items"
        )
