"""The widened-mining arms must refuse to run when they are not comparable.

The registration (plan item 3) names a size-matching control. This pins it in
code, because a control that lives only in prose gets skipped by the person who
is sure they remember it.

Three ways the arms can be incomparable, each measured on the live ledger before
any chairman call was spent:
  - an arm too small for the miner's own 6-12 pair floor
  - arms not matched on item count (the registered control)
  - treatment rows without a basin, which the prompt's cross-basin acceptance
    test cannot evaluate (res_085)

And one that is surfaced but not fatal: the arms are matched on item count and
NOT on prompt length (80 items render 18,340 chars, 78 render 27,061 — 32%
longer from fewer rows). That is inherent to the data, so it warns rather than
blocks, and the warning travels with the verdict.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

EXP = pathlib.Path(__file__).resolve().parent.parent / "internal" / "experiments"

pytestmark = pytest.mark.skipif(
    not (EXP / "widened_mining.py").exists(),
    reason="internal/ absent from the public export")


def _mod():
    spec = importlib.util.spec_from_file_location("widened_mining", EXP / "widened_mining.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["widened_mining"] = m
    spec.loader.exec_module(m)
    return m


class _D:
    def __init__(self, i, basin="b00", text="x"):
        self.id, self.basin = i, basin
        self.privileged, self.sacrificed = text, text
        self.valence, self.verbatim, self.prompt_id = "", "", None


class TestItRefusesIncomparableArms:
    def test_unmatched_item_counts_are_refused(self):
        m = _mod()
        pf = m.preflight([_D(f"d_{i}") for i in range(80)],
                         [_D(f"r_{i}") for i in range(40)])
        assert not pf["comparable"]
        assert any("not size-matched" in p for p in pf["problems"])

    def test_a_treatment_row_without_a_basin_is_refused(self):
        m = _mod()
        pf = m.preflight([_D(f"d_{i}") for i in range(78)],
                         [_D(f"r_{i}", basin=None if i == 0 else "b01") for i in range(78)])
        assert not pf["comparable"]
        assert any("without a basin" in p for p in pf["problems"])

    def test_an_arm_below_the_miner_floor_is_refused(self):
        m = _mod()
        pf = m.preflight([_D(f"d_{i}") for i in range(10)],
                         [_D(f"r_{i}") for i in range(10)])
        assert not pf["comparable"]
        assert any("too small" in p for p in pf["problems"])

    def test_matched_arms_pass(self):
        m = _mod()
        pf = m.preflight([_D(f"d_{i}") for i in range(80)],
                         [_D(f"r_{i}") for i in range(78)])
        assert pf["comparable"], pf["problems"]
        assert pf["treatment_citable_r_ids"] == 78


class TestItWarnsOnPromptLength:
    def test_a_much_longer_treatment_prompt_warns_without_blocking(self):
        m = _mod()
        pf = m.preflight([_D(f"d_{i}", text="short") for i in range(80)],
                         [_D(f"r_{i}", text="long " * 200) for i in range(78)])
        assert pf["comparable"], "a length skew is inherent to the data, not a refusal"
        assert pf["warnings"] and any("char skew" in w for w in pf["warnings"])

    def test_equal_length_prompts_do_not_warn(self):
        m = _mod()
        pf = m.preflight([_D(f"d_{i}", text="same") for i in range(80)],
                         [_D(f"r_{i}", text="same") for i in range(78)])
        assert not pf["warnings"]
