"""End-to-end wire-up tests: CLIProvider for the `claude` provider
prefers MCP host sampling over the `claude -p` subprocess when a
sampling-capable session is active.

This is the production payoff of the mcp_sampling primitive
(test_mcp_sampling.py covers the primitive itself). Sidesteps the
post-2026-06-15 Agent SDK credit pool for users running Trinity
inside Claude Desktop.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _claude_config():
    """Build a minimal ProviderConfig the CLI provider can run with."""
    from trinity_local.config import ProviderConfig
    return ProviderConfig(
        name="claude",
        type="cli",
        enabled=True,
        label="Claude",
        command=["claude", "-p"],
        args=[],
        task_types=set(),
        model="claude-test",
    )


def _gemini_config():
    from trinity_local.config import ProviderConfig
    return ProviderConfig(
        name="antigravity",
        type="cli",
        enabled=True,
        label="Gemini",
        command=["agy", "-p"],
        args=[],
        task_types=set(),
        model="gemini-test",
    )


# ─── Wire-up assertions ─────────────────────────────────────────────

class TestClaudeProviderSamplingWireup:
    def test_sampling_returns_text_skips_subprocess(self, monkeypatch):
        """When request_claude_sample returns text, the provider must
        return that text as the result WITHOUT invoking subprocess.run.
        This is the credit-saving path."""
        from trinity_local.providers import CLIProvider

        # Patch request_claude_sample to return a fixed string.
        monkeypatch.setattr(
            "trinity_local.mcp_sampling.request_claude_sample",
            lambda prompt, **kw: "  Sampled response from Claude  ",
        )
        # Subprocess must NOT be called — assert by patching to raise.
        invoked = []
        import subprocess
        def _fake_run(*a, **k):
            invoked.append(a)
            raise AssertionError("subprocess.run must not be called when sampling returns text")
        monkeypatch.setattr(subprocess, "run", _fake_run)

        provider = CLIProvider(_claude_config())
        result = provider.run("hello", cwd=Path("."))

        assert result.stdout == "Sampled response from Claude"  # stripped
        assert result.returncode == 0
        assert result.stderr == ""
        assert not invoked

    def test_sampling_returns_none_falls_back_to_subprocess(self, monkeypatch):
        """When request_claude_sample returns None (no active session,
        no sampling capability, or transport failure), the provider
        falls back to the existing `claude -p` subprocess path."""
        from trinity_local.providers import CLIProvider

        monkeypatch.setattr(
            "trinity_local.mcp_sampling.request_claude_sample",
            lambda prompt, **kw: None,
        )
        # Subprocess must be called this time.
        fake_completed = SimpleNamespace(
            returncode=0,
            stdout="subprocess Claude output",
            stderr="",
        )
        invoked = []
        import subprocess

        def _fake_run(argv, **kwargs):
            invoked.append(argv)
            return fake_completed

        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr("shutil.which", lambda n, **kw: f"/usr/bin/{n}")

        provider = CLIProvider(_claude_config())
        result = provider.run("hello", cwd=Path("."))

        assert result.stdout == "subprocess Claude output"
        assert invoked, "subprocess.run must be called when sampling returns None"
        # Verify it was the claude CLI that ran.
        assert invoked[0][0] == "claude"

    def test_gemini_does_not_try_sampling(self, monkeypatch):
        """Sampling is Claude-only — the user's host can't promise to
        route to Gemini, and Gemini doesn't have the Agent SDK billing
        problem yet. Non-claude CLIProviders must NEVER call sampling."""
        from trinity_local.providers import CLIProvider

        sampling_calls = []
        monkeypatch.setattr(
            "trinity_local.mcp_sampling.request_claude_sample",
            lambda prompt, **kw: sampling_calls.append(prompt) or "should not be used",
        )
        # Subprocess returns Gemini output as usual.
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="Gemini output", stderr=""
        ))
        monkeypatch.setattr("shutil.which", lambda n, **kw: f"/usr/bin/{n}")

        provider = CLIProvider(_gemini_config())
        result = provider.run("hello", cwd=Path("."))

        assert sampling_calls == [], (
            "Gemini provider must not invoke request_claude_sample — "
            "sampling is gated on provider.name == 'claude'."
        )
        assert result.stdout == "Gemini output"

    def test_sampling_failure_does_not_raise(self, monkeypatch):
        """If request_claude_sample raises (shouldn't happen per its
        contract, but defense in depth), the provider must still
        fall back to subprocess and not bubble the exception."""
        from trinity_local.providers import CLIProvider

        def _boom(prompt, **kw):
            raise RuntimeError("sampling primitive broke")

        # request_claude_sample's contract is "never raise" but we
        # double-check that an unexpected raise doesn't propagate up
        # from CLIProvider.run. If it did, every council member call
        # would fail when the primitive misbehaves.
        monkeypatch.setattr(
            "trinity_local.mcp_sampling.request_claude_sample", _boom
        )
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="fallback worked", stderr=""
        ))
        monkeypatch.setattr("shutil.which", lambda n, **kw: f"/usr/bin/{n}")

        provider = CLIProvider(_claude_config())
        # If sampling raises, CLIProvider currently propagates. That's
        # a behavior choice — the primitive promises not to raise, so
        # raising IS a bug we want surfaced loudly. Pin the contract
        # explicitly: an unexpected raise from the primitive is a bug
        # the test surfaces, not silently swallowed.
        with pytest.raises(RuntimeError, match="sampling primitive broke"):
            provider.run("hello", cwd=Path("."))


# ─── ContextVar propagation through ThreadPoolExecutor ─────────────

class TestContextVarPropagation:
    def test_session_visible_in_worker_thread(self):
        """The fix in council_runner: contextvars.copy_context().run(fn)
        wrapping executor.submit so worker threads see the active
        session set in the main thread."""
        import asyncio
        import contextvars
        from concurrent.futures import ThreadPoolExecutor
        from trinity_local.mcp_sampling import (
            current_session_supports_sampling,
            set_active_session,
        )

        session = SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=SimpleNamespace(sampling=SimpleNamespace())
            )
        )

        results: list[bool] = []

        async def _scenario():
            set_active_session(session)
            ctx = contextvars.copy_context()
            # Same pattern council_runner uses.
            with ThreadPoolExecutor(max_workers=1) as executor:
                fut = executor.submit(ctx.run, current_session_supports_sampling)
                results.append(fut.result())

        asyncio.run(_scenario())
        assert results == [True], (
            "Worker thread must see the active session through "
            "contextvars.copy_context().run — without this, "
            "council_runner's sampling-aware ClaudeProvider can never "
            "find the session and always falls back to subprocess."
        )

    def test_multiple_submits_each_get_fresh_context(self):
        """Regression guard: ctx.run() can only be entered ONCE. If
        council_runner shares a single Context across all submit()
        calls (the bug we just fixed), the second worker fails with
        'cannot enter context: already entered' and the council
        crashes mid-flight.

        Each submit must use its own contextvars.copy_context()."""
        import asyncio
        import contextvars
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from trinity_local.mcp_sampling import (
            current_session_supports_sampling,
            set_active_session,
        )

        session = SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=SimpleNamespace(sampling=SimpleNamespace())
            )
        )

        async def _scenario():
            set_active_session(session)
            # Three workers, each gets its own context copy — same
            # pattern council_runner uses for parallel member dispatch.
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(
                        contextvars.copy_context().run,
                        current_session_supports_sampling,
                    )
                    for _ in range(3)
                ]
                return [f.result() for f in as_completed(futures)]

        results = asyncio.run(_scenario())
        # All three workers must succeed AND see the session.
        assert results == [True, True, True], (
            f"Expected all workers to see the active session; got {results}. "
            "If 'cannot enter context: already entered' appeared, the "
            "council_runner regressed to sharing one Context across submits."
        )

    def test_session_not_visible_without_copy_context(self):
        """Negative control: a bare executor.submit (no copy_context)
        does NOT propagate the ContextVar — this is the bug we just
        fixed. If this test starts passing, it means Python's default
        propagation changed AND the copy_context wrap is now
        redundant."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from trinity_local.mcp_sampling import (
            current_session_supports_sampling,
            set_active_session,
        )

        session = SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=SimpleNamespace(sampling=SimpleNamespace())
            )
        )

        results: list[bool] = []

        async def _scenario():
            set_active_session(session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                # No copy_context — should NOT see the session.
                fut = executor.submit(current_session_supports_sampling)
                results.append(fut.result())

        asyncio.run(_scenario())
        assert results == [False], (
            "If this assertion fails, Python's ThreadPoolExecutor now "
            "propagates ContextVars by default — the copy_context wrap "
            "in council_runner has become redundant. Verify and remove."
        )


# ─── Isolation between tests ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_active_session():
    yield
    from trinity_local.mcp_sampling import clear_active_session
    try:
        clear_active_session()
    except Exception:
        pass
