"""Tail-read fast path for prompt_nodes.jsonl recent samples.

Real install observed: ~38K PromptNodes / ~1GB on disk.
iter_prompt_nodes(limit=10) parses the entire corpus first then
truncates (~3.5s). Doctor checks + drift surfaces only need "K most
recent" semantics — pure waste to parse 1GB to return 10 nodes.

tail_prompt_nodes_fast(K) reads from EOF backwards in 64KB chunks,
parses trailing JSON lines, returns up to K. Empirically ~50ms on
the live corpus — 70× faster than the iter+truncate path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _write_prompt_nodes(home: Path, records: list[dict]) -> Path:
    """Drop a synthetic prompt_nodes.jsonl. Returns the path."""
    from trinity_local.state_paths import prompts_dir
    target = prompts_dir() / "prompt_nodes.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return target


def _make_record(node_id: str, *, timestamp: str = "2026-05-24T00:00:00Z") -> dict:
    """Minimal PromptNode dict satisfying from_dict()."""
    return {
        "id": node_id,
        "transcript_id": f"t_{node_id}",
        "provider": "claude",
        "source_path": f"/fake/{node_id}",
        "turn_index": 0,
        "text": f"prompt text for {node_id}",
        "embedding": [0.1] * 8,  # tiny — keeps fixtures fast
        "created_at": timestamp,
        "timestamp": timestamp,
    }


class TestTailPromptNodesFast:
    def test_empty_returns_empty_list(self, home):
        from trinity_local.memory.store import tail_prompt_nodes_fast
        assert tail_prompt_nodes_fast(limit=10) == []

    def test_missing_file_returns_empty(self, home):
        from trinity_local.memory.store import tail_prompt_nodes_fast
        # Don't write the file at all
        assert tail_prompt_nodes_fast(limit=10) == []

    def test_zero_limit_returns_empty(self, home):
        from trinity_local.memory.store import tail_prompt_nodes_fast
        _write_prompt_nodes(home, [_make_record(f"n{i}") for i in range(5)])
        assert tail_prompt_nodes_fast(limit=0) == []

    def test_returns_last_n_records_in_reverse_order(self, home):
        """The fast path reads end-first, so newest (last appended) come
        first in the result. This matches the previous
        iter_prompt_nodes(limit=10) ordering semantics that callers rely on."""
        from trinity_local.memory.store import tail_prompt_nodes_fast
        # Append nodes node_0, node_1, ..., node_19 in order. node_19 is newest.
        _write_prompt_nodes(home, [_make_record(f"node_{i}") for i in range(20)])

        recent = tail_prompt_nodes_fast(limit=5)
        assert len(recent) == 5
        # First in returned list = newest appended = node_19
        ids = [n.id for n in recent]
        assert ids[0] == "node_19"
        # All five returned are the tail-five of the appended sequence
        # (some order tolerance: backward read parses end-to-start, so
        # node_19, node_18, ... node_15)
        assert set(ids) == {f"node_{i}" for i in range(15, 20)}

    def test_handles_records_larger_than_chunk_window(self, home):
        """A single PromptNode with a 100KB text field exceeds the 64KB
        chunk window — the backward reader must grow until it captures
        a full line, not silently drop the record."""
        from trinity_local.memory.store import tail_prompt_nodes_fast
        big = _make_record("big_one")
        big["text"] = "x" * 100_000  # 100KB, larger than CHUNK=64KB
        _write_prompt_nodes(home, [big])

        recent = tail_prompt_nodes_fast(limit=1)
        assert len(recent) == 1
        assert recent[0].id == "big_one"

    def test_skips_malformed_lines(self, home):
        """Pre-EOF garbage line shouldn't poison the parse."""
        from trinity_local.memory.store import tail_prompt_nodes_fast
        from trinity_local.state_paths import prompts_dir
        target = prompts_dir() / "prompt_nodes.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
            fh.write(json.dumps(_make_record("good_one")) + "\n")
            fh.write("\n")  # blank line
            fh.write(json.dumps(_make_record("good_two")) + "\n")

        recent = tail_prompt_nodes_fast(limit=5)
        ids = {n.id for n in recent}
        assert "good_one" in ids
        assert "good_two" in ids
        # No exception, no spurious entries
        assert len(recent) == 2
