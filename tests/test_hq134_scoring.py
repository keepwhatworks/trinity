"""hq_134's decision-level contract (council_96db249a82f2f9a2): exact picks from whatever letters
come back, abstention never guessed, both contrasts must be material and bounded above zero, and
the read waits for blinded power.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not (REPO / "internal" / "experiments").is_dir(), reason="internal/ is not present in the public export"
)


@pytest.fixture(scope="module")
def hq():
    sys.path.insert(0, str(REPO / "internal" / "experiments"))
    spec = importlib.util.spec_from_file_location("hq134", REPO / "internal" / "experiments" / "hq134_agent_as_me.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(lens, none, derange, sessions=40, per=8):
    return [{"tx": f"s{s}", "lens": lens, "none": none, "derange": derange} for s in range(sessions) for _ in range(per)]


class TestDecision:
    def test_a_returned_letter_outranks_a_missing_one(self, hq):
        assert hq.order_pick({"A": -3.0, "B": -0.1, "The": -0.01}) == "B"
        assert hq.order_pick({"A": -9.0, "The": -0.01}) == "A"
        assert hq.order_pick({"The": -0.01, "Neither": -1.0}) is None

    def test_pair_score_reads_both_orders_and_counts_abstentions(self, hq):
        assert hq.pair_score({"A": -0.1, "B": -3}, {"B": -0.1, "A": -3}) == (1.0, 0)
        assert hq.pair_score({"A": -3, "B": -0.1}, {"B": -0.1, "A": -3}) == (0.5, 0)   # pure position bias
        assert hq.pair_score({"The": -0.1}, {"B": -0.1}) == (0.75, 1)


class TestVerdict:
    def test_pass_needs_both_contrasts_material_and_bounded(self, hq):
        assert hq.verdict(_rows(0.7, 0.6, 0.6), True)["verdict"] == "PASS"

    def test_a_framing_effect_without_content_is_killed(self, hq):
        # The lens beats no lens by 10 points, but a shuffled lens does exactly as well.
        assert hq.verdict(_rows(0.7, 0.6, 0.7), True)["verdict"] == "KILL"

    def test_a_bounded_but_immaterial_gain_cannot_pass(self, hq):
        rows = _rows(0.62, 0.6, 0.6)   # +2 points, bounded above zero, under the 5-point bar
        assert hq.verdict(rows, True)["verdict"] == "KILL"

    def test_noise_never_passes(self, hq):
        import random
        rng = random.Random(3)
        rows = [{"tx": f"s{s}", "lens": rng.choice((0, 0.5, 1)), "none": rng.choice((0, 0.5, 1)),
                 "derange": rng.choice((0, 0.5, 1))} for s in range(40) for _ in range(2)]
        assert hq.verdict(rows, True)["verdict"] != "PASS"

    def test_too_many_abstentions_void_the_read(self, hq):
        assert hq.verdict(_rows(0.7, 0.6, 0.6), False, "abstained")["verdict"] == "VOID"


class TestPower:
    def test_power_is_zero_when_the_arms_never_differ(self, hq):
        assert hq.blinded_power(_rows(0.5, 0.5, 0.5)) == {"utility": 0.0, "specificity": 0.0}

    def test_power_grows_with_sessions_and_never_reads_the_label(self, hq):
        small = hq.blinded_power([{"tx": f"s{s}", "lens": 1.0, "none": 0.0 if s % 3 else 1.0, "derange": 0.5} for s in range(40)])
        large = hq.blinded_power([{"tx": f"s{s}", "lens": 1.0, "none": 0.0 if s % 3 else 1.0, "derange": 0.5} for s in range(400)])
        assert large["utility"] > small["utility"]
        flipped = hq.blinded_power([{"tx": f"s{s}", "lens": 0.0, "none": 1.0 if s % 3 else 0.0, "derange": 0.5} for s in range(40)])
        assert flipped == small   # magnitudes only: swapping which reply was missed changes nothing

    def test_the_read_month_needs_floors_power_and_a_closed_month(self, hq):
        ok = {"pairs": 320, "sessions": 45, "power": {"utility": 0.9, "specificity": 0.85}}
        weak = {**ok, "power": {"utility": 0.9, "specificity": 0.6}}
        assert hq.read_month({"2027-03": weak, "2027-04": ok}, "2027-05-01") == "2027-04"
        with pytest.raises(SystemExit):
            hq.read_month({"2027-04": ok}, "2027-04-20")
        assert hq.read_month({"2027-04": weak}, "2028-01-02") == "VOID"
        assert hq.read_month({"2028-01": ok}, "2028-02-02") == "VOID"

    def test_power_uses_magnitudes_so_mixed_signs_in_a_session_cannot_cancel(self, hq):
        mixed = [{"tx": f"s{s}", "lens": float(i), "none": float(1 - i), "derange": 0.5} for s in range(40) for i in (0, 1)]
        same = [{"tx": f"s{s}", "lens": 1.0, "none": 0.0, "derange": 0.5} for s in range(40) for _ in (0, 1)]
        assert hq.blinded_power(mixed) == hq.blinded_power(same)

    def test_each_contrast_is_bounded_on_its_own(self, hq):
        # Anti-correlated contrasts: each one-sided 95% lower bound clears zero (shares 0.959), but
        # the JOINT share with both above zero is 0.918. The registration bounds each contrast.
        rows = [{"tx": f"s{s}", "lens": 0.5, "none": 0.372 if s < 20 else 0.572, "derange": 0.572 if s < 20 else 0.372}
                for s in range(40) for _ in range(4)]
        reps = hq.bootstrap(rows)
        assert sum(r["utility"] > 0 and r["specificity"] > 0 for r in reps) / len(reps) < 0.95
        assert hq.verdict(rows, True)["gates"] == {"utility": "PASS", "specificity": "PASS"}
