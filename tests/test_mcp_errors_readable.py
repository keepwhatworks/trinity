"""MCP tool errors reach the agent as readable text (coherence review 2026-10-15).

Handlers return `[ErrorData(...)]` for refusals and exceptions. That is not valid tool
content, so the SDK's CallToolResult validation failed and the agent got
"5 validation errors for CallToolResult" with the real message truncated, reproduced on
the shipped v1.7.402 wheel. The old test checked only the handler's return value, never
what the SDK serialized. These go through the SDK's own request handler.
"""
from __future__ import annotations

import asyncio
import json

from mcp import types

from trinity_local import mcp_server


def _call(name: str, arguments: dict) -> types.CallToolResult:
    handler = mcp_server.server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(method="tools/call",
                                params=types.CallToolRequestParams(name=name, arguments=arguments))
    return asyncio.run(handler(req)).root


def _body(result: types.CallToolResult) -> dict:
    assert result.isError, "a refusal must be flagged isError on the wire"
    text = result.content[0].text
    assert "validation error" not in text, text
    return json.loads(text)


def test_a_refusal_carries_its_message(patch_trinity_home):
    body = _body(_call("verify", {"criteria": [], "diff": "x"}))
    assert body["ok"] is False and "criteria" in body["error"] and body["code"] == 400


def test_an_exception_carries_its_message(patch_trinity_home, monkeypatch):
    async def boom(args):
        raise RuntimeError("the ledger file is unreadable")
    monkeypatch.setattr(mcp_server, "_trust", boom)
    body = _body(_call("trust", {"query": "x"}))
    assert body["ok"] is False and "the ledger file is unreadable" in body["error"]


def test_an_unknown_tool_says_so(patch_trinity_home):
    body = _body(_call("no_such_tool", {}))
    assert body["code"] == 404 and "no_such_tool" in body["error"]
