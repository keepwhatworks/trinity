"""`search_prompt_nodes` must never spend two result slots on identical text.

WHY (measured 2026-08-01 on the real 40k-node corpus). 46.9% of prompt nodes are
duplicates — a `/loop` cron replays one prompt hundreds of times. MMR was meant
to absorb that, and in EMPTY-QUERY mode it did. In QUERY mode it did not, and the
cause was a SCALE MISMATCH rather than a badly chosen lambda:

    MMR picks argmax(lambda*relevance - (1-lambda)*similarity), so at
    lambda=0.72 an exact duplicate is penalised by at most 0.28.
      empty-query scores spanned 0.006  -> penalty ~47x the spread, dominates
      query-mode scores spanned 1.042   -> penalty ~27% of the spread, ignored

Measured waste at top_k=20 on four real queries BEFORE the fix: 25%, 35%, 45%,
60%. The worst case returned 8 distinct texts in 20 slots.

The fix collapses byte-identical texts before the candidate cut. These tests pin
the INVARIANT (no repeated text in results) rather than the mechanism, so a
future re-tuning of lambda or a switch to normalised relevance stays free —
what must not come back is the duplicate.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.memory.index import search_prompt_nodes
from trinity_local.memory.schemas import PromptNode

QUERY = "mcp server wiring for codex"
MATCHING = "how do I wire the mcp server into codex properly"


def _node(idx: int, text: str, *, councils: int = 0) -> PromptNode:
    return PromptNode(
        id=f"n{idx:03d}",
        transcript_id="t0",
        provider="claude",
        source_path="/x.jsonl",
        turn_index=idx,
        text=text,
        embedding=[],
        created_at="2026-06-01T00:00:00",
        timestamp="2026-06-01T00:00:00",
        preceding_assistant_text="prior assistant turn",
        following_assistant_text="next assistant turn",
        council_run_ids=[f"c{i}" for i in range(councils)],
    )


def _seed(nodes):
    from trinity_local.memory.store import prompt_nodes_path

    path = prompt_nodes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for n in nodes:
            fh.write(json.dumps(n.to_dict()) + "\n")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    import trinity_local.memory.store as store

    store._PROMPT_NODE_SKINNY_CACHE = None
    store._PROMPT_NODE_SKINNY_CACHE_KEY = None
    yield


def test_query_mode_never_returns_the_same_text_twice():
    """The regression that was live: 30 copies of one prompt filling the slots."""
    _seed([_node(i, MATCHING) for i in range(30)]
          + [_node(100 + i, f"a different mcp codex question number {i}") for i in range(5)])
    hits = search_prompt_nodes(QUERY, top_k=20)
    texts = [" ".join(h.text.split()).lower() for h in hits]
    assert len(texts) == len(set(texts)), f"duplicate text in results: {texts}"


def test_returns_fewer_than_top_k_rather_than_padding_with_repeats():
    """Honest under-fill. Only 3 distinct matching prompts exist, so 3 come back
    — never 20 slots with 17 repeats, which would look like recall it does not
    have."""
    _seed([_node(i, MATCHING) for i in range(25)]
          + [_node(50, "another mcp codex wiring question"),
             _node(51, "a third mcp codex wiring question")])
    hits = search_prompt_nodes(QUERY, top_k=20)
    assert 0 < len(hits) <= 3, f"expected at most 3 distinct hits, got {len(hits)}"
    texts = [" ".join(h.text.split()).lower() for h in hits]
    assert len(texts) == len(set(texts))


def test_the_surviving_copy_is_the_highest_scoring_one():
    """Collapsing must keep the BEST copy, not an arbitrary one. Council count
    feeds the score, so the copy with councils attached must be the survivor —
    otherwise dedup would silently discard the richest node."""
    plain = [_node(i, MATCHING) for i in range(5)]
    rich = _node(99, MATCHING, councils=3)
    _seed([*plain, rich])
    hits = search_prompt_nodes(QUERY, top_k=5)
    assert len(hits) == 1, f"identical text should collapse to one hit, got {len(hits)}"
    assert hits[0].council_count == 3, "dedup kept a weaker copy than the best available"


def test_whitespace_and_case_variants_count_as_the_same_text():
    """The corpus carries the same prompt with different wrapping; a collapse
    keyed on raw bytes would let those through."""
    _seed([
        _node(0, MATCHING),
        _node(1, MATCHING.upper()),
        _node(2, "  " + MATCHING.replace(" ", "\n") + "  "),
        _node(3, "a genuinely different mcp codex question"),
    ])
    hits = search_prompt_nodes(QUERY, top_k=10)
    texts = [" ".join(h.text.split()).lower() for h in hits]
    assert len(texts) == len(set(texts)) == 2, f"expected 2 distinct, got {texts}"


def test_empty_query_sampling_mode_still_deduplicates():
    """Empty-query mode already behaved (its score spread is comparable to the
    MMR penalty). Pinned so the fix cannot regress the mode that was fine."""
    _seed([_node(i, "review all project md files for consistency") for i in range(20)]
          + [_node(100 + i, f"a distinct question about topic {i}") for i in range(10)])
    hits = search_prompt_nodes("", top_k=10)
    texts = [" ".join(h.text.split()).lower() for h in hits]
    assert len(texts) == len(set(texts)), f"duplicate text in empty-query mode: {texts}"


def test_result_order_is_deterministic_across_calls():
    """The collapse keeps the first row of a total order; if that order were
    unstable, WHICH copy survives would flip between runs and so would the
    chairman's context."""
    _seed([_node(i, MATCHING) for i in range(10)]
          + [_node(100 + i, f"distinct mcp codex question {i}") for i in range(6)])
    first = [h.prompt_id for h in search_prompt_nodes(QUERY, top_k=10)]
    for _ in range(3):
        assert [h.prompt_id for h in search_prompt_nodes(QUERY, top_k=10)] == first
