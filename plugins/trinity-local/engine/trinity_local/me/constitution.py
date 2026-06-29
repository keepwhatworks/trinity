"""me/constitution.py — the lens constitution's MINER, and the airgap *as a type*.

The self-improving lens is a separation of powers (the paper's φ(r)=(c,q,m) +
"The Architecture of Endurance", wall III):

    MINER (this module)  →  PROPOSER (the Stage-3 chairman)  →  VALIDATOR (regression_gate)

The miner reads the user's preference acts and emits an `EvidenceBundle`: failures
*clustered by what would FIX them* (a shared correction axis+sign — "patchable by the
same edit", not "reads the same"), each carrying a φ(r) signature whose **q** component
is the causal-status confound guard. The proposer reads the bundle and authors the lens
edit; the validator gates the write.

**The airgap is the data structure, not a policy.** `EvidenceBundle`/`EvidenceCluster`/
`EvidenceSignature` carry EVIDENCE and nothing that prescribes an edit — no pole, no
`LensPair`, no proposed edit, no registry handle, no verdict — and this module imports
nothing that can write the lens (`lens_registry`, `save_*`, `reconcile`, `me_path`). So
the optimizer beneath cannot reach up and rewrite the rule above it: it cannot take the
derivative of a constraint that isn't in its hands. `tests/test_constitution_airgap.py`
enforces both halves structurally (AST + dataclass introspection) — delete a ban, it reds.

No LLM calls (Architectural commitment #1). Embeddings are not LLM calls; under the
TF-IDF fallback the per-act axis geometry is meaningless, so the miner **abstains**
(`ready=False`) rather than cluster on word overlap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .correction_lens import TASTE_AXES
from .preference_acts import PreferenceAct, Q_CONFOUND, Q_OPERATIVE, Q_UNCERTAIN

EmbedFn = Callable[[list[str]], Sequence[Sequence[float]]]

# q-attribution thresholds (the geometric tier — observational, no LLM call). Calibrated
# against the correction-vector lens: a random unit vector loads ±1/√768 ≈ 0.036, the mean
# operative loading is ≈ 0.20, so a real driver sits clearly above 0.10.
Q_OPERATIVE_LOADING_FLOOR = 0.10
# When the top axis beats the runner-up by less than this, *which* axis drove the choice is
# genuinely ambiguous — attributing it to the top one is the confound risk, so → CONFOUND.
Q_AMBIGUITY_MARGIN = 0.05


# ── φ(r) = (c, q, m) ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidenceSignature:
    """One failure's signature. `act_id` points back at the ledger act (provenance).
    `terminal_cause` (c) is what the user rejected on; `q_axis`/`q_status` (q) is the
    causal-status confound guard; `mechanism` (m) is the abstract, reusable fix the act
    exposes. It names evidence — it does NOT name the edit."""

    act_id: str
    terminal_cause: str
    q_axis: str
    q_status: str
    mechanism: str


@dataclass(frozen=True)
class EvidenceCluster:
    """Signatures that share a FIX (same axis+sign), grouped because one lens edit would
    address them all. `operative_support` counts the signatures whose axis is the
    OPERATIVE driver; `confound_fraction` is the share flagged CONFOUND — the proposer
    refuses a cluster that is mostly confound (§2: a candidate ≥50% confound-backed is
    refused). The cluster carries no proposed edit; it stops at the shared mechanism."""

    fix_key: str
    mechanism: str
    signatures: tuple[EvidenceSignature, ...]
    operative_support: int
    confound_fraction: float


@dataclass(frozen=True)
class EvidenceBundle:
    """The miner's whole output — the typed airgap. Clustered evidence, and nothing that
    prescribes an edit. `ready=False` (with a `reason`) under TF-IDF / thin data: degrade
    honestly, never emit a confident wrong clustering."""

    fix_clusters: tuple[EvidenceCluster, ...]
    n_acts: int
    ready: bool
    reason: str = ""


# ── geometry helpers (local, so the constitution doesn't depend on another module's
#    private internals) ───────────────────────────────────────────────────────────────
def _unit(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else list(vec)


def _mean(rows: list[Sequence[float]]) -> list[float]:
    if not rows:
        return []
    dim = len(rows[0])
    acc = [0.0] * dim
    for r in rows:
        for i, x in enumerate(r):
            acc[i] += x
    return [x / len(rows) for x in acc]


def _default_embed() -> EmbedFn | None:
    """The real embedder, or None under the TF-IDF fallback (so the caller abstains)."""
    try:
        from ..embeddings import embed_batch, mlx_actually_loaded
    except Exception:
        return None
    if not mlx_actually_loaded():
        return None
    return embed_batch


def _axis_vectors(embed_fn: EmbedFn) -> dict[str, list[float]]:
    """The taste-axis unit frame, built from the shared TASTE_AXES prototypes via the SAME
    embedder, so act projections and the axis frame live in one space."""
    out: dict[str, list[float]] = {}
    for name, (pos, neg) in TASTE_AXES.items():
        pcen = _mean(list(embed_fn(pos)))
        ncen = _mean(list(embed_fn(neg)))
        out[name] = _unit([p - q for p, q in zip(pcen, ncen)])
    return out


def label_q_status(acts: Iterable[PreferenceAct], *, embed_fn: EmbedFn | None = None) -> int:
    """Observational q-attribution (the φ q-component), GEOMETRIC tier — no LLM call. For
    each act, project the unit steer ``unit(privileged) − unit(sacrificed)`` onto the taste
    axes and label, mutating ``q_axis``/``q_status`` in place:

      * top loading < ``Q_OPERATIVE_LOADING_FLOOR``        → UNCERTAIN (no axis clearly involved)
      * top − runner-up < ``Q_AMBIGUITY_MARGIN``           → CONFOUND  (which axis drove it is
                                                              genuinely ambiguous — the exact
                                                              "learned the confound" risk)
      * else                                               → OPERATIVE (one axis dominant)

    Returns the count labeled. **Abstains** (leaves acts untouched, returns 0) under the
    TF-IDF fallback — never attribute causality on word-overlap geometry. A semantic
    chairman cross-check (catching the content confound the geometry can't see, e.g.
    "took the short answer because it led with the statute") is a later, additive tier;
    this geometric tier already makes the confound guard bite on attribution ambiguity."""
    acts = list(acts)
    if embed_fn is None:
        embed_fn = _default_embed()
    if embed_fn is None:
        return 0
    usable = [
        a for a in acts
        if len((a.sacrificed or "").strip()) > 4 and len((a.privileged or "").strip()) > 2
    ]
    if not usable:
        return 0
    axis_vecs = _axis_vectors(embed_fn)
    pri = [_unit(v) for v in embed_fn([a.privileged for a in usable])]
    sac = [_unit(v) for v in embed_fn([a.sacrificed for a in usable])]
    labeled = 0
    for a, pv, sv in zip(usable, pri, sac):
        steer = [p - s for p, s in zip(pv, sv)]
        loads = sorted(
            (abs(sum(x * y for x, y in zip(steer, av))), name)
            for name, av in axis_vecs.items()
        )
        top_abs, top_name = loads[-1]
        second_abs = loads[-2][0] if len(loads) > 1 else 0.0
        if top_abs < Q_OPERATIVE_LOADING_FLOOR:
            a.q_axis, a.q_status = top_name, Q_UNCERTAIN
        elif top_abs - second_abs < Q_AMBIGUITY_MARGIN:
            a.q_axis, a.q_status = top_name, Q_CONFOUND
        else:
            a.q_axis, a.q_status = top_name, Q_OPERATIVE
        labeled += 1
    return labeled


def mine_evidence(
    acts: Iterable[PreferenceAct], *, embed_fn: EmbedFn | None = None
) -> EvidenceBundle:
    """Cluster preference acts by *what would fix them* — the dominant correction axis +
    sign — into an `EvidenceBundle`. Pure transform: reads acts, returns evidence,
    touches no state and cannot write the lens. Abstains (`ready=False`) under the TF-IDF
    fallback / no substantive acts."""
    acts = list(acts)
    n = len(acts)
    if embed_fn is None:
        embed_fn = _default_embed()
    if embed_fn is None:
        return EvidenceBundle((), n, ready=False, reason="needs real embeddings (install [mlx])")

    usable = [
        a for a in acts
        if len((a.sacrificed or "").strip()) > 4 and len((a.privileged or "").strip()) > 2
    ]
    if not usable:
        return EvidenceBundle((), n, ready=False, reason="no substantive acts to mine")

    axis_vecs = _axis_vectors(embed_fn)
    pri = list(embed_fn([a.privileged for a in usable]))
    sac = list(embed_fn([a.sacrificed for a in usable]))

    grouped: dict[tuple[str, str], list[EvidenceSignature]] = {}
    for a, pv, sv in zip(usable, pri, sac):
        steer = [p - s for p, s in zip(pv, sv)]
        best_axis: str | None = None
        best_load = 0.0
        for name, av in axis_vecs.items():
            load = sum(x * y for x, y in zip(steer, av))
            if abs(load) > abs(best_load):
                best_axis, best_load = name, load
        if best_axis is None or best_load == 0.0:
            continue  # no dominant axis — not clusterable by fix
        sign = "+" if best_load > 0 else "-"
        pole_a, pole_b = (p.strip() for p in best_axis.split("↔"))
        toward, away = (pole_a, pole_b) if best_load > 0 else (pole_b, pole_a)
        mechanism = f"favor {toward} over {away}"
        fix_key = f"{best_axis}:{sign}"
        sig = EvidenceSignature(
            act_id=a.id,
            terminal_cause=(a.why or a.kind or "").strip(),
            # Prefer the act's chairman-labeled axis (Phase B) when present; else the
            # geometric one. q_status defaults UNCERTAIN until labeled (the safe default).
            q_axis=a.q_axis or best_axis,
            q_status=a.causal_status(),
            mechanism=mechanism,
        )
        grouped.setdefault((fix_key, mechanism), []).append(sig)

    clusters: list[EvidenceCluster] = []
    for (fix_key, mechanism), sigs in grouped.items():
        op = sum(1 for s in sigs if s.q_status == Q_OPERATIVE)
        cf = sum(1 for s in sigs if s.q_status == Q_CONFOUND) / len(sigs)
        clusters.append(EvidenceCluster(
            fix_key=fix_key,
            mechanism=mechanism,
            signatures=tuple(sigs),
            operative_support=op,
            confound_fraction=round(cf, 3),
        ))
    # Order by support so the proposer sees recurrent, high-value mechanisms first
    # (the paper's anti-overfit ordering).
    clusters.sort(key=lambda c: (-len(c.signatures), c.fix_key))
    return EvidenceBundle(tuple(clusters), n, ready=True)
