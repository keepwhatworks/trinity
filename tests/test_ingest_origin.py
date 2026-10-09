"""Provenance at ingest: every prompt carries how its session was started.

The "only the founder" lens filter (me/authorship.py, amd_0310) drops prompts from headless
sessions (`claude -p`, the Agent SDK, `codex exec`). Claude Code deletes old transcripts, so
the only durable record of a session's origin is the one taken at ingest. These pin the three
readings for both CLIs, the carry-through to prompt turns and nodes, and that a stored node
written before the field existed still loads.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local.ingest import iter_prompt_turns, parse_claude_code_session, parse_codex_session
from trinity_local.memory.schemas import PromptNode


def _claude(path: Path, entrypoint: str | None) -> Path:
    extra = {"entrypoint": entrypoint} if entrypoint else {}
    rows = [
        {"type": "user", "timestamp": "2026-06-01T00:00:00Z", **extra,
         "message": {"role": "user", "content": "please refactor the parser so it streams"}},
        {"type": "assistant", "timestamp": "2026-06-01T00:00:01Z", **extra,
         "message": {"role": "assistant", "model": "claude-opus-5-5",
                     "content": [{"type": "text", "text": "Streaming parser done, tests pass."}]}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _codex(path: Path, originator: str | None) -> Path:
    meta = {"id": "01a08dc2-2ae7-74d3-b7fb-ea47d81c0001", "cwd": "/tmp/x", "model_provider": "openai"}
    if originator:
        meta["originator"] = originator
    rows = [
        {"timestamp": "2026-09-10T23:58:27Z", "type": "session_meta", "payload": meta},
        {"timestamp": "2026-09-10T23:58:28Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "fix the flaky council poller"}},
        {"timestamp": "2026-09-10T23:58:28Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "fix the flaky council poller"}]}},
        {"timestamp": "2026-09-10T23:58:29Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "Fixed; the poller now gives up after 3 misses."}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


@pytest.mark.parametrize("entrypoint,expected", [("sdk-cli", "headless"), ("sdk-py", "headless"),
                                                 ("cli", "interactive"), (None, None)])
def test_claude_code_origin(tmp_path, entrypoint, expected):
    rec = parse_claude_code_session(_claude(tmp_path / "s.jsonl", entrypoint))
    assert rec is not None and rec.origin == expected
    turns = list(iter_prompt_turns(rec))
    assert turns and all(t.origin == expected for t in turns)


@pytest.mark.parametrize("originator,expected", [("codex_exec", "headless"), ("codex-tui", "interactive"),
                                                 ("Codex Desktop", "interactive"), (None, None)])
def test_codex_origin(tmp_path, originator, expected):
    rec = parse_codex_session(_codex(tmp_path / "rollout-2026-09-10T19-58-27-x.jsonl", originator))
    assert rec is not None and rec.origin == expected
    turns = list(iter_prompt_turns(rec))
    assert turns and all(t.origin == expected for t in turns)


def test_prompt_node_round_trips_origin_and_loads_legacy_rows():
    n = PromptNode(id="p1", transcript_id="t", provider="claude", source_path="/x", turn_index=0,
                   text="hi", embedding=[], created_at="2026-10-07T00:00:00+00:00", origin="headless")
    assert PromptNode.from_dict(n.to_dict()).origin == "headless"
    legacy = n.to_dict()
    legacy.pop("origin")
    assert PromptNode.from_dict(legacy).origin is None
