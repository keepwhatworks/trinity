"""me/regression_gate.py — the lens constitution's VALIDATOR: gate the WRITE.

The third power. The miner (`me/constitution.py`) emits evidence and cannot write; the
proposer (the Stage-3 chairman) authors candidate tensions; this module gates whether a
candidate is allowed to ACCRETE into the durable registry — the only stage permitted to
touch the write surface (it imports `reconcile`, which the miner may not).

It implements the paper's held-out regression gate as a **preference-collapse detector**:
before a candidate tension is committed, re-check it against the append-only ledger (the
"stake" / "the memory that catches the drift first"). A `LensPair`'s `pole_a` is the
*optimized-for* axis and `pole_b` the *traded-away* one (the pair-mining contract), so the
candidate predicts the user steers **toward pole_a**. `flip_check` projects the held-out
corrections onto that axis: if a statistically significant majority steer toward **pole_b**
instead, the candidate contradicts a still-holding revealed preference — a flip — and is
refused. A genuine bidirectional tension (corrections going both ways) is balanced, so it
ABSTAINS — it never falsely blocks a real tension.

Safety (Trinity's #1 failure class — a green over degenerate data, and the #194/#295 clobber
class):
  * **Flag-gated, default OFF** (`TRINITY_REGRESSION_GATE`). Off → byte-identical to
    `reconcile(accepted)`. Arm only after the shadow logs look right.
  * **Shrink-only.** The gate can only DROP candidates, never add; it never passes
    `allow_shrink=True`, so `save_registry`'s clobber guard still fires on a cliff-drop — a
    wall of flips is treated as a degenerate build (keep prior), not "the user reversed
    everything".
  * **Abstain over wrong.** Under the TF-IDF fallback, thin held-out evidence, or a
    balanced tension, it does nothing.

No LLM calls (embeddings only; reuses `holdout_scorer.one_sided_sign_p` for the significance
test — direct projection, no change to that module). Recency: the acts handed in are already
the build's recency-biased sample, so "still-holding" is approximated by "in the current act
set" rather than an explicit date join (PreferenceAct carries no timestamp).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable

from .constitution import EmbedFn, _default_embed, _unit
from .holdout_scorer import one_sided_sign_p
from .pair_mining import LensPair
from .preference_acts import PreferenceAct

# Pre-registered floors (the green-gate discipline — registered in docs/green-gate-checklist.md).
MIN_HELDOUT_ACTS = 8        # fewer axis-loading held-out acts → abstain (significance unreachable)
REJECT_P = 0.05             # discordant majority significant at this → reject the candidate
FLAG_P = 0.20               # weaker discordance → flag (kept, but logged)
_AXIS_NOISE = 0.05          # |projection| below this → the act doesn't load on the axis; ignore it


@dataclass(frozen=True)
class FlipVerdict:
    verdict: str            # "pass" | "flag" | "reject" | "abstain"
    concordant: int         # held-out acts steering toward pole_a (support the candidate)
    discordant: int         # held-out acts steering toward pole_b (contradict the candidate)
    p: float | None         # one-sided sign-test p for the discordant majority (None when abstaining)
    reason: str = ""


def _steer_unit(embed_fn: EmbedFn, privileged: str, sacrificed: str) -> list[float]:
    pv, sv = embed_fn([privileged, sacrificed])
    return [p - s for p, s in zip(_unit(pv), _unit(sv))]


def flip_check(
    pair: LensPair, acts: Iterable[PreferenceAct], *, embed_fn: EmbedFn | None = None
) -> FlipVerdict:
    """Held-out preference-regression check for one candidate tension. Abstains unless a
    significant majority of axis-loading held-out corrections steer AGAINST the candidate's
    optimized-for pole."""
    if embed_fn is None:
        embed_fn = _default_embed()
    if embed_fn is None:
        return FlipVerdict("abstain", 0, 0, None, "needs real embeddings")
    pa, pb = (pair.pole_a or "").strip(), (pair.pole_b or "").strip()
    if not pa or not pb:
        return FlipVerdict("abstain", 0, 0, None, "candidate has an empty pole")

    axis = _unit([a - b for a, b in zip(*[_unit(v) for v in embed_fn([pa, pb])])])
    if not any(axis):
        return FlipVerdict("abstain", 0, 0, None, "degenerate axis")

    concordant = discordant = 0
    for act in acts:
        priv = (act.privileged or "").strip()
        sac = (act.sacrificed or "").strip()
        if len(sac) <= 4 or len(priv) <= 2:
            continue
        steer = _steer_unit(embed_fn, priv, sac)
        proj = sum(x * y for x, y in zip(steer, axis))
        if proj > _AXIS_NOISE:
            concordant += 1          # steered toward pole_a → supports the candidate
        elif proj < -_AXIS_NOISE:
            discordant += 1          # steered toward pole_b → contradicts the candidate

    n = concordant + discordant
    if n < MIN_HELDOUT_ACTS:
        return FlipVerdict("abstain", concordant, discordant, None,
                           f"only {n} axis-loading held-out acts < {MIN_HELDOUT_ACTS}")
    if discordant <= concordant:
        # Balanced or supportive — a healthy tension / confirmed direction. Never block.
        return FlipVerdict("pass", concordant, discordant, None, "held-out evidence supports or balances")
    p = one_sided_sign_p(discordant, n)
    if p < REJECT_P:
        return FlipVerdict("reject", concordant, discordant, p,
                           f"{discordant}/{n} held-out corrections steer toward pole_b (p={p:.3f})")
    if p < FLAG_P:
        return FlipVerdict("flag", concordant, discordant, p,
                           f"{discordant}/{n} lean toward pole_b (p={p:.3f}) — kept, watch")
    return FlipVerdict("pass", concordant, discordant, p, "discordant majority not significant")


def regression_gate_enabled() -> bool:
    """The gate is OFF unless TRINITY_REGRESSION_GATE is explicitly truthy — so the write
    path is byte-identical to today until the founder arms it after seeing shadow logs."""
    return os.environ.get("TRINITY_REGRESSION_GATE", "").strip().lower() in ("1", "true", "yes", "on")


def commit_through_gate(
    accepted: list[LensPair],
    *,
    acts: Iterable[PreferenceAct] | None = None,
    embed_fn: EmbedFn | None = None,
    reconcile_fn: Callable[[list[LensPair]], object] | None = None,
):
    """The single clobber-safe write path — wraps BOTH `reconcile(accepted)` sites so there
    is no divergent twin. Default OFF → returns `reconcile(accepted)` unchanged. When armed,
    drops only the candidates `flip_check` REJECTS (shrink-only; never grows, never passes
    `allow_shrink=True`), shadow-logs every non-pass verdict, then delegates to `reconcile`
    — whose `save_registry` clobber guard still catches a cliff-drop."""
    if reconcile_fn is None:
        from .lens_registry import reconcile as reconcile_fn  # the validator MAY write
    if not regression_gate_enabled():
        return reconcile_fn(accepted)

    act_list = list(acts or [])
    kept: list[LensPair] = []
    rejected = 0
    for pair in accepted:
        v = flip_check(pair, act_list, embed_fn=embed_fn)
        if v.verdict == "reject":
            rejected += 1
            print(
                f"  Regression gate: REJECT '{pair.pole_a} ↔ {pair.pole_b}' — {v.reason}",
                flush=True,
            )
            continue
        if v.verdict == "flag":
            print(f"  Regression gate: FLAG '{pair.pole_a} ↔ {pair.pole_b}' — {v.reason}", flush=True)
        kept.append(pair)
    if rejected:
        print(f"  Regression gate: dropped {rejected}/{len(accepted)} candidate(s) as flips", flush=True)
    return reconcile_fn(kept)
