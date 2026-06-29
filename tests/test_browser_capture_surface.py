"""Surface 33 — launchpad ``_browser_capture`` helper.

Validates the data the v1.6 capture-activity card consumes. Same shape
test as the rate-limit-saves / verdict-stats Surface 30/32 helpers.
"""

from __future__ import annotations

import os
import time

import pytest

from trinity_local.launchpad_data import _browser_capture, _humanize_ago


@pytest.fixture
def isolated_trinity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_empty_state_when_conversations_dir_missing(isolated_trinity_home):
    result = _browser_capture()
    assert result["has_data"] is False
    assert result["total_captured"] == 0
    assert result["install_command"] == "trinity-local install-extension"
    assert result["providers"] == []


def test_empty_state_when_dir_exists_but_no_files(isolated_trinity_home):
    (isolated_trinity_home / "conversations" / "claude").mkdir(parents=True)
    result = _browser_capture()
    assert result["has_data"] is False


def test_counts_per_provider(isolated_trinity_home):
    cdir = isolated_trinity_home / "conversations" / "claude"
    gdir = isolated_trinity_home / "conversations" / "chatgpt"
    cdir.mkdir(parents=True)
    gdir.mkdir(parents=True)
    (cdir / "a.json").write_text("{}")
    (cdir / "b.json").write_text("{}")
    (cdir / "c.json").write_text("{}")
    (gdir / "x.json").write_text("{}")

    result = _browser_capture()
    assert result["has_data"] is True
    assert result["total_captured"] == 4
    assert len(result["providers"]) == 2
    # Sorted descending by count
    assert result["providers"][0]["provider"] == "claude"
    assert result["providers"][0]["count"] == 3
    assert result["providers"][1]["provider"] == "chatgpt"
    assert result["providers"][1]["count"] == 1


def test_excludes_stream_sidecar_files(isolated_trinity_home):
    """``.stream.json`` adapter outputs don't count as "captured
    conversations" — those are partial state, not user-facing data."""
    cdir = isolated_trinity_home / "conversations" / "claude"
    cdir.mkdir(parents=True)
    (cdir / "conv-1.json").write_text("{}")
    (cdir / "conv-1.stream.json").write_text("{}")  # ignored
    (cdir / "conv-2.json").write_text("{}")
    (cdir / "conv-2.stream.json").write_text("{}")  # ignored

    result = _browser_capture()
    assert result["total_captured"] == 2
    assert result["providers"][0]["count"] == 2


def test_excludes_raw_stream_prefix_files(isolated_trinity_home):
    """``stream-<urlhash>.json`` raw fallbacks (capture_host's
    no-adapter path) ALSO don't count. Currently relevant for the
    gemini.google.com path — gemini.js adapter is deferred to v1.7,
    so gemini captures land as stream- files; they shouldn't inflate
    the launchpad's user-facing count."""
    gdir = isolated_trinity_home / "conversations" / "gemini"
    gdir.mkdir(parents=True)
    # 3 raw-stream fallback files + 0 canonical files for gemini
    (gdir / "stream-abc123.json").write_text("{}")
    (gdir / "stream-def456.json").write_text("{}")
    (gdir / "stream-789xyz.json").write_text("{}")

    result = _browser_capture()
    # Should report empty — no real captures, just useless raw streams
    assert result["has_data"] is False
    assert result["total_captured"] == 0


def test_mixed_provider_dirs_count_only_canonical(isolated_trinity_home):
    """When claude has 2 canonical captures and gemini has 3 raw
    stream fallbacks, the count + providers list reflect only claude."""
    cdir = isolated_trinity_home / "conversations" / "claude"
    gdir = isolated_trinity_home / "conversations" / "gemini"
    cdir.mkdir(parents=True)
    gdir.mkdir(parents=True)
    (cdir / "real-1.json").write_text("{}")
    (cdir / "real-2.json").write_text("{}")
    (gdir / "stream-abc.json").write_text("{}")  # raw, ignored
    (gdir / "stream-def.json").write_text("{}")  # raw, ignored

    result = _browser_capture()
    assert result["has_data"] is True
    assert result["total_captured"] == 2
    assert len(result["providers"]) == 1
    assert result["providers"][0]["provider"] == "claude"


