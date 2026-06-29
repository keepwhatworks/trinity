"""The GUI-launched-IDE provider-resolution invariant (found 2026-06-06).

A council provider that is INSTALLED but reachable only via an injected runtime
bin dir (e.g. a Homebrew CLI in /opt/homebrew/bin) must be detected as available
by EVERY surface that gates/filters/pitches the council — because the runner
EXECUTES with that enriched PATH (run_with_runtime_env). The bug: the dispatch
gate (providers._ensure_binary), the lineup filter (config.installed_council_
providers), and the tier card (launchpad_data._council_tier_status) used a BARE
`shutil.which`, so under a GUI-launched IDE whose MCP server inherits a stripped
PATH (no /opt/homebrew/bin) they'd REJECT a provider the execution could run —
the council couldn't dispatch to a CLI the user actually has, and the launchpad
falsely pitched "install it."

Fix: all three resolve via `runtime_env.which_on_runtime_path` (the SAME enriched
PATH the execution uses). These tests pin that by placing a fake binary in an
injected dir that is NOT on the bare PATH and asserting each surface still sees
it. Mutation: revert any site to a bare `shutil.which` → that site's test fails.
"""
from __future__ import annotations

import pytest


def _codex_config():
    from trinity_local.config import ProviderConfig

    return ProviderConfig(
        name="codex",
        type="cli",
        enabled=True,
        label="Codex",
        command=["codex", "exec"],
        args=[],
        task_types=set(),
        model="codex-test",
    )


@pytest.fixture
def homebrew_only_provider(tmp_path, monkeypatch):
    """A fake `codex` reachable ONLY via an injected runtime dir, with the bare
    process PATH stripped of it — the GUI-launched-IDE shape."""
    inject = tmp_path / "homebrew_bin"
    inject.mkdir()
    fake = inject / "codex"
    fake.write_text("#!/bin/sh\necho 'codex 1.0'\n")
    fake.chmod(0o755)
    # Stripped bare PATH (lacks the injected dir) ...
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    # ... but the runtime env injects it (stands in for /opt/homebrew/bin).
    monkeypatch.setattr(
        "trinity_local.runtime_env.runtime_bin_paths", lambda *a, **k: [str(inject)]
    )
    return str(fake)


def test_which_on_runtime_path_finds_injected_binary(homebrew_only_provider):
    import shutil

    from trinity_local.runtime_env import which_on_runtime_path

    # Bare shutil.which can't see it (the stripped-PATH gate would reject).
    assert shutil.which("codex") is None
    # The runtime resolver does — matching what run_with_runtime_env executes with.
    assert which_on_runtime_path("codex") == homebrew_only_provider


def test_dispatch_gate_accepts_injected_provider(homebrew_only_provider):
    """providers._ensure_binary (the council runner's dispatch gate) must NOT
    raise for a provider reachable via the injected PATH — the injection that
    lets _run_command execute it would otherwise be defeated by the gate."""
    from trinity_local.providers import CLIProvider

    prov = CLIProvider(_codex_config())
    # Must not raise ProviderError("Provider binary not found").
    prov._ensure_binary()


def test_lineup_filter_includes_injected_provider(homebrew_only_provider, monkeypatch):
    """config.installed_council_providers (the council lineup) must include a
    provider reachable via the injected PATH, else the council is launched
    without a member the user actually has."""
    from trinity_local.config import installed_council_providers

    members = installed_council_providers()
    assert "codex" in members, (
        "lineup must include a Homebrew-installed provider off the bare PATH"
    )


def test_capture_host_readiness_finds_injected_host(tmp_path, monkeypatch):
    """The dispatch-readiness banner (launchpad_data.dispatch_readiness) resolves
    `trinity-local-capture-host` on the enriched runtime PATH — the host is a
    console_script installed in the venv bin / ~/.local/bin, both injected dirs.
    A bare shutil.which would falsely report "capture-host not on PATH" under a
    GUI-launched stripped PATH (the host actually runs via the Native-Messaging
    manifest's ABSOLUTE path regardless). Same class as the council gate fix.
    Mutation: revert to bare shutil.which → ready flips False with a bogus
    "reinstall" recommendation."""
    inject = tmp_path / "bin"
    inject.mkdir()
    host = inject / "trinity-local-capture-host"
    host.write_text("#!/bin/sh\n")
    host.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # stripped (no injected dir)
    monkeypatch.setattr(
        "trinity_local.runtime_env.runtime_bin_paths", lambda *a, **k: [str(inject)]
    )
    import shutil

    assert shutil.which("trinity-local-capture-host") is None  # bare misses it

    from trinity_local import launchpad_data

    # Extension configured so readiness hinges purely on host detection.
    monkeypatch.setattr(
        launchpad_data,
        "_browser_extension",
        lambda: {"configured": True, "id": "x", "manifest_present": True},
    )
    readiness = launchpad_data.dispatch_readiness()
    assert readiness["ready"] is True, (
        "capture-host installed in an injected dir must read as ready, not "
        "falsely 'not on PATH'"
    )
    assert readiness.get("recommended_action") is None


def test_tier_card_counts_injected_provider(homebrew_only_provider):
    """launchpad_data._council_tier_status must not falsely pitch 'install Codex'
    to a user whose Codex is reachable via the injected PATH."""
    from trinity_local.launchpad_data import _council_tier_status

    status = _council_tier_status()
    assert "codex" in status["installed"], (
        "tier card must count a Homebrew-installed provider off the bare PATH"
    )
    assert all(m["provider"] != "codex" for m in status["missing"]), (
        "tier card must not pitch installing a provider the user already has"
    )
