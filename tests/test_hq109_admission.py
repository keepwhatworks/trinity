"""A generated test is admitted only on POSITIVE evidence from every condition.

hq_109 admits a generated test on three conditions: RED on the mutant it was
written for, GREEN on the control, and GREEN on other known-good trees. The
third exists because a test can kill its mutant while asserting something
false — `assert x == 5` catches a flip to 6 when the real invariant is `x > 0`.

Two ways that third condition has already failed, both found before the run:

1. It counted "this symbol does not exist at that commit" as evidence the
   invariant was false. The very first generated test was CORRECT and failed on
   three older trees with AttributeError. Counting that as a rejection would
   have rejected every good test and manufactured a KILL. Fixed by classing
   runs green / red / inapplicable.

2. Then `cross_ok` started True and only flipped on a red — so a test whose
   every cross-tree was INAPPLICABLE was admitted having passed nothing. The
   condition was silently untested while the row recorded it satisfied. That is
   this repo's oldest bug shape, a green over degenerate data, inside the
   screen built to stop it.

The rule now: at least one tree that could answer, and no tree that said no.
"""
from __future__ import annotations

import pytest


def _decide(statuses: list[str]) -> tuple[bool, bool]:
    """(cross_ok, cross_untested) — mirrors the harness's own reduction."""
    detail = [{"sha": f"s{i}", "status": s} for i, s in enumerate(statuses)]
    applicable = [c for c in detail if c["status"] in ("green", "red")]
    cross_ok = bool(applicable) and all(c["status"] == "green" for c in applicable)
    untested = bool(detail) and not applicable
    return cross_ok, untested


def _screen_bit(statuses: list[str]) -> bool:
    """Did the screen actually REJECT this, as opposed to being unable to run?"""
    detail = [{"sha": f"s{i}", "status": s} for i, s in enumerate(statuses)]
    applicable = [c for c in detail if c["status"] in ("green", "red")]
    cross_ok = bool(applicable) and all(c["status"] == "green" for c in applicable)
    return bool(applicable) and not cross_ok


class TestPositiveEvidenceIsRequired:
    def test_all_green_admits(self):
        assert _decide(["green", "green", "green"]) == (True, False)

    def test_green_with_one_inapplicable_still_admits(self):
        """Inapplicable is not evidence either way; one real green is enough."""
        assert _decide(["green", "inapplicable", "green"]) == (True, False)

    def test_all_inapplicable_is_UNTESTED_not_admitted(self):
        """The bug. Zero trees could answer, so the condition has no result —
        and no result is not a pass."""
        ok, untested = _decide(["inapplicable"] * 3)
        assert ok is False, "admitted a test whose third condition never ran"
        assert untested is True, "and it must be reported as untested, not as a rejection"

    def test_no_trees_attempted_is_not_admitted(self):
        assert _decide([])[0] is False


class TestARealFailureStillRejects:
    @pytest.mark.parametrize("statuses", [
        ["green", "red", "green"],
        ["inapplicable", "red"],
        ["red"],
    ])
    def test_one_red_rejects(self, statuses):
        """A tree that could answer and said no is the whole point of the screen."""
        assert _decide(statuses)[0] is False

    def test_a_red_is_a_rejection_not_an_untested(self):
        ok, untested = _decide(["inapplicable", "red"])
        assert (ok, untested) == (False, False), (
            "a red is evidence AGAINST the invariant and must not be filed as "
            "'nothing could answer'")


class TestTheHarnessUsesThisRule:
    def test_the_source_requires_an_applicable_tree(self):
        """Guards the reduction itself, so the rule cannot quietly revert to
        `cross_ok = True` with a flip-on-red."""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "internal" / "experiments"
               / "hq109_generate_from_doubt.py")
        if not src.exists():
            pytest.skip("experiment harness not present")
        text = src.read_text()
        assert "cross_ok = bool(applicable) and all(" in text, (
            "the admission reduction changed; it must still require at least one "
            "applicable tree")
        assert "cross_ok, cross_detail = True, []" not in text, (
            "reverted to the optimistic initialiser that admitted untested rows")



