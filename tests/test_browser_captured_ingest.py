"""End-to-end ingest test for v1.6 browser-captured conversations.

Drops a canonical claude.ai conversation JSON in
``~/.trinity/conversations/claude/<conv_id>.json`` (the exact path the
capture host writes to) and verifies ``ingest_recent()`` picks it up
and produces ``PromptNode`` entries in the index. Closes the load-
bearing wire the spec calls for at line 422-425: captures must flow
into the existing memory pipeline so cortex/lens/picks see them.

Uses the real parser + the real watch_runtime dispatch — no
monkeypatching of the parse layer. The only env override is
``TRINITY_HOME`` to keep this test off the user's real corpus.
"""

from __future__ import annotations

import json

import pytest

from trinity_local.incremental_ingest import ingest_recent
from trinity_local.ingest import parse_captured_claude_conversation
from trinity_local.memory.store import iter_prompt_nodes


@pytest.fixture
def isolated_trinity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _canonical_payload(conv_id: str, *, name: str = "Test thread") -> dict:
    """Shape matches what claude.ai returns from
    GET /api/organizations/<org>/chat_conversations/<conv_id>.
    """
    return {
        "uuid": conv_id,
        "name": name,
        "summary": "",
        "model": "claude-3-opus",
        "created_at": "2026-05-15T00:00:00Z",
        "updated_at": "2026-05-15T00:05:00Z",
        "settings": {},
        "current_leaf_message_uuid": "m2",
        "chat_messages": [
            {
                "uuid": "m1",
                "sender": "human",
                "text": "What is Trinity Local?",
                "index": 0,
                "created_at": "2026-05-15T00:00:00Z",
                "updated_at": "2026-05-15T00:00:00Z",
                "input_mode": "user",
                "truncated": False,
                "parent_message_uuid": None,
            },
            {
                "uuid": "m2",
                "sender": "assistant",
                "text": "Trinity Local is the cross-provider memory layer.",
                "index": 1,
                "created_at": "2026-05-15T00:01:00Z",
                "updated_at": "2026-05-15T00:01:00Z",
                "input_mode": None,
                "truncated": False,
                "parent_message_uuid": "m1",
            },
        ],
    }


def test_parser_extracts_session_from_captured_file(tmp_path):
    """Direct parser test — no incremental-ingest harness."""
    path = tmp_path / "conv-direct.json"
    path.write_text(json.dumps(_canonical_payload("conv-direct")))

    rec = parse_captured_claude_conversation(path)
    assert rec is not None
    assert rec.provider == "claude_ai"
    assert rec.session_id == "conv-direct"
    assert rec.source_format == "claude_browser_capture"
    assert len(rec.messages) == 2
    assert rec.messages[0].role == "user"
    assert rec.messages[0].text == "What is Trinity Local?"
    assert rec.messages[1].role == "assistant"


def test_parser_returns_none_for_adapter_stream_sidecar(tmp_path):
    """``<conv_id>.stream.json`` files have no chat_messages — parser
    must return None so iter_prompt_turns has nothing to yield."""
    path = tmp_path / "conv-x.stream.json"
    path.write_text(json.dumps({
        "provider": "claude",
        "kind": "adapter_stream",
        "conv_id": "conv-x",
        "assistant_text": "Hello.",
    }))
    assert parse_captured_claude_conversation(path) is None


def test_parser_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "junk.json"
    path.write_text("not valid json {[")
    assert parse_captured_claude_conversation(path) is None


def test_ingest_recent_picks_up_browser_capture(isolated_trinity_home):
    """End-to-end: write a captured conversation file in the
    canonical capture-host directory, run ingest_recent with the
    "browser_claude" source, verify a PromptNode is appended.
    """
    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-real.json").write_text(json.dumps(_canonical_payload("conv-real")))

    result = ingest_recent(sources=["browser_claude"])

    assert result.scanned == 1, f"expected 1 file scanned, got {result.to_dict()}"
    assert result.added >= 1, "expected at least 1 PromptNode added"

    # The PromptNode for the user turn should now be in the store.
    nodes = [n for n in iter_prompt_nodes(limit=None) if n.transcript_id == "conv-real"]
    assert nodes, "PromptNode for conv-real not found in the index"
    # The user turn — "human" sender — is what iter_prompt_turns yields.
    texts = [n.text for n in nodes]
    assert any("What is Trinity Local?" in t for t in texts), (
        f"user turn text not preserved through ingest; texts={texts}"
    )


def test_ingest_recent_skips_stream_sidecar_files(isolated_trinity_home):
    """Filesystem-level filter: ``*.stream.json`` files must not even
    be parse-attempted. Saves a parse cycle and avoids a noisy
    skipped_parse count."""
    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-r.json").write_text(json.dumps(_canonical_payload("conv-r")))
    (capture_dir / "conv-r.stream.json").write_text(json.dumps({
        "provider": "claude",
        "kind": "adapter_stream",
        "conv_id": "conv-r",
        "assistant_text": "Hello.",
    }))

    result = ingest_recent(sources=["browser_claude"])

    # Only the canonical conv-r.json was scanned; the .stream.json was
    # filtered out at the glob level (see watch_runtime._iter_recent_paths).
    assert result.scanned == 1
    assert result.skipped_parse == 0


