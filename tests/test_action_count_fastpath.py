"""Fast-path action-status counting (perf + correctness).

Real install observed: 18,169 action files in ~/.trinity/actions/.
Status / launchpad code only needs the count per status, not the
full PendingAction objects — but the previous path went through
list_actions(status=X) twice, calling load_action() once per file
(~1.5s on the live corpus). count_actions_by_status() skims the
first 2KB of each file with a regex; same result, ~10× faster.

Pin both the correctness (counts match the full-load path) and the
robustness against long-field bias (the original 256-byte window
missed ~0.1% of files because task_cluster_id sometimes holds an
absolute path that pushes `status` past byte 256).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def actions_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    actions = tmp_path / "actions"
    actions.mkdir()
    return actions


def _write_action(dir_: Path, action_id: str, status: str, *, prefix_padding: str = "") -> None:
    payload = {
        "action_id": action_id,
        "task_id": f"task_{action_id}",
        # Optional padding to push `status` past short read windows
        # — exercises the long-prefix code path.
        "task_cluster_id": prefix_padding or f"cluster_{action_id}",
        "status": status,
        "kind": "recommendation",
        "title": "x",
        "message": "y",
        "created_at": "2026-05-24T00:00:00+00:00",
        "updated_at": "2026-05-24T00:00:00+00:00",
        "metadata": {},
    }
    (dir_ / f"{action_id}.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


class TestCountActionsByStatus:
    def test_empty_dir_returns_empty_dict(self, actions_home):
        from trinity_local.action_runtime import count_actions_by_status
        assert count_actions_by_status() == {}

    def test_mixed_statuses_counted_correctly(self, actions_home):
        from trinity_local.action_runtime import count_actions_by_status
        for i in range(5):
            _write_action(actions_home, f"p{i}", "pending")
        for i in range(3):
            _write_action(actions_home, f"c{i}", "completed")
        for i in range(2):
            _write_action(actions_home, f"d{i}", "dismissed")

        counts = count_actions_by_status()
        assert counts == {"pending": 5, "completed": 3, "dismissed": 2}

    def test_long_task_cluster_id_does_not_hide_status(self, actions_home):
        """Regression: a long absolute path in task_cluster_id used to
        push `status` past a too-small read window (256-byte window
        missed ~19 of 18K files on the real install). 2KB window covers
        every observed shape; this test pins the property."""
        from trinity_local.action_runtime import count_actions_by_status
        # A 1500-byte cluster_id reliably pushes `status` past 256B.
        long_path = "/Users/x/" + ("very/long/segment/" * 80)
        assert len(long_path) > 256
        _write_action(
            actions_home, "long",
            status="pending",
            prefix_padding=long_path,
        )

        counts = count_actions_by_status()
        assert counts.get("pending") == 1, (
            "long task_cluster_id must not hide the status field — "
            "the read window has to be wide enough to capture status"
        )

    def test_count_matches_load_action_path(self, actions_home):
        """Equivalence: fast-path count must equal slow-path count for
        every status, across many files of mixed shape."""
        from trinity_local.action_runtime import (
            count_actions_by_status,
            list_actions,
        )
        # Plant 50 pending (some with long padding) + 20 completed
        for i in range(50):
            padding = ("x" * 1200) if (i % 7 == 0) else ""
            _write_action(actions_home, f"p{i}", "pending", prefix_padding=padding)
        for i in range(20):
            _write_action(actions_home, f"c{i}", "completed")

        fast = count_actions_by_status()
        slow_pending = len(list_actions(status="pending"))
        slow_completed = len(list_actions(status="completed"))

        assert fast.get("pending") == slow_pending == 50
        assert fast.get("completed") == slow_completed == 20
