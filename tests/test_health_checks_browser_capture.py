"""Tests for ``_check_browser_capture`` — the v1.6 health-check preflight.

Four stages (first failure wins). All SOFT (ok=True) so the check
never breaks the `status` health summary for users who don't use
browser captures. (Lived in test_doctor_browser_capture.py until
2026-05-27 when health_checks.py was renamed from doctor.py.)
"""

from __future__ import annotations

import os
import time

import pytest

from trinity_local.health_checks import _check_browser_capture


@pytest.fixture
def isolated_trinity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_check_is_always_soft(isolated_trinity_home, monkeypatch):
    """The check must never set ok=False — the extension is optional
    and users may be CLI-only."""
    # Force the most-failing state (no host on PATH).
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: None)
    result = _check_browser_capture()
    assert result.ok is True
    assert result.name == "browser_capture"


def test_stage_1_no_host_on_path(isolated_trinity_home, monkeypatch):
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: None)
    result = _check_browser_capture()
    assert "not on PATH" in result.detail
    assert "pip install" in result.detail


def test_stage_2_host_present_but_manifest_missing(isolated_trinity_home, tmp_path, monkeypatch):
    """Host installed, but Chrome's Native Messaging manifest hasn't
    been written. install-extension --extension-id <ID> is the fix."""
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    # Point manifest path to a non-existent file by redirecting Home.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    result = _check_browser_capture()
    assert "Native Messaging manifest not written" in result.detail
    assert "install-extension" in result.detail


def _write_macos_manifest(home_dir):
    """Drop a fake Native Messaging manifest at the macOS path so
    Stage 2 passes."""
    manifest_dir = home_dir / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "local.trinity.capture.json").write_text("{}")


def test_stage_3_manifest_present_but_no_captures(isolated_trinity_home, tmp_path, monkeypatch):
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    _write_macos_manifest(fake_home)
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    result = _check_browser_capture()
    assert "no captures yet" in result.detail
    assert "chrome://extensions" in result.detail


def test_stage_4_stale_when_last_capture_older_than_24h(isolated_trinity_home, tmp_path, monkeypatch):
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    _write_macos_manifest(fake_home)
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    old = capture_dir / "old.json"
    old.write_text("{}")
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))

    result = _check_browser_capture()
    assert "but newest is" in result.detail
    assert "h old" in result.detail


def test_stage_4_fresh_captures_report_count_and_age(isolated_trinity_home, tmp_path, monkeypatch):
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    _write_macos_manifest(fake_home)
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    (capture_dir / "fresh.json").write_text("{}")
    (capture_dir / "fresher.json").write_text("{}")

    result = _check_browser_capture()
    assert "2 captures" in result.detail
    assert "newest" in result.detail


def test_future_mtime_capture_renders_no_negative_age(isolated_trinity_home, tmp_path, monkeypatch):
    """A capture file with a FUTURE mtime (clock skew, or a restored/rsync'd
    file with a preserved future timestamp) must NOT render
    "newest -179m ago." in the health-check detail — the founder symptom this
    guards. `_check_browser_capture` clamps `now - mtime` to 0, so a future
    capture reads as "just now" (0m), matching `_humanize_ago`'s future->now
    convention on the launchpad.
    """
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    _write_macos_manifest(fake_home)
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    future_file = capture_dir / "future.json"
    future_file.write_text("{}")
    # Render-INDEPENDENT precondition: the seed is genuinely in the FUTURE.
    future = time.time() + 3 * 3600
    os.utime(future_file, (future, future))
    assert future_file.stat().st_mtime > time.time(), "fixture mtime must be in the future to bite"

    result = _check_browser_capture()
    # (A) the surface paints (count present, the detail isn't a degraded fallback).
    assert "1 captures" in result.detail
    assert "newest" in result.detail
    # (B) the no-negative assertion keyed on the rendered binding — reds on the
    #     un-clamped "newest -179m ago." render.
    assert "newest -" not in result.detail, f"health-check capture detail leaked a NEGATIVE age at a future mtime: {result.detail!r}"
    assert "-" not in result.detail.split("newest", 1)[1], f"negative relative-time after 'newest': {result.detail!r}"


def test_excludes_stream_sidecar_files_from_count(isolated_trinity_home, tmp_path, monkeypatch):
    """The user-facing count must match Surface 33's count — both
    skip ``.stream.json`` adapter outputs."""
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    _write_macos_manifest(fake_home)
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    capture_dir = isolated_trinity_home / "conversations" / "claude"
    capture_dir.mkdir(parents=True)
    (capture_dir / "conv.json").write_text("{}")
    (capture_dir / "conv.stream.json").write_text("{}")  # NOT counted

    result = _check_browser_capture()
    assert "1 captures" in result.detail


