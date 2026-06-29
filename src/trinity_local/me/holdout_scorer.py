"""Holdout scorer for the lens — the falsifiable oracle (DRAFT, statistical engine).

Question it answers: does **lens@T** predict the *post-T* corrections better than
the strong baselines (recency = "next correction looks like the last one" and
basin-centroid = "the local cluster's average direction")? If it can't beat those
on a powered test, the named tensions are decoration (geometry-finds-LLM-names is
a *judgment* until this measures it — gates the #182/lens-architecture flip).

This module is the **statistical engine only** — pure stdlib, no embeddings, no
LLM, no quota. It takes per-(correction, axis) predictions (each method's
predicted sign + the actual projection sign) and returns a verdict. The
embedding feed that produces those predictions from the real corpus is a separate
concern (build_holdout_items, sketched at the bottom) so the math below can be
pressure-tested in isolation.

DESIGN (per the 2026-06-02 review thread — three agents, converged):

1. **Thread-level sign test, not pair-level McNemar.** Corrections within one
   transcript/thread are autocorrelated (the user redirects the *same way* across
   a task), so pairs are NOT independent — exact McNemar's independence
   assumption is false, and a cluster-robust/GEE variance is asymptotic in the
   number of clusters (we have ~13 threads — nowhere near asymptotic). The
   assumption-light tool that's honest at this scale: collapse each thread to the
   SIGN of its net (b − c) discordant count, then a sign test on those thread
   signs. That's an exact sign-flip permutation null — no asymptotics borrowed.

2. **The floor is on THREADS, not pairs.** A one-sided sign-flip null over N_c
   non-tied threads cannot return p below 2^(−N_c). To reject at α=0.05 you need
   2^(−N_c) ≤ 0.05, i.e. N_c ≥ 5 (2^−5 = 0.03125 ≤ 0.05 < 2^−4 = 0.0625). With
   fewer than five discordant-bearing, non-tied threads a win is **unreachable by
   arithmetic regardless of effect size** — so the pre-registered primary floor
   is N_c ≥ 5. (MIN_DISCORDANT_PAIRS is a secondary volume guard; the thread
   count is the one that bites.)

3. **Coverage is a first-class number.** Scoring only the axes the lens *asserts*
   engage makes the verdict conditional on lens assertion — "beats baselines on
   the axes it claims." That's meaningless without "how often does it claim
   anything." A lens that asserts on 3 easy corrections, nails them, and abstains
   on the other 37 would post a clean win and be decoration with good aim.
   Coverage (fraction of held-out corrections the lens speaks to at all) sits
   NEXT TO the verdict, or the verdict lies by omission.

4. **Abstention is the expected, honest first output.** The label itself is noisy
   (a single content-dominated correction's projection sign, coherence ~0.14)
   which attenuates every method toward 0.5 — it doesn't bias the *paired*
   direction (both methods face the same noisy labels) but shrinks the effect,
   pushing toward abstain. We don't denoise it (local aggregation would
   reintroduce the very autocorrelation we're escaping). So the most likely first
   verdict is a well-powered "not enough independent signal yet, N_c below floor"
   — and that IS the deliverable: the green-check-honesty principle turned on the
   validator itself. It's a standing monotonic gate that accrues power as
   corrections accumulate across threads; the un-flipped center of gravity is the
   correct state until N_c clears 5 AND the thread sign test fires for the lens
   against both recency and basin.

PRE-REGISTRATION: the three constants below are fixed BEFORE any real run and
echoed into every result dict (`preregistration`) so an "abstain, N_c=4" can't be
quietly relitigated into a win. Changing them is a visible code+test change.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import comb

# ── pre-registered thresholds (do not tune to the data) ──────────────────────
ALPHA = 0.05                 # one-sided significance
N_C_FLOOR = 5                # min non-tied discordant-bearing threads (2^-5 ≤ α)
MIN_DISCORDANT_PAIRS = 10    # secondary volume guard
# COVERAGE is a GATE on the flip, not a sibling field. A lens that sweeps the few
# axes it asserts but stays silent on most corrections is decoration-with-good-aim
# — it "wins" the sign test on its claimed axes yet can't be the spine. Caught
# 2026-06-02 (review thread): flip_recommended was win-only, so coverage=0.15 +
# clean sweeps returned flip=True ("first real evidence") — the exact
# green-check-while-degenerate bug this validator exists to catch, recurring one
# level up inside the validator. The floor is a product judgment set by principle:
# a spine must cover at least what it displaces. NOTE (review self-consistency
# catch, 2026-06-02): that principle actually argues HIGHER than 0.5 — if the
# incumbent chairman-lens names a tension for ~every correction, the bar should
# approach ITS coverage, not sit at half. So 0.5 is a PROVISIONAL lower-bound (a
# floor-on-the-floor, safe until measured), NOT the principled target. When
# build_holdout_items is wired it MUST measure the incumbent's coverage and
# re-anchor COVERAGE_FLOOR up to it — otherwise 0.5 ossifies as the permanent bar
# by inertia, which would itself be a too-lenient gate (the bug class, one more
# level out).
COVERAGE_FLOOR = 0.5
# binding baselines the lens must beat to justify the flip (both, conjunction →
# conservative, no multiple-comparison inflation since it's an intersection test)
BINDING_BASELINES = ("recency", "basin")


@dataclass
class ItemPrediction:
    """One (correction, axis) unit the lens SPEAKS TO (asserts the axis engages
    this correction's basin), with each method's predicted preferred sign and the
    actual projection sign. Signs are +1 / -1; 0 means undefined (dropped)."""

    thread_id: str
    actual_sign: int
    lens_sign: int
    baseline_signs: dict[str, int] = field(default_factory=dict)


def one_sided_sign_p(positive: int, n: int) -> float:
    """Exact one-sided p-value: P(X ≥ positive) for X ~ Binomial(n, 0.5). This is
    the sign-flip permutation null over n non-tied thread signs — no asymptotics."""
    if n <= 0:
        return 1.0
    positive = max(0, min(positive, n))
    return sum(comb(n, i) for i in range(positive, n + 1)) / (2 ** n)


@dataclass
class PairedVerdict:
    baseline: str
    threads_scored: int        # distinct threads with ≥1 discordant item
    discordant_pairs: int      # total b + c
    n_c: int                   # non-tied discordant-bearing threads (the floor unit)
    positive_threads: int      # threads where the lens net-won (b > c)
    p_value: float | None
    verdict: str               # win | no_win | abstain_floor | abstain_pairs
    reason: str

    @property
    def is_win(self) -> bool:
        return self.verdict == "win"


def paired_thread_sign_test(items: list[ItemPrediction], baseline: str) -> PairedVerdict:
    """Thread-level sign test of lens vs one baseline.

    Per thread, over the DISCORDANT items (exactly one of lens/baseline matches
    the actual sign): b = lens-right & baseline-wrong, c = lens-wrong &
    baseline-right. The thread's sign is sign(b − c); ties (b == c, includes
    threads with no discordant items) contribute nothing. Sign-test the thread
    signs against 0.5.
    """
    bc: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # thread -> [b, c]
    for it in items:
        bl = it.baseline_signs.get(baseline)
        if bl is None or it.actual_sign == 0 or it.lens_sign == 0:
            continue
        lens_right = it.lens_sign == it.actual_sign
        base_right = bl == it.actual_sign
        if lens_right == base_right:
            continue  # concordant — no discriminating information
        bc[it.thread_id][0 if lens_right else 1] += 1

    discordant_pairs = sum(b + c for b, c in bc.values())
    signs = [1 if (b - c) > 0 else -1 for b, c in bc.values() if (b - c) != 0]
    n_c = len(signs)
    positive = sum(1 for s in signs if s > 0)

    if n_c < N_C_FLOOR:
        return PairedVerdict(
            baseline, len(bc), discordant_pairs, n_c, positive, None,
            "abstain_floor",
            f"N_c={n_c} < {N_C_FLOOR}: a win is unreachable by arithmetic "
            f"(min one-sided p = 2^-{n_c} = {2**-n_c:.3g} > α={ALPHA})",
        )
    if discordant_pairs < MIN_DISCORDANT_PAIRS:
        return PairedVerdict(
            baseline, len(bc), discordant_pairs, n_c, positive, None,
            "abstain_pairs",
            f"only {discordant_pairs} discordant pairs < {MIN_DISCORDANT_PAIRS}",
        )
    p = one_sided_sign_p(positive, n_c)
    verdict = "win" if p <= ALPHA else "no_win"
    return PairedVerdict(
        baseline, len(bc), discordant_pairs, n_c, positive, p, verdict,
        f"{positive}/{n_c} threads favor the lens, one-sided p={p:.3g}",
    )


@dataclass
class HoldoutScorecard:
    corrections_post_t: int
    corrections_lens_speaks_to: int
    coverage: float
    verdicts: dict[str, PairedVerdict]
    flip_recommended: bool
    headline: str
    preregistration: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "corrections_post_t": self.corrections_post_t,
            "corrections_lens_speaks_to": self.corrections_lens_speaks_to,
            "coverage": round(self.coverage, 4),
            "verdicts": {
                k: {
                    "baseline": v.baseline, "verdict": v.verdict, "n_c": v.n_c,
                    "threads_scored": v.threads_scored,
                    "discordant_pairs": v.discordant_pairs,
                    "positive_threads": v.positive_threads,
                    "p_value": v.p_value, "reason": v.reason,
                }
                for k, v in self.verdicts.items()
            },
            "flip_recommended": self.flip_recommended,
            "headline": self.headline,
            "preregistration": self.preregistration,
        }


def score_holdout(
    items: list[ItemPrediction],
    *,
    corrections_post_t: int,
    corrections_lens_speaks_to: int,
    baselines: tuple[str, ...] = ("recency", "basin", "global_centroid"),
) -> HoldoutScorecard:
    """Run the thread-level sign test of the lens vs each baseline, bundle
    coverage, and decide the flip (lens must WIN against every BINDING baseline).

    `corrections_post_t` / `corrections_lens_speaks_to` are correction-level
    counts (not item rows) — coverage is per-correction, independent of how many
    axes each correction touches.
    """
    verdicts = {b: paired_thread_sign_test(items, b) for b in baselines}
    coverage = (
        corrections_lens_speaks_to / corrections_post_t
        if corrections_post_t else 0.0
    )
    binding = [verdicts[b] for b in BINDING_BASELINES if b in verdicts]
    wins_all = bool(binding) and all(v.is_win for v in binding)
    coverage_ok = coverage >= COVERAGE_FLOOR
    # Coverage is a GATE, not a sibling: a clean sweep on a tiny slice of the
    # corrections is decoration-with-good-aim, not a spine. flip requires BOTH.
    flip = wins_all and coverage_ok

    if not binding:
        headline = "no binding baselines scored"
    elif flip:
        headline = (
            f"lens beats {', '.join(BINDING_BASELINES)} at {coverage:.0%} "
            f"coverage (≥{COVERAGE_FLOOR:.0%}) — first real evidence for the "
            f"named tensions"
        )
    elif wins_all and not coverage_ok:
        # The capstone honesty branch: it won the sign test on the axes it
        # claims, but it only speaks to a slice — NOT a flip, and the headline
        # must not say "evidence".
        headline = (
            f"lens wins on its claimed axes but covers only {coverage:.0%} of "
            f"corrections (< {COVERAGE_FLOOR:.0%} floor) — too narrow to be the "
            f"spine (decoration-with-good-aim); coverage-gated, not evidence"
        )
    elif any(v.verdict.startswith("abstain") for v in binding):
        worst = next(v for v in binding if v.verdict.startswith("abstain"))
        headline = (
            f"not enough independent signal yet — {worst.reason}. "
            f"Honest 'I don't know'; gate stays closed (accrues power as "
            f"corrections accumulate across threads)."
        )
    else:
        headline = (
            f"lens does NOT beat {', '.join(BINDING_BASELINES)} at this power "
            f"(coverage {coverage:.0%}) — not yet evidence the tensions predict"
        )

    return HoldoutScorecard(
        corrections_post_t=corrections_post_t,
        corrections_lens_speaks_to=corrections_lens_speaks_to,
        coverage=coverage,
        verdicts=verdicts,
        flip_recommended=flip,
        headline=headline,
        preregistration={
            "alpha": ALPHA,
            "n_c_floor": N_C_FLOOR,
            "min_discordant_pairs": MIN_DISCORDANT_PAIRS,
            "coverage_floor": COVERAGE_FLOOR,
        },
    )


# ── real-data feed (sketch — wired in a follow-up; needs the local embedder) ──
#
# ⚠ TEMPORAL-LEAKAGE TRAP — the one place the next green-while-degenerate would
#   hide (review note 2026-06-02). lens@T must be the lens you WOULD HAVE HAD at T,
#   so EVERY corpus-derived ingredient must be REFIT on PRE-T corrections only:
#   the k-means BASINS, the lens_registry TENSIONS, and the per-axis embeddings.
#   The trap is INHERITANCE: if those were computed once on the FULL corpus and
#   lens@T just re-projects them, the lens has already seen post-T structure →
#   a spurious win (the same bug class, arriving by LOOKAHEAD instead of by
#   coverage). Fixed prototype axes (concrete↔abstract, action↔description, …) are
#   corpus-INDEPENDENT and safe to reuse; anything corpus-DERIVED is pre-T or it
#   leaks. Also report the COVERAGE NUMERATOR (corrections_lens_speaks_to)
#   honestly — that + the split are the two surfaces where the measurement could
#   lie. (Send build_holdout_items for a split/leakage review before trusting any
#   verdict it produces.)
#
# build_holdout_items(): temporal-split corrections at T (prompt_id → prompt_node
# timestamp), build geometric lens@T from pre-T corrections (basins + registry +
# axes refit on pre-T ONLY — see trap above), then for each post-T correction ×
# each lens-claimed tension axis (embed(pole_b) − embed(pole_a)):
#   actual_sign  = sign(projection of the correction vector onto the axis)
#   lens_sign    = pre-T majority projection sign on that axis (the lens's claim)
#   recency_sign = sign of the projection of the LAST pre-T correction IN THE SAME
#                  BASIN on that axis — basin-LOCAL recency, NOT global-last.
#                  Global-last is the weak baseline (the lens beats it cheaply);
#                  basin-local is the adversarial one, so the recency leg of the
#                  conjunction stays load-bearing (review note 2026-06-02).
#   basin_sign   = sign of the basin-centroid correction's projection
# thread_id = prompt_node.transcript_id. Pure-local embeddings (no quota); the
# geometric lens is contamination-immune (no chairman, no #263 session-riding).
