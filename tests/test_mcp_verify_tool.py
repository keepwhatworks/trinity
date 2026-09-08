"""`mcp__trinity-local__verify` — the pre-deploy gate on the MCP surface.

Kernel path only in the suite (no panel dispatch). Pins: the tool is listed
with its contract, the handler refuses malformed input with a 400 rather than
a 500, and a red kernel comes back as STOP through the same path the agent
will use.
"""
from __future__ import annotations

import asyncio
import json

from trinity_local.mcp_server import _verify, handle_call_tool, handle_list_tools


def _tool():
    return next(t for t in asyncio.run(handle_list_tools()) if t.name == "verify")


class TestListed:
    def test_verify_is_on_the_surface_with_its_contract(self):
        t = _tool()
        props = t.inputSchema["properties"]
        assert set(t.inputSchema["required"]) == {"criteria", "diff"}
        assert {"criteria", "diff", "context", "cwd", "members", "exclude_lab", "effort"} <= set(props)
        for word in ("STOP", "SKIP", "READ", "kind", "test", "judgment"):
            assert word in t.description, f"description must carry the rule and the contract ({word})"

    def test_description_says_why_the_test_is_required(self):
        assert "hq_104" in _tool().description


class TestRefusals:
    def test_missing_criteria_is_a_400(self):
        out = asyncio.run(_verify({"diff": "--- a\n+++ b\n"}))
        assert out and getattr(out[0], "code", None) == 400

    def test_empty_diff_is_a_400(self):
        out = asyncio.run(_verify({"criteria": [{"id": "t", "kind": "test", "statement": "x", "command": "true"}], "diff": "  "}))
        assert out and getattr(out[0], "code", None) == 400

    def test_bad_kind_is_a_400_not_a_500(self):
        out = asyncio.run(_verify({"criteria": [{"id": "t", "kind": "vibe", "statement": "x"}],
                                   "diff": "--- a\n+++ b\n", "run_panel": False}))
        assert out and getattr(out[0], "code", None) == 400


class TestKernelPath:
    def _payload(self, out):
        blob = out[0]
        text = blob.get("text") if isinstance(blob, dict) else getattr(blob, "text", None)
        return json.loads(text)

    def test_red_kernel_is_stop(self, tmp_path):
        out = asyncio.run(_verify({
            "criteria": [{"id": "t", "kind": "test", "statement": "fails", "command": "false"}],
            "diff": "--- a\n+++ b\n", "cwd": str(tmp_path), "run_panel": False}))
        d = self._payload(out)
        assert d["triage"] == "STOP" and d["kernel"]["green"] is False

    def test_green_without_panel_is_read(self, tmp_path):
        out = asyncio.run(_verify({
            "criteria": [{"id": "t", "kind": "test", "statement": "ok", "command": "true"}],
            "diff": "--- a\n+++ b\n", "cwd": str(tmp_path), "run_panel": False}))
        d = self._payload(out)
        assert d["triage"] == "READ" and d["kernel"]["green"] is True

    def test_dispatches_through_handle_call_tool(self, tmp_path):
        out = asyncio.run(handle_call_tool("verify", {
            "criteria": [{"id": "t", "kind": "test", "statement": "ok", "command": "true"}],
            "diff": "--- a\n+++ b\n", "cwd": str(tmp_path), "run_panel": False}))
        assert self._payload(out)["triage"] == "READ"