def test_excludes_raw_stream_prefix_files_from_count(isolated_trinity_home, tmp_path, monkeypatch):
    """Raw-stream fallback files (``stream-<urlhash>.json`` from
    capture_host's no-adapter path) don't count either. Currently
    relevant for the gemini.google.com path — gemini.js adapter is
    deferred to v1.7, so gemini captures land as raw stream files.
    Doctor stage 3 must not pretend those are real conversations.
    """
    import sys
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    _write_macos_manifest(fake_home)
    monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))

    gemini_dir = isolated_trinity_home / "conversations" / "gemini"
    gemini_dir.mkdir(parents=True)
    (gemini_dir / "stream-abc.json").write_text("{}")
    (gemini_dir / "stream-def.json").write_text("{}")
    (gemini_dir / "stream-789.json").write_text("{}")

    result = _check_browser_capture()
    # 3 raw-stream files → "no captures yet" because none are
    # user-facing conversations.
    assert "no captures yet" in result.detail


def test_unsupported_platform_skips_with_note(isolated_trinity_home, monkeypatch):
    import sys
    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/trinity-local-capture-host")
    monkeypatch.setattr(sys, "platform", "win32")
    result = _check_browser_capture()
    assert result.ok is True
    assert "macOS/Linux" in result.detail


def test_run_doctor_includes_browser_capture_check():
    """Regression guard: ``run_doctor()`` must include the new check
    in its sequence. If anyone removes the append call the check is
    silently missing from `trinity-local status` output. (run_doctor()
    is the underlying library function; the `doctor` CLI was retired
    2026-05-18 and its checks now surface via `status`.)"""
    from trinity_local.health_checks import run_doctor

    # Don't care about pass/fail of the actual check here — just that
    # it ran (the name is in the report).
    report = run_doctor()
    names = [c.name for c in report.checks]
    assert "browser_capture" in names, (
        f"browser_capture missing from run_doctor() sequence; got {names}"
    )


class TestCaptureCountSurfacesAgree:
    """v1.7.300 de-dup guard: the CLI doctor (`_check_browser_capture`) and the
    launchpad cockpit (`_browser_capture` → Surface 33) must report the SAME capture
    count. They now share `capture_host.iter_capture_files` instead of inlining
    independent copies of the stream-/gemini filter — this locks them so a future
    change to that filter (e.g. a 4th provider's file convention) can't make `status`
    and the cockpit disagree on the total. Live 2026-06-02 both reported 2384."""

    def _mixed_home(self, home, tmp_path, monkeypatch):
        """A home exercising every filter branch. Canonical count = 3."""
        import sys
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path",
                            lambda _: "/usr/local/bin/trinity-local-capture-host")
        monkeypatch.setattr(sys, "platform", "darwin")
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        _write_macos_manifest(fake_home)
        monkeypatch.setattr("trinity_local.health_checks.Path.home", classmethod(lambda cls: fake_home))
        conv = home / "conversations"
        (conv / "claude").mkdir(parents=True)
        (conv / "gemini").mkdir(parents=True)
        (conv / "chatgpt").mkdir(parents=True)
        # COUNTED (canonical):
        (conv / "claude" / "a.json").write_text("{}")
        (conv / "chatgpt" / "b.json").write_text("{}")
        (conv / "gemini" / "g.stream.json").write_text("{}")   # .stream.json IS canonical for gemini
        # NOT counted:
        (conv / "claude" / "a.stream.json").write_text("{}")   # claude sidecar
        (conv / "claude" / "stream-xyz.json").write_text("{}")  # raw-fallback orphan
        (conv / "gemini" / "stream-pqr.json").write_text("{}")  # raw-fallback orphan
        return 3  # expected canonical count

    def test_iter_capture_files_applies_the_full_filter(self, tmp_path, monkeypatch):
        home = tmp_path / "trinity"
        home.mkdir()
        monkeypatch.setenv("TRINITY_HOME", str(home))
        expected = self._mixed_home(home, tmp_path, monkeypatch)
        from trinity_local.capture_host import iter_capture_files
        files = iter_capture_files()
        assert len(files) == expected, sorted(f.name for f in files)
        names = {f.name for f in files}
        assert names == {"a.json", "b.json", "g.stream.json"}, names

    def test_doctor_and_cockpit_report_the_same_count(self, tmp_path, monkeypatch):
        import re
        home = tmp_path / "trinity"
        home.mkdir()
        monkeypatch.setenv("TRINITY_HOME", str(home))
        expected = self._mixed_home(home, tmp_path, monkeypatch)
        from trinity_local.health_checks import _check_browser_capture
        from trinity_local.launchpad_data import _browser_capture
        doctor = _check_browser_capture().detail
        cockpit = _browser_capture()
        doctor_n = int(re.search(r"(\d+) captures", doctor).group(1))
        assert doctor_n == cockpit["total_captured"] == expected, (
            f"doctor={doctor_n} cockpit={cockpit['total_captured']} expected={expected} — surfaces disagree"
        )
        # gemini's .stream.json canonical must land under the gemini provider row.
        providers = {r["provider"]: r["count"] for r in cockpit["providers"]}
        assert providers == {"claude": 1, "chatgpt": 1, "gemini": 1}, providers
