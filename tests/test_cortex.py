"""Tests for the routing-scoreboard I/O in cortex.py (#298 cortex collapse).

The v1.5 cortex ENGINE — the 6-component trust score, the LLM flagship
extractor, the chairman `--audit` pass, the SEPARATE cortex `basin_centroid`,
and `consolidate_basin` / `consolidate_all` — was deleted (council
`council_0dd6ee69698d620b`): routing is now a deterministic per-lens-basin
chairman-winner tally (see `lens_routing.py` + `test_lens_routing.py`). What
survives here is the picks-store I/O: load/save the flat lens-basin tally
`{basin_id: {winner, count, margin, n_episodes, evidence}}`, with the #194
clobber guard, plus the doctor/cockpit staleness primitives.
"""
from __future__ import annotations

import json

from trinity_local.cortex import (
    load_routing_patterns,
    save_routing_patterns,
)


def _pick(winner: str, *, count: int = 4, margin: float = 0.5, evidence=None) -> dict:
    """A post-collapse picks.json entry — the flat lens-basin tally."""
    return {
        "winner": winner,
        "count": count,
        "margin": margin,
        "n_episodes": count,
        "evidence": list(evidence or []),
    }


# ──────────────────────────────────────────────────────────────────────────────
# load/save round-trip — the NEW flat lens-basin tally schema.
# ──────────────────────────────────────────────────────────────────────────────

class TestLoadSaveRoundtrip:
    def test_round_trip_preserves_pick(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        picks = {
            "b00": _pick("claude", count=6, margin=0.42, evidence=["c1", "c2"]),
            "b01": _pick("codex", count=3, margin=0.2),
        }
        save_routing_patterns(picks)
        loaded = load_routing_patterns()
        assert set(loaded.keys()) == {"b00", "b01"}
        assert loaded["b00"]["winner"] == "claude"
        assert loaded["b00"]["count"] == 6
        assert loaded["b00"]["margin"] == 0.42
        assert loaded["b00"]["evidence"] == ["c1", "c2"]
        assert loaded["b01"]["winner"] == "codex"

    def test_load_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        assert load_routing_patterns() == {}

    def test_load_wrong_top_level_type_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.state_paths import cortex_routing_patterns_path

        cortex_routing_patterns_path().write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        # A list (not a dict) is corrupted-but-parseable wrong shape → no patterns.
        assert load_routing_patterns() == {}

    def test_load_skips_non_dict_entries(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.state_paths import cortex_routing_patterns_path

        cortex_routing_patterns_path().write_text(
            json.dumps({"good": _pick("claude"), "bad": [1, 2]}),
            encoding="utf-8",
        )
        loaded = load_routing_patterns()
        # The list-valued entry is dropped; the dict entry survives. (A legacy
        # RoutingPattern dict would also survive load — it's a dict — but yields
        # no `winner`, so every reader skips it.)
        assert "good" in loaded
        assert "bad" not in loaded
