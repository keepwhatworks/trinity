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
    def test_the_one_skip(self):
        assert triage(K(True), Panel(ran=True, votes=ALL_PASS)).outcome == "SKIP"

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

    def test_outcomes_are_exactly_three(self):
        seen = set()
        for k in (Kernel.not_run(), K(True), K(False), K(True, False), K(False, False)):
            for p in (Panel.not_run(), Panel(ran=True, votes=ALL_PASS), Panel(ran=True, votes=SPLIT),
                      Panel(ran=True, votes=ALL_FAIL)):
                seen.add(triage(k, p).outcome)
        assert seen == {"STOP", "SKIP", "READ"}

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

    def test_only_three_outcomes_and_no_skip_without_kernel(self):
        seen = set()
        for k, p in self._domain():
            t = triage(k, p)
            seen.add(t.outcome)
            if not k.ran:
                assert t.outcome != "SKIP"
        assert seen == {"STOP", "SKIP", "READ"}

    def test_domain_is_not_trivial(self):
        n = len(self._domain())
        assert n > 500, f"the exhaustive domain has only {n} points; the check is not exhaustive"
