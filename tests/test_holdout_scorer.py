"""The exact one-sided sign test (holdout_scorer.one_sided_sign_p), which preference_collapse and
regression_gate use. The draft holdout scorer this file also covered was retired 2026-10-28."""
from __future__ import annotations

from trinity_local.me.holdout_scorer import one_sided_sign_p


def test_one_sided_sign_p_is_exact_binomial():
    assert one_sided_sign_p(5, 5) == 1 / 32          # 2^-5 = 0.03125
    assert abs(one_sided_sign_p(4, 4) - 1 / 16) < 1e-12   # 2^-4 = 0.0625
    assert one_sided_sign_p(0, 0) == 1.0
    # why a five-sign floor exists at alpha 0.05: four perfect signs cannot reach it
    assert one_sided_sign_p(4, 4) > 0.05
    assert one_sided_sign_p(5, 5) <= 0.05
