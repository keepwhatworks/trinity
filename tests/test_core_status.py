"""Tests for `_core_status()` — the freshness-badge data the launchpad
reads to surface 'distill recommended' hints. Pairs with is_core_stale()
in distill.py (which determines whether to fire the flagship call);
_core_status is the read-side projection for UI."""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


class TestCoreStatus:
    def test_empty_state_when_no_memories(self, isolated_home):
        from trinity_local.launchpad_data import _core_status
        assert _core_status() == {"state": "empty"}

    def test_missing_state_when_memories_exist_but_no_core(self, isolated_home):
        from trinity_local.launchpad_data import _core_status
        from trinity_local.state_paths import lens_path

        lens_path().write_text("# Lens\n→ x.", encoding="utf-8")
        assert _core_status() == {"state": "missing"}

    def test_fresh_state_when_core_newer_than_all_sources(self, isolated_home):
        from trinity_local.launchpad_data import _core_status
        from trinity_local.state_paths import core_path, lens_path

        lens_path().write_text("# Lens\n→ x.", encoding="utf-8")
        time.sleep(0.05)
        core_path().write_text("You ship leverage.", encoding="utf-8")

        assert _core_status() == {"state": "fresh"}

    def test_stale_state_when_a_source_is_newer(self, isolated_home):
        """The case the launchpad badge most cares about — core exists but
        a source memory was just edited (e.g. lens-build ran but auto-
        distill skipped or failed)."""
        from trinity_local.launchpad_data import _core_status
        from trinity_local.state_paths import core_path, lens_path

        core_path().write_text("old paragraph", encoding="utf-8")
        time.sleep(0.05)
        lens_path().write_text("# Lens\n→ newer evidence", encoding="utf-8")

        result = _core_status()
        assert result["state"] == "stale"
        assert result.get("stale_source") == "lens.md"


class TestLaunchpadRendering:
    def test_payload_includes_core_status(self, isolated_home, monkeypatch):
        """build_page_data must surface coreStatus so the template can
        render the freshness hint."""
        from pathlib import Path
        from trinity_local import launchpad_data
        from trinity_local.adapters import AdapterStatus

        # Stub the heavy dependencies so we can test the payload shape.
        monkeypatch.setattr(launchpad_data, "check_all_adapters", lambda: [
            AdapterStatus(provider="claude", cli_name="claude", installed=True),
        ])

        payload = launchpad_data.build_page_data(
            live_review_path=Path("/tmp/x.html"),
            recent_councils=[],
        )
        assert "coreStatus" in payload
        assert payload["coreStatus"]["state"] == "empty"

    def test_stale_badge_renders_when_state_stale(self, isolated_home, monkeypatch):
        from trinity_local.launchpad_page import write_portal_html
        from trinity_local.state_paths import core_path, lens_path

        # Stale state: core older than lens.
        core_path().write_text("old", encoding="utf-8")
        time.sleep(0.05)
        lens_path().write_text("# Lens\n→ x", encoding="utf-8")

        # Need at least one rated council so the Routing card renders
        # (the hint is INSIDE the personal-routing card).
        # Stub the routing-table loader so we don't need real outcomes.
        monkeypatch.setattr(
            "trinity_local.launchpad_data._load_personal_routing_table",
            lambda: {"councils_aggregated": 5,
                     "best_per_task_type": {"coding": "claude"},
                     "by_task_type": {"coding": {"claude": {"overall": 8.0, "n": 5}}}},
        )

        path = write_portal_html(title="Test")
        html = path.read_text(encoding="utf-8")
        # Stale-state hint copy must appear (anchor on the unique phrase
        # so we're not just matching the verb "stale" elsewhere).
        assert "core.md</code> is stale" in html


class TestCoreFreshnessSurfacesAgree:
    """v1.7.301 de-dup guard: core-vs-sources staleness is computed in ONE place
    now — `distill.core_freshness()`. The launchpad cockpit (`_core_status`), the
    CLI/dream skip-gate (`distill.is_core_stale`), and `trinity-local status`'s
    memory marker all derive from it. This locks the three to the same answer so a
    future change to the source list or the mtime rule can't make `status` say
    'fresh' while the cockpit says 'stale' (they used to inline independent copies
    coupled only by a 'must match distill.py' comment). Live 2026-06-02 all three
    reported stale/lens.md on the real corpus."""

    def _build(self, isolated_home, state):
        """Materialize one of the 4 states; return the home."""
        from trinity_local.state_paths import core_path, lens_path
        if state == "empty":
            return  # no files
        if state == "missing":
            lens_path().write_text("# Lens\n→ x", encoding="utf-8")
            return
        if state == "fresh":
            lens_path().write_text("# Lens\n→ x", encoding="utf-8")
            time.sleep(0.05)
            core_path().write_text("core", encoding="utf-8")
            return
        if state == "stale":
            core_path().write_text("core", encoding="utf-8")
            time.sleep(0.05)
            lens_path().write_text("# Lens\n→ newer", encoding="utf-8")
            return

    @pytest.mark.parametrize("state", ["empty", "missing", "fresh", "stale"])
    def test_cockpit_matches_distill_primitive(self, isolated_home, state):
        from trinity_local.distill import core_freshness
        from trinity_local.launchpad_data import _core_status
        self._build(isolated_home, state)
        primitive = core_freshness()
        assert _core_status() == primitive, f"cockpit drifted from distill.core_freshness for {state}"
        assert primitive["state"] == state

    @pytest.mark.parametrize("state,expected_stale", [
        ("empty", False), ("missing", True), ("fresh", False), ("stale", True),
    ])
    def test_is_core_stale_derives_from_the_same_primitive(self, isolated_home, state, expected_stale):
        from trinity_local.distill import is_core_stale
        self._build(isolated_home, state)
        assert is_core_stale() is expected_stale, (
            f"is_core_stale()={is_core_stale()} but state={state} (skip-gate drifted)"
        )
