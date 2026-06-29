"""MCP import_provider_memory: the in-protocol provider-side loop.

Closes the lens / eval loop for agents inside Claude Code / Cursor —
they extract from their own conversation context, pipe the JSON via
this MCP tool, and Trinity writes it to lens / rejections state
without the user leaving the harness.

Same dispatch logic as the lens-import / eval-import CLI verbs (this
test pins the wiring, not the dispatch internals — those already
have their own dedicated test files).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _call(args: dict) -> dict:
    """Invoke the MCP handler synchronously, unwrap the {type, text} envelope."""
    from trinity_local.mcp_server import _import_provider_memory
    result = asyncio.run(_import_provider_memory(args))
    assert len(result) == 1
    text = result[0]["text"]
    return json.loads(text)


def _good_rejection() -> dict:
    return {
        "type": "REFRAME",
        "model_quote": "Let me first walk you through the rationale",
        "user_substitute": "Skip the rationale, just give me the patch",
        "why_signal": "user wants answer-first",
        "confidence": "high",
    }


def _good_tension() -> dict:
    return {
        "pole_a": "concrete specificity",
        "pole_b": "abstract pattern",
        "failure_a": "examples without principle",
        "failure_b": "principle without examples",
        "horizon": "tactical",
        "evidence": ["debug session", "spec walkthrough"],
        "confidence": "high",
        "why_matters": "Need both to ship",
    }


class TestKindValidation:
    def test_unknown_kind_rejected(self, home):
        result = _call({"kind": "memory", "payload": {}})
        assert result["ok"] is False
        assert "kind must be 'lens' or 'eval'" in result["error"]

    def test_missing_kind_rejected(self, home):
        result = _call({"payload": {}})
        assert result["ok"] is False
        assert "kind must be" in result["error"]

    def test_payload_not_object_rejected(self, home):
        result = _call({"kind": "eval", "payload": "not an object"})
        assert result["ok"] is False
        assert "payload must be an object" in result["error"]


class TestEvalImport:
    def test_canonical_rejection_payload_persists(self, home):
        result = _call({
            "kind": "eval",
            "payload": {
                "source_provider": "claude",
                "rejections": [_good_rejection()],
            },
        })
        assert result["ok"] is True
        assert result["kind"] == "eval"
        assert result["rejections"]["new"] == 1
        assert result["rejections"]["duplicates"] == 0

    def test_re_import_dedups_via_stable_id(self, home):
        payload = {"source_provider": "claude", "rejections": [_good_rejection()]}
        first = _call({"kind": "eval", "payload": payload})
        second = _call({"kind": "eval", "payload": payload})
        assert first["rejections"]["new"] == 1
        assert second["rejections"]["new"] == 0
        assert second["rejections"]["duplicates"] == 1

    def test_dry_run_does_not_persist(self, home):
        payload = {"source_provider": "claude", "rejections": [_good_rejection()]}
        result = _call({"kind": "eval", "payload": payload, "dry_run": True})
        assert result["dry_run"] is True
        # Follow-up real import should still see this as new (nothing landed yet)
        real = _call({"kind": "eval", "payload": payload})
        assert real["rejections"]["new"] == 1


class TestLensImport:
    def test_canonical_tension_payload_persists(self, home):
        result = _call({
            "kind": "lens",
            "payload": {
                "source_provider": "claude",
                "tensions": [_good_tension()],
            },
        })
        assert result["ok"] is True
        assert result["kind"] == "lens"
        assert result["tensions"]["new"] == 1

    def test_provider_override_attributes_to_caller(self, home):
        """When the agent supplies its own attribution, --provider wins."""
        result = _call({
            "kind": "lens",
            "payload": {
                "source_provider": "gemini",
                "tensions": [_good_tension()],
            },
            "provider": "claude",
        })
        assert result["source_provider"] == "claude"


class TestMcpToolRegistration:
    """Pin that the tool is wired into the MCP surface so an external
    SDK call by name actually dispatches."""

    def test_handle_call_tool_routes_unknown_kind(self, home):
        from trinity_local.mcp_server import handle_call_tool
        result = asyncio.run(handle_call_tool(
            "import_provider_memory",
            {"kind": "garbage", "payload": {}},
        ))
        # Same error structure as our direct-call tests
        text = result[0]["text"]
        body = json.loads(text)
        assert body["ok"] is False

    def test_tool_appears_in_list_tools(self):
        from trinity_local.mcp_server import handle_list_tools
        tools = asyncio.run(handle_list_tools())
        names = {t.name for t in tools}
        assert "import_provider_memory" in names
