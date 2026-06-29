"""me/preference_collapse.py — the lens self-preference / preference-collapse METER.

The constitution's regression gate (`me/regression_gate.py`) catches a candidate tension
that *contradicts* a past preference. It does NOT catch the lens quietly **collapsing to a
fixed point** — rewarding whatever already looks the user's shape and going blind to a
better answer that doesn't match its current surface features. That is the failure the
Red Queen Gödel Machine (arxiv 2606.26294) names: their strongest reviewer over-accepts
work that resembles what it recognizes (up to 1.91× the human rate), and it has no
regression test — only a held-out anchor catches it.

This is that detector, transposed to taste (the weakest anchor there is — sparse, noisy,
no labeled benchmark), and built **meter-first, not policy** (cf. `correction_residual` —
"prototype the meter, not the policy"):

  1. Fit the lens's aggregate taste DIRECTION on a TRAIN split of the user's corrections.
  2. On a held-out VALIDATION split, a **false-accept** is a correction the fitted direction
     ranks the REJECTED output (`sacrificed`) ABOVE the user's own substitute (`privileged`)
     — the lens, fit on the rest, is blind to whatever axis actually drove this choice.
  3. A one-sided sign test (reused from `holdout_scorer`) asks whether the direction is
     *significantly predictive* on held-out data at all. If it isn't, the single-direction
     lens is collapsing context-dependent taste into one axis and is blind where it reverses.

**Data isolation** (RQGM's safety primitive, and Trinity's airgap as a data split): the
direction is fit on TRAIN and only ever *measured* on VALIDATION it wasn't fit on — so the
signal isn't the circular "the mean agrees with its own members." The returned
`false_accept_ids` are exactly the de-biasing adversarial samples a future lens epoch would
weight up (the RQGM mechanism) — but this module only *measures*; it never auto-weights.

Abstains (`ready: False`) under the TF-IDF fallback or thin splits — never emits a collapse
verdict it can't support. No LLM calls (embeddings only).

NOTE on the split: acts carry no timestamp, so the train/validation split is a deterministic
id-hash (stable, data-isolated, but not temporal). A temporal split (older fits, newer
measures — the honest "does the past lens predict the future") is the upgrade once acts carry
time, the same deferred wiring as `holdout_scorer.build_holdout_items`.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from .constitution import EmbedFn, _default_embed, _mean, _unit
from .holdout_scorer import one_sided_sign_p
from .preference_acts import MODEL_MISS, PreferenceAct

MIN_TRAIN = 8           # below this the fitted direction is noise → abstain
MIN_VALIDATION = 8      # below this a held-out rate is unreachable → abstain
_AXIS_NOISE = 0.02      # |projection| below this → the act doesn't load on the direction
COLLAPSE_P = 0.05       # the direction must predict held-out signs at this to read "ok"

_VAL_HASH_BUCKETS = 10  # ~30% to validation (buckets 0,1,2)
_VAL_CUTOFF = 3


def _usable(acts: Iterable[PreferenceAct]) -> list[PreferenceAct]:
    return [
        a for a in acts
        if a.trigger == MODEL_MISS
        and len((a.sacrificed or "").strip()) > 4
        and len((a.privileged or "").strip()) > 2
    ]


def _split(acts: list[PreferenceAct]) -> tuple[list[PreferenceAct], list[PreferenceAct]]:
    """Deterministic id-hash split (~70 train / 30 validation). Stable across runs and
    data-isolated; not temporal (acts carry no time field — see the module note)."""
    train: list[PreferenceAct] = []
    val: list[PreferenceAct] = []
    for a in acts:
        h = int(hashlib.sha1((a.id or "").encode("utf-8")).hexdigest(), 16)
        (val if h % _VAL_HASH_BUCKETS < _VAL_CUTOFF else train).append(a)
    return train, val


def _fit_direction(train: list[PreferenceAct], embed_fn: EmbedFn) -> list[float]:
    pri = [_unit(v) for v in embed_fn([a.privileged for a in train])]
    sac = [_unit(v) for v in embed_fn([a.sacrificed for a in train])]
    return _unit(_mean([[p - s for p, s in zip(pv, sv)] for pv, sv in zip(pri, sac)]))


def evaluate_split(
    train: list[PreferenceAct], val: list[PreferenceAct], *, embed_fn: EmbedFn
) -> dict:
    """Core (testable directly): fit the direction on TRAIN, score held-out VALIDATION.
    `verdict` is 'ok' when the direction significantly predicts held-out correction signs,
    'collapse' when it doesn't (the lens is blind where taste reverses)."""
    if len(train) < MIN_TRAIN or len(val) < MIN_VALIDATION:
        return {"ready": False, "reason": (
            f"thin splits (train {len(train)}/{MIN_TRAIN}, val {len(val)}/{MIN_VALIDATION})"
        )}
    direction = _fit_direction(train, embed_fn)
    if not any(direction):
        return {"ready": False, "reason": "degenerate train direction"}

    vp = [_unit(v) for v in embed_fn([a.privileged for a in val])]
    vs = [_unit(v) for v in embed_fn([a.sacrificed for a in val])]
    concordant = 0
    false_accepts: list[str] = []
    for a, pv, sv in zip(val, vp, vs):
        score = sum(d * (p - s) for d, p, s in zip(direction, pv, sv))
        if score > _AXIS_NOISE:
            concordant += 1                 # direction ranks the user's substitute higher
        elif score < -_AXIS_NOISE:
            false_accepts.append(a.id)       # direction ranks the REJECTED output higher

    n = concordant + len(false_accepts)
    if n == 0:
        return {"ready": False, "reason": "no held-out acts load on the lens direction"}
    # Underpowered guard: the loading filter above can shrink the effective sample (n)
    # far below the raw val split that cleared MIN_VALIDATION. If even a PERFECT split
    # (all concordant) can't reach COLLAPSE_P — i.e. the minimum achievable p,
    # one_sided_sign_p(n, n), is already >= COLLAPSE_P — the test CANNOT reject, so a
    # "collapse" verdict would be a false alarm on thin data (n<5 at p<0.05). Abstain
    # instead of crying wolf. (2026-06-29: a freshly-rebuilt lens with n=4 and ZERO
    # false-accepts was mislabeled "collapse" because min p = 0.0625 >= 0.05.) This is
    # the module's stated contract — "never emits a collapse verdict it can't support."
    min_p = one_sided_sign_p(n, n)
    if min_p >= COLLAPSE_P:
        return {"ready": False, "reason": (
            f"only {n} held-out act(s) load on the lens direction — underpowered "
            f"(even a perfect split gives p {min_p:.3f} >= {COLLAPSE_P}); can't test collapse"
        )}
    p = one_sided_sign_p(concordant, n)      # is the concordant majority significant?
    verdict = "ok" if p < COLLAPSE_P else "collapse"
    return {
        "ready": True,
        "train_n": len(train),
        "val_n": n,
        "false_accept_rate": round(len(false_accepts) / n, 3),
        "p": round(p, 4),
        "verdict": verdict,
        "false_accept_ids": false_accepts[:20],
        "reason": (
            "lens direction generalizes to held-out corrections"
            if verdict == "ok"
            else "lens direction does not reliably rank held-out corrections — possible "
                 "preference collapse (blind where taste reverses)"
        ),
    }


def lens_collapse_signal(
    acts: Iterable[PreferenceAct] | None = None, *, embed_fn: EmbedFn | None = None
) -> dict:
    """Public meter. Pulls model_miss acts from the ledger (unless given), splits, and
    evaluates. Abstains (`ready: False`) under the TF-IDF fallback or thin data — never a
    false collapse verdict."""
    if acts is None:
        from .preference_acts import iter_preference_acts
        acts = iter_preference_acts()
    usable = _usable(acts)
    if embed_fn is None:
        embed_fn = _default_embed()
    if embed_fn is None:
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}
    train, val = _split(usable)
    return evaluate_split(train, val, embed_fn=embed_fn)
