"""Lens-basin placement for the scoped lens read.

What is live here: `place_query` (put a question in its nearest lens basin, or
abstain) and its two placement floors, used by `scope_for_query` /
`council_tensions` to hand a council the tensions of the question's own basin
(flag-gated off, hq_078).

What is not: this module once held the per-basin ROUTER — a recency-weighted
chairman-winner tally per lens basin (#298, council_0dd6ee69698d620b), the
`ask` gate over it, Thompson exploration on near-ties, and the picks.json
pruner. The router was removed 2026-08-11 (its held-out test scored at chance),
its writer `consolidate` with it, and the leftover code was retired 2026-10-26
once the memory viewer stopped showing picks.json (amd_0612). The tally lives
on, frozen, in internal/experiments/frozen_basin_routing.py, because the
pre-registered reopen condition below needs it to re-run T3/T4b.
"""

from __future__ import annotations

from typing import Any, Callable

from .embeddings.backend_tfidf import cosine_similarity


# The LENS-placement gates, calibrated on the real corpus. MATCH_FLOOR: below this
# query<->centroid cosine the task is out-of-domain (nearest basin is noise), so no
# basin. MARGIN_FLOOR: a near-tie between the top two basins is an ambiguous
# placement, so abstain rather than misplace.
MATCH_FLOOR = 0.36
MARGIN_FLOOR = 0.02


# ── Why there is no stable basin key (assessed + DECLINED 2026-07-31) ──
#
# The obvious repair for orphaned rules is to stop keying on an ordinal id and
# key on content instead — carry a rule across a rebuild by matching its old
# centroid to its nearest new centroid. It was assessed against the measured
# behaviour of this corpus (script `internal/experiments/basin_degeneracy.py`,
# results `internal/experiments/basin_degeneracy_results.json`, run with the real
# MLX embedder — backend `mlx`, nomic-ai/modernbert-embed-base:768 — over 40,236
# embedded nodes) and DECLINED. Three results, in the order that matters:
#
#   T3 — the thing a stable key would anchor to is not stable. Re-cluster the
#   SAME corpus at a different seed, and again on a 99% subsample: 52 ids
#   survive in each arm, and in BOTH the median membership Jaccard against the
#   base clustering is 0.000. Only 5.8% of ids reach Jaccard >= 0.5 (both arms);
#   at >= 0.8 it is 1.9% for the reseed arm and 0.0% for the subsample arm — i.e.
#   perturbing the corpus by ONE PERCENT leaves no id with a stable membership.
#   "Basin b07" is not a thing that persists and gets renumbered — it is
#   redrawn. A nearest-centroid re-key would therefore not RECOVER an identity,
#   it would MINT one, and every consumer would then read a stable id as
#   evidence of a stable topic.
#
#   T4b — and the payload attached to a surviving id is chance. Controlled arm:
#   same councils, same code, same corpus, only the clustering seed changed. Of
#   30 ids present in both runs the tallied winner disagrees on 16 (53.3%),
#   against an analytic chance rate of 55.7% and a permutation chance of 55.5%
#   (p5-p95 43.3-66.8%), p=0.476. A shared id predicts its own winner no better
#   than a random re-pairing does. n=30 is thin — this is "no evidence of
#   signal", not "proof of none" — but it is nowhere near a basis for building
#   machinery whose entire purpose is to preserve that payload.
#
#   T2 — the downstream claim does not discriminate either. Decisive routing
#   fires on 16.6% of 651 real council tasks and 16.0% of 200 word-salad nulls
#   (z=0.2, p=0.844). Placement itself does discriminate (65.0% vs 29.5%,
#   p<0.001); what does not is the routed VERDICT.
#
# So a stable keying scheme would be a stable pointer to something measured to
# carry nothing — a green over degenerate data with extra steps, and a harder
# one to see through than the orphans it replaces, because after re-keying the
# store would look continuous. Pruning is the honest handling: a rule whose
# basin was redrawn has lost its referent, and deleting it costs nothing that
# `pick_routes` was not already refusing to act on. The cheap structural half
# is not a key at all — it is ordering: re-consolidate after a lens build, so
# the tally is rebuilt against the topology it will be read with.
#
# PRE-REGISTERED REOPEN CONDITION (do not re-propose without it): re-run T3 and
# T4b and get median Jaccard >= 0.5 on shared ids AND a winner-disagreement rate
# at least 15 points below the permutation chance rate at n >= 60. Below that,
# any re-keying proposal is re-proposing this measurement's null.


