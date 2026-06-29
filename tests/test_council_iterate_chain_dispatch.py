"""Live-council chain dispatch (Refine / Continue / Auto-chain) must actually
reach a running council. Two bugs, found 2026-06-12 driving the REAL extension
against the served live council page (scripts/extension_harness.py):

Bug A — KEY MISMATCH. The live council page sent the chain token under
`'status-token'` (the hyphen CLI-FLAG spelling), but capture_host's
ACTION_ALLOWLIST reads `payload.get('status_token')` (underscore). So
`--status-token` was silently dropped: council-iterate ran under a fresh
bundle_id, not the chain token the page polls → the page 404'd that token
forever and surfaced "This council never started / no status file was ever
written." The launchpad's own dispatch has ALWAYS used underscore — only the
council page diverged.

Bug B — BLOCKING DISPATCH. `council-iterate` ran via the host's blocking
`subprocess.run` path (a full council round, 30-90s), but the page dispatcher
gives up after ACTION_TIMEOUT_MS (8s) → "Couldn't reach the Trinity extension."
`launch-council` never hit this because it's a _DETACHED_ACTION (instant ack +
poll). council-iterate must be too.

This is the cross-boundary guard ([[test_the_boundary_and_the_action]]): we test
the host side (it accepts BOTH spellings + detaches) AND the page side (it emits
the key the host reads), with a real message — so neither half can regress alone.
"""
from __future__ import annotations

import subprocess

import pytest

from trinity_local import capture_host


class _FakePopen:
    """Captures argv instead of spawning (detached path uses Popen)."""

    captured: dict = {}

    def __init__(self, argv, **kwargs):
        type(self).captured = {"argv": argv, "kwargs": kwargs}
        self.pid = 5151


class _ForbiddenRun:
    """subprocess.run must NOT be called for council-iterate — that's the
    blocking path the 8s dispatcher times out on."""

    def __call__(self, *a, **k):  # pragma: no cover - only fires on regression
        raise AssertionError(
            "council-iterate reached the BLOCKING subprocess.run path — it must be "
            "detached (subprocess.Popen) so the host acks instantly and the page polls"
        )


@pytest.fixture
def host_env(monkeypatch):
    _FakePopen.captured = {}
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "run", _ForbiddenRun())
    monkeypatch.setattr(capture_host, "build_runtime_env", lambda: {}, raising=False)
    return _FakePopen


def test_council_iterate_is_detached_not_blocking(host_env):
    # Bug B: must take the Popen (detached) branch, never subprocess.run.
    result = capture_host._run_action(
        {"kind": "council-iterate", "council": "council_abc", "status_token": "chain_xyz"}
    )
    assert result.get("ok") is True, f"council-iterate dispatch failed: {result}"
    assert result.get("detached") is True, "council-iterate must report detached:true"
    assert host_env.captured, "council-iterate never reached subprocess.Popen"


def test_council_iterate_underscore_token_threads_through(host_env):
    result = capture_host._run_action(
        {"kind": "council-iterate", "council": "council_abc", "status_token": "chain_underscore"}
    )
    argv = host_env.captured["argv"]
    assert "--status-token" in argv, f"--status-token missing from argv: {argv}"
    assert argv[argv.index("--status-token") + 1] == "chain_underscore"
    # And it's echoed back so the page can confirm the routing.
    assert result.get("status_token") == "chain_underscore"


def test_council_iterate_hyphen_token_is_tolerated(host_env):
    # Bug A defense-in-depth: a payload that still uses the hyphen spelling must
    # NOT silently drop the token (the in-process handlers already tolerate both).
    result = capture_host._run_action(
        {"kind": "council-iterate", "council": "council_abc", "status-token": "chain_hyphen"}
    )
    argv = host_env.captured["argv"]
    assert "--status-token" in argv, (
        "the hyphen-spelled status-token was dropped — council-iterate would run under "
        f"a fresh bundle_id, not the chain token the page polls. argv: {argv}"
    )
    assert argv[argv.index("--status-token") + 1] == "chain_hyphen"
    assert result.get("status_token") == "chain_hyphen"


def test_council_iterate_in_detached_set():
    assert "council-iterate" in capture_host._DETACHED_ACTIONS


def test_live_council_page_sends_underscore_status_token():
    """Page side of the contract: the rendered live council page must emit the
    `status_token` key the host reads — NOT the hyphen spelling that drops it."""
    from trinity_local.council_review import render_live_council_page

    html = render_live_council_page()
    # The council-iterate + stop-council extensionAction payloads carry the token.
    assert "status_token: newToken" in html, (
        "the council-iterate dispatch no longer sends `status_token:` (underscore) — "
        "if it reverted to 'status-token' the host drops the token (Bug A)"
    )
    assert "'status-token'" not in html and '"status-token"' not in html, (
        "the live council page emits a hyphen-spelled status-token key in a dispatch "
        "payload — capture_host reads payload['status_token'], so the token is dropped"
    )
