"""Tests for cross-provider pair discovery.

The `dream` Phase 1 (and the retired bootstrap-pairs CLI before it)
rides on `find_cross_provider_clusters` — given a list of PromptNodes
with embeddings, group nodes whose embeddings are close and that span
≥ 2 providers, dedupe to one response per provider per cluster, return
clusters sorted by coherence.

Tests are purely numerical (synthetic embeddings, no model load).
"""
from __future__ import annotations

import pytest

from trinity_local.cross_provider_pairs import (
    CrossProviderCluster,
    ProviderResponse,
    cluster_to_synthesis_args,
    find_cross_provider_clusters,
)
# cross_provider_pairs' private _cosine was a 4th copy of the canonical (unused in
# prod, only here) — unified. This now guards the canonical's cosine behavior
# (incl. the dimension-mismatch -> 0.0 guard the old nested pair_mining copy lacked).
from trinity_local.embeddings import cosine_similarity as _cosine
from trinity_local.memory.schemas import PromptNode


def _node(*, id_: str, provider: str, text: str, embedding: list[float], response: str = "answer") -> PromptNode:
    return PromptNode(
        id=id_,
        transcript_id=f"t_{id_}",
        provider=provider,
        source_path=f"/fake/{id_}",
        turn_index=0,
        text=text,
        embedding=embedding,
        created_at="2026-05-12T00:00:00Z",
        timestamp="2026-05-12T00:00:00Z",
        following_assistant_text=response,
    )


class TestCosine:
    def test_identical_vectors(self):
        assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_dim_mismatch_returns_zero(self):
        assert _cosine([1.0, 0.0], [1.0]) == 0.0

    def test_zero_norm_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_empty_returns_zero(self):
        assert _cosine([], []) == 0.0


