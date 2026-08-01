"""User-facing corpus counts must count NODES, not LINES.

WHY THIS EXISTS. `prompt_nodes.jsonl` is an append-only store with
latest-wins-by-id (`_iter_jsonl_latest_by_id`, `protect_field="embedding"`). Ingest
writes a node cheaply WITHOUT a vector; the embed pass appends a second row WITH one.
So every embedded-after-ingest node occupies two lines, and every such pair is
(unembedded, embedded).

Consumers that require a finite embedding dedupe for free — the filter drops the
unembedded twin. Consumers that merely COUNT do not, and two user-facing surfaces were
counting lines:

  * the milestone banner reported "48,337 prompts indexed" against 37,781 real nodes,
    a 28% overstatement;
  * `embedding_coverage` reported "78.2% embedded, 10,556 pending" against a true
    pending count of ZERO — and shipped a `fix` that could never clear it, because the
    superseded twins are never removed, so re-running the embedder changed nothing.
    That is an advice-closure failure on a health check, the sibling of this repo's
    green-check-over-degenerate-data family: a RED check over perfectly healthy data.

These guards seed a corpus where line-counting and node-counting DISAGREE, so any
regression to `sum(1 for line in f)` reds instead of passing on a corpus where the two
happen to be equal.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.memory.store import count_prompt_nodes


def _seed(tmp_home, rows):
    d = tmp_home / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "prompt_nodes.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


class TestCountsNodesNotLines:
    def test_ingest_then_embed_pair_counts_as_ONE_node(self, home):
        """The exact production shape: same id written twice, empty then embedded.

        A line count returns 2 here. This is the discriminating fixture — a corpus of
        distinct ids would pass under either implementation.
        """
        _seed(home, [
            {"id": "n1", "text": "a", "embedding": []},
            {"id": "n1", "text": "a", "embedding": [0.1, 0.2]},
        ])
        total, unembedded = count_prompt_nodes()
        assert total == 1, "two rows for one id must count as ONE node, not two"
        assert unembedded == 0, (
            "a node embedded on ANY row is embedded — counting the empty twin as "
            "pending invents a backfill queue that no embed pass can drain"
        )

    def test_order_does_not_matter(self, home):
        """The embedded row may appear before or after the empty one depending on
        ingest interleaving. Neither order may resurrect the phantom."""
        _seed(home, [
            {"id": "n1", "embedding": [0.1]},
            {"id": "n1", "embedding": []},
        ])
        assert count_prompt_nodes() == (1, 0)

    def test_a_genuinely_unembedded_node_is_still_reported(self, home):
        """The fix must not achieve zero-pending by declaring everything embedded —
        a real backfill queue still has to surface."""
        _seed(home, [
            {"id": "n1", "embedding": [0.1]},
            {"id": "n2", "embedding": []},
        ])
        assert count_prompt_nodes() == (2, 1)

    def test_mixed_corpus(self, home):
        """5 lines, 3 ids, 1 genuinely pending."""
        _seed(home, [
            {"id": "a", "embedding": []},
            {"id": "a", "embedding": [0.1]},
            {"id": "b", "embedding": []},
            {"id": "b", "embedding": [0.2]},
            {"id": "c", "embedding": []},
        ])
        total, unembedded = count_prompt_nodes()
        assert (total, unembedded) == (3, 1)

    def test_junk_lines_do_not_crash_or_count(self, home):
        d = home / "prompts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prompt_nodes.jsonl").write_text(
            '{"id": "n1", "embedding": [0.1]}\n'
            "not json at all\n"
            "[1,2,3]\n"            # valid JSON, wrong type
            '{"no_id": true}\n'
            "\n",
            encoding="utf-8",
        )
        assert count_prompt_nodes() == (1, 0)

    def test_missing_file_is_zero_not_a_crash(self, home):
        assert count_prompt_nodes() == (0, 0)


class TestSurfacesUseIt:
    def test_milestone_banner_reports_unique_nodes(self, home):
        """The banner said 'N prompts indexed'. N must be nodes."""
        _seed(home, [
            {"id": "n1", "embedding": []},
            {"id": "n1", "embedding": [0.1]},
            {"id": "n2", "embedding": [0.2]},
        ])
        from trinity_local import milestones

        sizes = milestones.compute_corpus_stats()
        assert sizes["prompts"] == 2, (
            f"banner would show {sizes['prompts']} for a 2-node corpus written "
            "across 3 lines"
        )

    def test_embedding_coverage_reports_no_phantom_backlog(self, home):
        """A fully-embedded corpus written ingest-then-embed must read 100%/0 pending.
        Before the fix this same corpus read 66.7% with 1 pending."""
        _seed(home, [
            {"id": "n1", "embedding": []},
            {"id": "n1", "embedding": [0.1]},
            {"id": "n2", "embedding": [0.2]},
        ])
        from trinity_local import health_checks

        r = health_checks._check_embedding_coverage()
        assert "pending" not in r.detail or "0 pending" in r.detail, (
            f"phantom backlog surfaced: {r.detail!r}"
        )
        assert "100.0%" in r.detail or "2/2" in r.detail, (
            f"a fully-embedded corpus must read as fully embedded: {r.detail!r}"
        )