def place_query(
    query: str,
    basins: list[dict[str, Any]],
    embed_fn: Callable[[str], list[float]],
    *,
    match_floor: float = MATCH_FLOOR,
    margin_floor: float = MARGIN_FLOOR,
) -> str | None:
    """Place a query into a lens basin, or abstain. Embed the query, take the
    nearest basin centroid, and apply the placement gates: below ``match_floor``
    the query is out-of-domain (nearest basin is noise); a top1−top2 gap below
    ``margin_floor`` is an ambiguous placement. Either → None, and the caller
    (``scope_for_query``) keeps the global tension slice. Otherwise the basin id.
    Pure (inject ``embed_fn``); production passes ``embeddings.embed``.

    (It was built as the ask-side half of the removed per-basin router; the
    frozen tally it was paired with shares MATCH_FLOOR / MARGIN_FLOOR.)
    """
    # Shape guard (#304 sibling): non-dict `basins` entries from a corrupt
    # topics.json crash the `b.get(...)` iteration below. ask wraps place_query
    # in try/except → kNN, but guard at the source so the degradation is by
    # design, not by rescue.
    basins = [b for b in basins if isinstance(b, dict)]
    qv = embed_fn(query)
    if not qv:
        return None
    sims = sorted(
        (
            (cosine_similarity(qv, b.get("centroid") or []), str(b.get("id") or ""))
            for b in basins
            if b.get("id")
        ),
        key=lambda s: s[0],
        reverse=True,
    )
    if not sims:
        return None
    top1, bid = sims[0]
    top2 = sims[1][0] if len(sims) > 1 else 0.0
    if top1 < match_floor or (top1 - top2) < margin_floor:
        return None
    return bid


def council_tensions(query: str, lens_md: str, k: int = 6) -> list[tuple[str, str]]:
    """The tensions a council is handed: the query's own basin when scoping is on (it is off by
    default), else the first k of the rendered lens. One rule for the chairman block and the
    member constraints; hq_133 freezes its output as the production-effective lens."""
    from .me.pipeline import _TENSION_HEADING
    return scope_for_query(query, k) or _TENSION_HEADING.findall(lens_md)[:k]


def scope_for_query(
    query: str,
    k: int = 6,
    *,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[tuple[str, str]]:
    """The k tensions belonging to the query's own basin, or [] to defer.

    Today every council is handed ``_TENSION_HEADING.findall(lens_md)[:6]`` --
    the first six of 167, ranked globally and INDEPENDENT of the question, with
    161 discarded per council. This selects by terrain instead.

    MEASURED before it was built (hq_078, `internal/experiments/
    terrain_scope_gate.py`, artifact `terrain_scope_gate_results.json`):
    permuting the basin label changes the payload by 96.7% against a
    pre-registered 25pp bar, over 109 councils with a resolvable basin. The
    basin is doing the selecting, so this tracks terrain rather than churning
    context. That was the whole point of the gate -- the 8.8%-overlap number
    that preceded it was disqualified by amd_0171 precisely because churn is
    not success.

    What the PASS does NOT license, and this docstring exists to hold the line
    (amd_0169/0170): no store, no CLI verb, no MCP resource, no ranking. And it
    says nothing about whether a chairman reading scoped tensions DECIDES
    differently -- that is unmeasured, against a consumer measured at 1/12
    causal, and needs its own registration.

    Flag-gated OFF (``TRINITY_DAG_SCOPED_LENS``). Returns [] for EVERY
    degradation -- flag off, no query, no basins, no registry, stub embedder,
    unplaceable query, empty scope -- so the caller keeps the global slice and
    the worst case is exactly today's behaviour.

    Reads lens artifacts and writes nothing back to them (founder-lock #1).
    """
    import os

    if os.environ.get("TRINITY_DAG_SCOPED_LENS", "0").strip().lower() not in ("1", "true"):
        return []
    if not (query or "").strip() or k <= 0:
        return []
    try:
        from .embeddings import embed, require_real_embedder
        from .me.basins import load_topics_basins
        from .me.lens_registry import registry_path, resolve_spanned_basins

        # Placement is semantic. Under the SHA-1 TF-IDF fallback the nearest
        # centroid is noise, and this repo's rule is that such flows ABSTAIN
        # rather than ship an inverted-TF-IDF answer.
        require_real_embedder()

        import json

        rp = registry_path()
        tensions = json.loads(rp.read_text(errors="replace")).get("tensions", []) if rp.exists() else []
        basins = load_topics_basins()
        if not tensions or not basins:
            return []

        basin = place_query(query, [dict(b) for b in basins], embed_fn or embed)
        if not basin:
            return []

        live_ids = {str(b.get("id") or "") for b in (dict(x) for x in basins)}
        out: list[tuple[str, str]] = []
        # Registry order IS lens.md order, which is the order the gate measured.
        # Re-ranking here would be a ranking layer, which amd_0169 killed.
        for t in tensions:
            if basin not in resolve_spanned_basins(t.get("basins_spanned") or [], live_ids):
                continue
            a, b = str(t.get("pole_a") or ""), str(t.get("pole_b") or "")
            if not a or not b:
                continue
            out.append((a, b))
            if len(out) >= k:
                break
        return out
    except Exception:
        # Any failure defers to the global slice. A scoped read is an
        # improvement on the margin; it is never worth a dead council.
        return []
