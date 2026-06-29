"""#266 — the claude sync pill never showed because claude.ai's
`?starred=true` chat_conversations_v2 variant (empty for a no-stars account)
overwrote the real recent-conversations sidebar with data:[].

Two guards: page-hook.js excludes the starred variant at the source, and
capture_host refuses to overwrite a populated _sidebar.json with an empty one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


class TestSidebarItemCount:
    def test_counts_each_provider_shape(self):
        from trinity_local.capture_host import _count_sidebar_items

        assert _count_sidebar_items({"sidebar": {"items": [{}, {}]}}) == 2  # chatgpt
        assert _count_sidebar_items({"sidebar": {"data": [{}, {}, {}]}}) == 3  # claude
        assert _count_sidebar_items({"sidebar": [{}, {}]}) == 2  # gemini DOM list
        assert _count_sidebar_items({"sidebar": {"data": []}}) == 0  # empty starred
        assert _count_sidebar_items({}) == 0


class TestSidebarClobberGuard:
    @pytest.fixture
    def conv_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "conversations"
        (d / "claude").mkdir(parents=True)
        monkeypatch.setattr("trinity_local.capture_host._conv_dir", lambda: d)
        return d

    def test_empty_does_not_overwrite_populated(self, conv_dir):
        from trinity_local.capture_host import _write_capture

        populated = {"sidebar": {"data": [{"uuid": "a"}, {"uuid": "b"}]}}
        empty = {"sidebar": {"data": []}, "url": "...?starred=true"}

        _write_capture("claude", "_sidebar", populated)
        _write_capture("claude", "_sidebar", empty)  # must be refused

        on_disk = json.loads((conv_dir / "claude" / "_sidebar.json").read_text())
        assert len(on_disk["sidebar"]["data"]) == 2, "empty starred list must not clobber"

    def test_populated_overwrites_empty(self, conv_dir):
        from trinity_local.capture_host import _write_capture

        _write_capture("claude", "_sidebar", {"sidebar": {"data": []}})
        _write_capture("claude", "_sidebar", {"sidebar": {"data": [{"uuid": "a"}]}})

        on_disk = json.loads((conv_dir / "claude" / "_sidebar.json").read_text())
        assert len(on_disk["sidebar"]["data"]) == 1, "a real list must replace an empty one"

    def test_first_write_always_lands(self, conv_dir):
        from trinity_local.capture_host import _write_capture

        # Even an empty sidebar writes when there's nothing to protect.
        _write_capture("claude", "_sidebar", {"sidebar": {"data": []}})
        assert (conv_dir / "claude" / "_sidebar.json").exists()


class TestPageHookExcludesStarred:
    def test_page_hook_filters_starred_variant(self):
        """The page-hook must not classify ?starred=true as the sidebar list."""
        src = (REPO / "browser-extension" / "page-hook.js").read_text()
        # The claude sidebar_list branch must gate on the starred param.
        assert 'searchParams.get("starred") !== "true"' in src
