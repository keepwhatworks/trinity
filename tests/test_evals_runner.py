"""Tests for the corpus-based eval runner + scorer (task #122 follow-up).

The runner dispatches each eval item's prompt to the target provider;
the scorer asks the chairman-judge whether the candidate response
avoided the rejected response's failure mode. These tests mock provider
dispatch so they don't hit real models — the full run + judge cycle
is too expensive for CI.

Real-corpus validation: a separate manual smoke test runs
`trinity-local eval-run --target gemini --limit 3` on the actual corpus.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _make_provider_config(name: str = "antigravity", *, model: str = "gemini-3-pro"):
    from trinity_local.config import ProviderConfig
    return ProviderConfig(
        name=name,
        type="cli",
        enabled=True,
        label=name.title(),
        command=[name],
        args=[],
        task_types=set(),
        model=model,
    )


def _make_eval_set(items=None):
    """Build an EvalSet with a few items for runner tests."""
    from trinity_local.evals.builder import EvalSet, EvalItem
    if items is None:
        items = [
            EvalItem(
                eval_item_id="ei_aaa",
                prompt="Write a quick spec",
                rejection_type="REDIRECT",
                rejected_response="Here's a 6-section strategy...",
                user_substitute="Just write the spec",
                rubric_signal="user wanted shape, not strategy",
                basin_id="b03",
                source="rejections",
                source_id="r_001",
                prompt_id="pn_1",
                provider_of_rejected_response="claude",
            ),
            EvalItem(
                eval_item_id="ei_bbb",
                prompt="Explain X concisely",
                rejection_type="COMPRESSION",
                rejected_response="X is a long topic that involves...",
                user_substitute="tldr",
                rubric_signal="user wanted shorter",
                basin_id="b00",
                source="rejections",
                source_id="r_002",
                prompt_id="pn_2",
                provider_of_rejected_response="codex",
            ),
        ]
    return EvalSet(
        eval_id="eval_aaaaaaaaaaaa",
        built_at="2026-05-14T00:00:00",
        source="rejections",
        stats={"items": len(items)},
        items=items,
    )


class FakeProvider:
    """Provider stub that returns a fixed response without shelling out.
    Lets us exercise the full runner path without paying for real models."""
    def __init__(self, response_text="A concise answer.", returncode=0, stderr=""):
        self.response_text = response_text
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def run(self, prompt, cwd):
        from trinity_local.providers import ProviderResult
        self.calls.append({"prompt": prompt, "cwd": cwd})
        return ProviderResult(
            provider="fake",
            stdout=self.response_text,
            stderr=self.stderr,
            returncode=self.returncode,
            elapsed_seconds=0.1,
        )


class TestRunner:
    def test_unknown_provider_raises(self, home):
        from trinity_local.evals.runner import run_eval
        eval_set = _make_eval_set()
        with pytest.raises(KeyError, match="Unknown provider"):
            run_eval(eval_set, "nonexistent", {})

    def test_dispatches_each_item_to_target(self, home):
        from trinity_local.evals.runner import run_eval
        fake = FakeProvider(response_text="Spec: ...")
        with patch("trinity_local.evals.runner.make_provider", return_value=fake):
            result = run_eval(
                _make_eval_set(),
                "antigravity",
                {"antigravity": _make_provider_config("antigravity")},
            )
        # One dispatch per eval item
        assert len(fake.calls) == 2
        # Prompts the target saw are the eval items' prompts
        assert fake.calls[0]["prompt"] == "Write a quick spec"
        assert fake.calls[1]["prompt"] == "Explain X concisely"
        # All marked completed
        assert result.items_total == 2
        assert result.items_completed == 2
        assert result.items_failed == 0
        # Each item carries target_response
        assert all(it.target_response == "Spec: ..." for it in result.items)

    def test_provider_failure_captured_per_item(self, home):
        """One bad dispatch must not abort the whole run."""
        from trinity_local.evals.runner import run_eval
        from trinity_local.providers import ProviderResult

        class HalfFailingProvider:
            def __init__(self):
                self.idx = 0
            def run(self, prompt, cwd):
                self.idx += 1
                if self.idx == 2:
                    return ProviderResult(
                        provider="antigravity",
                        stdout="",
                        stderr="rate limit",
                        returncode=1,
                        elapsed_seconds=0.05,
                    )
                return ProviderResult(
                    provider="antigravity",
                    stdout="OK",
                    stderr="",
                    returncode=0,
                    elapsed_seconds=0.05,
                )

        with patch("trinity_local.evals.runner.make_provider", return_value=HalfFailingProvider()):
            result = run_eval(
                _make_eval_set(),
                "antigravity",
                {"antigravity": _make_provider_config("antigravity")},
            )
        assert result.items_total == 2
        assert result.items_completed == 1
        assert result.items_failed == 1
        # The failure is captured per-item, not as a top-level error
        assert result.items[0].target_error is None
        assert "rate limit" in (result.items[1].target_error or "")

    def test_limit_caps_dispatched_items(self, home):
        from trinity_local.evals.runner import run_eval
        fake = FakeProvider()
        with patch("trinity_local.evals.runner.make_provider", return_value=fake):
            result = run_eval(
                _make_eval_set(),
                "antigravity",
                {"antigravity": _make_provider_config("antigravity")},
                limit=1,
            )
        assert len(fake.calls) == 1
        assert result.items_total == 1

    def test_save_and_load_roundtrip(self, home):
        from trinity_local.evals.runner import run_eval, save_run_result, load_run_result
        fake = FakeProvider()
        with patch("trinity_local.evals.runner.make_provider", return_value=fake):
            result = run_eval(
                _make_eval_set(),
                "antigravity",
                {"antigravity": _make_provider_config("antigravity")},
            )
        path = save_run_result(result)
        assert path.exists()
        reloaded = load_run_result(path)
        assert reloaded is not None
        assert reloaded.eval_id == result.eval_id
        assert reloaded.target_provider == "antigravity"
        assert len(reloaded.items) == 2
        assert reloaded.items[0].eval_item_id == "ei_aaa"

    @pytest.mark.parametrize("bad", [
        [{"name": "REFRAME", "mean_score": 0.8, "count": 6}],  # LIST (half-migrated)
        "garbled",                                              # STRING (hand-edit)
        42,                                                     # NUMBER
        None,                                                   # null for a required map
    ])
    def test_load_run_result_coerces_wrong_type_by_rejection_type(self, home, bad):
        """A valid-JSON-but-WRONG-TYPE `by_rejection_type` (a list/string/number/
        null where the per-axis map {axis: {mean_score, count}} is expected — a
        hand-edited / half-migrated / schema-drifted result file) must NOT land RAW
        on the dataclass: `collect_card_data_from_result` then crashes on
        `result.by_rejection_type.keys()` ('list' object has no attribute 'keys')
        and takes down the eval-share PNG render — the corrupt-state-file class.
        The loader coerces non-dict to {} at the boundary so every dataclass
        consumer (the share card, eval-show, the launchpad mirror) sees the dict
        contract. MUTATION: drop the isinstance coercion in runner.load_run_result
        → `collect_card_data_from_result` reds with 'has no attribute keys'.
        """
        import json
        from trinity_local.evals.runner import load_run_result
        from trinity_local.eval_card import collect_card_data_from_result

        p = home / "bad_result.json"
        p.write_text(json.dumps({
            "eval_id": "ev_corrupt",
            "target_provider": "codex",
            "target_model": "gpt-5.5",
            "aggregate_score": 0.81,
            "items_completed": 12,
            "items_total": 12,
            "by_rejection_type": bad,
            "items": [],
            "completed_at": "2026-06-18T12:00:00+00:00",
        }), encoding="utf-8")

        result = load_run_result(p)
        assert result is not None
        # The boundary coercion: a non-dict by_rejection_type degrades to {}.
        assert result.by_rejection_type == {}, (
            "wrong-type by_rejection_type was not coerced to {} at the load "
            f"boundary — it landed raw as {type(result.by_rejection_type).__name__}, "
            "so the share card's .keys() will crash"
        )
        # The downstream consumer must survive (no AttributeError) and emit an
        # empty per-axis breakdown rather than crashing the PNG render.
        card = collect_card_data_from_result(result)
        assert card.by_axis == []
        # The aggregate score is untouched — only the per-axis breakdown is lost.
        assert card.aggregate_score == 0.81


class TestScorer:
    def test_unknown_judge_raises(self, home):
        from trinity_local.evals.runner import run_eval
        from trinity_local.evals.scorer import score_run
        fake = FakeProvider()
        with patch("trinity_local.evals.runner.make_provider", return_value=fake):
            result = run_eval(_make_eval_set(), "antigravity",
                              {"antigravity": _make_provider_config("antigravity")})
        with pytest.raises(KeyError, match="Unknown judge provider"):
            score_run(result, "lens text", "nonexistent",
                      {"antigravity": _make_provider_config("antigravity")})

    def test_parse_judge_response_handles_json(self):
        from trinity_local.evals.scorer import _parse_judge_response
        score, reason = _parse_judge_response('{"score": 0.73, "reason": "matched the user frame"}')
        assert score == pytest.approx(0.73)
        assert reason == "matched the user frame"

    def test_parse_judge_response_handles_markdown_fence(self):
        from trinity_local.evals.scorer import _parse_judge_response
        raw = '```json\n{"score": 0.5, "reason": "neutral"}\n```'
        score, reason = _parse_judge_response(raw)
        assert score == pytest.approx(0.5)
        assert reason == "neutral"

    def test_parse_judge_response_clamps_score_to_unit_interval(self):
        from trinity_local.evals.scorer import _parse_judge_response
        score, _ = _parse_judge_response('{"score": 1.7, "reason": "x"}')
        assert score == 1.0
        score, _ = _parse_judge_response('{"score": -0.2, "reason": "x"}')
        assert score == 0.0

    def test_parse_judge_response_falls_back_to_neutral_on_garbage(self):
        from trinity_local.evals.scorer import _parse_judge_response
        score, reason = _parse_judge_response("the model just emitted prose with no JSON")
        assert score == 0.5
        assert "unparseable" in reason

    def test_score_run_populates_per_item_scores_and_aggregates(self, home):
        from trinity_local.evals.runner import run_eval
        from trinity_local.evals.scorer import score_run

        target_fake = FakeProvider(response_text="The candidate answer")
        judge_responses = iter([
            '{"score": 0.8, "reason": "good match"}',
            '{"score": 0.4, "reason": "too long"}',
        ])
        class JudgeProvider:
            def run(self, prompt, cwd):
                from trinity_local.providers import ProviderResult
                return ProviderResult(
                    provider="claude",
                    stdout=next(judge_responses),
                    stderr="",
                    returncode=0,
                    elapsed_seconds=0.1,
                )

        # Two-step patch: runner gets target_fake, scorer gets judge.
        with patch("trinity_local.evals.runner.make_provider", return_value=target_fake):
            result = run_eval(_make_eval_set(), "antigravity",
                              {"antigravity": _make_provider_config("antigravity")})

        with patch("trinity_local.evals.scorer.make_provider", return_value=JudgeProvider()):
            score_run(
                result, "lens excerpt", "claude",
                {"antigravity": _make_provider_config("antigravity"),
                 "claude": _make_provider_config("claude")},
            )

        # Per-item scores set
        assert result.items[0].score == pytest.approx(0.8)
        assert result.items[1].score == pytest.approx(0.4)
        assert all(it.judge_provider == "claude" for it in result.items)
        # Aggregate = mean of two scores
        assert result.aggregate_score == pytest.approx(0.6)
        # Per-rejection-type breakdown
        assert "REDIRECT" in result.by_rejection_type
        assert "COMPRESSION" in result.by_rejection_type
        assert result.by_rejection_type["REDIRECT"]["mean_score"] == pytest.approx(0.8)
        assert result.by_rejection_type["COMPRESSION"]["mean_score"] == pytest.approx(0.4)

    def test_score_run_skips_failed_dispatches(self, home):
        """Items that failed dispatch get score=None, not 0 — distinguishing
        'model performed badly' from 'dispatch never landed'."""
        from trinity_local.evals.runner import EvalRunResult, EvalItemRun
        from trinity_local.evals.scorer import score_run

        run_result = EvalRunResult(
            eval_id="eval_xxx",
            target_provider="antigravity",
            target_model="gemini-3",
            started_at="2026-05-14T00:00:00",
            completed_at="2026-05-14T00:00:00",
            items_total=2,
            items_completed=1,
            items_failed=1,
            items=[
                EvalItemRun(
                    eval_item_id="ok_item", rejection_type="REFRAME",
                    prompt="p", rejected_response="r", user_substitute="u",
                    rubric_signal="s", basin_id=None,
                    target_response="good answer", target_error=None,
                    elapsed_seconds=0.1,
                ),
                EvalItemRun(
                    eval_item_id="failed_item", rejection_type="REFRAME",
                    prompt="p", rejected_response="r", user_substitute="u",
                    rubric_signal="s", basin_id=None,
                    target_response="", target_error="rate limit",
                    elapsed_seconds=0.0,
                ),
            ],
        )

        class JudgeProvider:
            def run(self, prompt, cwd):
                from trinity_local.providers import ProviderResult
                return ProviderResult(
                    provider="claude", stdout='{"score": 0.7, "reason": "ok"}',
                    stderr="", returncode=0, elapsed_seconds=0.1,
                )

        with patch("trinity_local.evals.scorer.make_provider", return_value=JudgeProvider()):
            score_run(run_result, "lens", "claude",
                      {"claude": _make_provider_config("claude")})

        # Only one item scored — the failed dispatch is left None
        assert run_result.items[0].score == pytest.approx(0.7)
        assert run_result.items[1].score is None
        # Aggregate is mean of SCORED items only
        assert run_result.aggregate_score == pytest.approx(0.7)


class TestSelfJudgeFlag:
    """Judge-policy honesty (founder call 2026-06-09): a judge of the SAME
    provider family as the target grades its own family — biased HIGH. The
    founder relaxed the no-self-judge guard to ALLOW such runs as a labelled
    sanity signal, but they must never headline a cross-model ranking. `score_run`
    stamps `self_judge` so every surface can badge it.

    Mutation: drop the `self_judge = normalize(...) == normalize(...)` assignment
    in score_run → these flip and the CLI/card lose the "not a ranking" guard."""

    def _judge_provider(self):
        class JudgeProvider:
            def run(self, prompt, cwd):
                from trinity_local.providers import ProviderResult
                return ProviderResult(
                    provider="claude", stdout='{"score": 0.9, "reason": "self-flattering"}',
                    stderr="", returncode=0, elapsed_seconds=0.1,
                )
        return JudgeProvider()

    def _run_scored(self, home, target_slug, judge_slug):
        from trinity_local.evals.runner import run_eval
        from trinity_local.evals.scorer import score_run
        target_fake = FakeProvider(response_text="candidate")
        with patch("trinity_local.evals.runner.make_provider", return_value=target_fake):
            result = run_eval(_make_eval_set(), target_slug,
                              {target_slug: _make_provider_config(target_slug)})
        with patch("trinity_local.evals.scorer.make_provider", return_value=self._judge_provider()):
            score_run(result, "lens", judge_slug,
                      {target_slug: _make_provider_config(target_slug),
                       judge_slug: _make_provider_config(judge_slug)})
        return result

    def test_same_family_judge_sets_self_judge(self, home):
        result = self._run_scored(home, "claude", "claude")
        assert result.self_judge is True
        # The aggregate is KEPT (sanity signal) — not nulled like a degraded run.
        assert result.aggregate_score is not None

    def test_cross_family_judge_is_not_self_judge(self, home):
        result = self._run_scored(home, "claude", "codex")
        assert result.self_judge is False

    def test_web_era_slug_normalizes_to_self_judge(self, home):
        # A web-capture target slug (claude_ai) judged by the CLI claude slug is
        # still the same model family — normalize must catch it.
        result = self._run_scored(home, "claude_ai", "claude")
        assert result.self_judge is True

    def test_self_judge_round_trips_through_save_load(self, home):
        from trinity_local.evals.runner import save_run_result, load_run_result
        result = self._run_scored(home, "claude", "claude")
        loaded = load_run_result(save_run_result(result))
        assert loaded is not None
        assert loaded.self_judge is True

    def test_cli_headline_attributes_self_judge_neutrally(self):
        """The CLI headline must NOTE a self-judge run (same family as target) for
        transparency, but NOT penalize it. The self-preference experiment
        (2026-06-09) measured judges as non-self-preferential (claude −0.19 own,
        self-critical; antigravity +0.02), so the old 'biased / NOT a ranking'
        demotion was wrong — a self-judge score ranks like any other judge's. The
        headline attributes ('self-judge — same family as target') without the
        alarmist demotion."""
        import inspect
        from trinity_local.commands import eval as eval_cmd
        src = inspect.getsource(eval_cmd)
        assert 'getattr(run_result, "self_judge", False)' in src
        assert "self-judge" in src
        # the corrected framing: transparency note, not a bias penalty
        assert "non-self-preferential" in src
        assert "NOT a ranking" not in src and "sanity signal only" not in src


class TestEvalRunCLI:
    def test_eval_run_subcommand_registered(self):
        import argparse
        from trinity_local import main as main_module
        parser = main_module.build_parser()
        sub_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        choices = sub_actions[0].choices
        assert "eval-run" in choices
        # Required --target flag
        action = next((a for a in choices["eval-run"]._actions if a.dest == "target"), None)
        assert action is not None and action.required

    def test_handler_iterates_providers_as_dict(self):
        """Regression guard for `for p in config.providers if p.enabled` —
        config.providers is `dict[str, ProviderConfig]`, so iterating
        directly yields the keys (strings) and `p.enabled` blows up with
        AttributeError. Caught only by a real-corpus eval-run on day 2
        of the ship window — the unit tests passed because the handler
        was never invoked end-to-end with a real Config.

        Principle #4 (audit for shape): the same bug existed in multiple
        places originally (eval, handoff, mcp_server). Promote the
        assertion to a per-handler regression so the shape can't quietly
        come back. (The handoff handler this used to also check was
        retired 2026-05-26.)
        """
        import inspect
        from trinity_local.commands import eval as eval_cmd

        # The bad shape is `for p in config.providers` followed by
        # `p.enabled` or `p.name` — that only works for a list. Promote
        # to dict-safe by iterating `.items()` or `.values()`.
        for source in (
            inspect.getsource(eval_cmd.handle_eval_run),
        ):
            assert "for p in config.providers if" not in source, (
                "config.providers is dict[str, ProviderConfig]; iterating "
                "directly yields KEYS (strings), not provider configs. "
                "Use config.providers.items() or .values()."
            )


class TestEvalShowCLI:
    """The eval-show subcommand renders a past run result without
    requiring re-dispatch. Tests use a fake result file rather than
    running a real dispatch."""

    def _seed_result(self, home: Path, *, eval_id="eval_aaaaaaaaaaaa",
                     target="antigravity", aggregate=0.65, mtime=None):
        """Drop a synthetic run result into evals/results/."""
        from trinity_local.evals.runner import EvalItemRun, EvalRunResult, save_run_result
        items = [
            EvalItemRun(
                eval_item_id="ei_top", rejection_type="COMPRESSION",
                prompt="explain succinctly", rejected_response="long lecture",
                user_substitute="tldr", rubric_signal="too long",
                basin_id="b00", target_response="short answer",
                target_error=None, elapsed_seconds=0.1,
                score=0.9, score_reason="concise", judge_provider="claude",
            ),
            EvalItemRun(
                eval_item_id="ei_bot", rejection_type="REFRAME",
                prompt="strategic question", rejected_response="tech answer",
                user_substitute="strategic answer", rubric_signal="missed frame",
                basin_id="b01", target_response="wrong frame again",
                target_error=None, elapsed_seconds=0.1,
                score=0.4, score_reason="still missed", judge_provider="claude",
            ),
        ]
        run = EvalRunResult(
            eval_id=eval_id,
            target_provider=target,
            target_model=f"{target}-mock",
            started_at="2026-05-14T15:00:00",
            completed_at="2026-05-14T15:01:00",
            items_total=2, items_completed=2, items_failed=0,
            items=items,
            aggregate_score=aggregate,
            by_rejection_type={
                "COMPRESSION": {"count": 1, "mean_score": 0.9, "min_score": 0.9, "max_score": 0.9},
                "REFRAME": {"count": 1, "mean_score": 0.4, "min_score": 0.4, "max_score": 0.4},
            },
        )
        path = save_run_result(run)
        if mtime is not None:
            import os
            os.utime(path, (mtime, mtime))
        return path

    def test_show_finds_most_recent_when_no_filter(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        from types import SimpleNamespace
        # Two runs, second one newer
        self._seed_result(home, target="antigravity", aggregate=0.65, mtime=1000)
        self._seed_result(home, target="claude", aggregate=0.80, mtime=2000)
        args = SimpleNamespace(target=None, eval_id=None, limit_samples=0)
        handle_eval_show(args)
        out = capsys.readouterr().out
        # Most-recent picked: target=claude, aggregate=0.80
        assert "claude" in out
        assert "0.800" in out

    def test_show_target_filter_picks_correct_run(self, home, capsys):
        from trinity_local.commands.eval import handle_eval_show
        from types import SimpleNamespace
        self._seed_result(home, target="antigravity", aggregate=0.55, mtime=1000)
        self._seed_result(home, target="claude", aggregate=0.80, mtime=2000)
        # Newest is claude, but we filter to gemini
        args = SimpleNamespace(target="antigravity", eval_id=None, limit_samples=0)
        handle_eval_show(args)
        out = capsys.readouterr().out
        assert "antigravity" in out
        assert "0.550" in out

    def test_show_empty_state_with_actionable_hint(self, home, capsys):
        """No results → exit 1 with the runner command in the message."""
        from trinity_local.commands.eval import handle_eval_show
        from types import SimpleNamespace
        args = SimpleNamespace(target=None, eval_id=None, limit_samples=0)
        with pytest.raises(SystemExit) as exc:
            handle_eval_show(args)
        out = capsys.readouterr().out
        assert exc.value.code == 1
        assert "eval-run" in out

    def test_show_filtered_empty_state_explains_filter(self, home, capsys):
        """Filtering to a non-existent target should explain WHY no
        results — otherwise user thinks the data is missing entirely."""
        from trinity_local.commands.eval import handle_eval_show
        from types import SimpleNamespace
        self._seed_result(home, target="antigravity")
        args = SimpleNamespace(target="ollama", eval_id=None, limit_samples=0)
        with pytest.raises(SystemExit):
            handle_eval_show(args)
        out = capsys.readouterr().out
        assert "ollama" in out
        assert "try without filters" in out.lower() or "Filters" in out

    def test_show_renders_per_axis_breakdown_and_samples(self, home, capsys):
        """The rendered output should include the aggregate score, the
        per-rejection-axis breakdown (the marketing-legible artifact),
        and sample items when --limit-samples > 0."""
        from trinity_local.commands.eval import handle_eval_show
        from types import SimpleNamespace
        self._seed_result(home, target="antigravity")
        args = SimpleNamespace(target=None, eval_id=None, limit_samples=2)
        handle_eval_show(args)
        out = capsys.readouterr().out
        # Aggregate line
        assert "Aggregate score" in out
        # Per-axis breakdown — both rejection types should appear
        assert "COMPRESSION" in out
        assert "REFRAME" in out
        # Top/bottom sample sections
        assert "Top" in out or "Bottom" in out
        # The best item (0.90) should appear
        assert "0.90" in out

    def test_show_subcommand_registered(self):
        import argparse
        from trinity_local import main as main_module
        parser = main_module.build_parser()
        sub_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        choices = sub_actions[0].choices
        assert "eval-show" in choices


class TestGetEvalSummaryMCPRetired:
    """The `get_eval_summary` MCP tool was retired 2026-05-18 (commit
    `1fed7fc`) in the
    pre-launch simplification pass. Agents ground "which model is best
    for me at X" via `ask` + the picks table; the eval-summary surface
    remains on the launchpad card and `eval-show` CLI for direct user
    inspection. This single regression guard catches a future re-add
    that bypasses the user's explicit "agents are smart enough" call."""

    def test_get_eval_summary_tool_is_gone(self):
        import asyncio
        from trinity_local.mcp_server import handle_list_tools
        tools = asyncio.run(handle_list_tools())
        names = {t.name for t in tools}
        assert "get_eval_summary" not in names


