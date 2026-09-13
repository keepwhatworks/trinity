"""The verify rule is the product's whole pre-deploy judgment. Pin every cell."""
from __future__ import annotations

import pytest

from trinity_local.verify_rule import Kernel, Panel, Triage, triage

ALL_PASS = {"claude": True, "codex": True, "antigravity": True}
SPLIT = {"claude": True, "codex": False, "antigravity": True}
ALL_FAIL = {"claude": False, "codex": False, "antigravity": False}


def K(green, relevant=True):
    return Kernel(ran=True, relevant=relevant, green=green)


class TestStop:
    def test_relevant_red_stops_regardless_of_panel(self):
        for votes in (ALL_PASS, SPLIT, ALL_FAIL, {}):
            assert triage(K(False), Panel(ran=bool(votes), votes=votes)).outcome == "STOP"

    def test_red_under_consensus_is_a_false_green(self):
        t = triage(K(False), Panel(ran=True, votes=ALL_PASS))
        assert t.outcome == "STOP" and t.false_green is True

    def test_red_under_split_is_not_a_false_green(self):
        assert triage(K(False), Panel(ran=True, votes=SPLIT)).false_green is False

    def test_irrelevant_red_does_not_stop(self):
        """A red somewhere else is not this artifact's kernel."""
        assert triage(K(False, relevant=False), Panel(ran=True, votes=ALL_PASS)).outcome == "READ"


class TestSkip:
    """SKIP stopped shipping 2026-09-09. Two councils, six models, unanimous
    both times (amd_0217, amd_0226): no output may authorise omitting human
    review on a number nobody has measured. The branch still COMPUTES, so the
    condition can be recorded and studied, but it returns READ."""

    def test_skip_does_not_ship(self):
        r = triage(K(True), Panel(ran=True, votes=ALL_PASS))
        assert r.outcome == "READ"
        assert "would_skip" in r.reason

    def test_skip_is_unreachable_from_any_input(self):
        """The strong form: no combination of kernel and panel produces SKIP
        while the flag is off. A branch that can still fire somewhere is not
        withdrawn, it is hiding."""
        import itertools
        outcomes = set()
        for ran, rel, green in itertools.product([True, False], [True, False], [True, False, None]):
            for votes in ({}, {"claude": True}, {"claude": True, "codex": True}, ALL_PASS, SPLIT,
                          {"claude": False, "codex": False, "antigravity": False}):
                for pran in (True, False):
                    outcomes.add(triage(Kernel(ran, rel, green), Panel(ran=pran, votes=votes)).outcome)
        assert outcomes == {"STOP", "READ"}, f"reachable outcomes: {sorted(outcomes)}"

    def test_the_branch_still_computes_behind_the_flag(self):
        """Withdrawn, not deleted: re-entry has a locked contract (amd_0222,
        1% upper bound plus a 20% coverage floor), and a deleted branch cannot
        be re-earned or shadow-measured."""
        from trinity_local import verify_rule
        assert verify_rule.SKIP_SHIPS is False
        old = verify_rule.SKIP_SHIPS
        try:
            verify_rule.SKIP_SHIPS = True
            assert triage(K(True), Panel(ran=True, votes=ALL_PASS)).outcome == "SKIP"
        finally:
            verify_rule.SKIP_SHIPS = old

    def test_green_without_panel_is_read_not_skip(self):
        """A green kernel alone keeps its blind spots (amd_0202)."""
        assert triage(K(True), Panel.not_run()).outcome == "READ"

    def test_green_with_split_is_read(self):
        assert triage(K(True), Panel(ran=True, votes=SPLIT)).outcome == "READ"

    def test_two_labs_agreeing_is_not_consensus(self):
        """Two voters miss at 13% vs 8% at three (res_126); the rule wants the independence."""
        assert triage(K(True), Panel(ran=True, votes={"claude": True, "codex": True})).outcome == "READ"

    def test_three_votes_from_two_labs_is_not_consensus(self):
        votes = {"claude": True, "codex": True, "claude-sonnet": True}  # unknown slug -> its own lab
        # 'claude-sonnet' is not in LAB_OF so it counts as a distinct lab; make it explicit:
        votes = {"claude": True, "codex": True, "codex2": True}
        from trinity_local import verify_rule
        verify_rule.LAB_OF["codex2"] = "openai"
        try:
            assert triage(K(True), Panel(ran=True, votes=votes)).outcome == "READ"
        finally:
            del verify_rule.LAB_OF["codex2"]

    def test_irrelevant_green_with_consensus_is_read(self):
        assert triage(K(True, relevant=False), Panel(ran=True, votes=ALL_PASS)).outcome == "READ"


