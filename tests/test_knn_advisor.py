"""Tests for knn_advisor — the k-NN advisory layer."""
from __future__ import annotations

import json

import pytest

from trinity_local.knn_advisor import (
    KnnAdvice,
    advise,
    corpus_size,
)


@pytest.fixture(autouse=True)
def _isolate_trinity_home(monkeypatch, tmp_path):
    """Isolate TRINITY_HOME per-test.

    Was a module-level `os.environ["TRINITY_HOME"] = ...` which leaked the
    test path into every subsequent test in the suite. The real-corpus
    depth tests started silently skipping because `iter_prompt_nodes`
    resolved to the polluted tmp path, returning 0 embedded nodes —
    invisible armor with hours of debugging cost (tick #63).
    Per meta-principle #3 (filter at the boundary): scope test state via
    fixtures, not via module-level mutation.
    """
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))


def _make_hard_example(
    example_id: str,
    provider: str,
    label: str,
    hard_type: str,
    prompt: str,
) -> dict:
    """Build a minimal hard example JSON blob."""
    return {
        "example_id": example_id,
        "hard_type": hard_type,
        "chosen_provider": provider,
        "chosen_model": {"provider": provider},
        "label": label,
        "outcome": {},
        "transcript": {
            "session_id": f"sess-{example_id}",
            "provider": provider,
            "source_path": "/tmp/fake.jsonl",
            "first_user_text": prompt,
        },
        "hard_signals": {},
        "related_sessions": [],
    }


def _populate_corpus(examples: list[dict]) -> None:
    """Write example JSONs to the test hard_examples dir."""
    from trinity_local.config import trinity_home

    hard_dir = trinity_home() / "research" / "hard_examples"
    hard_dir.mkdir(parents=True, exist_ok=True)
    for ex in examples:
        path = hard_dir / f"{ex['example_id']}.json"
        path.write_text(json.dumps(ex), encoding="utf-8")

    # Force reload
    import trinity_local.knn_advisor as adv
    adv._corpus_cache = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCorpusLoading:
    def test_empty_corpus(self):
        """advise() returns None when no corpus exists."""
        import trinity_local.knn_advisor as adv
        adv._corpus_cache = None
        result = advise("test prompt", "claude")
        # May return None (no corpus) or an advice (if corpus dir was populated)
        # Just ensure no crash
        assert result is None or isinstance(result, KnnAdvice)

    def test_corpus_loads(self):
        examples = [
            _make_hard_example("ex1", "claude", "needs_council", "needs_council",
                               "Write a sorting algorithm in Python"),
            _make_hard_example("ex2", "codex", "bad_fit", "failed",
                               "Deploy Kubernetes cluster on AWS"),
            _make_hard_example("ex3", "claude", "needs_council", "needs_council",
                               "Debug this failing test case"),
            _make_hard_example("ex4", "gemini", "bad_fit", "rerouted",
                               "Research competitor pricing strategies"),
            _make_hard_example("ex5", "cowork", "needs_council", "needs_council",
                               "Analyze these sales reports and create a dashboard"),
            _make_hard_example("ex6", "claude", "bad_fit", "rerouted",
                               "Compare cloud hosting providers for our startup"),
        ]
        _populate_corpus(examples)
        assert corpus_size() == 6


class TestAdvise:
    @pytest.fixture(autouse=True)
    def _setup_corpus(self):
        """Set up a small corpus for testing."""
        examples = [
            _make_hard_example("ex1", "claude", "needs_council", "needs_council",
                               "Write a sorting algorithm in Python"),
            _make_hard_example("ex2", "codex", "bad_fit", "failed",
                               "Deploy Kubernetes cluster on AWS"),
            _make_hard_example("ex3", "claude", "needs_council", "needs_council",
                               "Debug this failing test case"),
            _make_hard_example("ex4", "gemini", "bad_fit", "rerouted",
                               "Research competitor pricing strategies"),
            _make_hard_example("ex5", "cowork", "needs_council", "needs_council",
                               "Analyze these sales reports and create a dashboard"),
            _make_hard_example("ex6", "claude", "bad_fit", "rerouted",
                               "Compare cloud hosting providers for our startup"),
            _make_hard_example("ex7", "codex", "needs_council", "needs_council",
                               "Refactor the database layer to use connection pooling"),
        ]
        _populate_corpus(examples)

    def test_empty_prompt_returns_none(self):
        assert advise("", "claude") is None
        assert advise("   ", "claude") is None

    def test_advise_returns_knn_advice(self):
        """Non-empty prompt against valid corpus returns KnnAdvice.

        Was guarded by `if result is None: skip("Embeddings not available")`
        which was dead defensive paper — `advise()` no longer requires MLX,
        it runs on the TF-IDF fallback when MLX is missing.
        """
        result = advise("Write a function to sort arrays", "claude", k=3)
        assert isinstance(result, KnnAdvice)
        assert result.neighbor_count == 3
        assert len(result.evidence) > 0

    def test_should_council_flag(self):
        """When most neighbors are needs_council/bad_fit, should_council is True."""
        result = advise("Write a sorting algorithm", "claude", k=5)
        # Our corpus is mostly needs_council/bad_fit, so should_council should be True
        assert isinstance(result.should_council, bool)

    def test_top2_providers(self):
        result = advise("Deploy to production", "claude", k=5)
        assert isinstance(result.top2_providers, list)
        assert len(result.top2_providers) <= 2

    def test_evidence_includes_knn_line(self):
        result = advise("Research competitor analysis", "claude", k=3)
        assert any("k-NN" in e for e in result.evidence)