class TestFindClusters:
    def test_two_providers_same_question_pair(self):
        """Two near-identical embeddings from different providers → 1 cluster."""
        nodes = [
            _node(id_="n1", provider="claude", text="best db?", embedding=[1.0, 0.0]),
            _node(id_="n2", provider="antigravity", text="best db?", embedding=[0.99, 0.05]),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert len(clusters) == 1
        assert clusters[0].n_providers == 2
        assert clusters[0].providers == {"claude", "antigravity"}

    def test_single_provider_cluster_dropped(self):
        """Two claude turns asking the same thing — no cross-provider signal."""
        nodes = [
            _node(id_="n1", provider="claude", text="best db?", embedding=[1.0, 0.0]),
            _node(id_="n2", provider="claude", text="best db?", embedding=[0.99, 0.05]),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert clusters == []

    def test_distant_embeddings_not_clustered(self):
        nodes = [
            _node(id_="n1", provider="claude", text="db?", embedding=[1.0, 0.0]),
            _node(id_="n2", provider="antigravity", text="weather?", embedding=[0.0, 1.0]),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert clusters == []

    def test_missing_embedding_skipped(self):
        nodes = [
            _node(id_="n1", provider="claude", text="x", embedding=[]),
            _node(id_="n2", provider="antigravity", text="x", embedding=[1.0, 0.0]),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert clusters == []

    def test_missing_response_text_skipped(self):
        nodes = [
            _node(id_="n1", provider="claude", text="x", embedding=[1.0, 0.0], response=""),
            _node(id_="n2", provider="antigravity", text="x", embedding=[0.99, 0.05]),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert clusters == []  # n1 had no response text, no useful pair

    def test_dedupes_to_one_per_provider(self):
        """Three claude responses + one gemini → cluster should have 1 claude
        (the one closest to seed) + 1 gemini, not 4 entries."""
        nodes = [
            _node(id_="n1", provider="claude", text="best db?", embedding=[1.0, 0.0], response="A"),
            _node(id_="n2", provider="claude", text="best db?", embedding=[0.99, 0.05], response="B"),
            _node(id_="n3", provider="claude", text="best db?", embedding=[0.97, 0.1], response="C"),
            _node(id_="n4", provider="antigravity", text="best db?", embedding=[0.98, 0.07], response="G"),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert len(clusters) == 1
        cluster = clusters[0]
        # One per provider — not 4 entries
        assert len(cluster.members) == 2
        provider_to_response = {m.provider: m.response_text for m in cluster.members}
        # Closest claude to seed is n1 (sim=1.0), not n2 or n3
        assert provider_to_response["claude"] == "A"
        assert provider_to_response["antigravity"] == "G"

    def test_min_providers_filter(self):
        """Pair across 2 providers → kept under min=2, dropped under min=3."""
        nodes = [
            _node(id_="n1", provider="claude", text="x", embedding=[1.0, 0.0]),
            _node(id_="n2", provider="antigravity", text="x", embedding=[0.99, 0.05]),
        ]
        assert len(find_cross_provider_clusters(nodes, min_providers=2, min_prompt_words=0)) == 1
        assert find_cross_provider_clusters(nodes, min_providers=3, min_prompt_words=0) == []

    def test_threshold_tightens_clusters(self):
        """Below default threshold, two distant questions get clustered;
        above it they don't."""
        nodes = [
            _node(id_="n1", provider="claude", text="x", embedding=[1.0, 0.0]),
            _node(id_="n2", provider="antigravity", text="y", embedding=[0.7, 0.7]),  # sim ≈ 0.71
        ]
        # 0.6 threshold → they pair
        assert len(find_cross_provider_clusters(nodes, similarity_threshold=0.6, min_prompt_words=0)) == 1
        # 0.85 threshold → they don't
        assert find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0) == []

    def test_coherence_sorted_descending(self):
        """Tightest cluster (highest coherence) comes first."""
        nodes = [
            # Cluster A: very tight (sim ≈ 1.0)
            _node(id_="a1", provider="claude", text="x", embedding=[1.0, 0.0]),
            _node(id_="a2", provider="antigravity", text="x", embedding=[1.0, 0.001]),
            # Cluster B: looser (sim ≈ 0.88)
            _node(id_="b1", provider="claude", text="y", embedding=[0.0, 1.0]),
            _node(id_="b2", provider="antigravity", text="y", embedding=[0.5, 0.866]),
        ]
        clusters = find_cross_provider_clusters(nodes, similarity_threshold=0.85, min_prompt_words=0)
        assert len(clusters) == 2
        assert clusters[0].coherence > clusters[1].coherence

    def test_handles_empty_input(self):
        assert find_cross_provider_clusters([], similarity_threshold=0.85, min_prompt_words=0) == []

    def test_uses_preceding_response_when_following_empty(self):
        """Gemini Takeout sometimes has the assistant text before, not after."""
        n1 = _node(id_="n1", provider="claude", text="x", embedding=[1.0, 0.0])
        n2 = PromptNode(
            id="n2",
            transcript_id="t",
            provider="antigravity",
            source_path="/fake",
            turn_index=0,
            text="x",
            embedding=[0.99, 0.05],
            created_at="2026-05-12T00:00:00Z",
            preceding_assistant_text="answer from preceding",
            following_assistant_text="",  # empty — fall back to preceding
        )
        clusters = find_cross_provider_clusters([n1, n2], similarity_threshold=0.85, min_prompt_words=0)
        assert len(clusters) == 1
        gemini = next(m for m in clusters[0].members if m.provider == "antigravity")
        assert gemini.response_text == "answer from preceding"


class TestClusterToSynthesisArgs:
    def test_shape_matches_synthesis_contract(self):
        cluster = CrossProviderCluster(
            representative_prompt="best db?",
            members=[
                ProviderResponse(provider="claude", prompt_text="best db?", response_text="postgres", node_id="n1", timestamp=None),
                ProviderResponse(provider="antigravity", prompt_text="best db?", response_text="duckdb", node_id="n2", timestamp=None),
            ],
            coherence=0.95,
        )
        args = cluster_to_synthesis_args(cluster)
        assert args["task"] == "best db?"
        assert args["responses"] == [
            {"provider": "claude", "content": "postgres"},
            {"provider": "antigravity", "content": "duckdb"},
        ]


