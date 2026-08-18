"""Guards for picks.json orphan pruning (2026-07-31).

picks.json is keyed by lens-basin id; basin ids are POSITIONAL (`b{i:02d}`
after a size sort in `me/basins.py`) and re-drawn on every lens build, while
topics.json and picks.json are rebuilt by two independent kicks with no
ordering guarantee. Every lens build that lands between consolidates therefore
leaves rules pointing at ids that no longer exist.

Measured on the founder's corpus 2026-07-31: 31 rules, 65 live basins, 6
orphans (`b01c b01d b07a b07b b07c b08b`) — and one of them, `b01d`
(margin 0.35, effective_n 3.06), CLEARED the routing gate. An unreachable rule
counted as a route is the repo's signature bug: a green computed over data that
cannot participate.

`pick_routes` is deliberately untouched by all of this. It admitted 4 of 31
rules under `margin >= 0.15 AND effective_n >= 3` before this change and must
keep doing exactly that — the gate is working as designed; orphan-ness is a
separate axis, applied by the CALLER that knows the live topology.
"""
from __future__ import annotations

from trinity_local.me import basins as _mb

import json


def _entry(margin: float = 0.4, effective_n: float = 5.0, winner: str = "claude") -> dict:
    return {
        "winner": winner,
        "count": 6,
        "margin": margin,
        "n_episodes": 6,
        "effective_n": effective_n,
        "weights": {"claude": 4.0, "codex": 1.0},
    }


class TestPruneOrphanRules:
    def test_drops_only_the_dead_keys(self):
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {"b00": _entry(), "b01": _entry(), "bZZ": _entry(), "bYY": _entry()}
        live = {"b00", "b01", "b02", "b03", "b04", "b05"}
        kept, dropped, reason = prune_orphan_rules(rules, live)
        assert set(kept) == {"b00", "b01"}
        assert dropped == ["bYY", "bZZ"]
        assert "pruned 2 orphan" in reason

    def test_clean_store_is_a_no_op_and_says_so(self):
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {"b00": _entry(), "b01": _entry()}
        live = {"b00", "b01", "b02", "b03", "b04", "b05"}
        kept, dropped, reason = prune_orphan_rules(rules, live)
        assert kept == rules
        assert dropped == []
        assert "no orphans" in reason

    def test_kept_entries_are_byte_identical(self):
        """Pruning must delete keys and change nothing else — the surviving
        tallies are the chairman's accumulated picks."""
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {"b00": _entry(margin=0.31), "bZZ": _entry()}
        kept, _, _ = prune_orphan_rules(rules, {"b00", "b1", "b2", "b3", "b4", "b5"})
        assert kept["b00"] == rules["b00"]


class TestPruneRefusesOnDegenerateInput:
    """Pruning is a DELETE against the only supervision signal in the product.
    Every refusal path below must keep the rules intact AND report zero drops —
    a prune that returns "0 dropped, all clean" off a failed topology read would
    be the green-over-degenerate this guard exists to prevent."""

    def test_refuses_when_no_live_basins(self):
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {"b00": _entry(), "bZZ": _entry()}
        kept, dropped, reason = prune_orphan_rules(rules, set())
        assert kept == rules
        assert dropped == []
        assert "REFUSED" in reason

    def test_refuses_when_basin_ids_is_none(self):
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {"b00": _entry()}
        kept, dropped, reason = prune_orphan_rules(rules, None)
        assert kept == rules and dropped == [] and "REFUSED" in reason

    def test_refuses_below_the_min_basins_floor(self):
        from trinity_local.lens_routing import MIN_BASINS_FOR_PRUNE, prune_orphan_rules

        live = {f"b{i:02d}" for i in range(MIN_BASINS_FOR_PRUNE - 1)}
        rules = {"b00": _entry(), "bZZ": _entry()}
        kept, dropped, reason = prune_orphan_rules(rules, live)
        assert kept == rules
        assert dropped == []
        assert "MIN_BASINS_FOR_PRUNE" in reason and "REFUSED" in reason

    def test_refuses_a_whole_scheme_change(self):
        """More than MAX_ORPHAN_DROP_FRACTION orphaned means the id scheme
        changed, not that a few rules went stale. Deleting most of the store
        on that reading would silently destroy the tally."""
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {f"old{i}": _entry() for i in range(9)}
        rules["b00"] = _entry()
        live = {f"b{i:02d}" for i in range(10)}
        kept, dropped, reason = prune_orphan_rules(rules, live)
        assert kept == rules
        assert dropped == []
        assert "MAX_ORPHAN_DROP_FRACTION" in reason and "REFUSED" in reason

    def test_at_the_fraction_boundary_it_still_prunes(self):
        """Exactly at the floor is allowed; only ABOVE it refuses. Pins the
        comparison direction so a `>=`/`>` slip can't quietly disable pruning."""
        from trinity_local.lens_routing import prune_orphan_rules

        rules = {"b00": _entry(), "b01": _entry(), "x0": _entry(), "x1": _entry()}
        live = {f"b{i:02d}" for i in range(8)}
        kept, dropped, _ = prune_orphan_rules(rules, live)
        assert dropped == ["x0", "x1"]
        assert set(kept) == {"b00", "b01"}


