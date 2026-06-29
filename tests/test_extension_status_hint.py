"""Tests for the MCP extension-status hint that piggybacks on _text().

The cross-bootstrap loop (5fa980c) made the launchpad + install.sh
proactively suggest the extension when missing. This adds the
agent-facing side: every MCP tool response carries an
`extension_status` hint when the user hasn't wired the extension,
so the agent can mention it inline ("you might also want to install
the Chrome extension…").

Cached for the process lifetime — the wiring state doesn't change
mid-process and we don't want to filesystem-probe on every response.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with no cached extension-status decision so
    monkeypatches actually take effect."""
    from trinity_local import mcp_server
    mcp_server._EXTENSION_HINT_CACHED = mcp_server._NOT_COMPUTED
    yield
    mcp_server._EXTENSION_HINT_CACHED = mcp_server._NOT_COMPUTED


class TestExtensionStatusHint:
    def test_returns_hint_when_extension_not_configured(self, monkeypatch):
        """User installed via curl|bash and never ran install-extension
        → MCP responses must carry the hint so agents mention it."""
        from trinity_local import mcp_server

        monkeypatch.setattr(
            "trinity_local.launchpad_data.dispatch_readiness",
            lambda: {"extension_configured": False, "host_on_path": True, "ready": False},
        )
        hint = mcp_server._extension_status_hint()
        assert hint is not None
        assert hint["configured"] is False
        assert "Chrome extension" in hint["message"]
        # Install doc URL must be present so the agent can hand it off.
        assert "INSTALL-extension" in hint["install_doc"]
        # Mentions browser capture (the value pitch).
        assert "capture" in hint["message"].lower()

    def test_returns_none_when_extension_configured(self, monkeypatch):
        """Once the user has wired the extension, the hint disappears.
        No 'your extension is fine, by the way' noise on every call."""
        from trinity_local import mcp_server

        monkeypatch.setattr(
            "trinity_local.launchpad_data.dispatch_readiness",
            lambda: {"extension_configured": True, "host_on_path": True, "ready": True},
        )
        assert mcp_server._extension_status_hint() is None

    def test_cached_across_calls(self, monkeypatch):
        """Process-lifetime cache: dispatch_readiness only called once.
        Without this we'd hit the filesystem (shutil.which on
        capture-host, JSON read of NM manifest) on every MCP response."""
        from trinity_local import mcp_server

        call_count = [0]

        def _spy():
            call_count[0] += 1
            return {"extension_configured": False, "host_on_path": True, "ready": False}

        monkeypatch.setattr("trinity_local.launchpad_data.dispatch_readiness", _spy)
        for _ in range(10):
            mcp_server._extension_status_hint()
        assert call_count[0] == 1, (
            "dispatch_readiness must be called once per process — "
            "MCP responses fire on every agent call, filesystem probes "
            "every time would add latency under load."
        )

    def test_exception_in_readiness_does_not_break_responses(self, monkeypatch):
        """If dispatch_readiness raises, the hint must just be absent —
        MUST NOT bubble the exception up through _text() and break
        every MCP response."""
        from trinity_local import mcp_server

        def _boom():
            raise RuntimeError("filesystem fault")

        monkeypatch.setattr("trinity_local.launchpad_data.dispatch_readiness", _boom)
        # Should return None, NOT raise.
        assert mcp_server._extension_status_hint() is None


class TestTextWrapperInjection:
    """The _text() wrapper is what every MCP tool response runs through.
    Verify the hint actually lands in the response payload."""

    def test_hint_injected_into_dict_response(self, monkeypatch):
        from trinity_local import mcp_server
        import json

        monkeypatch.setattr(
            "trinity_local.launchpad_data.dispatch_readiness",
            lambda: {"extension_configured": False, "host_on_path": True, "ready": False},
        )
        # Also stub cold_start so we don't pull in unrelated hint.
        monkeypatch.setattr(
            "trinity_local.cold_start.cold_start_hint", lambda: None
        )

        wrapped = mcp_server._text({"ok": True})
        parsed = json.loads(wrapped["text"])
        assert "extension_status" in parsed
        assert parsed["extension_status"]["configured"] is False

    def test_hint_absent_when_extension_configured(self, monkeypatch):
        from trinity_local import mcp_server
        import json

        monkeypatch.setattr(
            "trinity_local.launchpad_data.dispatch_readiness",
            lambda: {"extension_configured": True, "host_on_path": True, "ready": True},
        )
        monkeypatch.setattr(
            "trinity_local.cold_start.cold_start_hint", lambda: None
        )

        wrapped = mcp_server._text({"ok": True})
        parsed = json.loads(wrapped["text"])
        assert "extension_status" not in parsed, (
            "Configured extension should produce no hint — silence is the "
            "all-good signal."
        )

    def test_hint_does_not_overwrite_caller_set_field(self, monkeypatch):
        """If a tool explicitly puts extension_status in its response
        (unlikely but possible), respect it instead of overwriting."""
        from trinity_local import mcp_server
        import json

        monkeypatch.setattr(
            "trinity_local.launchpad_data.dispatch_readiness",
            lambda: {"extension_configured": False, "host_on_path": True, "ready": False},
        )
        monkeypatch.setattr(
            "trinity_local.cold_start.cold_start_hint", lambda: None
        )

        wrapped = mcp_server._text({"ok": True, "extension_status": "caller-set"})
        parsed = json.loads(wrapped["text"])
        assert parsed["extension_status"] == "caller-set"

    def test_string_payload_passes_through_unchanged(self):
        """Non-dict payloads (raw strings from some tool handlers) must
        not get any hint injection — strings are opaque to the agent
        anyway."""
        from trinity_local import mcp_server

        wrapped = mcp_server._text("just a string")
        assert wrapped["type"] == "text"
        assert wrapped["text"] == "just a string"
