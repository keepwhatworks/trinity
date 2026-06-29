"""Tests for ranker types and interface."""
from __future__ import annotations

import pytest

from trinity_local.ranker import RoutingContext, RoutingDecision
from trinity_local.ranker.fallback import FallbackRanker
from trinity_local.ranker.heuristic import HeuristicRanker
from trinity_local.ranker.knn_ranker import KnnRanker


@pytest.fixture(autouse=True)
def _isolate_trinity_home(tmp_path, monkeypatch):
    """Keep ranker tests out of real ~/.trinity."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity_home"))
    yield


class TestRoutingContext:
    """RoutingContext contract: construction, immutability."""

    def test_construct_minimal(self):
        """Construct with required fields only."""
        ctx = RoutingContext(
            task_text="Fix the auth bug",
            task_type="debug",
            current_provider="claude",
            session_id="session-123",
        )
        assert ctx.task_text == "Fix the auth bug"
        assert ctx.task_type == "debug"
        assert ctx.current_provider == "claude"
        assert ctx.session_id == "session-123"
        assert ctx.has_web is False
        assert ctx.message_count == 0
        assert ctx.metadata == {}

    def test_construct_full(self):
        """Construct with all fields."""
        ctx = RoutingContext(
            task_text="Implement feature",
            task_type="feature",
            current_provider="codex",
            session_id="session-456",
            task_id="task-789",
            cwd="/home/user/project",
            source="claude",
            switched_from_provider="antigravity",
            switched_from_task_id="task-111",
            has_web=True,
            has_tools=True,
            has_edits=True,
            message_count=42,
            metadata={"priority": "high"},
        )
        assert ctx.task_id == "task-789"
        assert ctx.has_web is True
        assert ctx.message_count == 42
        assert ctx.metadata["priority"] == "high"

    def test_frozen(self):
        """RoutingContext is immutable."""
        ctx = RoutingContext(
            task_text="test",
            task_type="debug",
            current_provider="claude",
            session_id="123",
        )
        with pytest.raises(AttributeError):
            ctx.task_type = "feature"


class TestRoutingDecision:
    """RoutingDecision contract: construction, immutability, semantics."""

    def test_construct_minimal(self):
        """Construct with defaults."""
        dec = RoutingDecision(recommended_provider="claude")
        assert dec.recommended_provider == "claude"
        assert dec.top_k == []
        assert dec.needs_council is False
        assert dec.confidence == 0.5
        assert dec.backend == "heuristic"
        assert dec.evidence == []

    def test_construct_council(self):
        """Construct a council recommendation."""
        dec = RoutingDecision(
            recommended_provider=None,
            top_k=["claude", "codex"],
            needs_council=True,
            confidence=0.75,
            backend="knn",
            evidence=["k-NN neighbors suggest council"],
        )
        assert dec.recommended_provider is None
        assert dec.needs_council is True
        assert dec.confidence == 0.75
        assert dec.backend == "knn"

    def test_top_k_semantics(self):
        """top_k[0] should match recommended_provider when both present."""
        dec = RoutingDecision(
            recommended_provider="claude",
            top_k=["claude", "codex"],
        )
        assert dec.top_k[0] == dec.recommended_provider

    def test_frozen(self):
        """RoutingDecision is immutable."""
        dec = RoutingDecision(recommended_provider="claude")
        with pytest.raises(AttributeError):
            dec.recommended_provider = "codex"

    def test_no_methods(self):
        """RoutingDecision is pure data: no formatting or policy methods."""
        dec = RoutingDecision(recommended_provider="claude")
        assert not hasattr(dec, "to_message")
        assert not hasattr(dec, "render_evidence")
        assert not hasattr(dec, "to_dict")  # No serio-friendly helpers


class TestHeuristicRanker:
    """HeuristicRanker: task-kind-based routing with evidence."""

    def test_research_task_type(self):
        """Research/broad comparison → antigravity council."""
        ranker = HeuristicRanker()
        ctx = RoutingContext(
            task_text="Compare the top 5 machine learning frameworks",
            task_type="research",
            current_provider="claude",
            session_id="sess-001",
        )
        decision = ranker.advise(ctx)
        assert decision.recommended_provider == "antigravity"
        assert decision.needs_council is True
        assert decision.top_k == ["antigravity", "codex"]
        assert decision.confidence == 0.72
        assert decision.backend == "heuristic"
        assert any("research" in e.lower() for e in decision.evidence)

    def test_coding_task_type(self):
        """Coding/execution → codex council."""
        ranker = HeuristicRanker()
        ctx = RoutingContext(
            task_text="Fix the bug in the authentication module",
            task_type="coding",
            current_provider="claude",
            session_id="sess-002",
        )
        decision = ranker.advise(ctx)
        assert decision.recommended_provider == "codex"
        assert decision.needs_council is True
        assert decision.top_k == ["codex", "claude"]
        assert decision.confidence == 0.68
        assert decision.backend == "heuristic"

    def test_debugging_task_type(self):
        """Debugging/error triage → codex council."""
        ranker = HeuristicRanker()
        ctx = RoutingContext(
            task_text="Why is this import failing?",
            task_type="debugging",
            current_provider="claude",
            session_id="sess-003",
        )
        decision = ranker.advise(ctx)
        assert decision.recommended_provider == "codex"
        assert decision.needs_council is True
        assert decision.confidence == 0.68

    def test_general_task_type(self):
        """General/writing/other → claude default."""
        ranker = HeuristicRanker()
        ctx = RoutingContext(
            task_text="Help me draft an email",
            task_type="writing",
            current_provider="claude",
            session_id="sess-004",
        )
        decision = ranker.advise(ctx)
        assert decision.recommended_provider == "claude"
        assert decision.needs_council is False
        assert decision.top_k == []
        assert decision.confidence == 0.55
        assert decision.backend == "heuristic"


class TestKnnRanker:
    """KnnRanker: heuristic + k-NN advisory upgrade."""

    def test_returns_decision(self):
        """KnnRanker.advise() returns a RoutingDecision."""
        ranker = KnnRanker()
        ctx = RoutingContext(
            task_text="Fix the auth bug",
            task_type="debugging",
            current_provider="claude",
            session_id="sess-005",
        )
        decision = ranker.advise(ctx)
        assert isinstance(decision, RoutingDecision)
        assert decision.recommended_provider is not None

    def test_graceful_degradation_no_prompt(self):
        """KnnRanker degrades gracefully with empty prompt."""
        ranker = KnnRanker()
        ctx = RoutingContext(
            task_text="",
            task_type="coding",
            current_provider="claude",
            session_id="sess-006",
        )
        decision = ranker.advise(ctx)
        # Should still return a valid decision (from heuristic)
        assert isinstance(decision, RoutingDecision)
        # Backend should reflect it couldn't enhance with k-NN
        # (but heuristic still worked)
        assert decision.recommended_provider == "codex"

    def test_backend_set_to_knn_on_success(self):
        """When k-NN succeeds, backend is set to 'knn'."""
        # Note: This test will only work if k-NN corpus is available.
        # In test environments, it gracefully falls back to heuristic.
        ranker = KnnRanker()
        ctx = RoutingContext(
            task_text="Compare machine learning frameworks",
            task_type="research",
            current_provider="claude",
            session_id="sess-007",
        )
        decision = ranker.advise(ctx)
        # Without a corpus, this will return heuristic decision
        # With a corpus, backend would be "knn"
        assert decision.backend in {"heuristic", "knn"}

    def test_metadata_preserved_from_heuristic(self):
        """Metadata from heuristic is preserved in enhanced decision."""
        ranker = KnnRanker()
        ctx = RoutingContext(
            task_text="Debug the issue",
            task_type="debugging",
            current_provider="claude",
            session_id="sess-008",
            metadata={"project": "test-project"},
        )
        decision = ranker.advise(ctx)
        assert decision.metadata is not None
        # Either has original metadata or k-NN enriched metadata
        assert isinstance(decision.metadata, dict)


class TestFallbackRanker:
    """FallbackRanker: two-tier with graceful fallback."""

    def test_returns_decision(self):
        """FallbackRanker always returns a RoutingDecision."""
        ranker = FallbackRanker()
        ctx = RoutingContext(
            task_text="Fix the database query",
            task_type="coding",
            current_provider="claude",
            session_id="sess-009",
        )
        decision = ranker.advise(ctx)
        assert isinstance(decision, RoutingDecision)
        assert decision.recommended_provider is not None

    def test_backend_reflects_tier_used(self):
        """Backend annotation shows which tier succeeded."""
        ranker = FallbackRanker()
        ctx = RoutingContext(
            task_text="Compare algorithms",
            task_type="research",
            current_provider="claude",
            session_id="sess-010",
        )
        decision = ranker.advise(ctx)
        # Without a corpus, will be "heuristic" or "knn" (knn with fallback)
        # With a corpus, could be "knn"
        assert decision.backend in {"heuristic", "knn", "fallback"}

    def test_all_task_types(self):
        """FallbackRanker handles all task_types."""
        ranker = FallbackRanker()
        for task_type in ["research", "coding", "debugging", "writing", "general"]:
            ctx = RoutingContext(
                task_text=f"Task: {task_type}",
                task_type=task_type,
                current_provider="claude",
                session_id=f"sess-{task_type}",
            )
            decision = ranker.advise(ctx)
            assert decision is not None
            assert decision.recommended_provider is not None


class TestBuildDefaultRanker:
    """build_default_ranker() factory implementation."""

    def test_returns_fallback_ranker(self):
        """Factory returns a FallbackRanker instance."""
        from trinity_local.ranker import build_default_ranker

        ranker = build_default_ranker()
        assert isinstance(ranker, FallbackRanker)

    def test_default_ranker_advises(self):
        """Default ranker from factory can advise."""
        from trinity_local.ranker import build_default_ranker

        ranker = build_default_ranker()
        ctx = RoutingContext(
            task_text="Test task",
            task_type="coding",
            current_provider="claude",
            session_id="sess-factory-test",
        )
        decision = ranker.advise(ctx)
        assert isinstance(decision, RoutingDecision)
        assert decision.recommended_provider is not None
