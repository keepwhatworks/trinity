"""Round-trip persistence invariants — #190 (gstack ratchet).

For every dataclass that has BOTH `to_dict()` and `from_dict()`, the
serialization boundary must round-trip: an instance written to disk
and read back is the same instance. These five types are Trinity's
load-bearing persistence boundaries — PromptNode + TurnWindow index
the corpus, CouncilChainStep + CouncilRoutingLabel are the council
outcome shapes the personal routing table trains on, and RegistryEntry
is the durable tension registry the accumulating lens reads/writes
(#197).

Why only these: a scan during #190 found ~50 classes with `to_dict()`
but only a handful with a matching `from_dict()`. The rest are
serialize-out only (JSON output for the launchpad / share cards /
analytics — never deserialized), so round-trip is N/A. The guard
TestRoundTripCoverageMatchesScan pins the ratio: if someone adds a
`from_dict` to a new class, this test fails until they add it to
ROUND_TRIPPABLE so the round-trip invariant covers it too.

The round-trip property tested is idempotent serialization:
`to_dict(from_dict(to_dict(x))) == to_dict(x)`. This is robust to
the empty-field filtering in to_dict + default-coercion in from_dict
(a fully-default instance's to_dict drops empties, from_dict re-adds
defaults — the SECOND to_dict is the canonical form, and re-loading
it must be stable). For fully-populated instances with canonical
provider slugs, direct object equality also holds — pinned where
applicable.
"""
from __future__ import annotations

import pytest

from trinity_local.council_schema import CouncilChainStep, CouncilRoutingLabel
from trinity_local.me.lens_registry import RegistryEntry
from trinity_local.me.preference_acts import PreferenceAct
from trinity_local.memory.schemas import PromptNode, TurnWindow


def _populated_prompt_node() -> PromptNode:
    return PromptNode(
        id="p_abc123",
        transcript_id="t_xyz789",
        provider="claude",
        source_path="/Users/x/.claude/projects/foo/session.jsonl",
        turn_index=3,
        text="how do I shape this gate?",
        embedding=[0.1, 0.2, 0.3, 0.4],
        created_at="2026-05-27T12:00:00",
        timestamp="2026-05-27T12:00:00",
        preceding_assistant_text="here's one approach",
        following_assistant_text="that won't scale",
        cluster_id="c_001",
        themes=["architecture", "gates"],
        council_run_ids=["council_aaa", "council_bbb"],
        chairman_winner="codex",
        uncertainty=0.3,
        importance=0.8,
        last_replayed_at="2026-05-27T13:00:00",
    )


def _populated_turn_window() -> TurnWindow:
    return TurnWindow(
        id="w_abc",
        transcript_id="t_xyz",
        center_prompt_id="p_abc123",
        text="user: ... assistant: ...",
        embedding=[0.5, 0.6, 0.7],
        turn_start=2,
        turn_end=4,
    )


def _populated_chain_step() -> CouncilChainStep:
    return CouncilChainStep(
        step_index=1,
        model_provider="claude",
        model_name="claude-opus-4-7",
        input_text="prior step output",
        output_text="refined answer",
        latency_seconds=12.5,
        cost_estimate_usd=0.04,
        started_at="2026-05-27T12:00:00",
        completed_at="2026-05-27T12:00:12",
        metadata={"round": 1},
    )


def _populated_routing_label() -> CouncilRoutingLabel:
    return CouncilRoutingLabel(
        winner="claude",
        confidence="high",
        runner_up="codex",
        task_type="architecture",
        task_domain="software",
        user_likely_values=["simplicity", "rigor"],
        provider_scores={"claude": {"quality": 0.9}, "codex": {"quality": 0.7}},
        routing_lesson="claude wins on architecture-shape questions",
        eval_seed="seed_001",
        major_failure_mode=None,
        agreed_claims=["both agree X"],
        disagreed_claims=[{"claim": "Y", "claude": "yes", "codex": "no"}],
    )


def _populated_registry_entry() -> RegistryEntry:
    return RegistryEntry(
        tension_id="tension_abc123",
        pole_a="speed",
        pole_b="rigor",
        failure_a="ships sloppy work",
        failure_b="never ships",
        basins_spanned=["b_arch", "b_review"],
        horizon="strategic",
        probe_text="speed · rigor · ships sloppy work · never ships",
        evidence_ids=["d_001", "d_002", "d_003"],
        first_seen="2026-05-01T00:00:00+00:00",
        last_confirmed="2026-05-25T00:00:00+00:00",
    )


def _populated_preference_act() -> PreferenceAct:
    return PreferenceAct(
        id="pa_abc123",
        trigger="model_miss",
        privileged="ship the small fix now",
        sacrificed="refactor the whole module first",
        kind="REDIRECT",
        why="user steered to the immediate goal",
        prompt_id="p_abc123",
        basin="b_arch",
        context="just fix the null check",
        source="lens-build",
        weight=2.0,
    )


