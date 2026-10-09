"""hq_133's frozen scoring contract (council_4da7e6e022d0062e): exact letters or nothing,
order averaging in log-odds, fixed-point-free derangements, paired session bootstrap, and a
verdict that cannot be reached when a reader call broke its contract.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not (REPO / "internal" / "experiments").is_dir(), reason="internal/ is not present in the public export"
)


@pytest.fixture(scope="module")
def hq():
    spec = importlib.util.spec_from_file_location("hq133", REPO / "internal" / "experiments" / "hq133_agent_as_me.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestScoring:
    @pytest.mark.parametrize("d", [-2.0, 0.0, 0.7, 3.0])
    def test_an_additive_letter_bias_cancels(self, hq, d):
        # The council's synthetic letter-bias test: a reader that adds b to letter A's
        # log-odds in both orders. Log-odds averaging recovers d whatever b is;
        # averaging probabilities does not.
        def sig(x):
            return 1 / (1 + math.exp(-x))
        clean = hq.pair_bits(d, d)
        prob_avg = []
        for b in (-4.0, -1.0, 1.5, 5.0):
            assert hq.pair_bits(d + b, d - b) == pytest.approx(clean, abs=1e-12)
            prob_avg.append(-math.log2((sig(d + b) + sig(d - b)) / 2))
        if d != 0:   # at d = 0 the two are both symmetric; anywhere else only log-odds is
            assert max(prob_avg) - min(prob_avg) > 0.05

    def test_bits_are_the_log_loss_of_the_founder_choice(self, hq):
        assert hq.pair_bits(0.0, 0.0) == pytest.approx(1.0)
        assert hq.pair_bits(50.0, 50.0) == pytest.approx(0.0, abs=1e-12)
        assert hq.pair_bits(-50.0, -50.0) == pytest.approx(50 / math.log(2), rel=1e-9)

    def test_a_missing_letter_is_never_imputed(self, hq):
        assert hq.order_score({"A": -0.1, "B": -2.3, "_": -9.0}, "A") == pytest.approx(2.2)
        assert hq.order_score({"A": -0.1, "B": -2.3}, "B") == pytest.approx(-2.2)
        with pytest.raises(hq.MissingLogit):
            hq.order_score({"A": -0.0, "_": -14.9}, "A")


class TestControl:
    def test_derangements_move_every_pole(self, hq):
        ds = hq.derangements(6, 8, seed=1)
        assert len(ds) == 8 and len({tuple(d) for d in ds}) == 8
        assert all(sorted(d) == list(range(6)) and all(i != j for i, j in enumerate(d)) for d in ds)
        assert ds == hq.derangements(6, 8, seed=1)

    def test_a_deranged_block_keeps_its_vocabulary_and_length(self, hq):
        ts = [[f"first pole {i}", f"second pole {i} " + "x" * i] for i in range(6)]
        real, fake = hq.block(ts), hq.block(ts, hq.derangements(6, 1, seed=2)[0])
        assert len(real) == len(fake) and real != fake
        assert sorted(real.replace("↔", "").split()) == sorted(fake.replace("↔", "").split())

    def test_the_no_lens_prompt_omits_the_block_only(self, hq):
        with_lens, without = hq.prompt("  1. a ↔ b", "reply one", "reply two"), hq.prompt(None, "reply one", "reply two")
        assert with_lens.endswith(without) and "a ↔ b" not in without


class TestInference:
    def _rows(self, utility, specificity, sessions=40, per=8):
        return [{"tx": f"s{s}", "lens": 0.5, "none": 0.5 + utility, "derange": 0.5 + specificity}
                for s in range(sessions) for _ in range(per)]

    def test_bootstrap_is_deterministic_and_keeps_contrasts_paired(self, hq):
        rows = self._rows(0.05, 0.01)
        reps = hq.bootstrap(rows, n=200)
        assert reps == hq.bootstrap(rows, n=200)
        assert all(r["utility"] == pytest.approx(0.05) and r["specificity"] == pytest.approx(0.01) for r in reps)

    def test_verdicts(self, hq):
        assert hq.verdict(hq.bootstrap(self._rows(0.06, 0.02), n=200), True).verdict == "PASS"
        assert hq.verdict(hq.bootstrap(self._rows(0.06, -0.02), n=200), True).verdict == "KILL"
        assert hq.verdict(hq.bootstrap(self._rows(0.01, 0.02), n=200), True).verdict == "KILL"
        assert hq.verdict(hq.bootstrap(self._rows(0.06, 0.02), n=200), False).verdict == "VOID"

    def test_the_read_waits_for_the_first_month_meeting_both_floors(self, hq):
        pairs = [{"tx": f"s{i % 50}", "date": f"2027-0{1 + i // 120}-15"} for i in range(360)]
        assert hq.eligible_month(pairs) == "2027-03"
        assert hq.eligible_month(pairs[:299]) is None

    def test_the_accrual_window_closes_at_expiry(self, hq):
        early = [{"tx": f"s{i % 50}", "date": "2027-03-15"} for i in range(320)]
        late = [{"tx": f"s{i % 50}", "date": "2028-01-15"} for i in range(320)]
        assert hq.read_month(early, "2027-04-01") == "2027-03"
        assert hq.read_month(early, "2028-06-01") == "2027-03"   # closed in time: read when run
        with pytest.raises(SystemExit):
            hq.read_month(early, "2027-03-20")                  # not closed yet
        with pytest.raises(SystemExit):
            hq.read_month(late, "2027-11-01")                   # not known yet
        assert hq.read_month(late, "2028-02-01") == "VOID"      # floors met only after expiry
