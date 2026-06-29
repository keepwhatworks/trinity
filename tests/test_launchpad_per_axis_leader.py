"""launchpad per_axis_leader: surface the wedge chips inline.

When ≥2 providers have eval-run results AND the per-axis breakdown is
populated, the launchpad's eval-summary card should render leader
chips above the leaderboard table — "COMPRESSION: codex 0.77 |
REFRAME: claude 0.81". The CLI surfaces this via `eval-show
--compare --by-axis`; the launchpad mirrors so the wedge claim ("X is
best at this kind of question") is visible without leaving the page.

Pins both the data computation (launchpad_data._compute_eval_summary
returns per_axis_leader) and the template rendering (chips appear
in HTML when the data is present).
"""
from __future__ import annotations

import json
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
    items_completed: int = 45,
    judge: str = "claude",
    ts: str = "20260101T000000",
    by_axis: dict | None = None,
) -> Path:
    from trinity_local.evals.builder import results_dir
    results_dir().mkdir(parents=True, exist_ok=True)
    path = results_dir() / f"eval_{eval_id}__model_{target}__{ts}.json"
    path.write_text(json.dumps({
        "eval_id": eval_id,
        "target_provider": target,
        "target_model": f"{target}-model",
        "started_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:10:00+00:00",
        "items_total": items_completed,
        "items_completed": items_completed,
        "items_failed": 0,
        "items": [{"judge_provider": judge, "score": 0.5, "rejection_type": "REFRAME"}],
        "aggregate_score": aggregate,
        "by_rejection_type": {
            axis: {"mean_score": score, "count": 5, "min_score": score, "max_score": score}
            for axis, score in (by_axis or {}).items()
        },
    }))
    return path


class TestPerAxisLeaderData:
    def test_compute_eval_summary_emits_per_axis_leader(self, home):
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        summary = _eval_summary()
        assert "per_axis_leader" in summary
        chips = {c["axis"]: c for c in summary["per_axis_leader"]}
        assert chips["REFRAME"]["target"] == "claude"
        assert chips["COMPRESSION"]["target"] == "codex"
        assert chips["COMPRESSION"]["score"] == 0.77

    def test_per_axis_leader_empty_when_no_by_rejection_type(self, home):
        """Older eval runs lacking by_rejection_type → empty chip list,
        not a crash. Template's v-if hides the chip row entirely."""
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79)
        summary = _eval_summary()
        assert summary["per_axis_leader"] == []

    def test_comparison_rows_carry_by_axis_dict(self, home):
        """The matrix-card path on the launchpad needs per-row by_axis
        scores; pin them so a future refactor doesn't drop them."""
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81})
        summary = _eval_summary()
        # Single-row comparison still emits the by_axis field
        assert "by_axis" in summary["comparison"][0]
        assert summary["comparison"][0]["by_axis"]["REFRAME"] == 0.81

    def test_degenerate_null_score_run_does_not_mask_real_scores(self, home):
        """Live 2026-05-31: a placeholder run with aggregate_score=null sat
        on disk with the NEWEST mtime. The headline picked candidates[0]
        blindly, so the benchmark card showed the empty "not scored yet"
        CTA while 30 real scored results sat behind it. The headline must
        skip null-score runs and surface the most-recent SCORED one."""
        from trinity_local.launchpad_data import _eval_summary
        # Real scored run first (older mtime)...
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   ts="20260101T000000", by_axis={"REFRAME": 0.81})
        # ...then a degenerate placeholder with the NEWEST mtime + no score.
        _write_run(home, eval_id="set_a", target="claude", aggregate=None,
                   ts="20260102T000000")
        summary = _eval_summary()
        assert summary["has_results"] is True
        # Headline reflects the scored run, not the null-score placeholder.
        assert summary["aggregate_score"] == 0.79
        # And the degenerate row is excluded from the comparison leaderboard.
        assert all(r.get("aggregate_score") is not None for r in summary["comparison"])

    def test_mixed_eval_sets_true_when_comparison_spans_eval_ids(self, home):
        """Live 2026-06-01: the launchpad benchmark card compared
        antigravity (on eval set eval_47e41a383a66) against a STALE gemini
        run (on the older eval_d32567a386b9). Scores from different eval sets
        aren't comparable, so _eval_summary must set mixed_eval_sets=True — the
        card uses it to soften the headline + suppress the per-axis leaderboard.
        This flag is tested for eval-show/eval-share but was UNGUARDED on the
        launchpad's own _eval_summary path (the surface the user actually sees).
        """
        from trinity_local.launchpad_data import _eval_summary
        # Two targets, two DIFFERENT eval sets → comparison rows span eval_ids.
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82,
                   ts="20260101T000000", by_axis={"REFRAME": 0.84})
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.44,
                   ts="20260102T000000", by_axis={"REFRAME": 0.40})
        summary = _eval_summary()
        # Sanity: the comparison really does span >1 eval set.
        assert len({r.get("eval_id") for r in summary["comparison"]}) == 2
        assert summary["mixed_eval_sets"] is True

    def test_mixed_eval_sets_false_within_single_eval_set(self, home):
        """The mirror case: when every comparison row is scored on the SAME
        eval set, the scores ARE comparable and the flag must stay False (so the
        card shows the full leaderboard without the mixed-set warning)."""
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82,
                   ts="20260101T000000", by_axis={"REFRAME": 0.84})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.77,
                   ts="20260102T000000", by_axis={"REFRAME": 0.74})
        summary = _eval_summary()
        assert len({r.get("eval_id") for r in summary["comparison"]}) == 1
        assert summary["mixed_eval_sets"] is False