class TestRead:
    def test_nothing_ran_is_read_and_says_so(self):
        t = triage(Kernel.not_run(), Panel.not_run())
        assert t.outcome == "READ" and "nothing vouched" in t.reason

    def test_no_test_with_consensus_is_read(self):
        """Consensus without a kernel is not a skip."""
        t = triage(Kernel.not_run(), Panel(ran=True, votes=ALL_PASS))
        assert t.outcome == "READ" and "without a kernel" in t.reason

    def test_no_test_with_split_is_read(self):
        assert triage(Kernel.not_run(), Panel(ran=True, votes=SPLIT)).outcome == "READ"

    def test_outcomes_are_exactly_two(self):
        """Was three. SKIP stopped shipping 2026-09-09 (amd_0217/0226)."""
        seen = set()
        for k in (Kernel.not_run(), K(True), K(False), K(True, False), K(False, False)):
            for p in (Panel.not_run(), Panel(ran=True, votes=ALL_PASS), Panel(ran=True, votes=SPLIT),
                      Panel(ran=True, votes=ALL_FAIL)):
                seen.add(triage(k, p).outcome)
        assert seen == {"STOP", "READ"}

    def test_result_is_frozen(self):
        t = triage(K(True), Panel(ran=True, votes=ALL_PASS))
        with pytest.raises(Exception):
            t.outcome = "READ"  # type: ignore[misc]
        assert isinstance(t, Triage)


class TestExhaustive:
    """Lean-grade for a ten-line function: every input, not a sample.

    The domain is finite and small — kernel in {not run} ∪ {ran × relevant? × green?},
    panel in {not run} ∪ every vote assignment over the three labs plus a fourth
    same-lab voter — so the four properties below are checked on ALL of it. A
    proof of a Lean model of this function would be a proof about a translation;
    this is a check of the function that ships.
    """
    def _domain(self):
        from itertools import product
        from trinity_local import verify_rule as VR
        kernels = [Kernel.not_run()] + [Kernel(ran=True, relevant=r, green=g)
                                        for r in (True, False) for g in (True, False)]
        provs = ["claude", "codex", "antigravity", "codex2"]     # codex2: a 4th voter, same lab as codex
        VR.LAB_OF.setdefault("codex2", "openai")
        panels = [Panel.not_run()]
        for n in range(1, 5):
            for subset in product(provs, repeat=n):
                if len(set(subset)) != n:
                    continue
                for votes in product((True, False), repeat=n):
                    panels.append(Panel(ran=True, votes=dict(zip(subset, votes))))
        return [(k, p) for k in kernels for p in panels]

    def test_skip_implies_relevant_green_and_three_lab_consensus(self):
        for k, p in self._domain():
            t = triage(k, p)
            if t.outcome == "SKIP":
                assert k.ran and k.relevant and k.green is True
                assert p.consensus_pass() and len(p.labs()) >= 3 and all(p.votes.values())

    def test_relevant_red_always_stops(self):
        for k, p in self._domain():
            if k.ran and k.relevant and k.green is False:
                assert triage(k, p).outcome == "STOP"

    def test_false_green_iff_stop_under_consensus(self):
        for k, p in self._domain():
            t = triage(k, p)
            assert t.false_green == (t.outcome == "STOP" and p.consensus_pass())

    def test_only_two_outcomes_across_the_whole_domain(self):
        """The exhaustive form of the withdrawal. SKIP must be unreachable from
        every input the rule can receive, not merely from the ones a unit test
        happens to try — a branch that still fires somewhere is hiding, not
        withdrawn. Also keeps the original invariant: nothing may skip without
        a kernel, which must hold again if SKIP is ever re-earned."""
        seen = set()
        for k, p in self._domain():
            t = triage(k, p)
            seen.add(t.outcome)
            if not k.ran:
                assert t.outcome != "SKIP"
        assert seen == {"STOP", "READ"}

    def test_domain_is_not_trivial(self):
        n = len(self._domain())
        assert n > 500, f"the exhaustive domain has only {n} points; the check is not exhaustive"