class TestDefaultJudgeProviderPicker:
    """Iter #61 caught a silent behavioral bug: `_default_judge_provider`'s
    preferred list was `("claude", "codex", "gemini")` — but `gemini` is
    not a slug in config.json post the 2026-05-21 Antigravity rebrand
    (task #127). The third-preference branch never matched, so eval-run
    with target=claude or target=codex fell through to alphabetical
    search across configs, picking MLX as judge when Antigravity was
    available + preferred. MLX returns empty stdout on judge prompts
    (pre-launch real-run discovery), defaulting every per-axis score
    to 0.5 — silently broken scoring.

    This guard pins the preferred list to the live config slugs so the
    drift can't recur if the slug ever changes again. Same shape as
    principle #14 ("every shipped feature gets a smoke regression guard
    within one tick") — the fix was iter #61; the guard is iter #62.
    """

    def _configs_for(self, *names: str) -> dict:
        return {n: _make_provider_config(n) for n in names}

    def test_picks_antigravity_when_target_is_claude(self):
        from trinity_local.commands.eval import _default_judge_provider
        # MLX intentionally first alphabetically — the bug would pick
        # MLX, the fix should pick antigravity.
        configs = self._configs_for("antigravity", "claude", "codex", "mlx")
        assert _default_judge_provider("claude", configs) == "codex"
        # codex is preferred before antigravity in the priority order
        # since it's a closer chairman-grade match; with codex absent,
        # antigravity wins.
        configs_no_codex = self._configs_for("antigravity", "claude", "mlx")
        assert _default_judge_provider("claude", configs_no_codex) == "antigravity"

    def test_picks_antigravity_when_target_is_codex(self):
        from trinity_local.commands.eval import _default_judge_provider
        # target=codex, claude available — claude wins (highest priority).
        configs = self._configs_for("antigravity", "claude", "codex", "mlx")
        assert _default_judge_provider("codex", configs) == "claude"
        # target=codex, claude absent — antigravity wins, NOT MLX.
        configs_no_claude = self._configs_for("antigravity", "codex", "mlx")
        assert _default_judge_provider("codex", configs_no_claude) == "antigravity"

    def test_no_gemini_in_preferred_list(self):
        """Direct AST-shape check: `gemini` must not appear in the
        preferred-judge list. If a future edit re-adds it (e.g. as
        a misguided "backward compat" entry), this guard fires."""
        import inspect
        from trinity_local.commands import eval as eval_mod
        src = inspect.getsource(eval_mod._default_judge_provider)
        # The tuple literal `preferred = (...)` is the load-bearing line.
        # `gemini` as a string anywhere in that function is a regression.
        # (The docstring intentionally mentions `gemini` in an
        # explanatory note — the assertion targets the tuple line
        # specifically.)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("preferred = ("):
                assert "gemini" not in stripped, (
                    "iter #61 fix regressed: `gemini` re-added to "
                    "_default_judge_provider's preferred list. Slug "
                    "is `antigravity` post task #127's 2026-05-21 "
                    "Antigravity rebrand. Found in: " + stripped
                )
                break
        else:
            raise AssertionError(
                "Could not find `preferred = (...)` line in "
                "_default_judge_provider — has the function been "
                "refactored? Update this guard accordingly."
            )