class TestPickRoutesGateUnchanged:
    """The task's hard constraint: the margin>=0.15 AND effective_n>=3 gate must
    keep behaving exactly as before. Orphan handling is a separate axis."""

    def test_gate_thresholds_are_unchanged(self):
        from trinity_local.lens_routing import (
            MIN_EFFECTIVE_N,
            WINNER_MARGIN_FLOOR,
            pick_routes,
        )

        assert WINNER_MARGIN_FLOOR == 0.15
        assert MIN_EFFECTIVE_N == 3.0
        assert pick_routes({"winner": "claude", "margin": 0.4, "effective_n": 3.0})
        assert not pick_routes({"winner": "claude", "margin": 0.14, "effective_n": 9.0})
        assert not pick_routes({"winner": "claude", "margin": 0.4, "effective_n": 2.99})
        assert pick_routes({"winner": "claude", "margin": 0.4})  # legacy, no effective_n

    def test_pick_routes_still_ignores_basin_identity(self):
        """`pick_routes` takes an ENTRY, not an id. It must not start knowing
        about topologies — the two concerns stay separable."""
        import inspect

        from trinity_local.lens_routing import pick_routes

        params = list(inspect.signature(pick_routes).parameters)
        assert params == ["entry"]


class TestClassifyBasinsOrphanClass:
    def test_orphan_key_is_omitted_when_no_basin_set_supplied(self):
        """Not looked at must not read as zero. Also keeps the default return
        shape byte-identical for every existing caller."""
        from trinity_local.lens_routing import classify_basins

        out = classify_basins({"b00": _entry()})
        assert "orphan" not in out
        assert out == {"decisive": 1, "explored": 0, "thin": 0, "total": 1}

    def test_orphans_are_not_counted_as_decisive(self):
        """The live failure: a gate-passing rule keyed to a redrawn basin was
        being reported as a route it could never serve."""
        from trinity_local.lens_routing import classify_basins

        rules = {"b00": _entry(), "bDEAD": _entry()}
        out = classify_basins(rules, {"b00", "b01"})
        assert out == {"decisive": 1, "explored": 0, "thin": 0, "orphan": 1, "total": 2}

    def test_non_dict_rules_degrade_consistently(self):
        from trinity_local.lens_routing import classify_basins

        assert classify_basins([]) == {"decisive": 0, "explored": 0, "thin": 0, "total": 0}
        assert classify_basins([], {"b00"}) == {
            "decisive": 0, "explored": 0, "thin": 0, "total": 0, "orphan": 0,
        }


class TestLiveBasinIds:
    def test_empty_set_when_topics_missing(self, tmp_path, monkeypatch):

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(_mb, "_TOPICS_BASINS_CACHE", None, raising=False)
        assert _mb.live_basin_ids() == set()

    def test_reads_ids_from_topics(self, tmp_path, monkeypatch):

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(_mb, "_TOPICS_BASINS_CACHE", None, raising=False)
        memories = tmp_path / "memories"
        memories.mkdir(parents=True)
        (memories / "topics.json").write_text(
            json.dumps({"basins": [{"id": "b00"}, {"id": "b01"}, {"nope": 1}]}),
            encoding="utf-8",
        )
        assert _mb.live_basin_ids() == {"b00", "b01"}






class TestNoStableRekeyingWasSmuggledIn:
    """The stable-keying assessment was DECLINED on measured evidence (median
    membership Jaccard 0.000 across re-clusterings; winner agreement on shared
    ids indistinguishable from chance, p=0.48). The rationale and the
    pre-registered reopen condition live in `lens_routing`'s docstring — pin
    them so a future re-key lands next to the measurement that killed it
    rather than replacing it silently."""

    def test_rationale_and_reopen_condition_are_documented(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent
            / "src" / "trinity_local" / "lens_routing.py"
        ).read_text(encoding="utf-8")
        assert "PRE-REGISTERED REOPEN CONDITION" in src
        assert "Jaccard" in src

    def test_no_centroid_rekeying_helper_exists(self):
        import trinity_local.lens_routing as lr

        smuggled = [
            n for n in dir(lr)
            if "rekey" in n.lower() or "re_key" in n.lower() or "stable_key" in n.lower()
        ]
        assert not smuggled, (
            f"a centroid re-keying helper appeared ({smuggled}) — see the "
            "reopen condition in lens_routing.py before reviving this"
        )
