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
        import trinity_local.me.build_meters as bm
        monkeypatch.setattr(bm, "record_build_meters", lambda: {})   # the kick is under test here

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


@pytest.mark.usefixtures("patch_trinity_home")
class TestBuildMetersRunOnEveryBuildPath:
    """The palate canary and the residual log stopped silently on 2026-08-24: the manual
    `lens` path ran them, the MCP background refresh (the path every rebuild took after
    that day) did not. These pin both paths and every future one."""

    def _meters(self, monkeypatch):
        import trinity_local.me.palate_registry as pr
        import trinity_local.me.residual_log as rl
        calls = []
        monkeypatch.setattr(pr, "record_direction_snapshot",
                            lambda *a, **k: (calls.append("palate"), {"ok": True, "fit_n": 9})[1])
        monkeypatch.setattr(rl, "record_snapshot", lambda *a, **k: (calls.append("residual"), {"at": "now"})[1])
        return calls

    def _kick(self, monkeypatch, summary):
        import trinity_local.me_builder as mb
        from trinity_local.cold_start import lens_refresh_marker_path, maybe_kick_lens_refresh
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "0")
        monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
        _seed_state(monkeypatch, built_at=_ago(48), prior_fp="100:aaa", cur_fp="110:bbb")
        monkeypatch.setattr(mb, "build_me_via_lens_pipeline", lambda *a, **k: (mb.me_path(), summary))
        assert maybe_kick_lens_refresh()["status"] == "kicked"
        for _ in range(100):
            try:
                m = json.loads(lens_refresh_marker_path().read_text())
                if m["status"] != "in_progress":
                    return m
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.02)
        raise AssertionError("refresh never finished")

    def test_background_refresh_runs_both_meters_when_the_lens_advanced(self, monkeypatch):
        calls = self._meters(monkeypatch)
        marker = self._kick(monkeypatch, {"ok": True})
        assert marker["status"] == "done"
        assert calls == ["palate", "residual"]
        assert marker["meters"]["palate_snapshot"] == {"ok": True, "fit_n": 9}
        assert marker["meters"]["residual_snapshot"] == {"at": "now"}

    def test_background_refresh_skips_meters_when_the_build_did_not_land(self, monkeypatch):
        calls = self._meters(monkeypatch)
        marker = self._kick(monkeypatch, {"ok": False, "aborted": "quota"})
        assert marker["status"] == "failed"
        assert calls == [] and "meters" not in marker

    def test_manual_lens_path_runs_the_same_meters_and_not_on_dry_run(self, monkeypatch):
        import trinity_local.distill as dist
        import trinity_local.personal_routing as routing
        import trinity_local.vocabulary as vocab
        from trinity_local.commands.me import _post_build_hooks
        calls = self._meters(monkeypatch)
        monkeypatch.setattr(routing, "freeze_routing_to_disk", lambda: {})
        monkeypatch.setattr(vocab, "distill_vocabulary", lambda: {"ok": True})
        monkeypatch.setattr(dist, "distill_via_chairman", lambda: {"ok": True})
        assert _post_build_hooks(True) == {} and calls == []
        out = _post_build_hooks(False)
        assert calls == ["palate", "residual"]
        assert out["palate_snapshot"]["ok"] is True and out["residual_snapshot"] == {"at": "now"}


def test_every_lens_build_call_site_also_records_the_meters():
    """Structural ratchet: any function in src/ that calls build_me_via_lens_pipeline() must
    also run the meters (record_build_meters, or _post_build_hooks which calls it). A new
    build path that skips them is exactly how the canary went silent for six weeks."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
    offenders = []
    for f in src.rglob("*.py"):
        if f.name == "me_builder.py":
            continue
        tree = ast.parse(f.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                     for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if "build_me_via_lens_pipeline" in names and not names & {"record_build_meters", "_post_build_hooks"}:
                offenders.append(f"{f.relative_to(src)}::{fn.name}")
    assert not offenders, f"lens build without the palate/residual meters: {offenders}"