class TestSuggestAltJudge:
    """When a judge returns empty/degenerate output (the codex-rate-limit /
    non-chat-judge failure mode — dogfooded 2026-06-06: codex hit its usage
    limit, returned empty stdout on every item, every score defaulted to 0.5,
    and #246 nulled the aggregate), the CLI must suggest re-grading with a
    DIFFERENT judge — not the one that just failed, and not the target."""

    def _configs_for(self, *names: str) -> dict:
        return {n: _make_provider_config(n) for n in names}

    def test_excludes_the_failed_judge_and_the_target(self):
        from trinity_local.commands.eval import _suggest_alt_judge
        configs = self._configs_for("antigravity", "claude", "codex", "mlx")
        # codex (the judge) hit its usage limit while scoring target=claude.
        # The suggestion must be neither codex (failed) nor claude (target).
        alt = _suggest_alt_judge("codex", "claude", configs)
        assert alt == "antigravity", alt
        assert alt not in ("codex", "claude")

    def test_claude_first_when_available(self):
        from trinity_local.commands.eval import _suggest_alt_judge
        # antigravity failed scoring target=codex → claude (most reliable
        # structured-verdict judge) is preferred.
        configs = self._configs_for("antigravity", "claude", "codex", "mlx")
        assert _suggest_alt_judge("antigravity", "codex", configs) == "claude"

    def test_falls_back_to_local_when_no_cloud_left(self):
        from trinity_local.commands.eval import _suggest_alt_judge
        # Only mlx remains once codex (failed) and claude (target) are excluded.
        configs = self._configs_for("claude", "codex", "mlx")
        assert _suggest_alt_judge("codex", "claude", configs) == "mlx"

    def test_none_when_no_alternative(self):
        from trinity_local.commands.eval import _suggest_alt_judge
        # Two-provider world: codex failed, claude is the target → nothing left.
        configs = self._configs_for("claude", "codex")
        assert _suggest_alt_judge("codex", "claude", configs) is None


