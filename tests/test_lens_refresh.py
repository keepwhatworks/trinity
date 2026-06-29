"""Activity-gated lens refresh — Anthropic's Auto-Dream trigger, not a cron.

Refresh an EXISTING lens when ≥REFRESH_MIN_AGE_H since the last build AND
≥REFRESH_MIN_NEW_PROMPTS new prompts accumulated. Evaluated at MCP connect
(an authenticated "session"), background-kicked, free on a quiet day.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import pytest


def _ago(hours: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()


def _seed_state(monkeypatch, *, built_at, prior_fp, cur_fp, lens=True):
    import trinity_local.me_builder as mb
    from trinity_local.me_builder import _lens_build_state_path, me_path

    if lens:
        p = me_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Lens\n", encoding="utf-8")
    sp = _lens_build_state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"built_at": built_at, "fingerprint": prior_fp}), encoding="utf-8")
    monkeypatch.setattr(mb, "_corpus_fingerprint", lambda: cur_fp)


@pytest.mark.usefixtures("patch_trinity_home")
class TestShouldRefreshLens:
    def test_fires_when_aged_and_enough_new(self, monkeypatch):
        from trinity_local.cold_start import should_refresh_lens
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="110:bbb")
        ok, reason = should_refresh_lens()
        assert ok is True
        assert "10 new prompts" in reason

    def test_no_lens_is_coldstart_not_refresh(self, monkeypatch):
        from trinity_local.cold_start import should_refresh_lens
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="110:bbb", lens=False)
        ok, reason = should_refresh_lens()
        assert ok is False and "cold-start" in reason

    def test_within_age_floor_does_not_fire(self, monkeypatch):
        from trinity_local.cold_start import should_refresh_lens
        _seed_state(monkeypatch, built_at=_ago(2), prior_fp="100:aaa", cur_fp="999:bbb")
        ok, reason = should_refresh_lens()
        assert ok is False and "floor" in reason

    def test_corpus_unchanged_does_not_fire(self, monkeypatch):
        from trinity_local.cold_start import should_refresh_lens
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="100:aaa")
        ok, reason = should_refresh_lens()
        assert ok is False and "unchanged" in reason

    def test_too_few_new_prompts_does_not_fire(self, monkeypatch):
        from trinity_local.cold_start import should_refresh_lens
        # aged + changed hash but only 2 new prompts (< 5)
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="102:bbb")
        ok, reason = should_refresh_lens()
        assert ok is False and "new prompt" in reason


@pytest.mark.usefixtures("patch_trinity_home")
class TestMaybeKickLensRefresh:
    def _enable_autoscan(self, monkeypatch):
        # conftest disables autoscan globally; re-enable for the kick tests.
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "0")
        # The lens is an opt-in add-on now (lens_addon gate); the kick tests
        # exercise the refresh path, so open that gate too.
        monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")

    def test_kicks_and_marks_done_when_gate_open(self, monkeypatch):
        import trinity_local.me_builder as mb
        from trinity_local.cold_start import (
            lens_refresh_marker_path,
            maybe_kick_lens_refresh,
        )

        self._enable_autoscan(monkeypatch)
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="110:bbb")
        calls = []
        monkeypatch.setattr(mb, "build_me_via_lens_pipeline",
                            lambda *a, **k: (calls.append(1), (mb.me_path(), {"ok": True}))[1])

        result = maybe_kick_lens_refresh()
        assert result and result["status"] == "kicked"
        # The background thread runs the (stubbed) rebuild.
        for _ in range(50):
            try:
                if json.loads(lens_refresh_marker_path().read_text())["status"] == "done":
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.02)
        marker = json.loads(lens_refresh_marker_path().read_text())
        assert marker["status"] == "done"
        assert calls == [1]

    def test_cooldown_blocks_second_kick(self, monkeypatch):
        import trinity_local.me_builder as mb
        from trinity_local.cold_start import maybe_kick_lens_refresh

        self._enable_autoscan(monkeypatch)
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="110:bbb")
        monkeypatch.setattr(mb, "build_me_via_lens_pipeline", lambda *a, **k: (mb.me_path(), {}))
        assert maybe_kick_lens_refresh() is not None
        # Immediately again — recently kicked → no-op.
        assert maybe_kick_lens_refresh() is None

    def test_autoscan_disabled_is_noop(self, monkeypatch):
        from trinity_local.cold_start import maybe_kick_lens_refresh
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="110:bbb")
        assert maybe_kick_lens_refresh() is None

    def test_gate_closed_is_noop(self, monkeypatch):
        from trinity_local.cold_start import maybe_kick_lens_refresh
        self._enable_autoscan(monkeypatch)
        _seed_state(monkeypatch, built_at=_ago(1), prior_fp="100:aaa", cur_fp="110:bbb")  # too recent
        assert maybe_kick_lens_refresh() is None
