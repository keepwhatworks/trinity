"""End-to-end ingest test for v1.8 browser-captured gemini.google.com conversations.

Task #135: closes the gemini.js capture gap. The browser extension's
``adapters/gemini.js`` writes adapter_stream payloads with best-effort
``assistant_text`` extraction from Google's batchexecute RPC frames.
The capture host lands them at
``~/.trinity/conversations/gemini/<conv_id>.stream.json``.

Unlike claude.ai / chatgpt.com (whose canonical fetches return the full
message tree), Gemini's batchexecute is reply-only — so the parser
yields ONE assistant message per file. The user prompt is recovered
from the other adapters' captures; here we only need to keep the
gemini reply text from being orphaned on disk.
"""

from __future__ import annotations

import json

import pytest

from trinity_local.incremental_ingest import ingest_recent
from trinity_local.ingest import parse_captured_gemini_conversation
from trinity_local.memory.store import iter_prompt_nodes


@pytest.fixture
def isolated_trinity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _adapter_payload(
    conv_id: str,
    assistant_text: str = "Trinity Local lets you keep the supervision signal local.",
    user_text: str = "How does Trinity handle supervision signal?",
) -> dict:
    """Shape matches what browser-extension/adapters/gemini.js emits.

    See gemini.js::adapt — `kind: "adapter_stream"`, `conv_id` from the
    user's URL, best-effort `assistant_text` extracted from batchexecute
    response frames, best-effort `user_text` extracted from the
    batchexecute REQUEST body, raw bodies preserved for re-extraction.
    """
    return {
        "provider": "gemini",
        "kind": "adapter_stream",
        "conv_id": conv_id,
        "message_id": "msg-abcdef12",
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute?rpcids=...",
        "method": "POST",
        "captured_at": "2026-05-22T12:00:00Z",
        "frames_count": 3,
        "events_count": 2,
        "user_text": user_text,
        "assistant_text": assistant_text,
        "_raw_body": ")]}'\n42\n[[\"wrb.fr\",\"abc\",\"...\"]]\n",
        "_raw_request_body": "f.req=...",
    }


def test_parser_extracts_session_from_captured_file(tmp_path):
    path = tmp_path / "conv-gem-1.stream.json"
    path.write_text(json.dumps(_adapter_payload("conv-gem-1")))

    rec = parse_captured_gemini_conversation(path)
    assert rec is not None
    assert rec.provider == "gemini"
    assert rec.session_id == "conv-gem-1"
    assert rec.source_format == "gemini_browser_capture"
    assert rec.cli_name == "gemini_webapp"
    # Both turns: user prompt (from request body) + assistant reply (from
    # response frames). Order matters — user first so iter_prompt_turns
    # picks the assistant text up as following_assistant_text.
    assert len(rec.messages) == 2
    assert rec.messages[0].role == "user"
    assert "supervision signal" in rec.messages[0].text
    assert rec.messages[1].role == "assistant"
    assert "Trinity Local" in rec.messages[1].text
    assert rec.messages[1].timestamp == "2026-05-22T12:00:00Z"


def test_parser_handles_missing_user_text_back_compat(tmp_path):
    """Captures from before page-hook.js learned to snapshot request
    bodies won't have a user_text field. Parser must still emit a
    SessionRecord (assistant-only), even though it contributes no
    PromptTurn entries.
    """
    payload = _adapter_payload("conv-legacy")
    del payload["user_text"]
    path = tmp_path / "conv-legacy.stream.json"
    path.write_text(json.dumps(payload))

    rec = parse_captured_gemini_conversation(path)
    assert rec is not None
    # Assistant-only — no user turn means iter_prompt_turns yields nothing.
    assert len(rec.messages) == 1
    assert rec.messages[0].role == "assistant"


def test_parser_returns_none_for_wrong_provider(tmp_path):
    """Defense in depth: if a misrouted claude/chatgpt payload lands in
    conversations/gemini/, the parser must reject — not corrupt the
    corpus with a misattributed session.
    """
    path = tmp_path / "misrouted.stream.json"
    path.write_text(json.dumps({
        "provider": "claude",
        "kind": "adapter_stream",
        "conv_id": "conv-x",
        "assistant_text": "Hello.",
    }))
    assert parse_captured_gemini_conversation(path) is None


def test_parser_returns_none_for_empty_assistant_text(tmp_path):
    """When Gemini's frame shape moves, the best-effort extractor may
    return empty assistant_text. Parser returns None so the file stays
    on disk for a later re-extraction with an updated adapter.
    """
    payload = _adapter_payload("conv-empty", assistant_text="")
    path = tmp_path / "conv-empty.stream.json"
    path.write_text(json.dumps(payload))
    assert parse_captured_gemini_conversation(path) is None


def test_parser_returns_none_for_missing_conv_id(tmp_path):
    payload = _adapter_payload("ok")
    del payload["conv_id"]
    path = tmp_path / "no-id.stream.json"
    path.write_text(json.dumps(payload))
    assert parse_captured_gemini_conversation(path) is None


def test_parser_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "junk.stream.json"
    path.write_text("not valid json {[")
    assert parse_captured_gemini_conversation(path) is None


def test_ingest_recent_picks_up_gemini_capture(isolated_trinity_home):
    capture_dir = isolated_trinity_home / "conversations" / "gemini"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-real.stream.json").write_text(
        json.dumps(_adapter_payload("conv-real"))
    )

    result = ingest_recent(sources=["browser_gemini"])

    assert result.scanned == 1, f"expected 1 file scanned, got {result.to_dict()}"
    assert result.added >= 1

    nodes = [n for n in iter_prompt_nodes(limit=None) if n.transcript_id == "conv-real"]
    assert nodes, "PromptNode for conv-real not found"
    # The assistant turn made it through ingest with its text intact.
    texts = [n.text for n in nodes]
    assert any("supervision signal" in t for t in texts), (
        f"assistant turn text not preserved through ingest; texts={texts}"
    )


def test_ingest_recent_includes_stream_json_for_gemini(isolated_trinity_home):
    """Unlike browser_claude / browser_chatgpt (where .stream.json files
    are sidecars to canonical fetches and excluded), browser_gemini's
    .stream.json files ARE the data — Gemini has no canonical fetch.
    Regression guard for the filter divergence in watch_runtime.py.
    """
    capture_dir = isolated_trinity_home / "conversations" / "gemini"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-stream.stream.json").write_text(
        json.dumps(_adapter_payload("conv-stream"))
    )

    result = ingest_recent(sources=["browser_gemini"])

    # The .stream.json file was scanned (not silently filtered out).
    assert result.scanned == 1
    assert result.skipped_parse == 0


def test_default_sources_includes_browser_gemini():
    """Regression guard: browser_gemini must stay in DEFAULT_SOURCES
    alongside browser_claude / browser_chatgpt so MCP hot-path ingest
    picks up new Gemini captures without CLI flags.
    """
    from trinity_local.incremental_ingest import DEFAULT_SOURCES
    assert "browser_gemini" in DEFAULT_SOURCES, (
        "browser_gemini dropped from DEFAULT_SOURCES; v1.8 gemini "
        "captures would stop flowing through MCP-hot-path ingest."
    )
