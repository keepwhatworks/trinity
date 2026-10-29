"""The launchpad's topics.json basin cache (`_load_topics_basins`).

This file also held the picks.json -> topology bridge tests
(`_task_to_topology_basin`) until that bridge was retired 2026-10-26 with no caller.
"""
from __future__ import annotations

from trinity_local.me import basins as _mb

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    return tmp_path


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
        _mb._TOPICS_BASINS_CACHE = None

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
        _mb._TOPICS_BASINS_CACHE = None

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
        _mb._TOPICS_BASINS_CACHE = None

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
        _mb._TOPICS_BASINS_CACHE = None
        assert lpd._load_topics_basins(), "first read should populate cache"
        # If the file disappears (e.g. user clears state), the helper
        # must return [] AND drop the cached payload so a re-add gets
        # picked up by the next call.
        topics_p.unlink()
        assert lpd._load_topics_basins() == []
        assert _mb._TOPICS_BASINS_CACHE is None, (
            "missing file should clear the cache, not keep stale data"
        )
