"""Tests for the corpus-based eval-set builder (task #122).

Pins the load-bearing schema + extraction behavior. The eval set is
the durable artifact that the runner+scorer consume in follow-up
ticks; if its shape drifts here, every downstream surface breaks.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import write_prompt_node as _write_prompt_node


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _write_rejections(home: Path, entries: list[dict]) -> Path:
    # #209: the unified ledger (preference_acts.jsonl) is the sole store.
    # Seed it via the canonical from_rejection adapter (trigger=model_miss)
    # so eval-build's iter_preference_acts(model_miss) sees these.
    from trinity_local.me.preference_acts import (
        from_rejection,
        preference_acts_path,
        save_preference_acts,
    )
    from trinity_local.me.turn_pairs import RejectionSignal

    acts = []
    for e in entries:
        pid = e.get("prompt_id")
        # Real rejections resolve to a real prompt (111/112 in the live corpus),
        # and the builder now DROPS items whose prompt_id can't resolve (the old
        # user_substitute fallback made prompt == gold by construction — the
        # candidate echoes the gold and every model scores ~1.0). So when a test
        # doesn't pin a prompt_id, auto-provision a resolving node whose text !=
        # user_substitute, keeping the item out of the unresolved + prompt==gold
        # floor-guards. A test that WANTS an unresolved item passes an explicit
        # (truthy) prompt_id with no matching node.
        if not pid:
            pid = f"pn_auto_{e.get('id', 'x')}"
            _write_prompt_node(home, pid, f"original question for {e.get('id', 'x')}")
        sig = RejectionSignal(
            id=e.get("id", ""),
            type=e.get("type", "REFRAME"),
            model_quote=e.get("model_quote", ""),
            user_substitute=e.get("user_substitute", ""),
            why_signal=e.get("why_signal", ""),
            prompt_id=pid,
            basin=e.get("basin"),
            next_user_turn=e.get("next_user_turn", ""),
        )
        acts.append(from_rejection(sig))
    save_preference_acts(acts, allow_shrink=True)
    return preference_acts_path()


class TestBuildEvalSet:
    def test_raises_when_no_rejections_file(self, home):
        from trinity_local.evals.builder import build_eval_set
        with pytest.raises(FileNotFoundError, match="No preference-act ledger"):
            build_eval_set()

    def test_unsupported_source_raises_not_implemented(self, home):
        _write_rejections(home, [])
        from trinity_local.evals.builder import build_eval_set
        with pytest.raises(NotImplementedError, match="cross_provider_pair"):
            build_eval_set(source="cross_provider_pair")

    def test_empty_rejections_yields_empty_set_with_stable_id(self, home):
        _write_rejections(home, [])
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        assert eval_set.items == []
        assert eval_set.stats["items"] == 0
        # Content-addressed: empty set has a deterministic id.
        assert eval_set.eval_id.startswith("eval_")

    def test_real_shape_item_renders(self, home):
        """The schema the runner will consume."""
        _write_prompt_node(home, "pn_42", "Build a spec for the terminal app.", provider="claude")
        _write_rejections(home, [{
            "id": "r_001",
            "type": "REDIRECT",
            "model_quote": "Here's a full GTM strategy...",
            "user_substitute": "Just write the spec.",
            "why_signal": "User ignored the GTM strategy and asked only for a build spec.",
            "prompt_id": "pn_42",
            "basin": "b03",
            "next_user_turn": "",
        }])
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        assert len(eval_set.items) == 1
        item = eval_set.items[0]
        # Every consumer-facing field present
        assert item.eval_item_id.startswith("ei_")
        assert item.prompt == "Build a spec for the terminal app."
        assert item.rejection_type == "REDIRECT"
        assert item.rejected_response == "Here's a full GTM strategy..."
        assert item.user_substitute == "Just write the spec."
        assert "User ignored" in item.rubric_signal
        assert item.basin_id == "b03"
        assert item.source == "rejections"
        assert item.source_id == "r_001"
        assert item.prompt_id == "pn_42"
        # Provider attribution flows through from the PromptNode
        assert item.provider_of_rejected_response == "claude"

    def test_unresolved_prompt_id_is_dropped_not_kept_as_degenerate(self, home):
        """Corpus churn: a rejection's prompt_id no longer resolves. The old
        behaviour fell back to prompt_text = user_substitute, but that makes
        prompt == gold BY CONSTRUCTION — the candidate is fed the gold as its
        prompt (runner: provider.run(item.prompt)), echoes it, and the judge
        (whose gold IS user_substitute) scores ~1.0 for EVERY model. That's the
        exact #247 degeneracy, except worse: resolved-degenerate items get
        dropped, whereas these unresolved ones were kept AND scored, inflating
        the per-model aggregate with zero rejection-axis signal.

        The builder must DROP unresolved items and surface the count in
        stats.skipped_unresolved so the loss is visible, never silent."""
        _write_rejections(home, [{
            "id": "r_orphan",
            "type": "COMPRESSION",
            "model_quote": "A long lecture...",
            "user_substitute": "tldr please",
            "why_signal": "User wanted shorter.",
            "prompt_id": "pn_does_not_exist",
            "basin": "b00",
        }])
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        # Dropped — no honest prompt to score against (would be prompt==gold).
        assert len(eval_set.items) == 0
        assert eval_set.stats["skipped_unresolved"] == 1
        # NOT counted as a resolved-degenerate (#247) drop — different cause.
        assert eval_set.stats["skipped_degenerate"] == 0

    def test_resolved_prompt_equal_to_gold_is_dropped_as_degenerate(self, home):
        """#247 POSITIVE guard: when a rejection's RESOLVED prompt text equals the
        user_substitute (gold), the candidate is fed the gold as its own prompt
        (runner: provider.run(item.prompt)), echoes it, and the judge — whose gold
        IS user_substitute — scores ~1.0 for every model. Zero rejection-axis
        signal that silently inflates the credibility-critical 'Model X scored Y
        on YOUR prompts' claim. The builder must DROP it and count
        skipped_degenerate.

        The suite asserted skipped_degenerate==0 in clean cases but never the
        TRIGGER itself — a regression in the prompt==gold comparison (or its
        case/whitespace normalization, _norm_eval_text) would have leaked
        degenerate items in unnoticed. The prompt here differs from the gold ONLY
        in case + whitespace, so it also exercises that normalization."""
        gold = "use sqlite for a single-user desktop app"
        # prompt text == gold modulo case + collapsed whitespace
        _write_prompt_node(home, "pn_dup", "  USE   SQLite for a Single-User   Desktop App ")
        _write_rejections(home, [{
            "id": "r_dup", "type": "COMPRESSION", "model_quote": "a long lecture",
            "user_substitute": gold, "prompt_id": "pn_dup", "basin": "b00",
        }])
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        assert len(eval_set.items) == 0, "a prompt==gold item must never enter the eval set"
        assert eval_set.stats["skipped_degenerate"] == 1
        assert eval_set.stats["skipped_unresolved"] == 0  # it resolved fine — just degenerate

    def test_fully_dropped_axis_is_surfaced_per_type(self, home):
        """#281: an axis whose model_miss acts are ALL degenerate (prompt==gold)
        vanishes from by_rejection_type — and the bare skipped_degenerate total
        can't say WHICH axis collapsed. The builder must record per-axis drop
        counts + a fully_dropped_types list so the silently-lost axis is visible
        (data_sampling_principle). Mirrors the live COMPRESSION collapse: terse
        user turns ("ship it" / "yes") make user_substitute == prompt for the
        whole axis."""
        # COMPRESSION: two acts, both prompt==gold (degenerate) → axis fully lost.
        _write_prompt_node(home, "pn_c1", "ship it")
        _write_prompt_node(home, "pn_c2", "yes")
        # REFRAME: one scoreable act (prompt != gold) → axis survives.
        _write_prompt_node(home, "pn_r1", "Walk me through the tradeoffs in detail.")
        _write_rejections(home, [
            {"id": "rc1", "type": "COMPRESSION", "model_quote": "a long lecture",
             "user_substitute": "ship it", "prompt_id": "pn_c1", "basin": "b00"},
            {"id": "rc2", "type": "COMPRESSION", "model_quote": "another lecture",
             "user_substitute": "yes", "prompt_id": "pn_c2", "basin": "b00"},
            {"id": "rr1", "type": "REFRAME", "model_quote": "X",
             "user_substitute": "Y", "prompt_id": "pn_r1", "basin": "b01"},
        ])
        from trinity_local.evals.builder import build_eval_set
        s = build_eval_set().stats
        assert s["fully_dropped_types"] == ["COMPRESSION"]
        assert s["skipped_degenerate_by_type"]["COMPRESSION"] == 2
        assert "COMPRESSION" not in s["by_rejection_type"]  # silently absent without #281
        assert s["by_rejection_type"].get("REFRAME") == 1   # surviving axis still counted
        # Per-type counts must reconcile with the bare total (no double-count / drop).
        assert sum(s["skipped_degenerate_by_type"].values()) == s["skipped_degenerate"]

    def test_no_fully_dropped_axes_when_all_have_scoreable_items(self, home):
        """#281 negative guard: when every axis keeps ≥1 item, fully_dropped_types
        is empty and the per-type drop maps stay empty — so a non-empty signal
        always means a real collapse (the warning never cries wolf)."""
        _write_prompt_node(home, "pn_a", "Walk me through the tradeoffs in detail.")
        _write_prompt_node(home, "pn_b", "Give me the full architecture writeup.")
        _write_rejections(home, [
            {"id": "ra", "type": "REFRAME", "model_quote": "A", "user_substitute": "B", "prompt_id": "pn_a"},
            {"id": "rb", "type": "COMPRESSION", "model_quote": "C", "user_substitute": "D", "prompt_id": "pn_b"},
        ])
        from trinity_local.evals.builder import build_eval_set
        s = build_eval_set().stats
        assert s["fully_dropped_types"] == []
        assert s["skipped_degenerate_by_type"] == {}
        assert s["skipped_unresolved_by_type"] == {}

    def test_eval_build_warns_on_fully_dropped_axis(self, home, capsys):
        """#281 CALL-SITE coverage: handle_eval_build must PRINT the warning, not
        just compute the stat. Mutation guard — removing the
        _print_dropped_axis_warning call leaves the helper green but this red."""
        from argparse import Namespace

        from trinity_local.commands.eval import handle_eval_build
        _write_prompt_node(home, "pn_c1", "ship it")
        _write_rejections(home, [{
            "id": "rc1", "type": "COMPRESSION", "model_quote": "a long lecture",
            "user_substitute": "ship it", "prompt_id": "pn_c1", "basin": "b00",
        }])
        handle_eval_build(Namespace(source="rejections", limit=None, eval_id=None))
        out = capsys.readouterr().out
        assert "fully dropped" in out
        assert "COMPRESSION" in out
        assert "lens-build" in out  # the actionable next step is surfaced

    def test_stats_aggregate_by_type_and_basin(self, home):
        # Each rejection needs a RESOLVING prompt_id whose text != the gold, else
        # the builder drops it (unresolved → skipped_unresolved; prompt==gold →
        # skipped_degenerate). Give each a distinct real prompt node.
        _write_prompt_node(home, "pn_1", "Walk me through the tradeoffs in detail.")
        _write_prompt_node(home, "pn_2", "Give me the full architecture writeup.")
        _write_prompt_node(home, "pn_3", "Explain the whole thing end to end.")
        _write_rejections(home, [
            {"id": "r1", "type": "REFRAME", "model_quote": "A", "user_substitute": "B", "prompt_id": "pn_1", "basin": "b00"},
            {"id": "r2", "type": "REFRAME", "model_quote": "C", "user_substitute": "D", "prompt_id": "pn_2", "basin": "b00"},
            {"id": "r3", "type": "COMPRESSION", "model_quote": "E", "user_substitute": "F", "prompt_id": "pn_3", "basin": "b01"},
        ])
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        # by_type counts each rejection_type
        assert eval_set.stats["by_rejection_type"] == {"REFRAME": 2, "COMPRESSION": 1}
        # by_basin counts each basin (entries with basin)
        assert eval_set.stats["by_basin"] == {"b00": 2, "b01": 1}
        # Sorted descending by count so the dominant axes lead.
        types = list(eval_set.stats["by_rejection_type"].items())
        assert types[0][1] >= types[-1][1]

    def test_skips_malformed_rejection_lines(self, home):
        # #209: the builder reads the unified ledger; malformed-line skipping
        # is the ledger loader's job (load_preference_acts). Write malformed
        # ledger lines + one valid model_miss act → only the valid one survives.
        from trinity_local.me.preference_acts import preference_acts_path
        # r1 needs a resolving prompt or the builder drops it as unresolved.
        _write_prompt_node(home, "pn_r1", "the original user question")
        p = preference_acts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join([
            "not json",
            json.dumps({"id": "r1", "trigger": "model_miss", "privileged": "u",
                        "sacrificed": "m", "kind": "REFRAME", "prompt_id": "pn_r1"}),
            "",
            json.dumps({"id": "r2", "trigger": "model_miss"}),  # missing privileged/sacrificed — skip
            json.dumps({"id": "", "trigger": "model_miss", "privileged": "u",
                        "sacrificed": "m"}),  # blank id — skip
        ]) + "\n", encoding="utf-8")
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        # Only r1 survives the structural-field check.
        assert len(eval_set.items) == 1
        assert eval_set.items[0].source_id == "r1"

    def test_limit_caps_items(self, home):
        # Distinct content per row — the unified reader content-dedups, so
        # identical (type, quote, substitute) rows would (correctly) collapse.
        _write_rejections(home, [
            {"id": f"r{i}", "type": "REFRAME", "model_quote": f"m{i}", "user_substitute": f"u{i}", "prompt_id": None}
            for i in range(10)
        ])
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set(limit=3)
        assert len(eval_set.items) == 3

    def test_content_addressed_eval_id_is_idempotent(self, home):
        """Same corpus state → same eval_id. This is what makes results
        diffable across runs (and across model releases)."""
        entries = [
            {"id": "r1", "type": "REFRAME", "model_quote": "m", "user_substitute": "u", "prompt_id": None},
            {"id": "r2", "type": "COMPRESSION", "model_quote": "m", "user_substitute": "u", "prompt_id": None},
        ]
        _write_rejections(home, entries)
        from trinity_local.evals.builder import build_eval_set
        a = build_eval_set()
        b = build_eval_set()
        assert a.eval_id == b.eval_id

    def test_eval_id_changes_when_corpus_changes(self, home):
        from trinity_local.evals.builder import build_eval_set
        _write_rejections(home, [
            {"id": "r1", "type": "REFRAME", "model_quote": "m", "user_substitute": "u", "prompt_id": None},
        ])
        first = build_eval_set().eval_id
        # r2 must be DISTINCT content (not just a distinct id) — the unified
        # reader content-dedups, so a duplicate-content row wouldn't grow the set.
        _write_rejections(home, [
            {"id": "r1", "type": "REFRAME", "model_quote": "m", "user_substitute": "u", "prompt_id": None},
            {"id": "r2", "type": "REFRAME", "model_quote": "m2", "user_substitute": "u2", "prompt_id": None},
        ])
        second = build_eval_set().eval_id
        assert first != second


class TestSaveLoadRoundtrip:
    def test_roundtrip_preserves_all_fields(self, home):
        from trinity_local.evals.builder import build_eval_set, save_eval_set, load_eval_set
        _write_prompt_node(home, "pn_1", "prompt text", provider="antigravity")
        _write_rejections(home, [{
            "id": "r1", "type": "SHARPENING",
            "model_quote": "vague",
            "user_substitute": "be specific about X, Y, Z",
            "why_signal": "user wanted precision",
            "prompt_id": "pn_1",
            "basin": "b04",
        }])
        eval_set = build_eval_set()
        path = save_eval_set(eval_set)
        assert path.exists()
        # Load it back, byte-for-byte equivalent on the shape that
        # matters for downstream consumers (runner / scorer).
        reloaded = load_eval_set(eval_set.eval_id)
        assert reloaded is not None
        assert reloaded.eval_id == eval_set.eval_id
        assert reloaded.source == eval_set.source
        assert reloaded.stats == eval_set.stats
        assert len(reloaded.items) == 1
        a, b = reloaded.items[0], eval_set.items[0]
        assert a.to_dict() == b.to_dict()

    def test_load_returns_none_for_unknown_eval_id(self, home):
        from trinity_local.evals.builder import load_eval_set
        assert load_eval_set("eval_does_not_exist") is None


class TestEvalBuildReScoreNudge:
    """After rebuilding an eval set, the CLI should nudge the user to
    re-score against the new set when prior runs exist (otherwise the
    leaderboard silently goes out of sync). Names the providers with
    prior results and emits copy-paste-ready commands per provider."""

    def _write_result(self, eval_id: str, target: str) -> None:
        from trinity_local.evals.builder import results_dir
        rd = results_dir()
        rd.mkdir(parents=True, exist_ok=True)
        path = rd / f"eval_{eval_id}__model_{target}__20260101T000000.json"
        path.write_text(json.dumps({
            "eval_id": eval_id, "target_provider": target,
            "items_completed": 5, "items": [],
            "aggregate_score": 0.5, "by_rejection_type": {},
        }))

    def test_targets_with_results_returns_distinct_providers(self, home):
        from trinity_local.commands.eval import _targets_with_results
        self._write_result("set_a", "claude")
        self._write_result("set_a", "codex")
        self._write_result("set_b", "claude")  # duplicate target
        assert _targets_with_results() == {"claude", "codex"}

    def test_targets_with_results_excludes_named_eval_id(self, home):
        """The nudge is about RE-scoring — exclude prior runs against
        the same set we just rebuilt."""
        from trinity_local.commands.eval import _targets_with_results
        self._write_result("set_a", "claude")
        self._write_result("set_b", "codex")
        # Pretend we just rebuilt set_a. Claude's set_a run shouldn't
        # count toward "needs re-scoring."
        targets = _targets_with_results(exclude_eval_id="set_a")
        assert targets == {"codex"}

    def test_targets_with_results_handles_missing_dir(self, home):
        from trinity_local.commands.eval import _targets_with_results
        # No results dir yet → empty set, not crash
        assert _targets_with_results() == set()

    def test_nudge_renders_per_target_eval_run_commands(self, home, capsys):
        """End-to-end smoke: after a rebuild with prior results, output
        contains one `eval-run --target X --eval-id Y` line per
        prior-scored provider."""
        from trinity_local.commands.eval import handle_eval_build
        from argparse import Namespace

        # Plant the ledger + prior runs against ANOTHER eval set. The prompt_id
        # must RESOLVE to a real prompt node so the rejection survives as a
        # scoreable item — otherwise build_eval_set drops it (unresolved) and the
        # set has 0 items, which now (correctly) suppresses the re-score nudge.
        _write_prompt_node(home, "pn_1", "How should I phrase the changelog entry?")
        _write_rejections(home, [{
            "id": "r_001", "type": "REFRAME",
            "model_quote": "long explanation",
            "user_substitute": "just the answer",
            "why_signal": "wants direct answers",
            "prompt_id": "pn_1", "basin": "b00", "next_user_turn": "",
        }])
        self._write_result("eval_OLD_set", "claude")
        self._write_result("eval_OLD_set", "codex")

        # Build args; argparse defaults via Namespace mimic
        args = Namespace(source="rejections", limit=None, eval_id=None)
        handle_eval_build(args)
        out = capsys.readouterr().out
        assert "already scored against prior eval sets" in out
        # Per-target commands surfaced
        assert "eval-run --target claude --eval-id" in out
        assert "eval-run --target codex --eval-id" in out


class TestEvalBuildZeroItemRefusesRunnableCTA:
    """Green-while-the-eval-is-empty guard (UX sweep). The launchpad's first-run
    gate is `rejections_available` (the ledger has >=1 act), but a ledger of only
    self_expressed (decision) acts — or all-degenerate model_miss acts — builds a
    0-item eval set. `build_eval_set` raises only on a MISSING ledger, not an empty
    RESULT, so eval-build used to print the runnable `Next: eval-run` success CTA
    over a set with nothing to score, steering the user to dispatch a hollow
    benchmark (real councils, real quota, no signal). On 0 items eval-build must
    REFUSE the runnable CTA and steer back to mining real rejections."""

    def test_zero_item_build_refuses_eval_run_cta(self, home, capsys):
        from argparse import Namespace

        from trinity_local.commands.eval import handle_eval_build

        # A ledger with a self_expressed-only act → rejections_available True, but
        # build_eval_set consumes only model_miss → 0 scoreable items.
        from trinity_local.me.preference_acts import (
            PreferenceAct,
            save_preference_acts,
        )
        save_preference_acts(
            [PreferenceAct(
                id="d1", trigger="self_expressed",
                privileged="ship the simple thing",
                sacrificed="the elaborate thing", kind="simplicity",
            )],
            allow_shrink=True,
        )

        handle_eval_build(Namespace(source="rejections", limit=None, eval_id=None))
        out = capsys.readouterr().out

        assert "Items: 0" in out, f"expected a 0-item build, got: {out!r}"
        # The load-bearing assertion: a 0-item set must NOT present the runnable
        # success CTA — that steers the user to dispatch an empty benchmark.
        assert "Next: `trinity-local eval-run" not in out, (
            "eval-build printed the runnable `Next: eval-run` success CTA over a "
            "0-item (hollow) eval set — the user is told to dispatch a benchmark "
            f"with nothing to score (green-while-the-eval-is-empty). Output: {out!r}"
        )
        # And it must say WHY + how to recover (lead with the answer).
        assert "0 scoreable items" in out, (
            "eval-build did not explain the set has 0 scoreable items — the user "
            f"can't tell the empty set from a real one. Output: {out!r}"
        )
        assert "trinity-local lens" in out, (
            "eval-build did not steer the user back to `lens` to mine real "
            f"model-miss rejections. Output: {out!r}"
        )


class TestDroppedAxisWarning:
    """#281: the eval-build/eval-stats warning that names a fully-dropped
    rejection axis + its cause, so a collapsed axis can't read as 'eval
    complete' (data_sampling_principle green-while-degenerate guard)."""

    def test_prints_axis_cause_and_remedy(self, capsys):
        from trinity_local.commands.eval import _print_dropped_axis_warning
        _print_dropped_axis_warning({
            "fully_dropped_types": ["COMPRESSION"],
            "skipped_degenerate_by_type": {"COMPRESSION": 17},
            "skipped_unresolved_by_type": {},
        })
        out = capsys.readouterr().out
        assert "COMPRESSION" in out
        assert "17 degenerate" in out          # the cause + count
        assert "lens-build" in out             # the remedy

    def test_reports_unresolved_cause(self, capsys):
        from trinity_local.commands.eval import _print_dropped_axis_warning
        _print_dropped_axis_warning({
            "fully_dropped_types": ["SHARPENING"],
            "skipped_degenerate_by_type": {},
            "skipped_unresolved_by_type": {"SHARPENING": 4},
        })
        out = capsys.readouterr().out
        assert "SHARPENING" in out
        assert "4 unresolved" in out

    def test_silent_when_nothing_dropped(self, capsys):
        from trinity_local.commands.eval import _print_dropped_axis_warning
        _print_dropped_axis_warning({"fully_dropped_types": []})
        assert capsys.readouterr().out == ""

    def test_silent_on_missing_key(self, capsys):
        # Older eval sets predate fully_dropped_types — must not crash or warn.
        from trinity_local.commands.eval import _print_dropped_axis_warning
        _print_dropped_axis_warning({})
        assert capsys.readouterr().out == ""


