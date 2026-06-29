"""Regression: `trinity-local update` must time out on a stalled git
operation rather than hang indefinitely.

Network failures (offline / DNS hang / captive portal mid-fetch) used
to wedge `_git()` in `commands/update.py` because it called
`subprocess.run()` with no `timeout=`. The user typed `trinity-local
update`, watched a hung terminal, and had to ^C without knowing
whether the underlying state was clean.

After this fix, `_git()` carries a 5-minute timeout and synthesizes
rc=124 (the conventional `timeout(1)` exit code) + a clear stderr
message on TimeoutExpired. Existing rc-based error paths surface
the timeout as a normal "fetch failed" + skip cleanly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch


def test_git_helper_has_timeout_protection():
    """Structural: the `_git` helper must carry a `timeout=` kwarg AND
    handle `subprocess.TimeoutExpired` so a stalled fetch doesn't wedge
    the update command. Without the catch, TimeoutExpired propagates
    and crashes the CLI rather than surfacing as a graceful failure."""
    src_path = Path(__file__).resolve().parent.parent / "src" / "trinity_local" / "commands" / "update.py"
    src = src_path.read_text()
    assert "timeout=300" in src, (
        "_git() must carry timeout=300 (5 minutes) so a stalled network "
        "doesn't wedge `trinity-local update` indefinitely"
    )
    assert "subprocess.TimeoutExpired" in src, (
        "_git() must handle TimeoutExpired explicitly — letting it propagate "
        "would crash the update command rather than surface a graceful error"
    )


def test_git_helper_returns_124_on_timeout():
    """Behavioral: when subprocess.run raises TimeoutExpired, _git
    must return (124, "", "<clear message>") so the caller's existing
    rc-based error paths treat it as a network failure rather than
    success."""
    from trinity_local.commands import update

    with patch.object(update.subprocess, "run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=300)
        rc, stdout, stderr = update._git("fetch", "--quiet", "origin", cwd=Path("/tmp"))

    assert rc == 124, "TimeoutExpired must produce rc=124 (conventional timeout exit code)"
    assert stdout == "", "stdout should be empty on timeout"
    assert "timed out" in stderr.lower(), (
        "stderr must mention timing out so the caller logs a sensible message"
    )
    assert "5 minutes" in stderr or "300" in stderr, (
        "stderr should name the timeout duration so the user knows what bound was hit"
    )


def test_fetch_and_compute_lag_handles_timeout_gracefully():
    """End-to-end: _fetch_and_compute_lag uses _git internally. When
    _git returns rc=124 (timeout), the lag-check should surface a
    network-failure message — NOT report "no updates" silently."""
    from trinity_local.commands import update

    # _git is called twice in _fetch_and_compute_lag — first for `fetch`,
    # then for rev-list. The fetch is the network-dependent step.
    timeout_response = (124, "", "git operation timed out after 5 minutes (network stall?)")
    with patch.object(update, "_git", return_value=timeout_response):
        behind, ahead, err = update._fetch_and_compute_lag(Path("/tmp"))

    assert behind == 0
    assert ahead == 0
    assert err is not None, (
        "Timeout must surface as a non-None error — silent fallthrough to "
        "(0, 0, None) would mean the user sees 'no updates' when in fact "
        "we never reached the remote"
    )
    assert "timed out" in err.lower(), "error must mention the timeout"
