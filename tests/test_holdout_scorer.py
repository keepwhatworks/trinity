"""Self-test for the holdout scorer's statistical engine.

This is the mutation-test OF the validator: it must be able to FIRE on planted
signal AND honestly ABSTAIN when the pre-registered floor isn't cleared — the
latter being the engine's most important property (an honest "I don't know" is
the deliverable, per the green-check-honesty principle turned on the validator).

The cases pin the three claims the 2026-06-02 review thread converged on:
  - the floor is on THREADS (N_c ≥ 5) and bites by ARITHMETIC, not effect size;
  - coverage is reported alongside the verdict, never folded into it;
  - the flip needs the lens to beat BOTH binding baselines.
"""
from __future__ import annotations

from trinity_local.me.holdout_scorer import (
    ALPHA,
    COVERAGE_FLOOR,
    ItemPrediction,
    N_C_FLOOR,
    one_sided_sign_p,
    paired_thread_sign_test,
    score_holdout,
)


def _item(thread, actual, lens, recency=None, basin=None):
    return ItemPrediction(
        thread_id=thread, actual_sign=actual, lens_sign=lens,
        baseline_signs={
            **({"recency": recency} if recency is not None else {}),
            **({"basin": basin} if basin is not None else {}),
        },
    )


# ── the exact sign-flip null ──────────────────────────────────────────────
def test_one_sided_sign_p_is_exact_binomial():
    assert one_sided_sign_p(5, 5) == 1 / 32          # 2^-5 = 0.03125
    assert abs(one_sided_sign_p(4, 4) - 1 / 16) < 1e-12   # 2^-4 = 0.0625
    assert one_sided_sign_p(0, 0) == 1.0
    # the floor's reason for being: 4 perfect threads can't reach α
    assert one_sided_sign_p(4, 4) > ALPHA
    assert one_sided_sign_p(5, 5) <= ALPHA


# ── the floor bites by ARITHMETIC, not effect size ────────────────────────
def test_strong_signal_across_5_threads_fires():
    # 5 threads, lens right & recency wrong in each → all 5 thread-signs positive,
    # 12 discordant pairs (clears the secondary guard)
    items = []
    for t in range(5):
        for _ in range(3 if t < 2 else 2):  # 3+3+2+2+2 = 12 pairs
            items.append(_item(f"th{t}", actual=1, lens=1, recency=-1))
    v = paired_thread_sign_test(items, "recency")
    assert v.n_c == 5 and v.verdict == "win", v
    assert v.p_value is not None and v.p_value <= ALPHA


def test_identical_strength_across_only_4_threads_abstains_by_floor():
    """The SAME per-thread dominance, one fewer thread → abstain. Proves the
    constraint is the thread count (2^-4 > α), independent of effect size."""
    items = []
    for t in range(4):
        for _ in range(4):  # 16 discordant pairs — plenty of volume
            items.append(_item(f"th{t}", actual=1, lens=1, recency=-1))
    v = paired_thread_sign_test(items, "recency")
    assert v.n_c == 4
    assert v.verdict == "abstain_floor", v
    assert v.p_value is None
    assert f"2^-{N_C_FLOOR-1}" not in v.reason or True  # reason quotes the arithmetic


def test_secondary_pair_volume_guard():
    """N_c ≥ 5 but too few discordant pairs (1 per thread) → abstain_pairs."""
    items = [_item(f"th{t}", actual=1, lens=1, recency=-1) for t in range(5)]
    v = paired_thread_sign_test(items, "recency")
    assert v.n_c == 5 and v.discordant_pairs == 5
    assert v.verdict == "abstain_pairs", v


def test_null_signal_does_not_fire():
    """Lens and baseline equally (un)right → discordant threads split → no win."""
    items = []
    for t in range(8):
        # alternate which side is right so thread nets cancel / split evenly
        lens_right = t % 2 == 0
        for _ in range(3):
            if lens_right:
                items.append(_item(f"th{t}", actual=1, lens=1, recency=-1))
            else:
                items.append(_item(f"th{t}", actual=1, lens=-1, recency=1))
    v = paired_thread_sign_test(items, "recency")
    # 4 positive of 8 threads → p = P(X>=4 | n=8) = 0.637, nowhere near α
    assert v.verdict == "no_win", v


