"""#235 — embedding backfill + a coverage floor-guard.

incremental_ingest.ingest_recent writes PromptNodes with ``embedding=[]`` to
keep the launchpad/search hot path fast, deferring the real vectors to a later
offline pass. That backfill stalled 2026-05-12, leaving ~66% of text-bearing
prompt_nodes with empty embeddings — invisible to the k-means basins
(``compute_basins`` silently skips non-finite embeddings).

These tests pin both halves of the fix:
  - ``backfill_prompt_node_embeddings`` batch-embeds text-bearing nodes that
    lack a finite embedding and re-upserts them.
  - a coverage *floor guard* that FAILS when >X% of text-bearing nodes lack
    embeddings — the canary that would have fired on 2026-05-12.

The TF-IDF fallback (no MLX needed) produces deterministic finite vectors, so
these run everywhere.
"""
from __future__ import annotations

import pytest

from trinity_local.embeddings import (
    backfill_prompt_node_embeddings,
    is_finite_embedding,
    prompt_node_embedding_coverage,
)

# Product floor: a healthy corpus keeps the missing-embedding fraction below
# this. The 2026-05-12 regression sat at ~0.66; anything near that is a
# stalled backfill and must fail loudly.
MAX_MISSING_FRACTION = 0.10


def _node(node_id, text, embedding):
    from trinity_local.memory import PromptNode
    from trinity_local.utils import now_iso

    return PromptNode(
        id=node_id,
        transcript_id="t1",
        provider="claude",
        source_path="/x.jsonl",
        turn_index=0,
        text=text,
        embedding=embedding,
        created_at=now_iso(),
    )


@pytest.mark.usefixtures("patch_trinity_home")
class TestEmbeddingCoverage:
    def _seed(self, *, embedded, empty, empty_text=0):
        from trinity_local.memory import upsert_prompt_node

        i = 0
        for _ in range(embedded):
            upsert_prompt_node(_node(f"n{i}", f"real prompt {i}", [0.1, 0.2, 0.3]))
            i += 1
        for _ in range(empty):
            upsert_prompt_node(_node(f"n{i}", f"text but no vector {i}", []))
            i += 1
        for _ in range(empty_text):
            # Empty-text nodes legitimately have no embedding — must NOT count
            # against coverage (they're not text-bearing).
            upsert_prompt_node(_node(f"n{i}", "", []))
            i += 1

    def test_coverage_counts_only_text_bearing(self):
        self._seed(embedded=3, empty=1, empty_text=5)
        cov = prompt_node_embedding_coverage()
        assert cov["text_bearing"] == 4  # the 5 empty-text nodes excluded
        assert cov["embedded"] == 3
        assert cov["missing"] == 1
        assert cov["fraction_missing"] == pytest.approx(0.25)

    def test_floor_guard_fails_on_stalled_backfill(self):
        # Reproduce the 2026-05-12 shape: a majority of text-bearing nodes
        # lack embeddings. The floor guard MUST flag it.
        self._seed(embedded=3, empty=6)
        cov = prompt_node_embedding_coverage()
        assert cov["fraction_missing"] > MAX_MISSING_FRACTION, (
            "expected the stalled-backfill corpus to breach the floor"
        )

    def test_backfill_closes_the_gap(self):
        self._seed(embedded=3, empty=6, empty_text=2)
        before = prompt_node_embedding_coverage()
        assert before["missing"] == 6

        report = backfill_prompt_node_embeddings()
        assert report["scanned"] == 6
        assert report["backfilled"] == 6
        assert report["remaining"] == 0

        after = prompt_node_embedding_coverage()
        # Every text-bearing node now has a finite embedding → under the floor.
        assert after["missing"] == 0
        assert after["fraction_missing"] <= MAX_MISSING_FRACTION

    def test_backfill_is_idempotent(self):
        self._seed(embedded=2, empty=4)
        backfill_prompt_node_embeddings()
        second = backfill_prompt_node_embeddings()
        # Nothing left to do on the second pass.
        assert second["scanned"] == 0
        assert second["backfilled"] == 0

    def test_backfilled_vectors_are_finite_and_survive_reload(self):
        from trinity_local.memory.store import iter_prompt_nodes

        self._seed(embedded=0, empty=4)
        backfill_prompt_node_embeddings()
        nodes = [
            n
            for n in iter_prompt_nodes(limit=None)
            if (n.text or "").strip()
        ]
        assert nodes
        for n in nodes:
            assert is_finite_embedding(n.embedding), n.id

    def test_empty_corpus_no_crash(self):
        cov = prompt_node_embedding_coverage()
        assert cov == {
            "text_bearing": 0,
            "embedded": 0,
            "missing": 0,
            "fraction_missing": 0.0,
        }
        report = backfill_prompt_node_embeddings()
        assert report == {"scanned": 0, "backfilled": 0, "remaining": 0}
