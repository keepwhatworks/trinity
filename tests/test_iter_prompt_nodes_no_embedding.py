"""Skinny iterator that skips embedding-field parsing.

PromptNode.embedding is a 768-element float array (~10KB serialized).
Callers that go through token-jaccard or substring scoring never read
it, but the full iter_prompt_nodes loads it on every cold render —
~1.85s of pure json.loads waste on the live 1GB corpus.

iter_prompt_nodes_no_embedding regex-strips the array before parsing.
PromptNode lands with embedding=[]; the rest of the record (text,
timestamps, council_run_ids, themes) is parsed normally.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _make_record(node_id: str, embedding_len: int = 768) -> dict:
    """A PromptNode-shaped record with a configurable embedding size."""
    return {
        "id": node_id,
        "transcript_id": f"t_{node_id}",
        "provider": "claude",
        "source_path": f"/fake/{node_id}",
        "turn_index": 0,
        "text": f"prompt text {node_id}",
        "embedding": [0.001] * embedding_len,
        "created_at": "2026-05-24T00:00:00Z",
        "timestamp": "2026-05-24T00:00:00Z",
    }


def _seed(home: Path, records: list[dict]) -> None:
    from trinity_local.state_paths import prompts_dir
    p = prompts_dir() / "prompt_nodes.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class TestIterPromptNodesNoEmbedding:
    def test_returns_nodes_with_empty_embedding(self, home):
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding
        _seed(home, [_make_record(f"n{i}") for i in range(5)])
        nodes = list(iter_prompt_nodes_no_embedding())
        assert len(nodes) == 5
        for n in nodes:
            assert n.embedding == [], (
                "embedding should be stripped to [], not populated"
            )

    def test_preserves_other_fields(self, home):
        """Stripping the embedding must NOT corrupt adjacent fields."""
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding
        record = _make_record("only", embedding_len=768)
        record["text"] = "exactly-this-text-must-survive"
        record["transcript_id"] = "tx_123"
        _seed(home, [record])

        nodes = list(iter_prompt_nodes_no_embedding())
        assert len(nodes) == 1
        n = nodes[0]
        assert n.id == "only"
        assert n.transcript_id == "tx_123"
        assert n.text == "exactly-this-text-must-survive"

    def test_dedups_by_id(self, home):
        """Same upsert semantics as iter_prompt_nodes — later wins."""
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding
        first = _make_record("dup")
        first["text"] = "old version"
        second = _make_record("dup")
        second["text"] = "new version"
        _seed(home, [first, second])

        nodes = list(iter_prompt_nodes_no_embedding())
        assert len(nodes) == 1
        assert nodes[0].text == "new version"

    def test_empty_file_returns_empty(self, home):
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding
        from trinity_local.state_paths import prompts_dir
        p = prompts_dir() / "prompt_nodes.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        assert list(iter_prompt_nodes_no_embedding()) == []

    def test_missing_file_returns_empty(self, home):
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding
        # Don't write a file.
        assert list(iter_prompt_nodes_no_embedding()) == []


class TestNonDictLineSkipped:
    """guard_shape_not_just_parse on the prompt CORPUS reader — the highest blast
    radius: every lens-build, search, ingest, and prompt-count reads it. A valid-
    JSON-but-non-dict LINE (a manual edit, a concatenation/partial-write bug) used to
    crash `record.get("id")` with an uncaught AttributeError, taking down the WHOLE
    corpus read. Both iter_prompt_nodes and iter_prompt_nodes_no_embedding must SKIP
    the junk line and keep the good records. Mutation: drop the `isinstance(record,
    dict)` guard in either reader → the non-dict line crashes → these red."""

    def _seed_with_junk(self, home: Path) -> None:
        from trinity_local.state_paths import prompts_dir
        p = prompts_dir() / "prompt_nodes.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(_make_record("good1")),
            "[1, 2, 3]",          # valid JSON, NOT a dict — the trap
            "42",                 # scalar
            '"a bare string"',    # string
            "null",               # null
            json.dumps(_make_record("good2")),
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_iter_prompt_nodes_skips_non_dict_lines(self, home):
        from trinity_local.memory.store import iter_prompt_nodes
        self._seed_with_junk(home)
        nodes = list(iter_prompt_nodes())  # must not raise
        assert sorted(n.id for n in nodes) == ["good1", "good2"]

    def test_iter_prompt_nodes_no_embedding_skips_non_dict_lines(self, home):
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding
        self._seed_with_junk(home)
        nodes = list(iter_prompt_nodes_no_embedding())  # must not raise
        assert sorted(n.id for n in nodes) == ["good1", "good2"]
