"""Output-shape smoke tests for live record/file producers — #193.

The bug class: a feature runs, doesn't crash, and produces EMPTY (or
wrong-shaped) output. Unit tests on the internals pass; only feeding
realistic input through the whole producer and asserting the output is
non-empty catches it. The #191 history audit found this class twice in
shipped code:
  - 030bad4: memory-compare silently returned 0/0 on real installs
  - the moves substrate: gate ran, zero promotions, substrate dormant

Both producers were retired, so these smokes target the LIVE ones:
  - build_eval_set (the eval harness entry)
  - stage4_post_filter (the lens-build final stage that writes lenses)

Each producer gets two smokes:
  1. realistic non-empty input → NON-EMPTY output (the core guard)
  2. empty/absent input → fails loudly OR returns empty — never
     silently fabricates garbage

These are deterministic (no LLM, no embedder-dependent path under the
count-only default), so they run on any backend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import write_prompt_node as _write_prompt_node


# ─── Producer 1: build_eval_set (eval harness) ──────────────────────


def _seed_rejections(home: Path, n: int) -> None:
    # #209: the eval harness reads the unified ledger now, so seed
    # model_miss preference acts (the rejection subset) directly. Each act
    # points at a resolving prompt node (text != privileged) so the builder
    # keeps it — mirrors the live corpus, where rejections carry a real prompt.
    led_path = home / "me" / "preference_acts.jsonl"
    led_path.parent.mkdir(parents=True, exist_ok=True)
    types = ["REFRAME", "REDIRECT", "COMPRESSION", "SHARPENING"]
    with led_path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            pid = f"pn_{i:04d}"
            _write_prompt_node(home, pid, f"the original question for item {i}")
            fh.write(json.dumps({
                "id": f"r_{i:04d}",
                "trigger": "model_miss",
                "privileged": f"just the spec, item {i}",
                "sacrificed": f"a verbose multi-paragraph answer number {i}",
                "kind": types[i % len(types)],
                "why": "user wanted the shape changed",
                "basin": f"b{i % 3:02d}",
                "prompt_id": pid,
            }) + "\n")


class TestEvalBuildOutputShape:
    def test_nonempty_rejections_produce_nonempty_eval_set(self, patch_trinity_home):
        """The core smoke: given N realistic rejections, the eval set
        has N items. Catches the '030bad4' class — producer runs but
        emits 0 items on real input."""
        _seed_rejections(patch_trinity_home, 5)
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        assert len(eval_set.items) == 5, (
            f"build_eval_set produced {len(eval_set.items)} items from "
            f"5 seeded rejections — the producer ran but dropped/emptied "
            f"the output. Check the field-presence filter in builder.py."
        )
        assert eval_set.stats.get("items") == 5

    def test_absent_rejections_fails_loudly_not_silently_empty(self, patch_trinity_home):
        """Empty/absent input must raise, not return an empty set —
        an empty eval set would silently mask a misconfig (the exact
        anti-pattern builder.py's docstring calls out)."""
        from trinity_local.evals.builder import build_eval_set
        with pytest.raises(FileNotFoundError):
            build_eval_set()

    def test_malformed_rejections_dropped_not_fatal(self, patch_trinity_home):
        """Realistic corpus churn: some rows missing structural fields.
        The producer should keep the valid ones (non-empty output),
        not crash and not return empty."""
        # r_ok needs a resolving prompt or the builder drops it as unresolved.
        _write_prompt_node(patch_trinity_home, "pn_ok", "the original question")
        led_path = patch_trinity_home / "me" / "preference_acts.jsonl"
        led_path.parent.mkdir(parents=True, exist_ok=True)
        led_path.write_text(
            json.dumps({"id": "r_ok", "trigger": "model_miss",
                        "privileged": "terse", "sacrificed": "verbose",
                        "kind": "REFRAME", "basin": "b00",
                        "prompt_id": "pn_ok"}) + "\n"
            + json.dumps({"id": "r_bad"}) + "\n"  # missing privileged/sacrificed
            + "not even json\n",
            encoding="utf-8",
        )
        from trinity_local.evals.builder import build_eval_set
        eval_set = build_eval_set()
        assert len(eval_set.items) == 1, (
            "valid rejection should survive; malformed rows dropped "
            "without taking the whole build down or emptying it"
        )


# ─── Producer 2: stage4_post_filter (lens-build final stage) ────────


class TestLensBuildStage4OutputShape:
    def _make_three_basin_tension(self):
        from trinity_local.me.pair_mining import LensPair
        from trinity_local.me.decisions import Decision
        decisions = [
            Decision(id="d1", privileged="long_view", sacrificed="last_day",
                     valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="last_day", sacrificed="long_view",
                     valence="regret", basin="b01", verbatim="y"),
            Decision(id="d3", privileged="long_view", sacrificed="last_day",
                     valence="correction", basin="b02", verbatim="z"),
        ]
        pair = LensPair(
            pole_a="long_view", pole_b="last_day",
            failure_a="paralysis", failure_b="hedonism",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        return [pair], decisions

    def test_valid_three_basin_tension_yields_nonempty_accepted(self, patch_trinity_home):
        """The core smoke: a tension spanning ≥3 basins must survive
        Stage 4 as an accepted lens. If Stage 4 ever empties the
        accepted set on valid input (the way the moves gate emptied
        the substrate), this fires.

        No basin_centroids passed → the T2 semantic filter is a no-op
        (count-only), so this is deterministic regardless of backend.
        """
        pairs, decisions = self._make_three_basin_tension()
        from trinity_local.me.pipeline import stage4_post_filter
        accepted, orderings = stage4_post_filter(pairs, decisions)
        assert len(accepted) >= 1, (
            "a valid 3-basin tension produced ZERO accepted lenses — "
            "Stage 4 is dropping legitimate input. This is the moves-"
            "dormancy class in the lens pipeline."
        )
        # And it was persisted (the producer writes to disk)
        lenses_file = patch_trinity_home / "me" / "lenses.json"
        assert lenses_file.exists(), "stage4 didn't persist lenses.json"
        written = json.loads(lenses_file.read_text(encoding="utf-8"))
        assert written, "lenses.json written but empty despite valid input"

    def test_topic_local_tension_demoted_not_silently_dropped(self, patch_trinity_home):
        """A 1-basin tension is correctly demoted to ordering — NOT
        silently vanished. Output-shape: the pair still appears, just
        in the orderings bucket, so the signal isn't lost."""
        from trinity_local.me.pair_mining import LensPair
        from trinity_local.me.decisions import Decision
        from trinity_local.me.pipeline import stage4_post_filter
        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="b00", verbatim="y"),
        ]
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1", "d2"], verdict="accepted",
        )
        accepted, orderings = stage4_post_filter([pair], decisions)
        assert len(accepted) == 0
        assert len(orderings) == 1, (
            "single-basin tension should land in orderings (preserved "
            "signal), not vanish entirely"
        )
