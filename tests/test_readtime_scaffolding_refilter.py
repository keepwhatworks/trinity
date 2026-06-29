"""Read-time scaffolding re-filter — the LENS-bearing read paths re-apply the
ingest filter so already-poisoned PromptNodes are excluded WITHOUT a re-ingest.

The PromptNode index is append-only: nodes captured under an OLDER, weaker
ingest filter persist until a full re-seed (cursor reset). Every time the ingest
filter (`_is_user_facing_prompt`) learns a new scaffolding shape, that
improvement is INERT on the existing corpus unless the read paths re-apply it.
Measured on the real corpus 2026-06-02: 1,166 already-ingested scaffolding nodes
(4.0% of 29,374) — Gemini UI buttons, image markers, <goal_context>, the /loop
driver — sat in the index, feeding agent search and lens-build.

Two LENS-bearing read paths re-filter on read:
  - `memory.index.search_prompt_nodes`  (agent k-NN / autofill / ask routing)
  - `me.turn_pairs.iter_turn_pairs`      (lens-build Stage 0 correction input)

These guards pin that behavior. They are mutation-tests: revert the
`if not is_user_facing_text(...)` skip in either read path and the matching test
goes red (scaffolding text reappears in the results).
"""
from __future__ import annotations

import json

import pytest

from trinity_local.ingest import is_user_facing_text
from trinity_local.memory.schemas import PromptNode


# Real user prompt (must SURVIVE both read paths) + scaffolding shapes that the
# ingest filter learned AFTER these nodes were already indexed (must be DROPPED
# on read). Texts mirror the real-corpus top-excluded shapes.
_REAL = "find the bug in this function that crashes on empty input"
_SCAFFOLDING = [
    "You are one member of a multi-model council.\n\nTask:\nDo the thing.",
    "<goal_context>\nContinue working toward the active thread goal.\n</goal_context>",
    "Used an Assistant feature",
    "[Request interrupted by user for tool use]",
    "start new end to end flow to test the prod setup in various environments if "
    "currently paused. do actual work per what needs to be tested.",
]


def _node(idx: int, text: str) -> PromptNode:
    # Same transcript so iter_turn_pairs' per-transcript assistant fallback can
    # yield each node; preceding_assistant_text set so each is a yieldable pair.
    return PromptNode(
        id=f"n{idx}",
        transcript_id="t0",
        provider="claude",
        source_path="/x.jsonl",
        turn_index=idx,
        text=text,
        embedding=[],
        created_at="2026-06-01T00:00:00",
        timestamp="2026-06-01T00:00:00",
        preceding_assistant_text="here is the prior assistant turn",
        following_assistant_text="here is the next assistant turn",
    )


def _seed_index(home, texts):
    """Write a real prompt_nodes.jsonl under an isolated TRINITY_HOME."""
    from trinity_local.memory.store import prompt_nodes_path

    path = prompt_nodes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i, t in enumerate(texts):
            fh.write(json.dumps(_node(i, t).to_dict()) + "\n")
    return path


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    # The skinny-node reader memoizes by (path, mtime, size); a fresh tmp home
    # per test sidesteps cross-test cache bleed, but clear it defensively.
    import trinity_local.memory.store as store
    store._PROMPT_NODE_SKINNY_CACHE = None
    store._PROMPT_NODE_SKINNY_CACHE_KEY = None
    yield


def test_helper_matches_canonical_filter():
    """is_user_facing_text is the text-only projection of _is_user_facing_prompt."""
    from trinity_local.ingest import _is_user_facing_prompt
    from trinity_local.session_schema import SessionMessage

    for t in [_REAL, *_SCAFFOLDING, "", "  ", "ok"]:
        assert is_user_facing_text(t) == _is_user_facing_prompt(
            SessionMessage(role="user", text=t)
        ), f"wrapper diverged from canonical filter for {t[:40]!r}"
    # And the specific contract: real survives, every scaffolding shape drops.
    assert is_user_facing_text(_REAL) is True
    for s in _SCAFFOLDING:
        assert is_user_facing_text(s) is False, f"should drop {s[:40]!r}"


def test_search_excludes_already_ingested_scaffolding(tmp_path):
    """search_prompt_nodes must not surface already-poisoned scaffolding nodes.

    Empty-query mode ranks every surviving node by replay value, so the only
    reason a scaffolding text is absent is the read-time filter — mutation:
    delete the `is_user_facing_text` skip and the scaffolding reappears here."""
    _seed_index(tmp_path, [_REAL, *_SCAFFOLDING])
    from trinity_local.memory.index import search_prompt_nodes

    results = search_prompt_nodes("", top_k=50)
    surfaced = {r.text for r in results}
    assert _REAL in surfaced, "the genuine user prompt must still be searchable"
    for s in _SCAFFOLDING:
        assert s not in surfaced, f"scaffolding leaked into agent search: {s[:40]!r}"


def test_turn_pairs_excludes_already_ingested_scaffolding(tmp_path):
    """iter_turn_pairs (lens-build Stage 0) must not feed scaffolding user-turns
    to the chairman. Each seeded node is a yieldable pair (preceding assistant
    set), so a scaffolding turn's absence is the read-time filter, not a missing
    pair — mutation: delete the skip and the scaffolding user-turns reappear."""
    _seed_index(tmp_path, [_REAL, *_SCAFFOLDING])
    from trinity_local.me.turn_pairs import iter_turn_pairs

    user_turns = {user for (_assistant, user, _pid, _next) in iter_turn_pairs()}
    assert _REAL in user_turns, "the genuine user prompt must still form a turn pair"
    for s in _SCAFFOLDING:
        assert s.strip() not in user_turns, (
            f"scaffolding fed into lens-build Stage 0: {s[:40]!r}"
        )