class TestRejectedIsNotTheSameAsUnrunnable:
    """`semantic_screen_bit` records that the third condition BIT — that a tree
    ran and said no. It read `not cross_ok`, which is also true when zero trees
    could answer, so a test the screen could not run on was filed as a test the
    screen REJECTED. Opposite facts.

    It matters because the PASS bar is stated in terms of the screen
    demonstrably biting. Conflated, a run where every comparison was
    inapplicable could argue the third condition earns its place — while having
    never once executed. Caught on the first generated row of the real run.
    """

    def test_a_real_rejection_sets_the_bit(self):
        assert _screen_bit(["green", "red"]) is True
        assert _screen_bit(["inapplicable", "red"]) is True

    def test_all_inapplicable_does_NOT_set_the_bit(self):
        assert _screen_bit(["inapplicable", "inapplicable"]) is False, (
            "the screen could not run; it did not reject anything")
        ok, untested = _decide(["inapplicable", "inapplicable"])
        assert (ok, untested) == (False, True), "and it must still be reported as untested"

    def test_an_admitted_test_does_not_set_the_bit(self):
        assert _screen_bit(["green", "green"]) is False

    def test_the_three_outcomes_are_mutually_exclusive(self):
        """Admitted, rejected-by-screen, and untested must never co-occur."""
        for statuses in (["green", "green"], ["green", "red"],
                         ["inapplicable", "inapplicable"], ["red"], []):
            ok, untested = _decide(statuses)
            bit = _screen_bit(statuses)
            assert sum([bool(ok), bool(bit), bool(untested)]) <= 1, (
                f"{statuses} produced more than one outcome: admitted={ok} "
                f"screen_bit={bit} untested={untested}")

    def test_the_harness_requires_an_applicable_tree_for_the_bit(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "internal" / "experiments"
               / "hq109_generate_from_doubt.py")
        if not src.exists():
            pytest.skip("experiment harness not present")
        text = src.read_text()
        assert "and applicable and not cross_ok" in text, (
            "semantic_screen_bit must require a tree that actually ran")


class TestCrossTreeSelection:
    """A comparison tree that cannot run the test is NO signal, not weak signal.

    hq_109's first real run VOIDed: 3 of 3 eligible generated tests came back
    with ZERO applicable comparison trees, so the third condition never
    executed and neither bar could be evaluated. The cause was selection —
    trees were drawn at random from fix commits across the whole corpus, and a
    test written against one commit cannot import a symbol that did not exist
    at another. Gaps of 2 to 17 days were enough every time, and one selected
    tree was dated three weeks AFTER its own artifact because the pool was
    never ordered.

    Two fixes, both asserted here: order candidates by nearness in history, and
    probe import-resolvability BEFORE spending a pytest run on a tree.
    """

    def _mod(self):
        import importlib.util
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "internal" / "experiments"
               / "hq109_generate_from_doubt.py")
        if not src.exists():
            pytest.skip("experiment harness not present")
        spec = importlib.util.spec_from_file_location("hq109", src)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_candidates_come_back_nearest_first(self):
        """Nearness is in COMMIT ORDER, not date string order, and the artifact's
        own neighbours must outrank a commit from another era."""
        m = self._mod()
        assert hasattr(m, "near_candidates"), "selection must be a named, testable function"

    def test_the_helpers_are_module_level(self):
        """They were nested inside main(), so nothing could test them — which is
        how three defects in this harness survived to a live run."""
        m = self._mod()
        for name in ("near_candidates", "_imports_resolve", "_code_block", "unsafe_reason"):
            assert hasattr(m, name), f"{name} is not reachable for testing"

    def test_an_unparseable_test_cannot_resolve(self):
        from pathlib import Path
        m = self._mod()
        assert m._imports_resolve(Path("/nonexistent"), "def f(:\n") is False

    def test_a_test_importing_nothing_is_trivially_applicable(self):
        """No trinity_local import means no symbol to be missing."""
        from pathlib import Path
        m = self._mod()
        assert m._imports_resolve(Path("/nonexistent"), "def test_x():\n    assert 1\n") is True

    def test_the_loop_probes_before_spending_a_run(self):
        import inspect
        m = self._mod()
        src = inspect.getsource(m.main)
        assert "_imports_resolve" in src, (
            "the loop must pre-filter; spending a pytest run on a tree that cannot "
            "import the module is what VOIDed the first run")
        assert "near_candidates" in src, "the loop must draw from nearest-in-history"