class TestEvalCLIRegistered:
    """The CLI is the user-facing surface for the marketing artifact.
    If it's not registered, the eval set never gets built."""

    def test_eval_build_and_eval_stats_in_parser(self):
        import argparse
        from trinity_local import main as main_module
        parser = main_module.build_parser()
        # Find the SubParsersAction specifically — other actions can
        # also have `choices` (e.g. --scope user|project) but their
        # choices are lists, not the subparser-name dict we want.
        sub_actions = [
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        ]
        assert sub_actions, "no subparser action found"
        choices = sub_actions[0].choices  # dict[name -> ArgumentParser]
        assert "eval-build" in choices
        assert "eval-stats" in choices


class TestPreferenceActAdapterContract:
    """Pin the RejectionSignal -> from_rejection -> PreferenceAct ->
    EvalItem adapter chain that Stage-2 eval-build now sources through.

    Every other test in this file writes rejections.jsonl as raw JSON,
    so a field-swap inside from_rejection (e.g. transposing privileged
    vs sacrificed, or mapping the wrong attribute onto kind) would stay
    green because the assertions never seed through the dataclass with
    all fields distinct. This test seeds via save_rejections([
    RejectionSignal(...)]) -- the real producer path -- with values
    that are pairwise distinct and asserts the resulting eval item's
    rejection_type / user_substitute / prompt_id equal the seeded
    RejectionSignal's type / user_substitute / prompt_id. A swap in the
    adapter (e.g. privileged=model_quote) then fails here.
    """

    def test_from_rejection_field_mapping_survives_to_eval_item(self, home):
        from trinity_local.evals.builder import build_eval_set

        # Pairwise-distinct field values so any transposition is visible.
        # The explicit prompt_id needs a resolving node (text != gold) or the
        # builder drops it as unresolved before the field mapping is exercised.
        _write_prompt_node(home, "PROMPT_ID_TEXT", "THE_ORIGINAL_PROMPT_TEXT")
        _write_rejections(home, [{
            "id": "r_adapter",
            "type": "REFRAME",
            "model_quote": "MODEL_QUOTE_TEXT",
            "user_substitute": "USER_SUBSTITUTE_TEXT",
            "why_signal": "WHY_SIGNAL_TEXT",
            "prompt_id": "PROMPT_ID_TEXT",
            "basin": "b07",
            "next_user_turn": "NEXT_USER_TURN_TEXT",
        }])

        eval_set = build_eval_set()
        assert len(eval_set.items) == 1
        item = eval_set.items[0]
        # type -> kind -> rejection_type
        assert item.rejection_type == "REFRAME"
        # user_substitute -> privileged -> user_substitute (NOT model_quote)
        assert item.user_substitute == "USER_SUBSTITUTE_TEXT"
        assert item.rejected_response == "MODEL_QUOTE_TEXT"
        # prompt_id passes through unchanged
        assert item.prompt_id == "PROMPT_ID_TEXT"
        assert item.source_id == "r_adapter"
