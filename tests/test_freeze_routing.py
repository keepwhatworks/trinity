"""Tests for `freeze_routing_to_disk` — the writer that materializes the
on-demand personal routing table into `~/.trinity/scoreboard/routing.json`
(was `~/.trinity/memories/routing.json` pre-v1.7; auto-migrated by
`state_paths._migrate_legacy_scoreboard_paths()`).

routing.json is one of the user-facing model-selection scoreboards
surfaced in the memory viewer. Phase 5 distill no longer reads it —
picks/routing are scoreboards, not cognitive shape — but the freeze
writer still has to produce a valid file (or skip cleanly when empty)
so the viewer and any external tooling can read it.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _seed_outcome(home, *, council_id: str, task_type: str, winner: str):
    """Plant a council outcome JSON that the routing-table walker will pick up."""
    outcomes = home / "council_outcomes"
    outcomes.mkdir(parents=True, exist_ok=True)
    (outcomes / f"council_{council_id}.json").write_text(json.dumps({
        "council_run_id": f"council_{council_id}",
        "bundle_id": f"bundle_{council_id}",
        "task_cluster_id": "c",
        "primary_provider": winner,
        "created_at": "2026-05-12T00:00:00",
        "routing_label": {
            "task_type": task_type,
            "winner": winner,
            # aggregate_routing_table expects per-provider dicts with an
            # `overall` key (the scan ignores raw floats).
            "provider_scores": {
                winner: {"overall": 0.9, "accuracy": 0.9, "fit_to_user": 0.9},
                "other": {"overall": 0.4, "accuracy": 0.5, "fit_to_user": 0.3},
            },
        },
        "metadata": {
            "user_verdict": {"user_winner": winner},
        },
    }))


class TestFreezeRouting:
    def test_writes_routing_json_when_outcomes_exist(self, isolated_home):
        from trinity_local.personal_routing import freeze_routing_to_disk, invalidate_cache
        from trinity_local.state_paths import routing_path

        _seed_outcome(isolated_home, council_id="a1", task_type="system_design", winner="codex")
        _seed_outcome(isolated_home, council_id="a2", task_type="system_design", winner="codex")
        invalidate_cache()

        table = freeze_routing_to_disk()
        assert table, "routing table must be non-empty when outcomes exist"
        assert routing_path().exists()
        on_disk = json.loads(routing_path().read_text())
        assert on_disk == table, "file content must match returned table"

    def test_write_portal_freezes_routing_for_memory_viewer_crosslinks(self, isolated_home):
        # The launchpad cheat-sheet links to
        # `memory.html?file=routing.json&task=<task>`, and the memory viewer reads
        # the FROZEN routing.json from disk. The cheat-sheet is computed LIVE from
        # council_outcomes, so without a freeze at render time the two diverge: a
        # fresh cheat-sheet row's deep-link lands on a "not yet — rate a council"
        # banner for a task the user has ALREADY rated (observed 1/38 on the real
        # corpus). write_portal_html must freeze routing.json so they stay in sync.
        from trinity_local.personal_routing import (
            compute_personal_routing_table,
            invalidate_cache,
        )
        from trinity_local.state_paths import routing_path

        _seed_outcome(isolated_home, council_id="x1", task_type="fresh_task", winner="claude")
        _seed_outcome(isolated_home, council_id="x2", task_type="fresh_task", winner="claude")
        invalidate_cache()
        assert not routing_path().exists()  # no stale frozen file to start

        from trinity_local.launchpad_page import write_portal_html

        write_portal_html()

        assert routing_path().exists(), (
            "write_portal_html did not freeze routing.json — the memory viewer's "
            "cross-link target goes stale vs the live cheat-sheet"
        )
        frozen = json.loads(routing_path().read_text()).get("by_task_type", {})
        live = compute_personal_routing_table().get("by_task_type", {})
        assert set(frozen) == set(live), (
            "frozen routing.json diverges from the live cheat-sheet source — fresh "
            f"cross-links land on the 'not yet' banner. live-not-frozen: {set(live) - set(frozen)}"
        )
        assert "fresh_task" in frozen, "the freshly-rated task_type must reach the frozen file"

    def test_skip_write_when_empty(self, isolated_home):
        """No outcomes on disk → `by_task_type` is empty → no file written.
        (The returned dict still has metadata keys like `computed_at`, but
        without routing signal we'd just be writing a useless file the
        chairman would have to skip.)"""
        from trinity_local.personal_routing import freeze_routing_to_disk, invalidate_cache
        from trinity_local.state_paths import routing_path

        invalidate_cache()
        table = freeze_routing_to_disk()
        assert table.get("by_task_type", {}) == {}
        # Empty file is worse than no file — chairman/distill would emit
        # an empty "ROUTING" header. Skip the write entirely.
        assert not routing_path().exists()

    def test_distill_does_not_read_frozen_routing(self, isolated_home):
        """End-to-end pinning: distill must NOT include ROUTING even when
        routing.json is on disk. routing is a model-selection scoreboard,
        not a cognitive memory — by design it is excluded from core.md."""
        from trinity_local.personal_routing import freeze_routing_to_disk, invalidate_cache
        from trinity_local.distill import build_distill_prompt
        from trinity_local.state_paths import lens_path

        lens_path().write_text("# Lens\n→ leverage over ownership.", encoding="utf-8")
        _seed_outcome(isolated_home, council_id="r1", task_type="code_refactor", winner="codex")
        _seed_outcome(isolated_home, council_id="r2", task_type="code_refactor", winner="codex")
        invalidate_cache()
        freeze_routing_to_disk()

        prompt = build_distill_prompt()
        assert "ROUTING" not in prompt
        assert "codex" not in prompt
