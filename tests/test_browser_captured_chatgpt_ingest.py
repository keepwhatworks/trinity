"""End-to-end ingest test for v1.6 browser-captured chatgpt.com conversations.

Drops a canonical chatgpt.com conversation JSON (mapping-graph shape) in
``~/.trinity/conversations/chatgpt/<conv_id>.json`` and verifies
``ingest_recent()`` picks it up and walks the parent-chain into linear
PromptNode entries.

Closes the same load-bearing wire as test_browser_captured_ingest.py
but for the OpenAI shape — ``mapping`` graph keyed by node id +
``current_node`` pointer, walked back to root then reversed.
"""

from __future__ import annotations

import json

import pytest

from trinity_local.incremental_ingest import ingest_recent
from trinity_local.ingest import parse_captured_chatgpt_conversation
from trinity_local.memory.store import iter_prompt_nodes


@pytest.fixture
def isolated_trinity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _canonical_payload(conv_id: str) -> dict:
    """Shape matches what chatgpt.com returns from
    GET /backend-api/conversation/<conv_id>. The mapping is a graph,
    parser walks from current_node back to root.
    """
    return {
        "title": "Trinity test thread",
        "create_time": 1747275000,
        "update_time": 1747275060,
        "default_model_slug": "gpt-4",
        "mapping": {
            "node-root": {
                "id": "node-root",
                "parent": None,
                "children": ["node-1"],
                "message": None,
            },
            "node-1": {
                "id": "node-1",
                "parent": "node-root",
                "children": ["node-2"],
                "message": {
                    "id": "msg-user-1",
                    "author": {"role": "user"},
                    "create_time": 1747275000,
                    "content": {
                        "content_type": "text",
                        "parts": ["What is Trinity Local?"],
                    },
                    "metadata": {},
                },
            },
            "node-2": {
                "id": "node-2",
                "parent": "node-1",
                "children": [],
                "message": {
                    "id": "msg-asst-2",
                    "author": {"role": "assistant"},
                    "create_time": 1747275060,
                    "content": {
                        "content_type": "text",
                        "parts": ["The cross-provider memory layer."],
                    },
                    "metadata": {"model_slug": "gpt-4"},
                },
            },
        },
        "current_node": "node-2",
        "conversation_id": conv_id,
    }


def test_parser_extracts_session_from_captured_file(tmp_path):
    path = tmp_path / "conv-cgpt.json"
    path.write_text(json.dumps(_canonical_payload("conv-cgpt")))

    rec = parse_captured_chatgpt_conversation(path)
    assert rec is not None
    assert rec.provider == "chatgpt"
    assert rec.session_id == "conv-cgpt"
    assert rec.source_format == "chatgpt_browser_capture"
    assert rec.model == "gpt-4"
    assert rec.title == "Trinity test thread"
    # Walked: node-1 (user) → node-2 (assistant). Linear order from root.
    assert len(rec.messages) == 2
    assert rec.messages[0].role == "user"
    assert rec.messages[0].text == "What is Trinity Local?"
    assert rec.messages[1].role == "assistant"
    assert rec.messages[1].text == "The cross-provider memory layer."


def test_parser_returns_none_for_adapter_stream_sidecar(tmp_path):
    """``<conv_id>.stream.json`` files have no ``mapping`` — parser
    must return None."""
    path = tmp_path / "conv-x.stream.json"
    path.write_text(json.dumps({
        "provider": "chatgpt",
        "kind": "adapter_stream",
        "conv_id": "conv-x",
        "assistant_text": "Hello.",
    }))
    assert parse_captured_chatgpt_conversation(path) is None


def test_parser_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "junk.json"
    path.write_text("not valid json {[")
    assert parse_captured_chatgpt_conversation(path) is None


def test_parser_handles_missing_current_node(tmp_path):
    """If ``current_node`` is missing, parser falls back to mapping
    insertion order (per existing parse_chatgpt_export behavior)."""
    payload = _canonical_payload("conv-noroot")
    payload["current_node"] = "missing-id"
    path = tmp_path / "conv-noroot.json"
    path.write_text(json.dumps(payload))

    rec = parse_captured_chatgpt_conversation(path)
    assert rec is not None
    # Insertion order: root (no message), node-1 (user), node-2 (assistant)
    assert len(rec.messages) == 2


def test_ingest_recent_picks_up_chatgpt_capture(isolated_trinity_home):
    capture_dir = isolated_trinity_home / "conversations" / "chatgpt"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-real.json").write_text(json.dumps(_canonical_payload("conv-real")))

    result = ingest_recent(sources=["browser_chatgpt"])

    assert result.scanned == 1, f"expected 1 file scanned, got {result.to_dict()}"
    assert result.added >= 1

    nodes = [n for n in iter_prompt_nodes(limit=None) if n.transcript_id == "conv-real"]
    assert nodes, "PromptNode for conv-real not found"
    texts = [n.text for n in nodes]
    assert any("What is Trinity Local?" in t for t in texts), (
        f"user turn text not preserved through ingest; texts={texts}"
    )


def test_ingest_recent_skips_chatgpt_stream_sidecar_files(isolated_trinity_home):
    capture_dir = isolated_trinity_home / "conversations" / "chatgpt"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-r.json").write_text(json.dumps(_canonical_payload("conv-r")))
    (capture_dir / "conv-r.stream.json").write_text(json.dumps({
        "provider": "chatgpt",
        "kind": "adapter_stream",
        "conv_id": "conv-r",
        "assistant_text": "Hello.",
    }))

    result = ingest_recent(sources=["browser_chatgpt"])

    assert result.scanned == 1
    assert result.skipped_parse == 0


def test_default_sources_includes_browser_chatgpt():
    """Regression guard: browser_chatgpt must stay in DEFAULT_SOURCES
    alongside browser_claude so MCP hot-path ingest picks up new
    captures from both providers without CLI flags."""
    from trinity_local.incremental_ingest import DEFAULT_SOURCES
    assert "browser_chatgpt" in DEFAULT_SOURCES, (
        "browser_chatgpt dropped from DEFAULT_SOURCES; v1.6 chatgpt "
        "captures would stop flowing through MCP-hot-path ingest."
    )
    assert "browser_claude" in DEFAULT_SOURCES, (
        "browser_claude dropped from DEFAULT_SOURCES — same regression."
    )