class TestLaunchpadChipsRender:
    def test_chips_render_when_per_axis_leader_populated(self, home):
        from trinity_local.launchpad_template import render_launchpad_html
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        summary = _eval_summary()
        html = render_launchpad_html(
            page_data={"evalSummary": summary},
        )
        # Vue v-for over per_axis_leader; template binding visible in
        # rendered HTML even before runtime.
        assert "per_axis_leader" in html
        # Format string for chip text — matches "<axis>: <target> <score>"
        assert "chip.axis" in html
        assert "chip.target" in html

    def test_per_axis_leader_suppressed_when_sample_too_small(self, home):
        """Live trigger 2026-05-25: COMPRESSION had n=2 per provider on
        the user's eval set, mean spreads of 0.7 between providers, but
        n=2 is noise. The leader-claim suppression rule should refuse
        to declare a winner when any contender on the axis has
        count < 3.

        Override _write_run's default count=5 by writing manually with
        count=2 to match the live shape."""
        import json as _json
        from trinity_local.evals.builder import results_dir
        from trinity_local.launchpad_data import _eval_summary
        rd = results_dir()
        rd.mkdir(parents=True, exist_ok=True)
        # Both providers on set_a, but COMPRESSION has count=2 (noise)
        # and REFRAME has count=10 (signal). Leader chip should fire
        # for REFRAME only.
        for target, scores in [
            ("claude", {"REFRAME": (0.81, 10), "COMPRESSION": (0.12, 2)}),
            ("codex",  {"REFRAME": (0.74, 10), "COMPRESSION": (0.77, 2)}),
        ]:
            path = rd / f"eval_set_a__model_{target}__20260101T000000.json"
            path.write_text(_json.dumps({
                "eval_id": "set_a", "target_provider": target,
                "items": [{"judge_provider": "claude"}], "items_completed": 12,
                "aggregate_score": 0.5,
                "by_rejection_type": {
                    axis: {"mean_score": s, "count": n, "min_score": s, "max_score": s}
                    for axis, (s, n) in scores.items()
                },
            }))
        summary = _eval_summary()
        chips = {c["axis"]: c["target"] for c in summary["per_axis_leader"]}
        # REFRAME fires (n=10 per provider, well above floor)
        assert "REFRAME" in chips
        assert chips["REFRAME"] == "claude"
        # COMPRESSION suppressed (n=2 < threshold)
        assert "COMPRESSION" not in chips, (
            "COMPRESSION leader chip should be suppressed when contenders "
            "have n=2 — same product-correctness rule the user explicitly "
            "ratified by noting 'n=2 is noise, not signal'"
        )

    def test_per_axis_leader_suppressed_when_axis_is_tied(self, home):
        """A per-axis 'X is best at REFRAME' wedge chip needs a REAL winner.
        When the top two providers TIE on an axis (round equal at the 2dp the
        chip shows), the slug tie-break would crown a deterministic but
        ARBITRARY name — a false 'best at' claim on a public surface (green-gate
        #35, the routing cheat-sheet's 'tied' shape). The chip must suppress for
        the tied axis; an axis with a real spread must STILL fire (the gate is
        scoped, not a blanket kill)."""
        from trinity_local.launchpad_data import _eval_summary
        from trinity_local.evals.composition_floor import scores_tied, TIE_DP_AXIS
        # REFRAME: a clear lead (0.81 vs 0.55). COMPRESSION: a TIE (0.75 vs 0.75).
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.78,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.75})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.65,
                   by_axis={"REFRAME": 0.55, "COMPRESSION": 0.75})
        # PRECONDITION (render-independent): COMPRESSION is a real tie, REFRAME is
        # not — so the suppression assertion below is non-vacuous.
        assert scores_tied(0.75, 0.75, dp=TIE_DP_AXIS)
        assert not scores_tied(0.81, 0.55, dp=TIE_DP_AXIS)
        summary = _eval_summary()
        chips = {c["axis"]: c["target"] for c in summary["per_axis_leader"]}
        # REFRAME fires (real spread → real leader).
        assert chips.get("REFRAME") == "claude", (
            f"REFRAME has a real 0.81-vs-0.55 spread; its leader chip must still "
            f"fire — the tie gate over-reached. chips: {chips!r}"
        )
        # THE BITE — the founder symptom: COMPRESSION tied 0.75/0.75 but a leader
        # chip crowned an arbitrary slug-tie-break winner.
        assert "COMPRESSION" not in chips, (
            "FALSE PER-AXIS TIE-WINNER on the launchpad: COMPRESSION is TIED "
            "0.75/0.75 but a per_axis_leader chip named a winner anyway "
            f"(arbitrary slug tie-break leaked the 'X is best at COMPRESSION' "
            f"claim, green-gate #35). chips: {chips!r}"
        )

    def test_per_axis_leader_suppressed_when_mixed_eval_sets(self, home):
        """The per-axis leader chips compare scores across providers.
        When those scores come from DIFFERENT eval sets, the comparison
        is exactly the operation the mixed-set warning forbids. Hide
        the chips entirely rather than render a misleading head-to-head."""
        from trinity_local.launchpad_data import _eval_summary
        # Each provider scored on a different eval set — the drift case
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        summary = _eval_summary()
        assert summary["mixed_eval_sets"] is True
        # Chips suppressed — the head-to-head isn't fair
        assert summary["per_axis_leader"] == [], (
            "per_axis_leader chips must hide when mixed_eval_sets is True "
            "— rendering a head-to-head across different eval sets contradicts "
            "the warning banner that says the comparison is invalid"
        )

    def test_per_axis_leader_renders_when_sets_agree(self, home):
        """Sanity: when both providers scored on the SAME eval set, the
        chips DO render. Suppression triggers only on drift."""
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81, "COMPRESSION": 0.48})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74, "COMPRESSION": 0.77})
        summary = _eval_summary()
        assert summary["mixed_eval_sets"] is False
        # Chips present + correct
        chips = {c["axis"]: c["target"] for c in summary["per_axis_leader"]}
        assert chips["REFRAME"] == "claude"
        assert chips["COMPRESSION"] == "codex"

    def test_mixed_eval_sets_flag_drives_warning_banner(self, home):
        """When the comparison list spans ≥2 distinct eval_ids, the
        launchpad template surfaces the warning banner. Mirrors the CLI
        warning eval-show --compare emits."""
        from trinity_local.launchpad_data import _eval_summary
        from trinity_local.launchpad_template import render_launchpad_html
        # Each provider against a different eval set — the drift case
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81})
        _write_run(home, eval_id="set_b", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74})
        summary = _eval_summary()
        assert summary["mixed_eval_sets"] is True
        html = render_launchpad_html(
            page_data={"evalSummary": summary},
        )
        # Banner v-if is wired so Vue mounts it when the flag fires.
        assert "mixed_eval_sets" in html
        # Banner copy matches the CLI warning vocabulary.
        assert "scores aren't directly comparable" in html

    def test_mixed_eval_sets_false_when_all_rows_agree(self, home):
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79,
                   by_axis={"REFRAME": 0.81})
        _write_run(home, eval_id="set_a", target="codex", aggregate=0.76,
                   by_axis={"REFRAME": 0.74})
        summary = _eval_summary()
        assert summary["mixed_eval_sets"] is False

    def test_comparison_rows_carry_eval_id_for_drift_detection(self, home):
        from trinity_local.launchpad_data import _eval_summary
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.79)
        summary = _eval_summary()
        assert summary["comparison"][0].get("eval_id") == "set_a"


