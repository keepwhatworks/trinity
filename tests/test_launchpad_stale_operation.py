"""A stale "running" council must not leave the launchpad's Launch button disabled.

`busy` (launchpad_template.py) is computed `operation && operation.status==='running'`,
seeded on load from `_active_launchpad_operation()`, which only returns statuses still
"running" after `load_council_status` → `_coerce_stale_running_status`.

That coercion marked a council dead via a PID-liveness check — but a dead runner's pid
can be REUSED by an unrelated live process, so the check was fooled. Observed live
2026-06-08: a council interrupted ~17h earlier still read "running" because its pid had
been reassigned → the launchpad's Launch Council button was disabled forever. Fix: a
council is alive only if its pid is alive AND its status was written recently
(_STALE_RUNNING_SECONDS). These pin: live-pid-but-stale → coerced failed (the bug),
live-pid-and-fresh → stays running, dead-pid → coerced failed (unchanged).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trinity_local.council_status import _STALE_RUNNING_SECONDS, load_council_status
from trinity_local.launchpad_data import _active_launchpad_operation
from trinity_local.state_paths import council_status_json_path


def _write_running(token: str, *, pid: int | None, updated_at: str | None) -> Path:
    raw: dict = {
        "status_token": token,
        "status": "running",
        "metadata": {"kind": "council", "members": ["claude", "codex", "antigravity"]},
        "members": {}, "synthesis": {}, "task_text": "test council",
    }
    if pid is not None:
        raw["runner_pid"] = pid
    if updated_at is not None:
        raw["updated_at"] = updated_at
    path = council_status_json_path(token)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_live_pid_but_stale_status_is_coerced_failed(patch_trinity_home: Path):
    # THE BUG: pid alive (reused), but the council last updated 45 min ago → dead.
    _write_running("reused", pid=os.getpid(), updated_at=_iso(45))
    coerced = load_council_status("reused")
    assert coerced is not None and coerced["status"] == "failed", (
        "a live-but-reused pid with a stale 'running' status must be coerced to failed, "
        "not left running (which disables the launchpad's Launch Council button forever)"
    )
    assert _active_launchpad_operation() is None


def test_live_pid_and_fresh_status_stays_active(patch_trinity_home: Path):
    # A genuinely-running council: pid alive AND just updated → still active.
    _write_running("live", pid=os.getpid(), updated_at=_iso(0.1))
    assert load_council_status("live")["status"] == "running"
    op = _active_launchpad_operation()
    assert op is not None and op["status"] == "running" and op["statusToken"] == "live"


def test_dead_pid_is_coerced_failed(patch_trinity_home: Path):
    # No live runner pid → dead (the pre-existing liveness path), regardless of freshness.
    _write_running("dead", pid=None, updated_at=_iso(0.1))
    assert load_council_status("dead")["status"] == "failed"
    assert _active_launchpad_operation() is None


def test_stale_threshold_is_well_past_any_real_council():
    # Sanity: the floor is generous (councils finish in 30s–2min) so a genuinely-slow
    # council is never killed, but a clearly-dead one (hours) always is.
    assert _STALE_RUNNING_SECONDS >= 15 * 60
