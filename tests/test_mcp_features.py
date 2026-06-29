"""#264 — MCP protocol features beyond tools/resources/sampling: logging,
progress, roots, elicitation, and prompts.

The host-side helpers (mcp_features) run from worker threads and schedule onto
the captured event loop, so the session-active tests spin a real loop in a
background thread and register it the way handle_call_tool would.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


# ─── Fake session + live loop harness ───────────────────────────────


class _FakeSession:
    def __init__(self):
        self.logs: list = []
        self.progress: list = []
        self.roots_result = None
        self.elicit_result = None

    async def send_log_message(self, *, level, data, logger=None, related_request_id=None):
        self.logs.append((level, data, logger))

    async def send_progress_notification(
        self, *, progress_token, progress, total=None, message=None, related_request_id=None
    ):
        self.progress.append((progress_token, progress, total, message))

    async def list_roots(self):
        return self.roots_result

    async def elicit(self, *, message, requestedSchema, related_request_id=None):
        return self.elicit_result


@pytest.fixture
def live_session(monkeypatch):
    """Register a fake session + a real background loop, as handle_call_tool
    does, then tear both down. Yields the fake session."""
    from trinity_local import mcp_features
    from trinity_local import mcp_sampling

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    session = _FakeSession()
    sess_token = mcp_sampling._active.set(
        mcp_sampling._ActiveSampling(session=session, loop=loop)
    )
    mcp_features.set_request_context(request_id="req-1", progress_token="tok-1")
    # Reset the connection-wide log threshold to the default for each test.
    monkeypatch.setattr(mcp_features, "_min_log_level", "info")
    try:
        yield session
    finally:
        mcp_features.clear_request_context()
        mcp_sampling._active.reset(sess_token)
        loop.call_soon_threadsafe(loop.stop)


# ─── Prompts ────────────────────────────────────────────────────────


class TestPrompts:
    def test_list_prompts_exposes_council_ask_lens(self):
        from trinity_local import mcp_server

        prompts = asyncio.run(mcp_server.handle_list_prompts())
        names = {p.name for p in prompts}
        assert {"council", "ask", "lens"} <= names

    def test_get_prompt_renders_arg_into_message(self):
        from trinity_local import mcp_server

        res = asyncio.run(
            mcp_server.handle_get_prompt("council", {"task": "ship friday?"})
        )
        text = res.messages[0].content.text
        assert "ship friday?" in text
        assert "run_council" in text

    def test_get_prompt_optional_arg_absent_is_fine(self):
        from trinity_local import mcp_server

        res = asyncio.run(mcp_server.handle_get_prompt("lens", {}))
        assert "get_persona" in res.messages[0].content.text

    def test_get_prompt_unknown_raises(self):
        from trinity_local import mcp_server

        with pytest.raises(ValueError):
            asyncio.run(mcp_server.handle_get_prompt("nope", {}))

    def test_capabilities_advertise_prompts_and_logging(self):
        from mcp.server.lowlevel.server import NotificationOptions

        from trinity_local import mcp_server

        caps = mcp_server.server.get_capabilities(NotificationOptions(), {})
        assert caps.prompts is not None
        assert caps.logging is not None


# ─── Logging ────────────────────────────────────────────────────────


class TestLogging:
    def test_log_degrades_to_false_without_session(self):
        from trinity_local import mcp_features

        assert mcp_features.mcp_log("info", "x") is False

    def test_log_dispatches_when_session_active(self, live_session):
        from trinity_local import mcp_features

        assert mcp_features.mcp_log("warning", "hello") is True
        # give the background loop a beat to run the coroutine
        for _ in range(50):
            if live_session.logs:
                break
            import time as _t

            _t.sleep(0.01)
        assert live_session.logs and live_session.logs[0][0] == "warning"

    def test_level_filter_drops_below_threshold(self, live_session):
        from trinity_local import mcp_features

        mcp_features.set_min_log_level("error")
        assert mcp_features.mcp_log("info", "dropped") is False
        assert mcp_features.mcp_log("error", "kept") is True

    def test_set_logging_level_handler_updates_threshold(self):
        import asyncio as _a

        from trinity_local import mcp_features, mcp_server

        _a.run(mcp_server.handle_set_logging_level("error"))
        assert mcp_features._level_enabled("info") is False
        assert mcp_features._level_enabled("error") is True
        mcp_features.set_min_log_level("info")  # restore


# ─── Progress ───────────────────────────────────────────────────────


class TestProgress:
    def test_progress_noop_without_progress_token(self, live_session):
        from trinity_local import mcp_features

        # Re-register the request context WITHOUT a progress token.
        mcp_features.set_request_context(request_id="r", progress_token=None)
        assert mcp_features.mcp_progress(0.5) is False
        assert live_session.progress == []

    def test_progress_dispatches_with_token(self, live_session):
        from trinity_local import mcp_features

        assert mcp_features.mcp_progress(0.5, 1.0, message="half") is True
        for _ in range(50):
            if live_session.progress:
                break
            import time as _t

            _t.sleep(0.01)
        assert live_session.progress
        token, prog, total, msg = live_session.progress[0]
        assert token == "tok-1" and prog == 0.5 and msg == "half"


# ─── Roots ──────────────────────────────────────────────────────────


class TestRoots:
    def test_roots_degrade_to_empty_without_session(self):
        from trinity_local import mcp_features

        assert mcp_features.discover_roots() == []

    def test_roots_parses_file_uris(self, live_session):
        from trinity_local import mcp_features

        live_session.roots_result = SimpleNamespace(
            roots=[
                SimpleNamespace(uri="file:///Users/x/proj"),
                SimpleNamespace(uri="file:///tmp/y"),
            ]
        )
        roots = mcp_features.discover_roots()
        assert roots == ["/Users/x/proj", "/tmp/y"]


# ─── Elicitation ────────────────────────────────────────────────────


class TestElicitation:
    def test_elicit_degrades_to_none_without_session(self):
        from trinity_local import mcp_features

        assert mcp_features.elicit("confirm?", {"type": "object"}) is None

    def test_elicit_accept_returns_content(self, live_session):
        from trinity_local import mcp_features

        live_session.elicit_result = SimpleNamespace(
            action="accept", content={"confirmed": True}
        )
        assert mcp_features.elicit("ok?", {"type": "object"}) == {"confirmed": True}

    def test_elicit_decline_returns_none(self, live_session):
        from trinity_local import mcp_features

        live_session.elicit_result = SimpleNamespace(action="decline", content=None)
        assert mcp_features.elicit("ok?", {"type": "object"}) is None
