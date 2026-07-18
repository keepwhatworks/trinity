"""Thompson exploration on coin-flip basins (council 2026-07-14). The path
ONLY fires on measured near-ties with fresh evidence — mutation targets:
drop the margin guard (decisive basins would get randomized — RED), drop the
effective_n guard (stale basins would explore — RED), drop the logging (the
instrument goes dark — RED)."""
from __future__ import annotations

import json
import random

from trinity_local.lens_routing import (
    MIN_EFFECTIVE_N,
    WINNER_MARGIN_FLOOR,
    thompson_route,
)


def _entry(margin=0.05, eff=10.0, weights=None):
    return {"winner": "claude", "count": 20, "margin": margin,
            "effective_n": eff, "n_episodes": 20,
            "weights": weights or {"claude": 5.0, "codex": 4.5}}


class TestThompsonGate:
    def test_decisive_basin_never_sampled(self):
        assert thompson_route(_entry(margin=WINNER_MARGIN_FLOOR)) is None
        assert thompson_route(_entry(margin=0.4)) is None

    def test_stale_or_thin_basin_never_sampled(self):
        assert thompson_route(_entry(eff=MIN_EFFECTIVE_N - 0.1)) is None

    def test_legacy_entry_without_weights_declines(self):
        e = _entry()
        del e["weights"]
        assert thompson_route(e) is None

    def test_single_provider_declines(self):
        assert thompson_route(_entry(weights={"claude": 5.0})) is None

    def test_wrong_typed_weights_degrade(self):
        assert thompson_route(_entry(weights="garbled")) is None
        assert thompson_route(_entry(weights={"claude": "x", "codex": None})) is None

    def test_near_tie_samples_a_participating_provider(self):
        rng = random.Random(7)
        picks = {thompson_route(_entry(), rng=rng) for _ in range(50)}
        assert picks <= {"claude", "codex"}
        # a flat posterior must actually EXPLORE — both arms appear over 50 draws
        assert len(picks) == 2

    def test_sharp_posterior_mostly_exploits(self):
        rng = random.Random(7)
        entry = _entry(weights={"claude": 30.0, "codex": 2.0})
        picks = [thompson_route(entry, rng=rng) for _ in range(60)]
        assert picks.count("claude") > 50  # sharp posterior → mostly the leader


class TestAskWiring:
    def test_near_tie_routes_via_thompson_and_logs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        import trinity_local.lens_routing as lr
        from trinity_local.ask import _decide_from_rule

        rule = _entry(margin=0.05, eff=10.0)
        monkeypatch.setattr(lr, "thompson_route", lambda e, rng=None: "codex")
        decision = _decide_from_rule("b07", rule, available_providers=["claude", "codex"])
        assert decision is not None and decision.routed_to == "codex"
        assert "Thompson" in decision.reason or "near-tie" in decision.reason
        log = tmp_path / "analytics" / "exploration_routes.jsonl"
        assert log.exists(), "exploration route was not logged — the instrument is dark"
        row = json.loads(log.read_text().splitlines()[0])
        assert row["basin"] == "b07" and row["sampled"] == "codex"
        # identity-triple stamping (2026-07-14): the row must CARRY the model/
        # effort fields (None allowed when config is absent in the isolated
        # home) — a slug-only row can't answer "which codex" a month later
        assert "model" in row and "effort" in row

    def test_decisive_basin_still_routes_the_winner(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.ask import _decide_from_rule
        rule = _entry(margin=0.4, eff=10.0)
        decision = _decide_from_rule("b07", rule, available_providers=["claude", "codex"])
        assert decision is not None and decision.routed_to == "claude"
        assert not (tmp_path / "analytics" / "exploration_routes.jsonl").exists()


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


class TestClassifyBasins:
    """Single source of truth for the decisive/explored/thin split the
    launchpad card + status liveness line both report (2026-07-17). MUTATION:
    if classify_basins used raw n instead of pick_routes' decayed effective_n,
    the stale-decisive fixture would miscount as decisive → RED; if it dropped
    the thompson_eligible branch, the near-tie would count as thin → RED."""

    def test_split_matches_the_router_predicates(self):
        from trinity_local.lens_routing import (
            classify_basins, WINNER_MARGIN_FLOOR, MIN_EFFECTIVE_N)
        rules = {
            # decisive: margin over floor AND fresh evidence
            "b_dec": _entry(margin=WINNER_MARGIN_FLOOR + 0.1, eff=10.0),
            # explored: measured near-tie WITH fresh evidence + >=2 weights
            "b_exp": _entry(margin=0.05, eff=MIN_EFFECTIVE_N + 1),
            # thin (churn): margin clears the floor but decayed evidence is dead
            "b_stale": _entry(margin=WINNER_MARGIN_FLOOR + 0.1,
                              eff=MIN_EFFECTIVE_N - 0.1),
            # thin (no weights): a near-tie the router can't sample
            "b_nowts": {k: v for k, v in _entry(margin=0.05).items()
                        if k != "weights"},
        }
        s = classify_basins(rules)
        assert s == {"decisive": 1, "explored": 1, "thin": 2, "total": 4}, s

    def test_stale_high_margin_is_not_counted_decisive(self):
        # The exact drift this catches: a raw-n count would call a churn-dead
        # basin decisive (its margin is high); pick_routes demotes it to thin.
        from trinity_local.lens_routing import (
            classify_basins, WINNER_MARGIN_FLOOR, MIN_EFFECTIVE_N)
        s = classify_basins({"b": _entry(margin=WINNER_MARGIN_FLOOR + 0.2,
                                         eff=MIN_EFFECTIVE_N - 0.5)})
        assert s["decisive"] == 0 and s["thin"] == 1

    def test_non_dict_input_abstains(self):
        from trinity_local.lens_routing import classify_basins
        assert classify_basins([]) == {"decisive": 0, "explored": 0,
                                       "thin": 0, "total": 0}

    def test_eligible_mirrors_route(self):
        # thompson_eligible must be True exactly when thompson_route would fire.
        from trinity_local.lens_routing import thompson_eligible
        assert thompson_eligible(_entry(margin=0.05)) is True
        assert thompson_eligible(_entry(margin=0.4)) is False   # decisive
        assert thompson_eligible(_entry(weights={"claude": 5.0})) is False