def test_ingest_recent_idempotent_across_two_runs(isolated_trinity_home):
    """Same captured file scanned twice → second run adds zero nodes.

    Validates two layers: cursor advancement gates a second scan, and
    even if cursor were missing, the stable_id dedup blocks reappends.
    """
    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv-idem.json").write_text(json.dumps(_canonical_payload("conv-idem")))

    first = ingest_recent(sources=["browser_claude"])
    assert first.added >= 1

    second = ingest_recent(sources=["browser_claude"])
    assert second.added == 0


def test_default_sources_includes_browser_claude():
    """Regression guard: the v1.6 capture source must stay in
    DEFAULT_SOURCES so MCP ``ask`` calls trigger incremental ingest
    of newly-captured conversations without requiring CLI flags.
    If it drops off the default list the captures silently fail to
    flow into the index. (Earlier draft of this docstring mentioned
    ``search_prompts``; that MCP tool was retired pre-launch
    2026-05-17 — ``ask`` is the live ingest-triggering entry point.)"""
    from trinity_local.incremental_ingest import DEFAULT_SOURCES
    assert "browser_claude" in DEFAULT_SOURCES, (
        "browser_claude dropped from DEFAULT_SOURCES; v1.6 captures would "
        "stop flowing through MCP-hot-path incremental ingest. Re-add it "
        "to incremental_ingest.DEFAULT_SOURCES."
    )


class TestCaptureHostToIngestRoundTrip:
    """End-to-end SEAM: the file capture_host actually WRITES must be the file
    ingest READS. The existing tests pin each HALF with independently-hardcoded
    filenames (test_capture_host_stdio writes conv-x.stream.json; the ingest
    tests above write conv-r.stream.json directly) — so a write/read CONVENTION
    DRIFT (capture_host renames the sidecar suffix without updating the ingest
    glob, or vice versa) passes both halves while the real flow breaks. These
    feed capture_host's REAL output (_extract_target + _write_capture) into the
    REAL ingest_recent. (test_the_boundary_and_the_action: test the seam with a
    real message, not each side in isolation.)"""

    def _write_via_capture_host(self, payload):
        from trinity_local import capture_host as ch
        provider, conv_id, conv = ch._extract_target(payload)
        return ch._write_capture(provider, conv_id, conv)

    def test_claude_canonical_written_by_host_is_ingested(self, isolated_trinity_home):
        self._write_via_capture_host({
            "provider": "claude", "kind": "canonical",
            "conversation": _canonical_payload("conv-rt"),
        })
        result = ingest_recent(sources=["browser_claude"])
        assert result.scanned == 1, result.to_dict()
        assert result.added >= 1
        nodes = [n for n in iter_prompt_nodes(limit=None) if n.transcript_id == "conv-rt"]
        assert any("What is Trinity Local?" in n.text for n in nodes), (
            "user turn not preserved through the capture_host→ingest seam"
        )

    def test_claude_stream_sidecar_written_by_host_is_excluded(self, isolated_trinity_home):
        # capture_host writes BOTH the canonical AND the adapter-stream sidecar
        # for the same conversation. The sidecar filename capture_host produces
        # (<conv_id>.stream.json) must be excluded by the ingest glob — only the
        # canonical is scanned. A drift between the two breaks here.
        self._write_via_capture_host({
            "provider": "claude", "kind": "canonical",
            "conversation": _canonical_payload("conv-rt2"),
        })
        self._write_via_capture_host({
            "provider": "claude", "kind": "adapter_stream",
            "conv_id": "conv-rt2", "assistant_text": "Hello.",
        })
        result = ingest_recent(sources=["browser_claude"])
        assert result.scanned == 1, (
            "capture_host's stream-sidecar filename drifted from the ingest glob "
            f"exclusion (claude/chatgpt skip .stream.json): {result.to_dict()}"
        )

    def test_gemini_stream_written_by_host_is_ingested(self, isolated_trinity_home):
        # Gemini's ONLY capture is the .stream.json (batchexecute is reply-only),
        # so — UNLIKE claude/chatgpt — the ingest glob must INCLUDE it. A drift
        # that made browser_gemini exclude .stream.json would silently lose EVERY
        # gemini capture. Feed capture_host's real gemini-stream output.
        self._write_via_capture_host({
            "provider": "gemini", "kind": "adapter_stream",
            "conv_id": "g-rt", "file_stem": "g-rt__m1",
            "user_text": "write a haiku about parsers",
            "assistant_text": "Tokens flow like streams.",
        })
        result = ingest_recent(sources=["browser_gemini"])
        assert result.scanned == 1, (
            "capture_host's gemini .stream.json isn't ingested — a glob-exclusion "
            f"drift would silently lose every gemini capture: {result.to_dict()}"
        )
        assert result.added >= 1
