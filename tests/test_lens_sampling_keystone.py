"""#263 keystone: the auto lens build/refresh must route its Claude stages
through MCP sampling instead of burning `claude -p` quota.

Two things make that work, and these tests pin both:

1. The kicks fire on the FIRST TOOL CALL (where a session is registered),
   not at server startup (before any session exists). A startup-time kick
   could never sample — that was the bug.
2. The build thread inherits the active-sampling ContextVar via
   copy_context(), so a Claude stage running inside it sees the session.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace


def _mock_session_with_sampling():
    """A fake ServerSession whose client advertised sampling capability."""
    return SimpleNamespace(
        client_params=SimpleNamespace(
            capabilities=SimpleNamespace(sampling=SimpleNamespace())
        )
    )


class TestKicksFireOnFirstToolCall:
    def test_startup_no_longer_kicks_the_llm_builds(self):
        """run_stdio_server must NOT reference the two LLM-making kicks —
        they can't sample before a session exists. Only the no-LLM cold-start
        scan is allowed at startup. Source-level regression guard against
        re-introducing the startup-kick bug."""
        import inspect

        from trinity_local import mcp_server

        src = inspect.getsource(mcp_server.run_stdio_server)
        assert "maybe_kick_cold_start" in src  # the no-LLM scan stays
        assert "maybe_kick_first_lens_build" not in src
        assert "maybe_kick_lens_refresh" not in src

    def test_fire_lens_kicks_is_idempotent(self, monkeypatch):
        """_maybe_fire_lens_kicks calls each kick exactly once per process,
        even across many tool calls."""
        from trinity_local import mcp_server

        first = []
        refresh = []
        monkeypatch.setattr(
            "trinity_local.cold_start.maybe_kick_first_lens_build",
            lambda: first.append(1),
        )
        monkeypatch.setattr(
            "trinity_local.cold_start.maybe_kick_lens_refresh",
            lambda: refresh.append(1),
        )
        # Reset the fire-once latch for a clean assertion.
        monkeypatch.setattr(mcp_server, "_lens_kicks_fired", False)

        for _ in range(5):
            mcp_server._maybe_fire_lens_kicks()

        assert first == [1]
        assert refresh == [1]

    def test_fire_lens_kicks_never_raises(self, monkeypatch):
        """A kick failure must not break the tool call that triggered it."""
        from trinity_local import mcp_server

        def _boom():
            raise RuntimeError("kick exploded")

        monkeypatch.setattr(
            "trinity_local.cold_start.maybe_kick_first_lens_build", _boom
        )
        monkeypatch.setattr(
            "trinity_local.cold_start.maybe_kick_lens_refresh", _boom
        )
        monkeypatch.setattr(mcp_server, "_lens_kicks_fired", False)

        # Must not raise.
        mcp_server._maybe_fire_lens_kicks()


class TestBuildThreadInheritsSamplingSession:
    """The copy_context() wrap is what lets the background build sample. With
    a sampling session active when the kick fires, the build thread must see
    `current_session_supports_sampling() is True` — proving the Claude stages
    inside it would route through sampling rather than `claude -p`."""

    def _run_kick_capturing_sampling(self, monkeypatch, kick_name):
        from trinity_local import cold_start, mcp_sampling
        from trinity_local.mcp_sampling import current_session_supports_sampling

        seen = {}
        done = threading.Event()

        def _fake_build(*a, **k):
            # Captured INSIDE the build thread — this is the assertion target.
            seen["sampling"] = current_session_supports_sampling()
            done.set()
            return (None, {})

        monkeypatch.setattr(
            "trinity_local.me_builder.build_me_via_lens_pipeline", _fake_build
        )
        # Open the gate + bypass cooldown/lock so the kick actually spawns.
        monkeypatch.setattr(
            cold_start, "_recently_kicked", lambda: False
        )
        monkeypatch.setattr(
            cold_start, "_try_claim_refresh_lock", lambda: True
        )
        monkeypatch.setattr(cold_start, "_release_refresh_lock", lambda: None)
        monkeypatch.setattr(cold_start, "_write_refresh_marker", lambda *a, **k: None)
        monkeypatch.setattr(cold_start, "_autoscan_disabled", lambda: False)
        # The kick early-returns unless the lens add-on is enabled — which it
        # isn't on a fresh CI checkout (no embedder marker). Open that gate too so
        # the build thread actually spawns (the assertion target).
        monkeypatch.setattr("trinity_local.lens_addon.lens_enabled", lambda: True)

        # Register a sampling-capable session as handle_call_tool would.
        # set_active_session() captures asyncio.get_running_loop(), which a
        # sync test has no access to — set the ContextVar directly with a
        # dummy loop (the sampling-capability probe reads only .session).
        token = mcp_sampling._active.set(
            mcp_sampling._ActiveSampling(
                session=_mock_session_with_sampling(), loop=None
            )
        )
        try:
            getattr(cold_start, kick_name)()
            assert done.wait(timeout=5.0), "build thread never ran"
        finally:
            mcp_sampling._active.reset(token)
        return seen.get("sampling")

    def test_first_build_thread_sees_sampling(self, monkeypatch):
        from trinity_local import cold_start

        monkeypatch.setattr(
            cold_start, "should_build_first_lens", lambda: (True, "test")
        )
        assert (
            self._run_kick_capturing_sampling(
                monkeypatch, "maybe_kick_first_lens_build"
            )
            is True
        )

    def test_refresh_build_thread_sees_sampling(self, monkeypatch):
        from trinity_local import cold_start

        monkeypatch.setattr(
            cold_start, "should_refresh_lens", lambda: (True, "test")
        )
        assert (
            self._run_kick_capturing_sampling(
                monkeypatch, "maybe_kick_lens_refresh"
            )
            is True
        )
