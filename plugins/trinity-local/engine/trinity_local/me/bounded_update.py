"""Bounded, trust-region update for the lens — SkillOpt's edit budget applied to taste.

The dream currently re-summarizes the WHOLE lens each cycle: one chairman call overwrites
lens.md wholesale (me_builder), guarded only against the extreme →empty downgrade (#295).
That is an *unbounded rewrite* — the instability SkillOpt (arXiv 2605.23904) addresses with
a textual learning rate / edit budget, and the general case of the #295 clobber bug.

This bounds how far one build can move the lens. Given the PRIOR lens and a CANDIDATE the
build produced:
  - tensions the candidate still supports (embedding-matched to a prior) are KEPT, as the
    prior wording — the stable core doesn't churn;
  - at most ``budget`` genuinely-new tensions are admitted;
  - at most ``budget`` established tensions are dropped per cycle;
  - a DEGENERATE candidate (empty, or sharing nothing with the prior = a total rewrite) is
    REFUSED — the prior is kept unchanged (this is the #295 clobber guard generalized);
  - ``protected`` keys are never removed (the slow-update / core region).

Net: the lens becomes a slowly, verifiably-evolving object instead of a freshly-rewritten
one — "context is the durable asset" made operational. This is the REWARD-FREE half: a pure
trust-region / learning-rate cap. The held-out acceptance GATE (accept an edit only if it
lifts a held-out score) is a separate piece that waits for #296's outcome reward.

Pure + dependency-injected (``embed_fn``, ``key``) so it tests without the real embedder and
wires into either lens-build path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class UpdateReport:
    """What the bounded update did — for logging + the launchpad (auditability is the
    point: you can see the lens evolve edit-by-edit)."""
    kept: int            # prior tensions the candidate still supports
    added: int           # new tensions admitted this cycle
    removed: int         # established tensions dropped this cycle
    refused: bool        # True when the candidate was degenerate → prior kept unchanged
    reason: str = ""


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return num / (na * nb)


# Match floor for the drift-stable-core INTERSECTION — deliberately STRICTER than the
# registry's accretion threshold (0.80). Accretion's 0.80 is tuned to AVOID splitting a
# tension run-to-run, i.e. it leans TOWARD matching; reused for the intersection it would
# protect a persistent-old contamination tension that only coincidentally rhymes with some
# clean candidate — erring liberal, the unsafe direction. The intersection's whole value is
# that it "can only err conservative (protect too little)", and that holds only if the match
# leans toward NON-match. So the protect floor is higher: a persistent-old tension is
# protected only if a clean-rebuild candidate STRONGLY restates it. (Same shape as the
# green-gate discipline: a loose match is a green that passes on coincidental rhyme.)
_DRIFT_STABLE_MATCH_MIN = 0.85


def drift_stable_core(
    persistent_old: list[T],
    clean_probes: Sequence[str],
    *,
    key: Callable[[T], str],
    embed_fn: Callable[[str], Sequence[float]],
    match_threshold: float = _DRIFT_STABLE_MATCH_MIN,
) -> list[T]:
    """The drift-stable core = (persistent in the OLD trajectory) ∩ (present in the CLEAN
    rebuild). Returns the subset of ``persistent_old`` whose probe (via ``key``) strictly
    matches some probe in ``clean_probes`` (the clean rebuild's tension probe texts).

    Why the intersection and not persistence alone (the founder's catch): persistence over a
    *contaminated* history is a degenerate green — the drivers ("continue"/"status") were
    typed every chapter, so a contamination-derived tension is MAXIMALLY persistent. Protect
    by persistence and you freeze the most stable dirt first. The clean-rebuild side excludes
    stable contamination (persistent-old but GONE once the drivers are de-weighted); the
    persistence side excludes transient clean-noise (in one clean build but never stable).
    Only the intersection is both stable AND uncontaminated.

    Failure modes (all conservative-safe):
      • contamination tension — persistent-old, absent-clean → excluded;
      • real taste — both → protected;
      • clean noise — clean-only, not in ``persistent_old`` → cannot be returned;
      • a genuinely new tension — not yet persistent → not protected, but unprotected ≠
        deleted (it survives as a normal tension and accretes forward as it persists).

    Pure + dependency-injected (``key``, ``embed_fn``) — tests without the real embedder,
    wires into either lens-build path. Empty either side → empty intersection."""
    if not persistent_old or not clean_probes:
        return []
    clean_vecs = [embed_fn(p) for p in clean_probes if p]
    if not clean_vecs:
        return []
    out: list[T] = []
    for item in persistent_old:
        probe = key(item)
        if not probe:
            continue
        v = embed_fn(probe)
        if any(_cos(v, cv) >= match_threshold for cv in clean_vecs):
            out.append(item)
    return out


def bounded_update(
    prior: list[T],
    candidate: list[T],
    *,
    key: Callable[[T], str],
    embed_fn: Callable[[str], Sequence[float]],
    budget: int = 2,
    match_threshold: float = 0.80,
    protected: Callable[[T], bool] | None = None,
) -> tuple[list[T], UpdateReport]:
    """Merge ``candidate`` into ``prior`` under an edit budget. Returns (merged, report)."""
    if budget < 0:
        raise ValueError("budget must be >= 0")
    protected = protected or (lambda *_: False)

    # Cold start: no prior lens → adopt the candidate as-is (nothing to bound against).
    if not prior:
        return list(candidate), UpdateReport(kept=0, added=len(candidate), removed=0,
                                             refused=False, reason="cold-start")

    prior_vecs = [embed_fn(key(it)) for it in prior]
    cand_vecs = [embed_fn(key(it)) for it in candidate]

    # Which prior items does the candidate still support? (best candidate match >= threshold)
    prior_supported = [False] * len(prior)
    cand_is_new = [True] * len(candidate)
    for ci, cv in enumerate(cand_vecs):
        best_i, best_s = -1, match_threshold
        for pi, pv in enumerate(prior_vecs):
            s = _cos(cv, pv)
            if s >= best_s:
                best_i, best_s = pi, s
        if best_i >= 0:
            prior_supported[best_i] = True
            cand_is_new[ci] = False  # this candidate restates an existing tension

    # DEGENERATE candidate (empty, or shares nothing with the prior = a total rewrite):
    # refuse it and keep the prior unchanged. This is the #295 clobber guard generalized
    # from "→ empty" to "→ wholesale replacement".
    if not candidate or not any(prior_supported):
        return list(prior), UpdateReport(
            kept=len(prior), added=0, removed=0, refused=True,
            reason="degenerate candidate (empty or no overlap with prior) — prior kept",
        )

    new_items = [it for it, isnew in zip(candidate, cand_is_new) if isnew]

    # Remove at most `budget` established tensions per cycle — and never a protected one.
    # Prior is strength-ordered (front = strongest), so we drop from the WEAK end first.
    # Index-based so dataclass equality/identity never enters in.
    dropped_idx = [i for i, sup in enumerate(prior_supported) if not sup]
    removable_idx = [i for i in dropped_idx if not protected(prior[i])]
    remove_idx = set(removable_idx[-budget:]) if budget else set()

    survived_prior = [prior[i] for i in range(len(prior)) if i not in remove_idx]
    admitted_new = new_items[:budget]

    merged = survived_prior + admitted_new
    report = UpdateReport(
        kept=sum(prior_supported),
        added=len(admitted_new),
        removed=len(remove_idx),
        refused=False,
    )
    return merged, report
