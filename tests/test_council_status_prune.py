"""Pruning of stale council_status files.

Caught on a real install: 250 files / 5.8MB had accumulated in
~/.trinity/portal_pages/status/ over ~30 days. No GC ran anywhere —
status files lived forever once the council completed and the
canonical outcome landed in council_outcomes/. Linear growth with
launches; eventually a real disk concern.

The fix attaches a cheap mtime-based prune to init_council_run_state
(the council-launch path) — so cleanup is rate-limited by launch
cadence with zero separate cron.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _status_dir(home: Path) -> Path:
    return home / "portal_pages" / "status"


class TestStatusFilePrune:
    def test_old_files_deleted_on_init(self, home, monkeypatch):
        """A status file older than the 14-day cutoff should be removed
        when a new council launches."""
        from trinity_local.council_status import init_council_run_state

        d = _status_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        old = d / "council_status_oldtoken.json"
        old.write_text("{}", encoding="utf-8")
        # Backdate the mtime to 30 days ago — well past the 14-day cutoff.
        ancient = time.time() - (30 * 24 * 3600)
        os.utime(old, (ancient, ancient))

        init_council_run_state(
            "freshtoken",
            task_text="x",
            bundle_id="b1",
            members=["claude"],
        )
        assert not old.exists(), "old status file should have been pruned"
        # The fresh one IS expected — confirms the prune ran but didn't
        # accidentally delete the file we just wrote.
        assert (d / "council_status_freshtoken.json").exists()

    def test_recent_files_preserved(self, home, monkeypatch):
        """A status file inside the polling window (here: 1 day old) must
        be kept — the user may still be polling it from a launchpad tab."""
        from trinity_local.council_status import init_council_run_state

        d = _status_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        recent = d / "council_status_recenttoken.json"
        recent.write_text("{}", encoding="utf-8")
        one_day_ago = time.time() - (24 * 3600)
        os.utime(recent, (one_day_ago, one_day_ago))

        init_council_run_state(
            "freshtoken",
            task_text="x",
            bundle_id="b2",
            members=["claude"],
        )
        assert recent.exists(), "recent status file must NOT be pruned"

    def test_non_status_files_ignored(self, home, monkeypatch):
        """A foreign file (manual notes, sibling state) in the same dir
        must not be deleted even when ancient — only files matching the
        `council_status_*` naming get touched."""
        from trinity_local.council_status import init_council_run_state

        d = _status_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        foreign = d / "my_personal_notes.md"
        foreign.write_text("# notes", encoding="utf-8")
        ancient = time.time() - (90 * 24 * 3600)
        os.utime(foreign, (ancient, ancient))

        init_council_run_state(
            "freshtoken",
            task_text="x",
            bundle_id="b3",
            members=["claude"],
        )
        assert foreign.exists(), (
            "files not matching council_status_* must be left alone"
        )