class TestPerAxisLeaderFloorIsOneSourceOfTruth:
    """ANGLE B — the per-provider per-axis LEADER floor was hardcoded
    `MIN_AXIS_SAMPLES = 3` in FIVE places (eval_card.py, launchpad_data.py,
    commands/eval.py ×2) with comments cross-referencing each other as "the same
    threshold." That's a drift trap: bump one copy and the launchpad chips, the
    eval-share PNG matrix, and the CLI eval-show/eval-share leader lines DISAGREE
    about which axis claims are publishable — the #281 confidence-honesty class
    where one surface shows a leader the next surface suppresses. Consolidated to
    one source of truth (evals.composition_floor.MIN_AXIS_LEADER_N) that every
    site imports. These guards BITE if a surface re-hardcodes / drifts.
    """

    def _write_axis_run(self, home: Path, *, target: str, by_axis_with_n: dict, eval_id: str = "set_a", ts: str = "20260101T000000") -> None:
        import json as _json
        from trinity_local.evals.builder import results_dir
        rd = results_dir()
        rd.mkdir(parents=True, exist_ok=True)
        (rd / f"eval_{eval_id}__model_{target}__{ts}.json").write_text(_json.dumps({
            "eval_id": eval_id, "target_provider": target,
            "items": [{"judge_provider": "claude"}], "items_completed": 12,
            "aggregate_score": 0.5,
            "by_rejection_type": {
                axis: {"mean_score": s, "count": n, "min_score": s, "max_score": s}
                for axis, (s, n) in by_axis_with_n.items()
            },
        }))

    def _launchpad_leader_axes(self) -> set[str]:
        from trinity_local.launchpad_data import _eval_summary
        return {c["axis"] for c in _eval_summary()["per_axis_leader"]}

    def _cli_leader_axes(self, tmp_path) -> set[str]:
        """Drive the REAL CLI `eval-share --compare --by-axis --json` path
        (commands/eval.handle_eval_share) and read its emitted per_axis_leader.
        This executes the CLI module's OWN floor, so a behavioral drift in the
        CLI's constant flips this set — the guard bites on the real code path,
        not a re-implementation."""
        import io
        import json as _json
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from trinity_local.commands.eval import handle_eval_share

        args = SimpleNamespace(
            compare=True, by_axis=True, eval_id=None,
            out=str(tmp_path / "compare_matrix.png"), open_after=False, json=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            handle_eval_share(args)
        # The --compare branch prints ONLY the indented summary JSON object.
        out = buf.getvalue()
        summary = _json.loads(out[out.index("{"):])
        return set((summary.get("per_axis_leader") or {}).keys())

    def test_launchpad_and_cli_agree_on_leader_axes_at_the_floor(self, home, tmp_path):
        """Seed a fixture AT the floor boundary: REFRAME is well-sampled on
        both providers (n=10 ≥ floor), COMPRESSION has one provider BELOW the
        floor (codex n=2). The launchpad chips and the CLI leader lines must
        produce the IDENTICAL leader-axis set — REFRAME in, COMPRESSION out.
        They diverge the instant one surface's floor drifts from the other's.
        """
        from trinity_local.evals.composition_floor import MIN_AXIS_LEADER_N
        below = MIN_AXIS_LEADER_N - 1
        above = MIN_AXIS_LEADER_N + 7
        self._write_axis_run(home, target="claude",
                             by_axis_with_n={"REFRAME": (0.81, above), "COMPRESSION": (0.40, above)})
        self._write_axis_run(home, target="codex", ts="20260101T000001",
                             by_axis_with_n={"REFRAME": (0.74, above), "COMPRESSION": (0.95, below)})
        lp = self._launchpad_leader_axes()
        cli = self._cli_leader_axes(tmp_path)
        assert lp == cli, (
            "DIVERGENCE: the launchpad per-axis-leader chips and the CLI "
            "eval-show leader lines disagree on which axes are publishable — "
            f"launchpad={sorted(lp)} vs CLI={sorted(cli)}. The per-axis-leader "
            "floor drifted between surfaces (the old hardcoded MIN_AXIS_SAMPLES=3 "
            "copies); both must import composition_floor.MIN_AXIS_LEADER_N."
        )
        # Boundary is real: REFRAME (above floor on both) leads, COMPRESSION
        # (below floor on codex) is suppressed — on BOTH surfaces.
        assert lp == {"REFRAME"}, (
            f"expected only REFRAME above the n={MIN_AXIS_LEADER_N} floor, got {sorted(lp)}"
        )

    def test_every_surface_imports_the_shared_floor_not_a_local_literal(self):
        """No surface may re-declare `MIN_AXIS_SAMPLES = <literal>`. Each must
        import composition_floor.MIN_AXIS_LEADER_N. A re-hardcoded copy (the
        drift trap that motivated the consolidation) reds this immediately.
        """
        import re
        from pathlib import Path as _P
        import trinity_local.evals.composition_floor as _cf
        import trinity_local.eval_card as _ec
        import trinity_local.launchpad_data as _ld

        # The constant resolves to the same object/value everywhere it's used.
        assert _ec.MIN_AXIS_SAMPLES == _cf.MIN_AXIS_LEADER_N

        # Source-scan: no module re-introduces a hardcoded numeric floor as a
        # live assignment (anchored at statement position, not a prose comment).
        # Catches `MIN_AXIS_SAMPLES = 3` / `MIN_AXIS_LEADER_N = 3` reappearing in
        # a consumer module; ignores documentary mentions inside comments.
        src_root = _P(_ld.__file__).parent
        literal = re.compile(r"^\s*MIN_AXIS_(?:SAMPLES|LEADER_N)\s*=\s*\d")
        offenders = []
        for rel in ("eval_card.py", "launchpad_data.py", "commands/eval.py"):
            text = (src_root / rel).read_text(encoding="utf-8")
            for ln, line in enumerate(text.splitlines(), 1):
                if literal.search(line):
                    offenders.append(f"{rel}:{ln}: {line.strip()}")
        assert not offenders, (
            "a per-axis-leader floor was re-hardcoded as MIN_AXIS_SAMPLES = <n> "
            "instead of importing composition_floor.MIN_AXIS_LEADER_N — the exact "
            "five-copy drift trap the consolidation removed:\n  " + "\n  ".join(offenders)
        )

    def test_single_target_low_n_axis_row_is_demoted_via_opacity(self):
        """The eval-summary card lists every axis (with n) for the
        single-target view. Low-n axes (count < 3) should render at
        reduced opacity so they don't look as authoritative as
        well-sampled axes. Visual demotion, not suppression — user
        can still see the data + decide.

        Test pin: rendered HTML carries the conditional opacity
        binding tied to axis.count < 3."""
        from trinity_local.launchpad_template import render_launchpad_html
        html = render_launchpad_html(
            page_data={
                "evalSummary": {
                    "has_results": True,
                    "target": "claude",
                    "aggregate_score": 0.77,
                    "axes": [
                        {"name": "REFRAME", "count": 4, "mean": 0.93, "min": 0.86, "max": 0.98},
                        {"name": "COMPRESSION", "count": 1, "mean": 0.12, "min": 0.12, "max": 0.12},
                    ],
                },
            },
        )
        # Vue conditional opacity tied to axis.count < 3 — the binding
        # exists in the template source. (Pixel-level assertion is too
        # brittle for a launchpad smoke; template binding is the
        # contract a future refactor would have to keep.)
        assert "axis.count < 3" in html, (
            "The axis row's :style binding should include the "
            "'axis.count < 3 → opacity: 0.4' rule so low-sample bars "
            "don't read as authoritative."
        )
        # Tooltip surfaces the reason on hover
        assert "Low-confidence axis" in html

    def test_meta_line_advertises_by_axis_variants(self):
        from trinity_local.launchpad_template import render_launchpad_html
        html = render_launchpad_html(
            page_data={"evalSummary": {"has_results": True}},
        )
        # CLI mirror + PNG export both discoverable from launchpad copy
        assert "--compare --by-axis" in html
        assert "eval-share --compare --by-axis" in html


class TestPerAxisLeaderNeedsAContest:
    """A per-axis "leader" needs a CONTEST. With ONE provider scored on disk,
    the lone row "leads" every axis only because nobody else ran — the council-
    card SOLO-OVERCLAIM shape (#35 green-while-degenerate). The eval-share PNG
    matrix card already demotes this (`_distinct_target_count(rows) <= 1`), but
    THREE sibling surfaces shipped the lone-provider "leader" anyway:
      - CLI `eval-show --compare --by-axis` printed "Per-axis leader: REFRAME →
        claude (0.84)" off a single-provider eval,
      - CLI `eval-share --compare --by-axis --json` emitted a populated
        per_axis_leader dict,
      - launchpad `_eval_summary` emitted per_axis_leader chips (the template
        nested them under comparison>=2, but the DATA carried the overclaim).
    All three now gate on composition_floor.MIN_AXIS_LEADER_CONTENDERS (>=2
    distinct providers, web-slug-folded). These guards drive the REAL paths and
    BITE if any surface drops the contender gate.
    """

    def _cli_show_text(self) -> str:
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from trinity_local.commands.eval import handle_eval_show
        args = SimpleNamespace(
            compare=True, by_axis=True, eval_id=None, target=None,
            json=False, limit=None, out=None, open_after=False,
        )
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                handle_eval_show(args)
        except SystemExit:
            pass
        return buf.getvalue()

    def _cli_share_leader_axes(self, tmp_path) -> set[str]:
        import io
        import json as _json
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from trinity_local.commands.eval import handle_eval_share
        args = SimpleNamespace(
            compare=True, by_axis=True, eval_id=None,
            out=str(tmp_path / "m.png"), open_after=False, json=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            handle_eval_share(args)
        out = buf.getvalue()
        summary = _json.loads(out[out.index("{"):])
        return set((summary.get("per_axis_leader") or {}).keys())

    def test_single_provider_emits_no_leader_on_any_surface(self, home, tmp_path):
        """ONE provider scored (claude, both axes well above the n-floor). No
        surface may declare a per-axis leader — there is no opponent to lead."""
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82,
                   by_axis={"REFRAME": 0.84, "COMPRESSION": 0.80})
        # Precondition: the single-provider sample really IS above the n-floor,
        # so the ONLY thing suppressing the leader is the contender gate (not the
        # sample-size gate). count=5 (>= MIN_AXIS_LEADER_N) is set by _write_run.
        from trinity_local.evals.composition_floor import (
            MIN_AXIS_LEADER_N, distinct_target_count,
        )
        from trinity_local.launchpad_data import _eval_summary
        summary = _eval_summary()
        assert distinct_target_count(summary["comparison"]) == 1, (
            "fixture precondition: exactly one distinct provider on disk"
        )
        assert all(
            (r.get("by_axis_n") or {}).get("REFRAME", 0) >= MIN_AXIS_LEADER_N
            for r in summary["comparison"]
        ), "fixture precondition: REFRAME is above the n-floor (sample gate inert)"

        # 1. CLI eval-show --compare --by-axis text — NO "Per-axis leader:" line.
        text = self._cli_show_text()
        assert "Per-axis leader" not in text, (
            "CLI eval-show --compare --by-axis printed a 'Per-axis leader:' line "
            "off a SINGLE-provider eval — the lone provider 'leads' only because "
            "nobody else ran (the council-card solo-overclaim shape #35). Got:\n"
            + text
        )
        # The per-axis MATRIX TABLE still renders (each score is meaningful per se).
        assert "claude" in text and "REFRAME" in text, (
            "demote-not-hide: the single-provider per-axis matrix table must still "
            "render its own scores; only the head-to-head 'leader' callout drops"
        )

        # 2. CLI eval-share --compare --by-axis --json — empty per_axis_leader.
        cli_axes = self._cli_share_leader_axes(tmp_path)
        assert cli_axes == set(), (
            "eval-share --compare --by-axis --json emitted a per_axis_leader for a "
            f"single-provider eval (axes={sorted(cli_axes)}) — a 'leader' with one "
            "contender is the solo-overclaim shape #35"
        )

        # 3. launchpad _eval_summary — no per_axis_leader chips.
        assert summary["per_axis_leader"] == [], (
            "launchpad _eval_summary emitted per_axis_leader chips for a single-"
            f"provider eval ({summary['per_axis_leader']}) — the data layer must "
            "not ship a 'leader' with one contender; any consumer (the eval-share "
            "JSON, a future side-panel card) would inherit the overclaim"
        )

    def test_two_distinct_providers_still_emit_a_leader(self, home, tmp_path):
        """The contest case (claude vs codex) must STILL declare leaders on all
        three surfaces — the gate suppresses the no-opponent case, not real
        head-to-heads (otherwise it would be a vacuous always-suppress)."""
        _write_run(home, eval_id="set_a", target="claude", aggregate=0.82,
                   by_axis={"REFRAME": 0.84, "COMPRESSION": 0.40})
        _write_run(home, eval_id="set_a", target="codex", ts="20260101T000001",
                   aggregate=0.75, by_axis={"REFRAME": 0.70, "COMPRESSION": 0.95})
        from trinity_local.launchpad_data import _eval_summary
        summary = _eval_summary()
        chips = {c["axis"]: c["target"] for c in summary["per_axis_leader"]}
        assert chips == {"REFRAME": "claude", "COMPRESSION": "codex"}, (
            "two-provider contest must still emit per-axis leaders; the contender "
            f"gate over-suppressed a REAL head-to-head: {chips}"
        )
        assert "Per-axis leader" in self._cli_show_text()
        assert self._cli_share_leader_axes(tmp_path) == {"REFRAME", "COMPRESSION"}

    def test_same_lab_two_slugs_is_not_a_contest(self, home, tmp_path):
        """A `gemini` web-capture run + an `antigravity` CLI run are the SAME lab.
        They must NOT read as two contenders — the contender count folds web-era
        capture slugs to the dispatch slug (distinct_target_count normalization).
        With both folding to antigravity, this is still a one-contender solo case
        and no leader may be declared."""
        _write_run(home, eval_id="set_a", target="gemini", aggregate=0.82,
                   by_axis={"REFRAME": 0.84, "COMPRESSION": 0.80})
        _write_run(home, eval_id="set_a", target="antigravity", ts="20260101T000001",
                   aggregate=0.81, by_axis={"REFRAME": 0.83, "COMPRESSION": 0.79})
        from trinity_local.evals.composition_floor import distinct_target_count
        from trinity_local.launchpad_data import _eval_summary
        summary = _eval_summary()
        # The leaderboard merges both runs into ONE Gemini row (per-target dedup),
        # so there is exactly one contender — no contest.
        assert distinct_target_count(summary["comparison"]) == 1, (
            "gemini + antigravity are the same lab — must fold to one contender"
        )
        assert summary["per_axis_leader"] == [], (
            "two slugs of the SAME lab faked a contest — the contender gate must "
            "normalize web-era capture slugs before counting"
        )
        assert "Per-axis leader" not in self._cli_show_text()
        assert self._cli_share_leader_axes(tmp_path) == set()
