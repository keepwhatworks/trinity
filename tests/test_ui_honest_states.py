"""Surfaces stop claiming progress or success they do not have (UI batch U3b, coherence review
2026-10-15): a dead lens build no longer animates forever, the health card counts the checks it
runs, and the live council page knows how stale a "running" status may be.
"""
from __future__ import annotations

import json
from types import SimpleNamespace


def _progress(status: str, updated_at: str) -> None:
    from trinity_local.lens_progress import progress_path
    path = progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": "stage3", "label": "Naming tensions", "pct": 70,
                                "status": status, "started_at": updated_at,
                                "updated_at": updated_at}), encoding="utf-8")


def test_a_dead_lens_build_reads_stopped_not_building(patch_trinity_home):
    from trinity_local.launchpad_data import _lens_build_for_launchpad
    from trinity_local.lens_progress import STALLED_ERROR
    _progress("running", "2026-01-01T00:00:00Z")
    from trinity_local.cold_start import _hours_since
    if (_hours_since("2026-01-01T00:00:00Z") or 0) > 24:
        # Older than a day: the card hides like any old failure.
        assert _lens_build_for_launchpad() is None
    _progress("running", _ago(hours=2))
    card = _lens_build_for_launchpad()
    assert card and card["building"] is False and card["status"] == "failed"
    assert card["error"] == STALLED_ERROR


def test_a_live_lens_build_still_reads_building(patch_trinity_home):
    from trinity_local.launchpad_data import _lens_build_for_launchpad
    from trinity_local.utils import now_iso
    _progress("running", now_iso())
    card = _lens_build_for_launchpad()
    assert card and card["building"] is True and card["status"] == "running"


def test_lens_stop_does_not_call_a_dead_build_running(patch_trinity_home, capsys):
    from trinity_local.commands.me import handle_lens_stop
    _progress("running", _ago(hours=2))
    handle_lens_stop(SimpleNamespace())
    out = json.loads(capsys.readouterr().out)
    assert out["was_running"] is False


def test_the_health_card_counts_the_checks_it_runs(patch_trinity_home):
    from trinity_local.launchpad_data import _memory_health
    health = _memory_health()
    assert health["total_count"] == 6, health
    assert health["ok_count"] == health["total_count"] - len(health["issues"])


def test_the_live_council_page_carries_the_servers_staleness_window():
    from trinity_local.council_review import render_live_council_page
    from trinity_local.council_status import _stale_running_seconds
    html = render_live_council_page()
    assert f'"staleRunningMs":{int(_stale_running_seconds() * 1000)}' in html
    assert "This council stopped updating" in html


def _ago(*, hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
