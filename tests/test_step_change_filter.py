"""Guards for the step-change filter (amd_0159).

A tactic reaches burn-once CONFIRM only on a STEP (>=20% relative over the
incumbent), never a margin. Adopted on this arc's own record: of seven marginal
effects, six were narrowed, killed or tied to a control, and every confound
found on 2026-08-10 surfaced inside a marginal gain.

These tests are the mechanism, not the policy — they assert the filter REFUSES
the band where artifacts concentrate, and that it says so out loud rather than
dropping tactics silently.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def sweep(request):
    """Import the harness with the path scoped to this fixture.

    A module-level sys.path.insert leaks into every subsequent test in the run
    (pytest imports all modules at collection, before anything executes) and the
    repo has a guard against exactly that — it creates invisible armor, where a
    later test passes only because shared state was poisoned. Caught by
    test_no_module_level_env_mutation on the first full run of this file.
    """
    import sys
    exp = str(Path(__file__).resolve().parent.parent / "internal" / "experiments")
    added = exp not in sys.path
    if added:
        sys.path.insert(0, exp)
    try:
        import tactic_sweep
        yield tactic_sweep
    finally:
        if added and exp in sys.path:
            sys.path.remove(exp)


class TestStepFloor:
    def test_floor_is_twenty_percent(self, sweep):
        assert sweep.STEP_FLOOR == 0.20

    @pytest.mark.parametrize("name,gain", [
        ("neural model class", 0.582),
        ("exactly at the floor", 0.20),
        ("far above", 1.5),
    ])
    def test_step_changes_carry(self, sweep, name, gain):
        assert sweep._is_step_change(gain) is True, name

    @pytest.mark.parametrize("name,gain", [
        ("shingle (lzma-only, NARROWED)", 0.18),
        ("overlapping cover (KILLED)", 0.094),
        ("surface_register_fusion (NARROWED)", 0.082),
        ("coherent context", 0.054),
        ("basin skin (ties random)", 0.0009),
        ("just under the floor", 0.199),
        ("zero", 0.0),
        ("negative", -0.3),
    ])
    def test_marginal_gains_do_not_carry(self, sweep, name, gain):
        """Every one of these is a real measured effect from this arc, and six of
        the seven were narrowed, killed or tied. The filter must refuse them."""
        assert sweep._is_step_change(gain) is False, name

    def test_the_arcs_whole_history_partitions_correctly(self, sweep):
        """The rule was adopted BECAUSE of this table; the table is the test."""
        measured = {
            "shingle": (0.18, "NARROWED"),
            "surface_register_fusion": (0.082, "NARROWED"),
            "overlapping_cover": (0.094, "KILLED"),
            "basin_skin": (0.0009, "TIED"),
            "lens_schema": (0.0132, "INCONCLUSIVE"),
            "memory_import": (0.079, "LOSES_TO_RAW"),
            "coherent_context": (0.054, "HELD"),
            "neural_model_class": (0.582, "HELD"),
        }
        carried = {k for k, (g, _) in measured.items() if sweep._is_step_change(g)}
        assert carried == {"neural_model_class"}, (
            "exactly one effect in this arc's history should clear the floor; "
            f"got {carried}")
        # and every non-survivor is below it
        for k, (g, outcome) in measured.items():
            if outcome in ("NARROWED", "KILLED", "TIED", "INCONCLUSIVE", "LOSES_TO_RAW"):
                assert not sweep._is_step_change(g), f"{k} should not carry"


class TestFilterIsHonest:
    def test_marginal_tactics_are_reported_not_silently_dropped(self, sweep):
        """A silent filter is how a real effect disappears. The sweep must PRINT
        each marginal tactic and its gain — reported, scored, just not carried."""
        import inspect
        src = inspect.getsource(sweep.main)
        assert "STEP-CHANGE FILTER" in src
        assert "MARGINAL" in src
        assert "marginal" in src

    def test_rule_carries_its_own_caveat(self, sweep):
        """n=1 in the step bucket. The docstring must say so, because a rule that
        hides its own weakness is how a prior hardens into a law."""
        doc = sweep._is_step_change.__doc__ or ""
        assert "n=1" in doc
        assert "ABSENCE of marginal survivors" in doc

    def test_scoring_still_happens_below_the_floor(self, sweep):
        """The filter gates CARRY-FORWARD, not measurement. Marginal tactics are
        still scored and still land in the run registry, or the registry stops
        being a complete record and selective reporting creeps back in."""
        import inspect
        src = inspect.getsource(sweep.main)
        carry_at = src.index("STEP-CHANGE FILTER")
        register_at = src.index("_register_run")
        assert register_at < carry_at, (
            "runs must be registered BEFORE the step filter, so below-floor "
            "tactics still appear in the append-only record")


def test_mutation_proof(sweep, monkeypatch):
    """Drop the floor to zero and the arc's dead tactics would all carry —
    proving the floor is what refuses them."""
    monkeypatch.setattr(sweep, "STEP_FLOOR", 0.0)
    assert sweep._is_step_change(0.0009, floor=sweep.STEP_FLOOR) is True, (
        "with the floor removed, a 0.09% tie should carry — the guard above is "
        "therefore testing the floor and not something else")
