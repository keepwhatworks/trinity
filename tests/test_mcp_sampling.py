"""Tests for the MCP host-sampling primitive (mcp_sampling.py).

This is the foundation for the 2026-06-15 billing-change adaptation:
when Trinity-MCP runs inside a chat client that supports sampling
(Claude Desktop), we ask the host for Claude completions instead of
subprocessing `claude -p` — which avoids the Agent SDK credit pool
entirely. These tests pin the degradation contract (returns None,
never raises) since the live council path will lean on that.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ─── Helpers ────────────────────────────────────────────────────────

def _mock_session_with_sampling(sample_text: str = "Hello from Claude!"):
    """Build a fake ServerSession with sampling capability + a
    ``create_message`` coroutine returning the given text."""
    result = SimpleNamespace(
        content=SimpleNamespace(type="text", text=sample_text)
    )
    session = SimpleNamespace(
        client_params=SimpleNamespace(
            capabilities=SimpleNamespace(sampling=SimpleNamespace())
        ),
        create_message=AsyncMock(return_value=result),
    )
    return session


def _mock_session_without_sampling():
    """Session whose client did NOT advertise sampling capability."""
    return SimpleNamespace(
        client_params=SimpleNamespace(
            capabilities=SimpleNamespace(sampling=None)
        ),
        create_message=AsyncMock(),
    )


# ─── Capability detection ──────────────────────────────────────────

class TestCapabilityDetection:
    def test_no_active_session_returns_false(self):
        from trinity_local.mcp_sampling import (
            clear_active_session,
            current_session_supports_sampling,
        )
        clear_active_session()
        assert current_session_supports_sampling() is False

    def test_session_without_sampling_returns_false(self):
        from trinity_local.mcp_sampling import (
            current_session_supports_sampling,
            set_active_session,
            clear_active_session,
        )

        async def _run():
            try:
                set_active_session(_mock_session_without_sampling())
                return current_session_supports_sampling()
            finally:
                clear_active_session()

        assert asyncio.run(_run()) is False

    def test_session_with_sampling_returns_true(self):
        from trinity_local.mcp_sampling import (
            current_session_supports_sampling,
            set_active_session,
            clear_active_session,
        )

        async def _run():
            try:
                set_active_session(_mock_session_with_sampling())
                return current_session_supports_sampling()
            finally:
                clear_active_session()

        assert asyncio.run(_run()) is True

    def test_malformed_session_does_not_raise(self):
        """Defensive: an SDK update may rename/restructure the
        capability object. Detection MUST not raise — the live council
        path falls back to subprocess on False."""
        from trinity_local.mcp_sampling import (
            current_session_supports_sampling,
            set_active_session,
            clear_active_session,
        )

        async def _run():
            try:
                # Session without the attributes we expect.
                set_active_session(SimpleNamespace())
                return current_session_supports_sampling()
            finally:
                clear_active_session()

        assert asyncio.run(_run()) is False


# ─── Sampling call ─────────────────────────────────────────────────

class TestRequestClaudeSample:
    def test_no_session_returns_none(self):
        """No active session → return None, do not raise.
        Caller's subprocess fallback covers this case."""
        from trinity_local.mcp_sampling import (
            request_claude_sample,
            clear_active_session,
        )
        clear_active_session()
        assert request_claude_sample("hi") is None

    def test_session_without_sampling_returns_none(self):
        """Session active but client refused sampling capability →
        return None, do not even attempt create_message."""
        from trinity_local.mcp_sampling import (
            request_claude_sample,
            set_active_session,
        )
        session = _mock_session_without_sampling()

        # The worker thread pattern: asyncio.to_thread keeps the loop
        # pumping so run_coroutine_threadsafe can dispatch. `thread.join`
        # would block the loop and deadlock.
        async def _scenario():
            set_active_session(session)
            return await asyncio.to_thread(request_claude_sample, "hi")

        try:
            result = asyncio.run(_scenario())
            assert result is None
            # create_message must NOT have been called when sampling
            # capability is absent.
            session.create_message.assert_not_called()
        finally:
            asyncio.run(_clear())

    def test_session_with_sampling_returns_text(self):
        """Happy path: session has sampling, create_message returns
        a text result → request_claude_sample returns the text."""
        from trinity_local.mcp_sampling import (
            request_claude_sample,
            set_active_session,
        )
        session = _mock_session_with_sampling(sample_text="It's me, Claude.")

        async def _scenario():
            set_active_session(session)
            return await asyncio.to_thread(
                request_claude_sample,
                "Hello?",
                system_prompt="Be concise.",
            )

        try:
            assert asyncio.run(_scenario()) == "It's me, Claude."
        finally:
            asyncio.run(_clear())

    def test_create_message_failure_returns_none(self):
        """Host returns an error / transport fails → return None so
        the subprocess fallback runs. Must NOT raise."""
        from trinity_local.mcp_sampling import (
            request_claude_sample,
            set_active_session,
        )
        session = _mock_session_with_sampling()
        session.create_message = AsyncMock(side_effect=RuntimeError("transport bad"))

        async def _scenario():
            set_active_session(session)
            return await asyncio.to_thread(request_claude_sample, "hi")

        try:
            assert asyncio.run(_scenario()) is None
        finally:
            asyncio.run(_clear())

    def test_request_passes_system_prompt_and_model_hint(self):
        """create_message must be invoked with:
        - the prompt as a user message
        - the given system_prompt
        - a model preference hinting Claude
        """
        from trinity_local.mcp_sampling import (
            request_claude_sample,
            set_active_session,
        )
        session = _mock_session_with_sampling(sample_text="ok")

        async def _scenario():
            set_active_session(session)
            await asyncio.to_thread(
                request_claude_sample,
                "the prompt",
                system_prompt="be Claude",
            )

        try:
            asyncio.run(_scenario())
        finally:
            asyncio.run(_clear())

        assert session.create_message.call_args is not None, (
            "create_message was not called — the sampling path didn't fire"
        )
        kwargs = session.create_message.call_args.kwargs
        assert kwargs["system_prompt"] == "be Claude"
        assert len(kwargs["messages"]) == 1
        # Content can be SamplingMessage / TextContent — verify the text
        # made it through regardless of which wrapper the SDK uses.
        msg = kwargs["messages"][0]
        text = getattr(msg.content, "text", None) or msg.content
        assert "the prompt" in str(text)
        # Model preferences must hint Claude so the host doesn't pick a
        # different model when it has options.
        prefs = kwargs.get("model_preferences")
        assert prefs is not None
        hints = getattr(prefs, "hints", None)
        assert hints is not None
        assert any(getattr(h, "name", "") == "claude" for h in hints)


# ─── Async helper for clearing session state between tests ─────────

async def _clear() -> None:
    from trinity_local.mcp_sampling import clear_active_session
    clear_active_session()


# ─── Module-level isolation ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_active_session():
    """Every test starts with no active session. Without this, a test
    that forgets to clear leaks the session into the next one."""
    yield
    try:
        asyncio.run(_clear())
    except RuntimeError:
        # No running loop — fine, contextvar is per-context.
        pass
