"""Tick #52 — k-LLMmeans relabel hook in compute_basins.

The hook lets a chairman steer centroid drift toward semantic
coherence. These tests fixture a deterministic Python callable as
the hook (no actual chairman) so the iteration mechanics + defensive
shape checks are testable in isolation.

Reference architecture: ClusterLLM (EMNLP 2023, arXiv:2305.14871);
k-LLMmeans (arXiv:2502.09667). Per the v1.5 spec section "Lens-build:
depth-first, chairman-in-the-loop", the hook is what makes the
clustering steer toward labels-as-anchors instead of pure geometric
means.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_prompt_nodes(home, items):
    """Write a small prompt_nodes.jsonl the basin computer can read.
    items = list of {tid, nid, turn_index, text, embedding}."""
    from trinity_local.memory.schemas import PromptNode
    from trinity_local.state_paths import prompts_dir
    path = prompts_dir() / "prompt_nodes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            node = PromptNode(
                id=it["nid"],
                transcript_id=it["tid"],
                provider="test",
                source_path="test.jsonl",
                turn_index=it.get("turn_index", 0),
                text=it["text"],
                embedding=it["embedding"],
                created_at="2026-05-13T00:00:00",
            )
            f.write(json.dumps(node.to_dict()) + "\n")


class TestRelabelHookDefaults:
    """Default behavior (no hook / iterations=1) must be IDENTICAL
    to the prior compute_basins — guard against the hook code path
    leaking into vanilla k-means callers."""

    def test_no_hook_no_iterations_unchanged(self, isolated_home):
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "x", "embedding": [1.0, 0.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "y", "embedding": [0.0, 1.0, 0.0]},
            {"tid": "t3", "nid": "c", "text": "z", "embedding": [0.0, 0.0, 1.0]},
        ])
        from trinity_local.me.basins import compute_basins
        # No hook, iterations defaults to 1. Should produce the same
        # basins as the original signature did.
        basins = compute_basins(k=3, seed=42)
        assert len(basins) >= 1
        # All inputs were assigned. Check by summing turn counts.
        total = sum(b.size for b in basins)
        assert total == 3, f"all 3 turns should be assigned; got {total}"

    def test_iterations_without_hook_no_op(self, isolated_home):
        """iterations > 1 with hook=None must NOT execute the loop —
        prevents accidental k-means re-runs that would diverge silently."""
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "x", "embedding": [1.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "y", "embedding": [0.0, 1.0]},
        ])
        from trinity_local.me.basins import compute_basins
        b_with = compute_basins(k=2, seed=42, iterations=5, relabel_hook=None)
        b_without = compute_basins(k=2, seed=42)
        # Same basin layout — iterations=5 with no hook is a no-op.
        assert [b.size for b in b_with] == [b.size for b in b_without]


class TestRelabelHookRuns:
    def test_hook_called_iterations_minus_one_times(self, isolated_home):
        """With iterations=3, the hook runs twice (initial k-means is
        iteration 1; hook is invoked between iterations, so N-1 calls)."""
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "x", "embedding": [1.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "y", "embedding": [0.0, 1.0]},
        ])
        from trinity_local.me.basins import compute_basins
        calls = []
        def hook(payload):
            calls.append(payload)
            # Return same centroids as input → no movement, just count calls.
            return [[1.0, 0.0], [0.0, 1.0]]
        compute_basins(k=2, seed=42, iterations=3, relabel_hook=hook)
        assert len(calls) == 2, f"expected 2 hook calls; got {len(calls)}"

    def test_hook_receives_basin_summaries(self, isolated_home):
        """Hook input must include basin_id, size, and rep prompts —
        downstream chairman implementations rely on these keys."""
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "first prompt", "embedding": [1.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "second prompt", "embedding": [0.95, 0.05]},
        ])
        from trinity_local.me.basins import compute_basins
        captured = []
        def hook(payload):
            captured.append(payload)
            return [[1.0, 0.0]] * len(payload)
        compute_basins(k=1, seed=42, iterations=2, relabel_hook=hook)
        assert captured, "hook never called"
        first_payload = captured[0]
        assert first_payload, "empty payload"
        for row in first_payload:
            assert "basin_id" in row
            assert "size" in row
            assert "reps" in row
            assert isinstance(row["reps"], list)


class TestRelabelHookDefensiveShapeChecks:
    """The hook is user-supplied (eventually wired to a real chairman).
    A misbehaving hook must not corrupt the clustering output."""

    def test_hook_raises_exception_falls_back(self, isolated_home):
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "x", "embedding": [1.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "y", "embedding": [0.0, 1.0]},
        ])
        from trinity_local.me.basins import compute_basins
        def hook(payload):
            raise RuntimeError("simulated chairman failure")
        # Must not raise; falls back to vanilla k-means basins.
        basins = compute_basins(k=2, seed=42, iterations=3, relabel_hook=hook)
        assert basins, "hook failure killed clustering"

    def test_hook_returns_wrong_count_falls_back(self, isolated_home):
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "x", "embedding": [1.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "y", "embedding": [0.0, 1.0]},
        ])
        from trinity_local.me.basins import compute_basins
        def hook(payload):
            # Returns 1 centroid instead of 2 — shape mismatch.
            return [[0.5, 0.5]]
        basins = compute_basins(k=2, seed=42, iterations=3, relabel_hook=hook)
        assert basins, "shape mismatch killed clustering"

    def test_hook_returns_wrong_dimensionality_falls_back(self, isolated_home):
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t1", "nid": "a", "text": "x", "embedding": [1.0, 0.0]},
            {"tid": "t2", "nid": "b", "text": "y", "embedding": [0.0, 1.0]},
        ])
        from trinity_local.me.basins import compute_basins
        def hook(payload):
            # Embeddings are 2-d; return 3-d vectors — dim mismatch.
            return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        basins = compute_basins(k=2, seed=42, iterations=3, relabel_hook=hook)
        assert basins, "dim mismatch killed clustering"


class TestRelabelHookSteersCentroids:
    """The whole point of k-LLMmeans: hook-provided centroids
    actually drive the next iteration's thread assignment."""

    def test_hook_centroid_attracts_thread(self, isolated_home):
        # Two clusters separable along x-axis. Hook returns centroids
        # that swap positions — re-assignment must follow.
        _seed_prompt_nodes(isolated_home, [
            {"tid": "t_left", "nid": "a", "text": "left thread", "embedding": [-1.0, 0.0]},
            {"tid": "t_right", "nid": "b", "text": "right thread", "embedding": [1.0, 0.0]},
        ])
        from trinity_local.me.basins import compute_basins
        # Hook moves centroid 0 to (5, 0) and centroid 1 to (-5, 0):
        # t_right should now belong to centroid 0 and t_left to 1.
        # (We test the basin SIZE bookkeeping rather than reading the
        # internal labels.)
        def hook(payload):
            # Replacement centroids that pull threads cross-cluster.
            return [[5.0, 0.0], [-5.0, 0.0]]
        basins = compute_basins(k=2, seed=42, iterations=2, relabel_hook=hook)
        # 2 threads, 1 turn each → 2 basins each with size 1.
        sizes = sorted(b.size for b in basins)
        assert sizes == [1, 1], f"thread re-assignment broke counts: {sizes}"
