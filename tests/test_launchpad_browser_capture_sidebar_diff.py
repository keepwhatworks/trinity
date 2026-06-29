"""Tests for the sidebar-sync diff on the launchpad's browser-capture
card. Parity with the status CLI's Captures: section (50e7610).

Same data source for all three surfaces:
- status CLI Captures: section
- in-provider auto-sync pill (browser-extension/sync-pill.js)
- launchpad browser-capture card (this file's tests)

All call `capture_host._query_sync_status` so they never drift apart.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _make_capture(home, provider, count, sidebar_ids=None):
    """Create N capture files for provider; optionally pin a sidebar."""
    import json

    conv_dir = home / "conversations" / provider
    conv_dir.mkdir(parents=True)
    for i in range(count):
        conv_id = f"conv_{provider}_{i}"
        (conv_dir / f"{conv_id}.json").write_text("{}", encoding="utf-8")
    if sidebar_ids is not None:
        sidebar = {"sidebar": {"items": [{"id": cid} for cid in sidebar_ids]}}
        (conv_dir / "_sidebar.json").write_text(json.dumps(sidebar), encoding="utf-8")


class TestBrowserCaptureSidebarDiff:
    def test_provider_with_no_sidebar_lacks_diff_fields(self, isolated_home):
        """If the extension hasn't snapshotted a sidebar yet (cold install
        or older extension version), the row should still render with
        count + count_24h but NO sidebar_count / missing_count fields.
        Template's v-if guards on those keys so absence is fine."""
        _make_capture(isolated_home, "claude", count=5)
        from trinity_local.launchpad_data import _browser_capture

        result = _browser_capture()
        assert result["has_data"] is True
        claude_row = next(r for r in result["providers"] if r["provider"] == "claude")
        # Diff fields absent — extension never wrote _sidebar.json
        assert claude_row.get("sidebar_count", 0) == 0 or "sidebar_count" not in claude_row
        assert claude_row.get("missing_count", 0) == 0 or "missing_count" not in claude_row

    def test_provider_fully_synced_has_zero_missing(self, isolated_home):
        """When on-disk captures match sidebar 1:1, missing_count is 0
        — template hides the unsynced suffix (silent-when-zero)."""
        _make_capture(
            isolated_home, "chatgpt", count=3,
            sidebar_ids=["conv_chatgpt_0", "conv_chatgpt_1", "conv_chatgpt_2"],
        )
        from trinity_local.launchpad_data import _browser_capture

        result = _browser_capture()
        chatgpt_row = next(r for r in result["providers"] if r["provider"] == "chatgpt")
        assert chatgpt_row["sidebar_count"] == 3
        assert chatgpt_row["missing_count"] == 0

    def test_provider_with_missing_threads_surfaces_count(self, isolated_home):
        """The load-bearing case: 3 captures on disk, sidebar lists 5 →
        missing_count = 2. Template renders "2 unsynced" suffix."""
        _make_capture(
            isolated_home, "gemini", count=3,
            sidebar_ids=[
                "conv_gemini_0", "conv_gemini_1", "conv_gemini_2",
                "conv_extra_in_sidebar", "conv_other_missing",
            ],
        )
        from trinity_local.launchpad_data import _browser_capture

        result = _browser_capture()
        gemini_row = next(r for r in result["providers"] if r["provider"] == "gemini")
        assert gemini_row["sidebar_count"] == 5
        assert gemini_row["missing_count"] == 2

    def test_sidebar_only_provider_is_not_a_phantom_capture(self, isolated_home):
        """UX sweep iter 83 — the NEVER-SYNCED state must not phantom-count.

        A provider whose sidebar was scraped (`_sidebar.json` written by a
        `sidebar_list` capture) but whose threads were NEVER captured has ZERO
        real conversations on disk. `_sidebar.json` is a `_`-prefixed sentinel,
        NOT a conversation.

        THE DEFECT this pins: `iter_capture_files`'s non-gemini branch did NOT
        exclude the `_`-sentinel (the gemini branch + `_query_sync_status`
        already did), so for claude/chatgpt the lone `_sidebar.json` was counted
        as "1 captured conversation". The launchpad card then showed
        "claude · 1 captured · synced" for a provider with 0 captures and N
        unsynced threads — a green-while-degraded inversion (a never-synced
        provider read identically to a working one). It also inflated
        total_captured + skewed the distribution bar.

        With the fix: a sidebar-only provider contributes NOTHING to the count
        and does NOT appear as a captured-provider row. (Whether to surface the
        never-synced state as its OWN signal is a separate product call; this
        guard only forbids the phantom capture.)
        """
        import json

        # chatgpt: 2 REAL captures (the card needs has_data so it renders).
        _make_capture(isolated_home, "chatgpt", count=2)
        # claude: ONLY a sidebar snapshot of 5 threads, ZERO conversations.
        claude_dir = isolated_home / "conversations" / "claude"
        claude_dir.mkdir(parents=True)
        sidebar = {
            "url": "https://claude.ai/api/organizations/abc123def456/chat_conversations",
            "sidebar": {"chat_conversations": [{"uuid": f"u{i}"} for i in range(5)]},
        }
        (claude_dir / "_sidebar.json").write_text(json.dumps(sidebar), encoding="utf-8")

        from trinity_local.capture_host import iter_capture_files
        from trinity_local.launchpad_data import _browser_capture

        # The sentinel must not be enumerated as a capture file.
        names = {f.name for f in iter_capture_files()}
        assert "_sidebar.json" not in names, (
            "FOUND-BUT-PHANTOM: _sidebar.json (a `_`-sentinel, not a conversation) "
            "is being counted as a captured file — a never-synced provider would "
            "read as '1 captured · synced'"
        )

        result = _browser_capture()
        # total is the 2 real chatgpt captures only — NOT 3 (no phantom claude).
        assert result["total_captured"] == 2, (
            f"phantom capture inflated the total: got {result['total_captured']}, "
            "the lone claude _sidebar.json must not count as a captured conversation"
        )
        provider_names = {r["provider"] for r in result["providers"]}
        assert "claude" not in provider_names, (
            "a sidebar-only (never-synced, 0 captured) provider must NOT appear as "
            "a captured-provider row reading 'claude · 1 · synced'"
        )
        assert "chatgpt" in provider_names
        chatgpt_row = next(r for r in result["providers"] if r["provider"] == "chatgpt")
        assert chatgpt_row["count"] == 2

    def test_sidebar_only_provider_is_not_a_phantom_capture_gemini(self, isolated_home):
        """Companion: gemini's branch already excluded `_sidebar.json` by exact
        name; generalizing it to the `_`-prefix convention must keep that behavior
        (a gemini sidebar-only provider stays uncounted)."""
        import json

        _make_capture(isolated_home, "chatgpt", count=2)
        gem_dir = isolated_home / "conversations" / "gemini"
        gem_dir.mkdir(parents=True)
        (gem_dir / "_sidebar.json").write_text(
            json.dumps({"sidebar": {"items": [{"conv_id": f"g{i}"} for i in range(3)]}}),
            encoding="utf-8",
        )
        from trinity_local.launchpad_data import _browser_capture

        result = _browser_capture()
        assert result["total_captured"] == 2
        assert "gemini" not in {r["provider"] for r in result["providers"]}

    def test_sidebar_diff_failure_does_not_break_browser_capture(self, isolated_home, monkeypatch):
        """Per analytics-never-crash: a bug in _query_sync_status must
        not propagate out of _browser_capture. The row renders without
        the sidebar fields; rest of the surface stays intact."""
        _make_capture(isolated_home, "claude", count=2)

        def explode(payload):
            raise RuntimeError("simulated sidebar lookup bug")

        from trinity_local import capture_host as capture_mod
        monkeypatch.setattr(capture_mod, "_query_sync_status", explode)

        from trinity_local.launchpad_data import _browser_capture

        # Must not raise
        result = _browser_capture()
        assert result["has_data"] is True
        claude_row = next(r for r in result["providers"] if r["provider"] == "claude")
        # Sidebar fields absent (the try/except swallowed the error)
        assert "sidebar_count" not in claude_row
        assert "missing_count" not in claude_row