class TestModelIdentityTriple:
    """#239 — the recorded identity is slug + model + thinking level. The same
    model at a different effort is a different contestant, so effort is
    recorded and survives the save/load round-trip + reaches the card."""

    def _config(self, *, model=None, effort=None):
        from trinity_local.config import ProviderConfig
        return ProviderConfig(
            name="claude", type="cli", enabled=True, label="Claude",
            command=["claude"], args=[], task_types=set(),
            model=model, effort=effort,
        )

    def test_identity_effort_recorded_when_separate(self):
        from trinity_local.evals.runner import _identity_effort
        cfg = self._config(model="claude-opus-4-8", effort="high")
        assert _identity_effort(cfg, "claude-opus-4-8") == "high"

    def test_identity_effort_suppressed_when_baked_into_model(self):
        # agy renders the level into the model string — don't double-render.
        from trinity_local.evals.runner import _identity_effort
        cfg = self._config(model="Gemini 3.1 Pro (high)", effort="high")
        assert _identity_effort(cfg, "Gemini 3.1 Pro (high)") is None

    def test_identity_effort_none_when_unset(self):
        from trinity_local.evals.runner import _identity_effort
        cfg = self._config(model="gpt-5.3-codex", effort=None)
        assert _identity_effort(cfg, "gpt-5.3-codex") is None

    def test_effort_round_trips_through_save_load(self, tmp_path, monkeypatch):
        import trinity_local.state_paths as sp
        monkeypatch.setattr(sp, "state_dir", lambda: tmp_path)
        from trinity_local.evals.runner import (
            EvalRunResult, save_run_result, load_run_result,
        )
        rr = EvalRunResult(
            eval_id="e1", target_provider="claude",
            target_model="claude-opus-4-8", target_effort="high",
            started_at="2026-05-30T00:00:00+00:00",
            completed_at="2026-05-30T00:01:00+00:00",
            items_total=3, items_completed=3, items_failed=0,
        )
        path = save_run_result(rr)
        loaded = load_run_result(path)
        assert loaded is not None
        assert loaded.target_effort == "high"

    def test_card_renders_identity_line_with_effort(self):
        from trinity_local.eval_card import collect_card_data_from_result

        class _Result:
            target_provider = "claude"
            target_model = "claude-opus-4-8"
            target_effort = "high"
            aggregate_score = 0.79
            items_total = 20
            items_completed = 20
            by_rejection_type = {}

        data = collect_card_data_from_result(_Result())
        assert data.target_effort == "high"
        assert data.to_dict()["target_effort"] == "high"