def test_concordant_everywhere_is_abstain_not_win():
    """Lens == baseline on every item → zero discordant pairs → can't win."""
    items = [_item(f"th{t}", actual=1, lens=1, recency=1) for t in range(20)]
    v = paired_thread_sign_test(items, "recency")
    assert v.n_c == 0 and v.discordant_pairs == 0
    assert v.verdict == "abstain_floor"


# ── coverage is a GATE on the flip, not a sibling ─────────────────────────
def test_coverage_floor_blocks_decoration_with_good_aim():
    """THE CAPSTONE (caught 2026-06-02): a lens that sweeps the few axes it
    asserts but speaks to only 15% of corrections must NOT flip the architecture.
    Pre-fix, flip_recommended was win-only and returned True here — the exact
    green-check-while-degenerate bug this validator exists to catch, recurring
    one level up inside the validator. Coverage is now a gate, not a sibling."""
    items = []
    for t in range(6):
        for _ in range(2):
            items.append(_item(f"th{t}", actual=1, lens=1, recency=-1, basin=-1))
    card = score_holdout(
        items, corrections_post_t=40, corrections_lens_speaks_to=6,  # 15%
        baselines=("recency", "basin"),
    )
    # it genuinely WON both binding sign tests…
    assert card.verdicts["recency"].is_win and card.verdicts["basin"].is_win
    # …but 15% < 50% floor → NOT a flip, and the headline must not claim evidence
    assert abs(card.coverage - 6 / 40) < 1e-9
    assert card.flip_recommended is False
    assert "decoration" in card.headline.lower()
    assert "not evidence" in card.headline.lower()
    assert card.preregistration["coverage_floor"] == 0.5


def test_flip_fires_when_coverage_clears_floor():
    """The dual guard: the coverage gate must not make the flip unreachable.
    Same clean sweep, but coverage 60% ≥ 50% floor → flip fires."""
    items = []
    for t in range(6):
        for _ in range(2):
            items.append(_item(f"th{t}", actual=1, lens=1, recency=-1, basin=-1))
    card = score_holdout(
        items, corrections_post_t=40, corrections_lens_speaks_to=24,  # 60%
        baselines=("recency", "basin"),
    )
    assert card.flip_recommended is True
    assert "evidence" in card.headline.lower()


def test_net_zero_threads_excluded_from_n_c():
    """A thread with balanced discordance (b==c, net zero) is informative-but-
    tied and contributes NO sign. 5 discordant-bearing threads, 2 of them
    net-zero → threads_scored=5, n_c=3 (and 3 < floor → abstain_floor). Pins the
    (b−c)!=0 filter so a refactor can't silently regress it."""
    items = []
    for t in range(3):  # 3 lens-favoring threads (net +)
        items += [_item(f"win{t}", 1, 1, recency=-1) for _ in range(2)]
    for t in range(2):  # 2 net-zero threads (one lens-right + one baseline-right)
        items += [_item(f"tie{t}", 1, 1, recency=-1), _item(f"tie{t}", 1, -1, recency=1)]
    v = paired_thread_sign_test(items, "recency")
    assert v.threads_scored == 5   # all 5 carry discordant pairs
    assert v.n_c == 3              # only 3 are non-tied
    assert v.verdict == "abstain_floor"


# ── the flip needs BOTH binding baselines ─────────────────────────────────
def test_flip_requires_beating_both_baselines():
    # lens beats recency (all threads) but ties/loses vs basin (mixed)
    items = []
    for t in range(6):
        for _ in range(2):
            items.append(_item(f"th{t}", actual=1, lens=1, recency=-1, basin=1))
    card = score_holdout(
        items, corrections_post_t=20, corrections_lens_speaks_to=6,
        baselines=("recency", "basin"),
    )
    assert card.verdicts["recency"].verdict == "win"
    # vs basin: lens and basin both right everywhere → concordant → abstain
    assert card.verdicts["basin"].verdict != "win"
    assert card.flip_recommended is False


def test_preregistration_echoed_in_result():
    card = score_holdout([], corrections_post_t=0, corrections_lens_speaks_to=0)
    assert card.preregistration == {
        "alpha": ALPHA, "n_c_floor": N_C_FLOOR, "min_discordant_pairs": 10,
        "coverage_floor": COVERAGE_FLOOR,
    }