def _populated_turn_arc():
    from trinity_local.me.arc_mining import TurnArc
    return TurnArc(
        transcript_id="t_abc", kind="COMPRESSION", count=4, turn_span=8,
        act_ids=["a1", "a2", "a3", "a4"], exemplars=["just the spec", "drop the preamble"],
    )


def _populated_trajectory():
    from trinity_local.me.arc_mining import Trajectory
    return Trajectory(
        kind="COMPRESSION", thread_count=3, total_pulls=11,
        exemplars=["just the spec"],
    )


def _populated_basin():
    from trinity_local.me.basins import Basin
    return Basin(
        id="b_arch",
        size=42,
        top_terms=["gate", "topology", "lens"],
        centroid=[0.1, 0.2, 0.3, 0.4],
        prompt_ids=["p_001", "p_002", "p_003"],
        representatives=[
            {
                "transcript_id": "t_xyz",
                "turn_count": 5,
                "headline": "shaping the gate",
                "turns": [{"id": "p_001", "snippet": "how do I gate this?", "turn_index": 3}],
            }
        ],
        thread_count=7,
        label="architecture gates",
        intent_type="decision",
        language="en",
    )


from trinity_local.me.arc_mining import Trajectory, TurnArc  # noqa: E402
from trinity_local.me.basins import Basin  # noqa: E402

ROUND_TRIPPABLE = {
    "PromptNode": (PromptNode, _populated_prompt_node),
    "TurnWindow": (TurnWindow, _populated_turn_window),
    "CouncilChainStep": (CouncilChainStep, _populated_chain_step),
    "CouncilRoutingLabel": (CouncilRoutingLabel, _populated_routing_label),
    "RegistryEntry": (RegistryEntry, _populated_registry_entry),
    "PreferenceAct": (PreferenceAct, _populated_preference_act),
    "TurnArc": (TurnArc, _populated_turn_arc),
    "Trajectory": (Trajectory, _populated_trajectory),
    "Basin": (Basin, _populated_basin),
}


class TestRoundTripPersistence:
    @pytest.mark.parametrize("name", sorted(ROUND_TRIPPABLE))
    def test_idempotent_serialization(self, name):
        """to_dict(from_dict(to_dict(x))) == to_dict(x). Robust to
        empty-filtering + default-coercion asymmetry."""
        cls, factory = ROUND_TRIPPABLE[name]
        instance = factory()
        once = instance.to_dict()
        twice = cls.from_dict(once).to_dict()
        assert twice == once, (
            f"{name} serialization is not idempotent — a record "
            f"written + read + re-written changed shape. Diff: "
            f"keys-only-in-first={set(once) - set(twice)}, "
            f"keys-only-in-second={set(twice) - set(once)}"
        )

    @pytest.mark.parametrize("name", sorted(ROUND_TRIPPABLE))
    def test_populated_instance_round_trips_to_equal_object(self, name):
        """For a fully-populated instance with canonical provider
        slugs, from_dict(to_dict(x)) == x directly. Catches a
        dropped field in to_dict OR a mis-mapped field in from_dict."""
        cls, factory = ROUND_TRIPPABLE[name]
        instance = factory()
        restored = cls.from_dict(instance.to_dict())
        assert restored == instance, (
            f"{name} did not round-trip to an equal object. A field "
            f"is dropped in to_dict() or mis-read in from_dict(). "
            f"original={instance!r} restored={restored!r}"
        )

    @pytest.mark.parametrize("name", sorted(ROUND_TRIPPABLE))
    def test_from_dict_tolerates_unknown_keys(self, name):
        """All four from_dict implementations filter unknown keys so
        older on-disk records (with since-removed fields) still load.
        Pins that contract."""
        cls, factory = ROUND_TRIPPABLE[name]
        payload = factory().to_dict()
        payload["a_field_that_was_removed_in_some_future_version"] = "junk"
        # Must not raise
        restored = cls.from_dict(payload)
        assert restored == factory()


class TestRoundTripCoverageMatchesScan:
    """Ratchet: if a new class grows a from_dict(), it must join
    ROUND_TRIPPABLE so the round-trip invariant covers it. Catches
    the drift where someone adds deserialization to a new persistence
    type but forgets to test the round-trip."""

    def test_all_roundtrippable_classes_are_under_test(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
        found: set[str] = set()
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = {
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if "to_dict" in methods and "from_dict" in methods:
                    found.add(node.name)

        covered = set(ROUND_TRIPPABLE)
        missing = found - covered
        assert not missing, (
            f"These classes have both to_dict() and from_dict() but "
            f"aren't in tests/test_roundtrip_persistence.py::ROUND_TRIPPABLE: "
            f"{sorted(missing)}. Add a factory + register them so the "
            f"round-trip invariant covers the new persistence boundary."
        )
        # Inverse: a registered class that lost its from_dict should be
        # removed from ROUND_TRIPPABLE (otherwise the test above is
        # testing a contract the class no longer claims).
        stale = covered - found
        assert not stale, (
            f"These classes are in ROUND_TRIPPABLE but no longer have "
            f"both to_dict() + from_dict(): {sorted(stale)}. Remove "
            f"them from the registry."
        )
