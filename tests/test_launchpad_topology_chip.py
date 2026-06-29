"""Tick #34 — launchpad recent-card → topology chip.

The card already had → pick and → routing chips (tick #15). The
third chip → topology closes the loop from the launchpad directly,
sparing the bounce through picks. The Python-side centroid match
must agree with the JS-side match in memory_viewer (same threshold,
same first-task-wins rule) — these tests guard the Python half.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_topics(home: Path, basins: list[dict]) -> None:
    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": basins}), encoding="utf-8"
    )


def _seed_picks(home: Path, patterns: dict) -> None:
    # cortex.load_routing_patterns reads from picks_path() which today
    # resolves to ~/.trinity/scoreboard/picks.json (the cortex_routing_
    # patterns_path is a back-compat alias).
    (home / "scoreboard").mkdir(parents=True, exist_ok=True)
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps(patterns), encoding="utf-8"
    )


class TestTaskToTopologyBasin:
    """POST-COLLAPSE (#298): the routing picks ARE keyed by the lens basin id
    (b00..), the SAME id space topics.json uses, so the picks→topology bridge is
    a plain identity match (a pick links to the topology basin of the same id).
    These pin the contract: identity, presence-gating, graceful degradation."""

    def test_cold_install_returns_empty(self, isolated_home):
        from trinity_local.launchpad_data import _task_to_topology_basin
        # No topics.json, no picks → empty map (must NOT raise).
        assert _task_to_topology_basin() == {}

    def test_no_picks_returns_empty(self, isolated_home):
        # Topics exist but no picks → still empty.
        _seed_topics(isolated_home, [
            {"id": "b00", "centroid": [1.0, 0.0, 0.0]},
        ])
        from trinity_local.launchpad_data import _task_to_topology_basin
        assert _task_to_topology_basin() == {}

    def test_match_returns_basin_for_aligned_centroid(self, isolated_home):
        # A pick keyed b00 bridges to the topology basin b00 (identity).
        _seed_topics(isolated_home, [
            {"id": "b00", "centroid": [1.0, 0.0, 0.0]},
            {"id": "b01", "centroid": [0.0, 1.0, 0.0]},
        ])
        _seed_picks(isolated_home, {
            "b00": _minimal_pattern_payload("b00"),
        })
        from trinity_local.launchpad_data import _task_to_topology_basin
        result = _task_to_topology_basin()
        assert result == {"b00": "b00"}

    def test_pick_basin_absent_from_topics_drops_match(self, isolated_home):
        # A pick whose basin id has no corresponding topology basin → no bridge.
        _seed_topics(isolated_home, [
            {"id": "b00", "centroid": [1.0, 0.0, 0.0]},
            {"id": "b01", "centroid": [0.0, 1.0, 0.0]},
        ])
        _seed_picks(isolated_home, {
            "b99": _minimal_pattern_payload("b99"),  # not in topics
        })
        from trinity_local.launchpad_data import _task_to_topology_basin
        assert _task_to_topology_basin() == {}

    def test_malformed_pick_without_winner_skipped(self, isolated_home):
        # A legacy/malformed pick (no `winner`) does not bridge even if its
        # basin id matches a topology basin.
        _seed_topics(isolated_home, [
            {"id": "b00", "centroid": [1.0, 0.0, 0.0]},
        ])
        _seed_picks(isolated_home, {
            "b00": {"count": 5, "margin": 0.8},  # no winner
        })
        from trinity_local.launchpad_data import _task_to_topology_basin
        assert _task_to_topology_basin() == {}

    def test_multiple_picks_each_bridge_their_own_basin(self, isolated_home):
        # Each live pick bridges to the topology basin of the same id.
        _seed_topics(isolated_home, [
            {"id": "b00", "centroid": [1.0, 0.0, 0.0]},
            {"id": "b01", "centroid": [0.0, 1.0, 0.0]},
        ])
        _seed_picks(isolated_home, {
            "b00": _minimal_pattern_payload("b00"),
            "b01": _minimal_pattern_payload("b01"),
        })
        from trinity_local.launchpad_data import _task_to_topology_basin
        result = _task_to_topology_basin()
        assert result == {"b00": "b00", "b01": "b01"}


class TestCortexCardTopologyAnnotation:
    """The launchpad picks card carries a → topology link per pick when that
    pick's basin id exists in topics.json (post-collapse #298 the routing basins
    ARE the topology basins). Annotation happens in _load_cortex_rules so the Vue
    template just reads r.topology_basin."""

    def test_topology_basin_attached_when_match(self, isolated_home, monkeypatch):
        from trinity_local import launchpad_data
        # Stub the bridge so the test doesn't need to populate topics.json —
        # we're testing the annotation, not the identity match (covered above).
        monkeypatch.setattr(
            launchpad_data,
            "_task_to_topology_basin",
            lambda: {"b07": "b07"},
        )
        pick = {"winner": "claude", "count": 5, "margin": 0.7,
                "n_episodes": 5, "evidence": []}
        import trinity_local.cortex
        monkeypatch.setattr(
            trinity_local.cortex, "load_routing_patterns", lambda: {"b07": pick}
        )
        from trinity_local.launchpad_data import _load_cortex_rules
        payload = _load_cortex_rules()
        assert payload is not None, "picks payload is None"
        assert payload["rules"], "no rules in payload"
        row = next(r for r in payload["rules"] if r["basin_id"] == "b07")
        assert row.get("topology_basin") == "b07", (
            f"topology_basin not annotated; got {row.get('topology_basin')!r}"
        )

    def test_topology_basin_absent_when_no_match(self, isolated_home, monkeypatch):
        from trinity_local import launchpad_data
        monkeypatch.setattr(launchpad_data, "_task_to_topology_basin", lambda: {})
        pick = {"winner": "claude", "count": 1, "margin": 0.5,
                "n_episodes": 1, "evidence": []}
        import trinity_local.cortex
        monkeypatch.setattr(
            trinity_local.cortex, "load_routing_patterns", lambda: {"b09": pick}
        )
        from trinity_local.launchpad_data import _load_cortex_rules
        payload = _load_cortex_rules()
        rule_row = payload["rules"][0]
        assert "topology_basin" not in rule_row, (
            f"topology_basin should be absent when no match; got {rule_row.get('topology_basin')!r}"
        )


class TestTopicsBasinsCache:
    """Tick #42 — `_load_topics_basins` caches the parsed payload
    keyed on file mtime. One launchpad render previously parsed
    topics.json 4× (cortex card + recent-card + each topology
    helper); the cache collapses those into 1 read per file
    version. Invalidates when the file's mtime changes."""

    def test_cache_hits_on_second_call(self, isolated_home, monkeypatch):
        import json
        (isolated_home / "memories" / "topics.json").write_text(
            json.dumps({"basins": [
                {"id": "b00", "top_terms": ["foo"], "centroid": [1.0, 0.0]},
            ]}),
            encoding="utf-8",
        )
        # Reset module-level cache so a previous test's residue can't
        # mask the assertion. Module attribute approach matches the
        # implementation; direct attribute reset is the only way.
        import trinity_local.launchpad_data as lpd
        lpd._TOPICS_BASINS_CACHE = None

        # Patch json.loads to count parses through the helper.
        original_loads = json.loads
        call_count = {"n": 0}
        def counting_loads(s):
            call_count["n"] += 1
            return original_loads(s)
        monkeypatch.setattr(lpd.json, "loads", counting_loads)

        # Two consecutive calls — second should hit the cache.
        b1 = lpd._load_topics_basins()
        b2 = lpd._load_topics_basins()
        assert b1 == b2, "cache returned divergent result"
        assert call_count["n"] == 1, (
            f"expected 1 parse (cache hit on 2nd call), got {call_count['n']}"
        )

    def test_cache_invalidates_on_mtime_change(self, isolated_home, monkeypatch):
        import json, os, time
        topics_p = isolated_home / "memories" / "topics.json"
        topics_p.write_text(
            json.dumps({"basins": [{"id": "b00", "top_terms": ["foo"]}]}),
            encoding="utf-8",
        )
        import trinity_local.launchpad_data as lpd
        lpd._TOPICS_BASINS_CACHE = None

        first = lpd._load_topics_basins()
        assert first[0]["id"] == "b00"

        # Rewrite the file with a different mtime — must invalidate.
        topics_p.write_text(
            json.dumps({"basins": [{"id": "b99", "top_terms": ["bar"]}]}),
            encoding="utf-8",
        )
        # Force a different mtime in case the test runs sub-second.
        future = time.time() + 5
        os.utime(topics_p, (future, future))

        second = lpd._load_topics_basins()
        assert second[0]["id"] == "b99", (
            f"cache did not invalidate on mtime bump; got {second[0]['id']}"
        )

    def test_cache_keyed_on_path_not_just_mtime(self, tmp_path, monkeypatch):
        """Tick #43 — bug fix: cache key was just mtime, so two
        isolated_home dirs with same second-level mtime could leak
        cached basins across each other. Now keyed on (path, mtime)
        so different homes get different cache slots."""
        import json, os
        import trinity_local.launchpad_data as lpd
        lpd._TOPICS_BASINS_CACHE = None

        home_a = tmp_path / "home_a"
        home_b = tmp_path / "home_b"
        (home_a / "memories").mkdir(parents=True)
        (home_b / "memories").mkdir(parents=True)

        topics_a = home_a / "memories" / "topics.json"
        topics_b = home_b / "memories" / "topics.json"
        topics_a.write_text(json.dumps({"basins": [{"id": "from_A"}]}), encoding="utf-8")
        topics_b.write_text(json.dumps({"basins": [{"id": "from_B"}]}), encoding="utf-8")
        # Force identical mtimes so only the path differentiates the cache.
        shared_time = topics_a.stat().st_mtime
        os.utime(topics_b, (shared_time, shared_time))

        monkeypatch.setenv("TRINITY_HOME", str(home_a))
        first = lpd._load_topics_basins()
        assert first[0]["id"] == "from_A"

        monkeypatch.setenv("TRINITY_HOME", str(home_b))
        second = lpd._load_topics_basins()
        assert second[0]["id"] == "from_B", (
            f"cache leaked across homes; got {second[0]['id']!r} "
            "(should be 'from_B' since we switched TRINITY_HOME)"
        )

    def test_missing_file_clears_stale_cache(self, isolated_home):
        import json
        topics_p = isolated_home / "memories" / "topics.json"
        topics_p.write_text(json.dumps({"basins": [{"id": "b00"}]}), encoding="utf-8")
        import trinity_local.launchpad_data as lpd
        lpd._TOPICS_BASINS_CACHE = None
        assert lpd._load_topics_basins(), "first read should populate cache"
        # If the file disappears (e.g. user clears state), the helper
        # must return [] AND drop the cached payload so a re-add gets
        # picked up by the next call.
        topics_p.unlink()
        assert lpd._load_topics_basins() == []
        assert lpd._TOPICS_BASINS_CACHE is None, (
            "missing file should clear the cache, not keep stale data"
        )


# TestRecentCardTopologyTooltip + TestRecentCardTopologyChip removed
# 2026-05-21 in commit e5ef20c. The recent-card cross-memory chip strip
# (→ pick / → routing / → topology + PICK PNG / ROUTING PNG / SHARE
# PNG share buttons) was deleted in commit 8f1fd95 per user direction
# "what are the buttons under it doing? remove them". The data layer
# the topology chip read from (_task_to_topology_basin,
# _topology_basin_labels) still exists and is exercised by
# TestTaskToTopologyBasin + TestTopicsBasinsCache above — those keep
# the underlying mechanism guarded. Only the now-deleted chip surface
# is sunset.


def _minimal_pattern_payload(basin_id: str) -> dict:
    """A post-collapse (#298) picks.json entry — the flat lens-basin tally."""
    return {
        "winner": "claude",
        "count": 5,
        "margin": 0.7,
        "n_episodes": 5,
        "evidence": [],
    }
