"""Trajectory lens — diachronic arc-pair extraction (#182).

Pins: within-thread recurrence ≥MIN_ARC_LEN of one kind → a TurnArc;
below threshold → nothing; cross-thread aggregation into Trajectories;
deterministic ordering; storage round-trip; the lens.md section render;
and the chairman-enrichment prompt/parse path.
"""
from __future__ import annotations

import pytest

from trinity_local.me.arc_mining import (
    MIN_ARC_LEN,
    Trajectory,
    TurnArc,
    aggregate_trajectories,
    detect_arcs,
    parse_trajectories,
    render_arc_prompt,
    render_trajectory_lines,
)
from trinity_local.me.preference_acts import MODEL_MISS, SELF_EXPRESSED, PreferenceAct


def _act(id, kind, prompt_id, privileged="cut to the point", trigger=MODEL_MISS):
    return PreferenceAct(
        id=id, trigger=trigger, privileged=privileged, sacrificed="a long answer",
        kind=kind, prompt_id=prompt_id,
    )


class TestDetectArcs:
    def test_recurring_kind_in_one_thread_is_an_arc(self):
        # 3 COMPRESSION acts in thread t1 across turns 0,2,4 → one arc.
        acts = [_act(f"a{i}", "COMPRESSION", f"p{i}") for i in range(3)]
        lookup = {"p0": ("t1", 0), "p1": ("t1", 2), "p2": ("t1", 4)}
        arcs = detect_arcs(acts, lookup)
        assert len(arcs) == 1
        a = arcs[0]
        assert a.kind == "COMPRESSION" and a.transcript_id == "t1"
        assert a.count == 3 and a.turn_span == 4

    def test_below_threshold_is_not_an_arc(self):
        acts = [_act(f"a{i}", "REFRAME", f"p{i}") for i in range(MIN_ARC_LEN - 1)]
        lookup = {f"p{i}": ("t1", i) for i in range(MIN_ARC_LEN - 1)}
        assert detect_arcs(acts, lookup) == []

    def test_same_kind_across_different_threads_is_not_one_arc(self):
        # One COMPRESSION in each of 3 threads → no single-thread arc.
        acts = [_act(f"a{i}", "COMPRESSION", f"p{i}") for i in range(3)]
        lookup = {"p0": ("t1", 0), "p1": ("t2", 0), "p2": ("t3", 0)}
        assert detect_arcs(acts, lookup) == []

    def test_self_expressed_acts_are_ignored(self):
        # Only model_miss acts carry a rejection kind + thread origin.
        acts = [_act(f"a{i}", "satisfaction", f"p{i}", trigger=SELF_EXPRESSED) for i in range(3)]
        lookup = {f"p{i}": ("t1", i) for i in range(3)}
        assert detect_arcs(acts, lookup) == []

    def test_unresolvable_prompt_id_is_skipped(self):
        acts = [_act(f"a{i}", "REFRAME", f"p{i}") for i in range(3)]
        lookup = {"p0": ("t1", 0), "p1": ("t1", 1)}  # p2 missing → only 2 resolve
        assert detect_arcs(acts, lookup) == []


class TestAggregateTrajectories:
    def test_aggregates_same_kind_across_threads(self):
        arcs = [
            TurnArc(transcript_id="t1", kind="COMPRESSION", count=3, turn_span=4,
                    exemplars=["just the spec"]),
            TurnArc(transcript_id="t2", kind="COMPRESSION", count=4, turn_span=6,
                    exemplars=["drop the preamble"]),
        ]
        trajs = aggregate_trajectories(arcs)
        assert len(trajs) == 1
        t = trajs[0]
        assert t.kind == "COMPRESSION"
        assert t.thread_count == 2
        assert t.total_pulls == 7
        assert "just the spec" in t.exemplars and "drop the preamble" in t.exemplars

    def test_distinct_kinds_stay_separate_and_sorted_by_pulls(self):
        arcs = [
            TurnArc(transcript_id="t1", kind="REFRAME", count=3, turn_span=2),
            TurnArc(transcript_id="t2", kind="COMPRESSION", count=5, turn_span=8),
        ]
        trajs = aggregate_trajectories(arcs)
        assert [t.kind for t in trajs] == ["COMPRESSION", "REFRAME"]  # 5 pulls > 3


class TestRender:
    def test_section_renders_with_trajectories(self):
        trajs = [Trajectory(kind="COMPRESSION", thread_count=4, total_pulls=14,
                            exemplars=["just the spec"])]
        out = "\n".join(render_trajectory_lines(trajs))
        assert "## Trajectories" in out
        assert "COMPRESSION" in out
        assert "4 threads" in out and "14 pulls" in out
        assert "just the spec" in out

    def test_empty_renders_nothing(self):
        assert render_trajectory_lines([]) == []

    def test_pipeline_render_includes_section(self):
        from trinity_local.me.pair_mining import LensPair
        from trinity_local.me.pipeline import render_me_markdown
        trajs = [Trajectory(kind="REDIRECT", thread_count=3, total_pulls=9)]
        out = render_me_markdown(
            [LensPair(pole_a="a", pole_b="b", failure_a="", failure_b="", verdict="accepted")],
            [], None, None, None, trajs,
        )
        assert "## Trajectories" in out and "REDIRECT" in out


@pytest.mark.usefixtures("patch_trinity_home")
class TestStorage:
    def test_arc_round_trip(self):
        from trinity_local.me.arc_mining import load_arcs, save_arcs
        arcs = [TurnArc(transcript_id="t1", kind="REFRAME", count=3, turn_span=2,
                        exemplars=["reframe it"])]
        save_arcs(arcs)
        back = load_arcs()
        assert len(back) == 1
        assert back[0].kind == "REFRAME" and back[0].count == 3
        assert back[0].exemplars == ["reframe it"]

    def test_trajectory_round_trip(self):
        from trinity_local.me.arc_mining import load_trajectories, save_trajectories
        trajs = [Trajectory(kind="COMPRESSION", thread_count=2, total_pulls=7)]
        save_trajectories(trajs)
        back = load_trajectories()
        assert len(back) == 1 and back[0].total_pulls == 7

    def test_load_missing_is_empty(self):
        from trinity_local.me.arc_mining import load_arcs, load_trajectories
        assert load_arcs() == [] and load_trajectories() == []


class TestChairmanEnrichmentPath:
    def test_arc_prompt_includes_kind_and_exemplar(self):
        arcs = [TurnArc(transcript_id="t1", kind="COMPRESSION", count=3, turn_span=4,
                        exemplars=["just the spec"])]
        prompt = render_arc_prompt(arcs)
        assert "COMPRESSION" in prompt and "just the spec" in prompt
        assert '"direction"' in prompt  # the schema the chairman emits

    def test_parse_keeps_valid_records_only(self):
        raw = (
            '{"kind": "COMPRESSION", "direction": "cut to the spec"}\n'
            '{"kind": "BOGUS", "direction": "x"}\n'        # invalid kind → drop
            '{"kind": "REFRAME"}\n'                          # no direction → drop
            'not json\n'
        )
        out = parse_trajectories(raw)
        assert out == [{"kind": "COMPRESSION", "direction": "cut to the spec"}]
