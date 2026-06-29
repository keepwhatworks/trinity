"""Auto-open-council gate: after every council writes, if
`telemetry.auto_open_council` is True, shell out `open <review_path>` so
the harness doesn't need to know. The CLI toggle commands
(`auto-open-enable`/`auto-open-disable`) were retired 2026-05-17; the
underlying setting still round-trips through the settings file (see
test_auto_open_setting_persists below) and the hook still fires when
the flag is True. The hook lives in council_runner._maybe_auto_open.
These tests cover the gating logic WITHOUT actually spawning `open` (we
monkeypatch subprocess.Popen).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_default_off_no_open_call(isolated_home, monkeypatch):
    """Without `auto-open-enable`, the hook is a no-op — no subprocess
    spawned, no errors raised."""
    from trinity_local import council_runner

    popen_mock = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    council_runner._maybe_auto_open(Path("/tmp/anywhere.html"))
    popen_mock.assert_not_called()


def test_enabled_shells_out_on_darwin(isolated_home, monkeypatch):
    """With the setting on AND running on macOS, the launcher HTML is
    written and `open -g <launcher>` fires. The launcher uses
    window.open(url, "trinity-council") so subsequent councils reuse the
    same named window — no tab spam."""
    from trinity_local import council_runner
    from trinity_local.state_paths import portal_pages_dir
    from trinity_local.telemetry import load_telemetry_settings, save_telemetry_settings

    settings = load_telemetry_settings()
    settings.auto_open_council = True
    save_telemetry_settings(settings)

    monkeypatch.setattr(sys, "platform", "darwin")
    popen_mock = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    council_runner._maybe_auto_open(Path("/tmp/x.html"))
    popen_mock.assert_called_once()
    args = popen_mock.call_args[0][0]
    # Must use -g (background, no focus steal).
    assert args[0] == "open"
    assert args[1] == "-g"
    # And open the launcher (stable path), not the council page directly.
    launcher = portal_pages_dir() / "_open_council.html"
    assert args[2] == str(launcher)
    # The launcher must reference the council via window.open into a
    # named window (so tabs get reused across councils).
    body = launcher.read_text(encoding="utf-8")
    assert "trinity-council" in body
    assert "window.open" in body
    assert "/tmp/x.html" in body


def test_enabled_but_linux_silently_skips(isolated_home, monkeypatch):
    """`open` is a macOS binary. Linux/Windows must not crash trying to
    spawn it — silently skip when sys.platform != 'darwin'."""
    from trinity_local import council_runner
    from trinity_local.telemetry import load_telemetry_settings, save_telemetry_settings

    settings = load_telemetry_settings()
    settings.auto_open_council = True
    save_telemetry_settings(settings)

    monkeypatch.setattr(sys, "platform", "linux")
    popen_mock = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    council_runner._maybe_auto_open(Path("/tmp/x.html"))
    popen_mock.assert_not_called()


def test_subprocess_exception_swallowed(isolated_home, monkeypatch):
    """If subprocess.Popen itself raises (e.g., PATH issue, sandbox
    block), the council write has already succeeded — the failure to
    open a browser must not propagate up and break the council return."""
    from trinity_local import council_runner
    from trinity_local.telemetry import load_telemetry_settings, save_telemetry_settings

    settings = load_telemetry_settings()
    settings.auto_open_council = True
    save_telemetry_settings(settings)

    monkeypatch.setattr(sys, "platform", "darwin")
    def boom(*args, **kwargs):
        raise OSError("PATH problem")
    monkeypatch.setattr("subprocess.Popen", boom)

    # Should NOT raise.
    council_runner._maybe_auto_open(Path("/tmp/x.html"))


def test_launcher_is_overwritten_per_council(isolated_home, monkeypatch):
    """Two consecutive councils MUST write the same launcher path —
    the named-window mechanism (window.open(url, "trinity-council"))
    only reuses the tab when there's one stable launcher URL across
    calls. Test that the launcher path doesn't drift per-council."""
    from trinity_local import council_runner
    from trinity_local.state_paths import portal_pages_dir
    from trinity_local.telemetry import load_telemetry_settings, save_telemetry_settings

    settings = load_telemetry_settings()
    settings.auto_open_council = True
    save_telemetry_settings(settings)

    monkeypatch.setattr(sys, "platform", "darwin")
    popen_mock = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    council_runner._maybe_auto_open(Path("/tmp/council_a.html"))
    council_runner._maybe_auto_open(Path("/tmp/council_b.html"))

    # Both calls open the SAME launcher URL (browser can reuse tab).
    launcher = portal_pages_dir() / "_open_council.html"
    assert popen_mock.call_count == 2
    assert popen_mock.call_args_list[0][0][0][2] == str(launcher)
    assert popen_mock.call_args_list[1][0][0][2] == str(launcher)
    # Launcher now references the LATEST council.
    body = launcher.read_text(encoding="utf-8")
    assert "/tmp/council_b.html" in body
    assert "/tmp/council_a.html" not in body  # overwritten, not appended


def test_auto_open_setting_persists(isolated_home):
    """The auto_open_council setting still round-trips through the
    settings file even after the CLI toggle commands were retired
    (2026-05-17). Whoever flips the flag programmatically (config
    overlay, future settings UI) gets the same persistence."""
    from trinity_local.telemetry import load_telemetry_settings, save_telemetry_settings

    settings = load_telemetry_settings()
    assert settings.auto_open_council is False

    settings.auto_open_council = True
    save_telemetry_settings(settings)
    assert load_telemetry_settings().auto_open_council is True

    settings.auto_open_council = False
    save_telemetry_settings(settings)
    assert load_telemetry_settings().auto_open_council is False