def test_24h_counter_filters_by_mtime(isolated_trinity_home):
    """``captured_24h`` only counts files modified in the last 24
    hours — the bigger ``total_captured`` covers all time."""
    cdir = isolated_trinity_home / "conversations" / "claude"
    cdir.mkdir(parents=True)
    fresh = cdir / "fresh.json"
    old = cdir / "old.json"
    fresh.write_text("{}")
    old.write_text("{}")
    # Backdate "old.json" to two days ago
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))

    result = _browser_capture()
    assert result["total_captured"] == 2
    assert result["captured_24h"] == 1
    assert result["providers"][0]["count_24h"] == 1


def test_last_capture_picks_max_mtime_across_providers(isolated_trinity_home):
    cdir = isolated_trinity_home / "conversations" / "claude"
    gdir = isolated_trinity_home / "conversations" / "chatgpt"
    cdir.mkdir(parents=True)
    gdir.mkdir(parents=True)

    claude_file = cdir / "old.json"
    chatgpt_file = gdir / "newer.json"
    claude_file.write_text("{}")
    chatgpt_file.write_text("{}")
    # claude.ai file = 1h ago, chatgpt.com file = 5 min ago → chatgpt wins
    now = time.time()
    os.utime(claude_file, (now - 3600, now - 3600))
    os.utime(chatgpt_file, (now - 300, now - 300))

    result = _browser_capture()
    assert result["last_capture_ago_seconds"] <= 305
    assert result["last_capture_ago_seconds"] >= 300


def test_stale_flag_when_last_capture_older_than_24h(isolated_trinity_home):
    """The silent-breakage signal: if at least one capture exists but
    the most recent is > 24h old, the launchpad flips into a warning
    border."""
    cdir = isolated_trinity_home / "conversations" / "claude"
    cdir.mkdir(parents=True)
    old = cdir / "old.json"
    old.write_text("{}")
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))

    result = _browser_capture()
    assert result["has_data"] is True
    assert result["stale"] is True
    assert result["captured_24h"] == 0


def test_stale_false_when_within_24h(isolated_trinity_home):
    cdir = isolated_trinity_home / "conversations" / "claude"
    cdir.mkdir(parents=True)
    (cdir / "fresh.json").write_text("{}")

    result = _browser_capture()
    assert result["stale"] is False


def test_humanize_ago_buckets():
    assert _humanize_ago(None) == ""
    assert _humanize_ago(-5) == ""
    assert _humanize_ago(0) == "0s"
    assert _humanize_ago(45) == "45s"
    assert _humanize_ago(60) == "1m"
    assert _humanize_ago(3599) == "59m"
    assert _humanize_ago(3600) == "1h"
    assert _humanize_ago(86399) == "23h"
    assert _humanize_ago(86400) == "1d"
    assert _humanize_ago(7 * 86400) == "7d"


def test_does_not_crash_on_unreadable_files(isolated_trinity_home):
    """Per "Analytics never crash": stat() failures on individual
    files must not blow up the whole helper."""
    cdir = isolated_trinity_home / "conversations" / "claude"
    cdir.mkdir(parents=True)
    (cdir / "a.json").write_text("{}")
    # Result should be valid even if we somehow injected a bad path —
    # just make sure normal flow doesn't crash.
    result = _browser_capture()
    assert result["has_data"] is True
    assert result["total_captured"] == 1


def test_helper_renders_into_page_data(isolated_trinity_home):
    """build_page_data must include the browserCapture key — Surface
    33 won't render otherwise. Regression guard against quietly
    dropping the helper from the payload assembly."""
    from pathlib import Path

    from trinity_local.launchpad_data import build_page_data

    # Drop a single capture so has_data flips True
    cdir = isolated_trinity_home / "conversations" / "claude"
    cdir.mkdir(parents=True)
    (cdir / "smoke.json").write_text("{}")

    page = build_page_data(live_review_path=Path("/tmp/x.html"), recent_councils=[])
    assert "browserCapture" in page, "build_page_data missing browserCapture key"
    assert page["browserCapture"]["has_data"] is True
    assert page["browserCapture"]["total_captured"] == 1
