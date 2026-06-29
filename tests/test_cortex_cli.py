"""Tests for the `consolidate` CLI handler in commands/cortex.py.

POST-COLLAPSE (#298): `consolidate` is an LLM-FREE pass — it places each
real-contest council into its nearest lens basin and tallies the recency-weighted
chairman winner, via `lens_routing.consolidate_via_lens_basins`. The engine itself
is covered by test_lens_routing.py; these tests pin the CLI's branches
(empty-state, dry-run shape, write, the #194 clobber guard) by injecting a known
routing dict so they don't depend on the real embedder. The old gating flags
(`--audit` / `--audit-provider` / `--min-basin-size` / `--basin` / `--provider`)
were removed with the cortex engine.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import trinity_local.lens_routing as lens_routing


def _args(**overrides) -> SimpleNamespace:
    base = {"dry_run": False}
    base.update(overrides)
    return SimpleNamespace(**base)


def _routing(**winners) -> dict:
    """Build a routing dict in the post-collapse picks schema."""
    return {
        bid: {"winner": w, "count": 4, "margin": 0.5, "n_episodes": 4, "evidence": []}
        for bid, w in winners.items()
    }


class TestConsolidateCLI:
    def test_no_routable_basins_returns_zero_with_reason(self, tmp_path, monkeypatch, capsys):
        from trinity_local.commands.cortex import handle_consolidate

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # No lens / no councils → the engine returns {} → exit 0 (valid no-op).
        monkeypatch.setattr(lens_routing, "consolidate_via_lens_basins", lambda: {})
        rc = handle_consolidate(_args())
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["ok"] is False
        assert "no routable lens basins" in payload["reason"]

    def test_dry_run_reports_winners_without_writing(self, tmp_path, monkeypatch, capsys):
        from trinity_local.commands.cortex import handle_consolidate
        from trinity_local.state_paths import cortex_routing_patterns_path

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(
            lens_routing,
            "consolidate_via_lens_basins",
            lambda: _routing(b00="claude", b01="codex"),
        )
        rc = handle_consolidate(_args(dry_run=True))
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["mode"] == "dry-run"
        assert payload["routable_basins"] == 2
        assert payload["winners"] == {"b00": "claude", "b01": "codex"}
        # Dry-run must NOT write picks.json.
        assert not cortex_routing_patterns_path().exists()

    def test_write_persists_new_schema_picks(self, tmp_path, monkeypatch, capsys):
        from trinity_local.commands.cortex import handle_consolidate
        from trinity_local.cortex import load_routing_patterns

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(
            lens_routing,
            "consolidate_via_lens_basins",
            lambda: _routing(b00="claude", b01="codex"),
        )
        rc = handle_consolidate(_args())
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["ok"] is True
        assert payload["routable_basins"] == 2
        # picks.json now holds the flat lens-basin tally (no trust_score / centroid).
        loaded = load_routing_patterns()
        assert loaded["b00"]["winner"] == "claude"
        assert loaded["b01"]["winner"] == "codex"
        assert "trust_score" not in loaded["b00"]
        assert "basin_centroid" not in loaded["b00"]

    def test_clobber_guard_refuses_cliff_drop(self, tmp_path, monkeypatch, capsys):
        """The #194 clobber guard: a consolidation that produces a cliff-drop
        (few/no basins where many existed) must not erase the live picks store —
        the CLI surfaces the DegenerateExtractionError as exit 1, not a wipe."""
        from trinity_local.commands.cortex import handle_consolidate
        from trinity_local.cortex import load_routing_patterns, save_routing_patterns

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # Seed a populated picks store (12 basins → cliff-drop floor int(12*0.25)=3).
        save_routing_patterns(
            _routing(**{f"b{i:02d}": "claude" for i in range(12)}), allow_shrink=True
        )
        # The engine now yields a single basin (1 < floor 3) → cliff-drop.
        monkeypatch.setattr(
            lens_routing, "consolidate_via_lens_basins", lambda: _routing(b00="codex")
        )
        rc = handle_consolidate(_args())
        payload = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert payload["ok"] is False
        assert "DegenerateExtractionError" in payload["reason"]
        # Live picks preserved — the 12 basins are intact.
        assert len(load_routing_patterns()) == 12
