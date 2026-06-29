"""#269 — thread-signal scoring + the lens SEED gate.

High-signal threads (real multi-turn decisions/iteration) must clear
LOW_SIGNAL_FLOOR; throwaway tests ("make the monkey better", "say hi"), pure
output-probes, and long mechanical agent-loops must fall below it so the lens
seed ignores them.
"""
from __future__ import annotations

from trinity_local.me.thread_signal import (
    LOW_SIGNAL_FLOOR,
    score_thread,
)


def _deep_thread(turns=20, char=1500):
    return [("x " * (char // 2)).strip() for _ in range(turns)]


class TestScoreThread:
    def test_empty_is_zero(self):
        assert score_thread([]) == 0.0

    def test_deep_substantive_thread_clears_floor(self):
        s = score_thread(_deep_thread())
        assert s >= LOW_SIGNAL_FLOOR
        assert s > 0.3  # genuinely high

    def test_monkey_test_thread_below_floor(self):
        monkey = [
            "make the monkey look better",
            "better",
            "make it better so it looks like a monkey not a blob",
            "put the monkey on the barrels",
        ]
        assert score_thread(monkey) < LOW_SIGNAL_FLOOR

    def test_say_hi_below_floor(self):
        assert score_thread(["say hi"]) < LOW_SIGNAL_FLOOR

    def test_output_probe_below_floor(self):
        assert score_thread(["Reply with exactly the word OK."]) < LOW_SIGNAL_FLOOR

    def test_agent_loop_penalized(self):
        """A long CLI agent-loop must NOT outrank a real deliberative thread of
        the same length — the agent penalty caps it."""
        agent = [
            "check everything in this dir and update claude.md",
            "[Request interrupted by user for tool use]",
            "continue the plan",
        ] * 10
        real = _deep_thread(turns=8)
        assert score_thread(agent) < score_thread(real)

    def test_corrections_raise_score(self):
        base = _deep_thread(turns=6)
        assert score_thread(base, corrections=12) >= score_thread(base, corrections=0)

    def test_outcome_markers_raise_score(self):
        plain = ["here is a long substantive question " * 40 for _ in range(5)]
        resolved = plain + ["that works, perfect, ship it"]
        assert score_thread(resolved) >= score_thread(plain)


class TestSeedGateWiring:
    def test_collect_turn_pairs_imports_the_gate(self):
        """The seed gate must be wired into collect_turn_pairs."""
        import inspect

        from trinity_local.me import pipeline

        src = inspect.getsource(pipeline.collect_turn_pairs)
        assert "compute_thread_signals" in src
        assert "LOW_SIGNAL_FLOOR" in src
        # Three-pass structure: floor+cap, floor-only, thin-corpus fallback.
        assert src.count("_sig(pair)") >= 2


class TestEvalNominationWiring:
    def test_eval_build_prefers_high_signal_threads_when_limiting(self):
        """#269: build_eval_set ranks rejections by their thread's signal before
        truncating to `limit`, so the benchmark draws from the best threads."""
        import inspect

        from trinity_local.evals import builder

        src = inspect.getsource(builder.build_eval_set)
        assert "compute_thread_signals" in src
        assert "items.sort" in src and "items[:limit]" in src
