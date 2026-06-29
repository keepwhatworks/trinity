"""Tier-2 eval trust gate — SET composition (the second tier).

Tier 1 (`baseline_floor`) asks "is each ITEM valid + does the judge discriminate?"
Tier 2 asks the orthogonal question: "is the SET balanced, diverse, and big enough
per cell to mean anything?" An all-valid-items set can still be a skewed, one-thread-
dominated, single-axis benchmark — and the headline aggregate hides it.

These are the green-gate disqualifiers (#35) lifted from the item to the SET, checked
as DISTRIBUTIONS with pre-registered floors:

  - axis balance: no single rejection axis may exceed MAX_AXIS_SHARE of the items
    (else the "score on YOUR kind of question" headline is really "score on REFRAME").
  - per-axis reportability: an axis with < MIN_AXIS_N items must NOT get its own
    per-axis bar (COMPRESSION=2 reading as a real score is the live bug, #281).
  - thread concentration: no single transcript may exceed MAX_THREAD_SHARE of the
    items (one conversation shouldn't be a sixth of your "taste").
  - source diversity: at least MIN_THREADS distinct transcripts must be represented.

Pure data — no LLM, no judge calls. Cheap enough to run on every build and refuse /
caveat the headline when the SET is degenerate, the same way baseline_floor refuses
when the JUDGE is degenerate.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# Pre-registered floors (green-gate: the disqualifier lives IN the gate, with a
# committed threshold, not a proxy tuned after the fact).
MAX_AXIS_SHARE = 0.60       # one axis > 60% of items → the headline is that axis
MIN_AXIS_N = 5              # < 5 items in an axis → its per-axis score is noise
MAX_THREAD_SHARE = 0.15     # one transcript > 15% of items → over-weighted source
MIN_THREADS = 5            # < 5 distinct transcripts → not a corpus, an anecdote

# Per-PROVIDER per-axis floor for a cross-provider LEADER claim. Distinct from
# MIN_AXIS_N above: MIN_AXIS_N gates whether an axis is reportable AT ALL on the
# SET (total items in that axis); MIN_AXIS_LEADER_N gates whether a head-to-head
# "X leads axis Y" claim is publishable — it needs enough items PER CONTENDER, so
# a 0.7 mean-spread at n=2 doesn't read as signal. The single source of truth for
# the per-axis-leader suppression that the launchpad chips, the eval-share PNG
# matrix de-emphasis, and the CLI `eval-show`/`eval-share --compare --by-axis`
# leader lines all share. It was hardcoded `MIN_AXIS_SAMPLES = 3` in FIVE places
# (eval_card.py, launchpad_data.py, commands/eval.py ×2) with "same threshold"
# comments cross-referencing each other — a drift trap: bump one and the surfaces
# disagree about which axis claims are publishable (the #281 confidence-honesty
# class). Import this; never re-hardcode the literal.
MIN_AXIS_LEADER_N = 3      # < 3 items PER CONTENDER on an axis → leader-by-noise

# Minimum number of DISTINCT providers (contenders) on the leaderboard before a
# per-axis "X leads axis Y" callout — or ANY "leader" claim — may be emitted. A
# "leader" with one contender is the council-card SOLO-OVERCLAIM shape (#35
# green-while-degenerate): the lone provider "leads" because nobody else ran, not
# because it won a contest. The eval-share PNG matrix card already gates on this
# (`_distinct_target_count(rows) <= 1` → demote-not-hide), but the CLI
# `eval-show`/`eval-share --compare --by-axis` text + JSON and the launchpad
# per_axis_leader chips were the asymmetric siblings that shipped the lone-
# provider "leader" anyway ("Per-axis leader: REFRAME → claude (0.84)" with claude
# the ONLY provider scored). Single source of truth — import, never re-hardcode.
MIN_AXIS_LEADER_CONTENDERS = 2  # < 2 distinct providers → no head-to-head leader

# Precision (decimal places) at which two leaderboard scores read as TIED. The
# leaderboard rows + CLI margin display the aggregate at 3dp ("0.750", "+0.000");
# the per-axis chips/cards display at 2dp ("0.75"). A "leader" callout whose
# margin formats to "+0.000"/"+0.00" is naming the winner of a contest that ENDED
# TIED — the same green-gate green-while-degenerate shape (#35) the per-task-type
# routing cheat-sheet already demotes ("tied 2 of 4" not "Best: X"). Two scores
# that round to the same displayed value can't be told apart by the reader, so
# they MUST NOT carry a "X leads Y by +0.000" / "X is best at axis" claim — that's
# a false winner on a PUBLIC, journalist-screenshottable surface. Detect the tie
# at the SAME precision the surface shows, so the gate fires exactly when the
# rendered margin would be all-zeros. Single source of truth: the aggregate
# leaderboard (CLI eval-show --compare, the eval-share compare PNG, the launchpad
# leaderboard headline) uses TIE_DP_AGGREGATE; the per-axis leader chips (CLI text,
# the matrix PNG, the launchpad wedge chips) use TIE_DP_AXIS.
TIE_DP_AGGREGATE = 3  # leaderboard / CLI margin precision — tie iff round(.,3) equal
TIE_DP_AXIS = 2       # per-axis chip / matrix precision — tie iff round(.,2) equal


def scores_tied(a, b, *, dp: int = TIE_DP_AGGREGATE) -> bool:
    """True when two scores are TIED at the surface's displayed precision.

    A "leads"/"is best at" claim with a runner-up this close paints a winner of
    a contest that actually ended even (the routing cheat-sheet's "tied" shape on
    the eval surfaces). Returns False if either value is missing/non-finite — a
    null score has no claimable rank, so it's never "tied" (the leader gate's own
    None-handling already drops it). `dp` matches the precision the SURFACE shows:
    aggregate leaderboard/margin = 3dp; per-axis chips/cards = 2dp. The tie test
    is `round(a, dp) == round(b, dp)`, so it fires exactly when the rendered
    margin would print as all-zeros — never on a 0.001 lead the 3dp row shows.
    """
    import math

    if a is None or b is None:
        return False
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(fa) and math.isfinite(fb)):
        return False
    return round(fa, dp) == round(fb, dp)


def top_two_tied(rows, *, dp: int = TIE_DP_AGGREGATE) -> bool:
    """True when the top-two leaderboard rows tie on aggregate_score at `dp`.

    `rows` is the aggregate-desc-sorted leaderboard (the same shape every eval
    surface passes around: dicts carrying `aggregate_score`). A "X leads Y by
    +0.000" headline / "+0.000 ahead of" subhead on such a state is a FALSE
    tie-winner (#35 green-while-degenerate) on a public card. Surfaces gate their
    "leads"/margin claim on `not top_two_tied(rows)` and fall back to honest
    "tied at S" framing. Fewer than 2 rows, or a null top-2 score, is not a tie.
    """
    if not rows or len(rows) < 2:
        return False
    a = rows[0].get("aggregate_score") if isinstance(rows[0], dict) else None
    b = rows[1].get("aggregate_score") if isinstance(rows[1], dict) else None
    return scores_tied(a, b, dp=dp)


def distinct_target_count(rows) -> int:
    """How many DISTINCT (normalized) providers a leaderboard row set has.

    A "leads"/"leader" verdict needs >= MIN_AXIS_LEADER_CONTENDERS contestants.
    Folds web-era capture slugs to dispatch slugs so two runs of the same provider
    (a `gemini` capture + an `antigravity` CLI run) never read as two contenders.
    Each row is a dict carrying a "target" slug; empty/None targets don't count.
    The single source of truth shared by eval_card.py, commands/eval.py, and
    launchpad_data.py so all per-axis-leader surfaces agree on "is this a contest?"
    """
    from ..council_schema import normalize_provider_slug

    seen: set[str] = set()
    for r in rows or []:
        t = r.get("target") if isinstance(r, dict) else None
        if not t:
            continue
        seen.add(str(normalize_provider_slug(t) or t))
    return len(seen)


@dataclass
class CompositionVerdict:
    n_items: int
    by_axis: dict[str, int]
    axis_share: float                       # share of the single largest axis
    dominant_axis: str | None
    reportable_axes: dict[str, bool]        # axis -> has >= MIN_AXIS_N items
    n_threads: int
    thread_share: float                     # share of the single largest transcript
    dominant_thread: str | None
    violations: list[str] = field(default_factory=list)
    balanced: bool = False                  # no hard violations

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def evaluate_composition(
    items,
    *,
    prompt_id_to_thread: dict[str, str] | None = None,
) -> CompositionVerdict:
    """Assess the composition of an eval-item collection.

    `items` is any iterable of objects with `.rejection_type` and `.prompt_id`.
    `prompt_id_to_thread` maps a prompt_id to its transcript id; when omitted,
    thread-concentration checks are skipped (n_threads/thread_share = 0/None-ish)
    so the gate degrades gracefully rather than crashing.
    """
    items = list(items)
    n = len(items)

    by_axis = Counter(getattr(it, "rejection_type", None) or "?" for it in items)
    dominant_axis, dom_axis_n = (by_axis.most_common(1)[0] if by_axis else (None, 0))
    axis_share = (dom_axis_n / n) if n else 0.0
    reportable_axes = {ax: cnt >= MIN_AXIS_N for ax, cnt in by_axis.items()}

    n_threads = 0
    thread_share = 0.0
    dominant_thread = None
    if prompt_id_to_thread:
        threads = Counter(
            prompt_id_to_thread.get(getattr(it, "prompt_id", "") or "", "")
            for it in items
        )
        threads.pop("", None)  # un-mappable items don't count toward concentration
        if threads:
            n_threads = len(threads)
            dominant_thread, dom_thr_n = threads.most_common(1)[0]
            thread_share = dom_thr_n / n if n else 0.0

    violations: list[str] = []
    if n == 0:
        violations.append("empty eval set")
    if axis_share > MAX_AXIS_SHARE:
        violations.append(
            f"axis imbalance: {dominant_axis} is {axis_share:.0%} of items "
            f"(> {MAX_AXIS_SHARE:.0%}) — the headline is really a {dominant_axis} score"
        )
    thin = sorted(ax for ax, ok in reportable_axes.items() if not ok)
    if thin:
        violations.append(
            f"thin axes (< {MIN_AXIS_N} items, do NOT show a per-axis score): "
            + ", ".join(f"{ax}={by_axis[ax]}" for ax in thin)
        )
    if prompt_id_to_thread:
        if thread_share > MAX_THREAD_SHARE:
            violations.append(
                f"thread concentration: one transcript is {thread_share:.0%} of items "
                f"(> {MAX_THREAD_SHARE:.0%})"
            )
        if n_threads and n_threads < MIN_THREADS:
            violations.append(
                f"source too narrow: only {n_threads} distinct transcripts "
                f"(< {MIN_THREADS})"
            )

    return CompositionVerdict(
        n_items=n,
        by_axis=dict(by_axis),
        axis_share=round(axis_share, 4),
        dominant_axis=dominant_axis,
        reportable_axes=reportable_axes,
        n_threads=n_threads,
        thread_share=round(thread_share, 4),
        dominant_thread=dominant_thread,
        violations=violations,
        balanced=not violations,
    )


def prompt_id_to_thread_map() -> dict[str, str]:
    """prompt_id → transcript_id over the live node index, for the thread checks."""
    from ..memory.store import iter_prompt_nodes_no_embedding
    return {
        getattr(n, "id", ""): (getattr(n, "transcript_id", "") or "")
        for n in iter_prompt_nodes_no_embedding(limit=None)
        if getattr(n, "id", "")
    }
