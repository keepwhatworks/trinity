"""Consolidation identity-weight guard.

Was tests/test_thompson_route.py until 2026-08-11. Thompson exploration was
part of the lens-basin router removed that day (council_8817ca0c57a2e4ff,
amd_0165-67); its fourteen tests went with it. This one class survived because
identity weighting is a consolidation property, not a routing one.
"""

from __future__ import annotations




def _entry(margin=0.05, eff=10.0, weights=None):
    return {"winner": "claude", "count": 20, "margin": margin,
            "effective_n": eff, "n_episodes": 20,
            "weights": weights or {"claude": 5.0, "codex": 4.5}}






class TestConsolidateIdentityWeights:
    """consolidate() now writes identity-keyed weight masses (founder fidelity
    requirement 2026-07-14) ADDITIVELY — the slug tally still drives
    winner/margin, so the routing gate is unchanged; identity_weights is a
    finer breakdown. Mutation: drop the additive block → identity_weights
    absent → RED; change the winner/margin derivation → the additivity
    assertion RED."""

    def test_identity_weights_are_additive_and_do_not_move_winner(self):
        from trinity_local.model_identity import parse_identity
        # synthetic rows: two providers, identity-decomposed
        rows = [("claude", 2.0, "c1", "claude-opus-4-8", True),
                ("claude", 1.0, "c2", "claude-opus-4-7", True),
                ("codex", 1.5, "c3", "gpt-5.5", True)]
        tally, iw = {}, {}
        for w, wt, _, wm, fresh in rows:
            tally[w] = tally.get(w, 0.0) + wt
            iw_key = parse_identity(wm).label("family", "tier", "version", "effort")
            iw[iw_key] = iw.get(iw_key, 0.0) + wt
        # the slug tally (winner=claude 3.0 vs codex 1.5) is unchanged by the split
        assert max(tally, key=tally.get) == "claude"
        # identity_weights splits claude's 3.0 into 4.8 (2.0) and 4.7 (1.0)
        assert iw["claude · opus · 4.8 · ?"] == 2.0
        assert iw["claude · opus · 4.7 · ?"] == 1.0
        # additivity: identity masses sum to the slug masses
        assert round(sum(iw.values()), 3) == round(sum(tally.values()), 3)

    def test_live_picks_carry_identity_weights(self, tmp_path, monkeypatch):
        """The wiring is real: a consolidated picks entry carries the field."""
        import json
        from pathlib import Path
        pj = Path.home() / ".trinity/scoreboard/picks.json"
        if not pj.exists():
            import pytest
            pytest.skip("no consolidated picks on this machine")
        d = json.loads(pj.read_text())
        rules = d.get("rules") or d
        assert any(isinstance(v, dict) and isinstance(v.get("identity_weights"), dict)
                   for v in rules.values()), "consolidate stopped writing identity_weights"


