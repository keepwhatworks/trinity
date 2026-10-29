"""The exact one-sided sign test the lens's statistical gates share.

`one_sided_sign_p` is the sign-flip permutation null over n non-tied signs: no asymptotics, so it
stays honest at the small n these gates see. preference_collapse and regression_gate use it.

The module keeps its name from the draft holdout scorer it used to hold (score_holdout and its
flip_recommended verdict, 2026-06-02). Its real-data feed was only ever sketched, so nothing called
it; it was retired 2026-10-28 (see retired_names).
"""
from __future__ import annotations

from math import comb


def one_sided_sign_p(positive: int, n: int) -> float:
    """Exact one-sided p-value: P(X ≥ positive) for X ~ Binomial(n, 0.5). This is
    the sign-flip permutation null over n non-tied thread signs — no asymptotics."""
    if n <= 0:
        return 1.0
    positive = max(0, min(positive, n))
    return sum(comb(n, i) for i in range(positive, n + 1)) / (2 ** n)
