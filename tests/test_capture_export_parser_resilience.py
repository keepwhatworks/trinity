"""Resilience: the CAPTURE + WEB-EXPORT parsers (the cross-provider moat —
claude.ai / chatgpt.com / gemini.google.com captures + Takeout/web exports) must
recover from a non-UTF8 byte, not crash.

These are a SEPARATE parser set from the CLI transcript parsers (ingest.parse_*_
session), and the v1.7.358 `errors="replace"` hardening only reached the CLI ones.
The capture/export parsers read `json.loads(path.read_text(encoding="utf-8"))` with
`except (OSError, json.JSONDecodeError)` — which does NOT catch `UnicodeDecodeError`
(a ValueError). So a single stray byte in a captured conversation (an interrupted
Native-Messaging write, odd content) or a web export (a corrupt download) raised
straight out of the parser: `watch_runtime` ingests captures by calling
`parse_captured_*` directly, and the import-export full-parse re-reads the whole file
AFTER detection sniffed only the first 8KB as UTF-8 — so a bad byte past 8KB passed
detection then crashed the import.

Found dogfooding the capture/export parsers with an adversarial battery (all 5 crashed
on a non-UTF8 byte; shape guards for wrong-type top-level / nested were already fine).
Fixed by `errors="replace"` at all 7 `read_text` sites — recover the conversation
(the bad byte → U+FFFD) instead of dropping it. (guard_shape_not_just_parse class,
the encoding sibling — [[guard_shape_not_just_parse]].)

Mutation-proven: revert any of the read sites to bare `encoding="utf-8"` and the
matching recovery test reds with UnicodeDecodeError.
"""
from __future__ import annotations

import json
from pathlib import Path

from trinity_local.ingest import (
    parse_captured_chatgpt_conversation,
    parse_captured_claude_conversation,
    parse_chatgpt_export,
    parse_claude_ai_export,
)

_BAD = b"\xff\xfe"  # invalid UTF-8


def _inject_bad_byte(payload: dict, marker: str, path: Path) -> None:
    """Dump `payload` to JSON bytes, then replace `marker` (inside a message string)
    with raw non-UTF8 bytes — a stray byte INSIDE a value, so `errors='replace'`
    recovers a parseable doc (U+FFFD in the text) rather than a structural break."""
    raw = json.dumps(payload).encode("utf-8")
    assert marker.encode() in raw, "marker not in the serialized payload"
    path.write_bytes(raw.replace(marker.encode(), _BAD))


def _claude_payload() -> dict:
    return {
        "uuid": "conv-1", "name": "t", "model": "claude-3-opus",
        "chat_messages": [
            {"uuid": "m1", "sender": "human",
             "text": "What is MARKERHERE Trinity Local?", "index": 0},
            {"uuid": "m2", "sender": "assistant",
             "text": "The cross-provider memory layer.", "index": 1},
        ],
    }


def _chatgpt_payload() -> dict:
    return {
        "title": "t", "conversation_id": "conv-c", "current_node": "n2",
        "mapping": {
            "n1": {"id": "n1", "parent": None, "children": ["n2"],
                   "message": {"id": "u1", "author": {"role": "user"},
                               "content": {"content_type": "text",
                                           "parts": ["a MARKERHERE prompt"]}}},
            "n2": {"id": "n2", "parent": "n1", "children": [],
                   "message": {"id": "a2", "author": {"role": "assistant"},
                               "content": {"content_type": "text",
                                           "parts": ["a reply"]}}},
        },
    }


def test_captured_claude_recovers_on_non_utf8_byte(tmp_path):
    f = tmp_path / "conv.json"
    _inject_bad_byte(_claude_payload(), "MARKERHERE", f)
    rec = parse_captured_claude_conversation(f)  # must NOT raise UnicodeDecodeError
    assert rec is not None, "a non-UTF8 byte dropped the whole captured claude.ai conversation"
    assert any(m.role == "user" and m.text for m in rec.messages), (
        "recovered no user turn from the captured conversation around the bad byte"
    )


def test_captured_chatgpt_recovers_on_non_utf8_byte(tmp_path):
    f = tmp_path / "conv.json"
    _inject_bad_byte(_chatgpt_payload(), "MARKERHERE", f)
    rec = parse_captured_chatgpt_conversation(f)  # must NOT raise
    assert rec is not None, "a non-UTF8 byte dropped the whole captured chatgpt.com conversation"
    assert any(m.role == "user" and m.text for m in rec.messages)


def test_export_parsers_survive_non_utf8(tmp_path):
    """The web-export parsers (Takeout / downloaded JSON) must also not raise on a
    stray byte — a corrupt download recovers its good conversations."""
    f = tmp_path / "conversations.json"
    f.write_bytes(b'[{"name":"t","chat_messages":[]}]' + _BAD)
    list(parse_claude_ai_export(f))   # must NOT raise
    list(parse_chatgpt_export(f))     # must NOT raise


def test_export_full_parse_survives_bad_byte_beyond_8kb_sniff(tmp_path):
    """The detection-passes-then-crashes gap: import-export sniffs only the first
    ~8KB to classify a conversations.json, but the FULL parse re-reads the whole
    file. A conversation that is clean UTF-8 for the first 8KB but has a stray byte
    LATER passed detection then crashed the import. The parser must recover."""
    # >8KB of valid UTF-8 padding in an early turn, then a bad byte in a LATER one
    # (the conversation needs an assistant turn to yield a session at all).
    payload = [{
        "name": "t", "uuid": "c1",
        "chat_messages": [
            {"uuid": "m0", "sender": "human", "text": "a real question " + "x" * 9000, "index": 0},
            {"uuid": "m1", "sender": "assistant", "text": "a reply MARKERHERE here", "index": 1},
        ],
    }]
    f = tmp_path / "conversations.json"
    raw = json.dumps(payload).encode("utf-8")
    f.write_bytes(raw.replace(b"MARKERHERE", _BAD))
    sessions = list(parse_claude_ai_export(f))  # must NOT raise UnicodeDecodeError
    assert sessions, "the export with a bad byte past the 8KB sniff dropped everything"
