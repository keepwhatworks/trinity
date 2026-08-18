"""Deterministic routing derived from the LENS basins — the cortex collapse (#298).

Council `council_0dd6ee69698d620b` (unanimous) + founder directive: remove the
cortex's trust/centroid ENGINE, derive routing from the already-solid lens. The
job — given a task, pick which provider should handle it — collapses to:

  1. place the task in one of the lens's 48 basins (`topics.json`, which already
     carries live 768-d centroids rebuilt daily with the lens), and
  2. read that basin's recency-weighted chairman-winner tally over real-contest
     councils.

No 6-component trust score, no SEPARATE cortex centroids (so #277 — stale cortex
centroids in an orthogonal embedding space — is structurally impossible, because
the only centroids are the lens's, always in the live space), no per-basin LLM
extraction. The chairman's winner stays the sole signal (no user-pick layer).

This module is the CONSTRUCTIVE half (increment A of #298): the pure tally +
a thin production loader. Wiring `ask` onto it and deleting the old
trust/centroid engine are later increments — so this file is purely additive and
changes no existing behavior yet.
"""
from __future__ import annotations

from typing import Any, Callable

from .embeddings.backend_tfidf import cosine_similarity

# lens_routing still CONSUMES the topology readers it no longer owns.
# They moved to me/basins.py on 2026-08-10 (step 2a of the routing removal)
# because their consumers are the lens and its surfaces, not the router --
# but consolidate_via_lens_basins and prune_orphan_rules read the topology
# too, and a consumer of a moved symbol is as valid as any other.
from .me.basins import load_topics_basins

# The LENS-placement gates. These are the SAME floors `ask._best_centroid_match`
# used for cortex placement — calibrated on the real corpus — kept here as the
# lens-basin placement gate. MATCH_FLOOR: below this query↔centroid cosine the
# task is out-of-domain (nearest basin is noise) → no basin, fall to kNN.
# MARGIN_FLOOR: a near-tie between the top two basins is an ambiguous placement
# → abstain rather than misroute. MIN_COUNT: a basin needs this many real-contest
# councils before its winner is trustworthy enough to route on.
MATCH_FLOOR = 0.36
MARGIN_FLOOR = 0.02
MIN_COUNT = 2
HALF_LIFE_DAYS = 30.0  # recency weight halves every N days, relative to the newest council

# The WINNER-decisiveness gate (distinct from MARGIN_FLOOR, which is about
# query↔basin PLACEMENT ambiguity). A basin whose winner only barely edged the
# runner-up — `margin` = (winner_weight − runner_weight)/total below this floor —
# is a coin flip, not a learned preference. `ask` routes on a basin only when its
# winner margin clears this; below it the tally is advisory (kNN decides), not
# decisive. This is the confidence floor the retired 6-component TrustScore band
# gate used to enforce, collapsed to the one quantity that actually carries the
# signal. Sampled on the real corpus: most basins sat at margin 0.00–0.08 (near
# ties); only ~4/23 cleared 0.15 decisively.
WINNER_MARGIN_FLOOR = 0.15

# Model-churn honesty (council_39e25084ea339099, 2026-07-04 — Design B + floor).
# An episode whose winner_model differs from that provider's CURRENTLY
# configured model is evidence about a model that no longer answers — it stays
# usable (Design C's reset discards too much) but at half weight (0.5 ratified
# by the claude+antigravity majority over codex's 0.25). winner_model=None
# (legacy records) is treated as stale/unknown — the agreed None-rule.
STALE_MODEL_DECAY = 0.5
# The council's key addition over plain Design B: margin-only decay fails when
# ALL of a basin's wins are stale — the margin survives while the evidence is
# dead, a confidently-stale pick. `effective_n` (the post-decay weight mass)
# must clear this floor or the basin refuses to route (falls to kNN).
MIN_EFFECTIVE_N = 3.0


def thompson_route(entry: dict, rng=None) -> str | None:
    """Exploration on coin-flip basins (council_9f4b3ab0a9b640c2, 2026-07-14).

    When `pick_routes` refuses a basin for MARGIN (a measured near-tie — the
    models are indistinguishable there), don't drop straight to kNN: sample
    each provider's Beta posterior on the basin's post-decay weight masses and
    route on the sample's argmax. This explores exactly where exploration is
    free (any pick is defensible where the posterior is flat) and exploits
    where it's sharp — no epsilon to tune, no user-visible cost. It NEVER
    fires where the evidence is stale or thin (effective_n below the same
    floor pick_routes uses): stale coin flips stay with kNN, because sampling
    dead evidence is exploration theater.

    Returns the sampled provider, or None when this path shouldn't decide
    (decisive basin, thin/stale evidence, no weights recorded yet — legacy
    entries gain `weights` on the next consolidate).
    """
    import random
    clean = _thompson_masses(entry)
    if clean is None:
        return None
    total = sum(clean.values())
    rng = rng or random
    # deterministic tie-break on slug so equal samples can't flip on dict order
    best = max(
        sorted(clean.items()),
        key=lambda kv: rng.betavariate(kv[1] + 1.0, max(total - kv[1], 0.0) + 1.0),
    )
    return best[0]


def _thompson_masses(entry: dict) -> dict[str, float] | None:
    """The positive per-provider weight masses a coin-flip basin would sample
    over, or None if the basin is NOT Thompson-eligible (decisive, thin/stale,
    or no recorded weights). One source of truth shared by `thompson_route`
    (which samples) and `thompson_eligible`/`classify_basins` (which surface),
    so the router and every card that labels a basin "explored" can never
    disagree on what exploration actually fires on."""
    if not isinstance(entry, dict):
        return None
    weights = entry.get("weights")
    if not isinstance(weights, dict) or len(weights) < 2:
        return None
    try:
        margin = float(entry.get("margin") or 0.0)
        eff = float(entry.get("effective_n") or 0.0)
    except (TypeError, ValueError):
        return None
    if margin >= WINNER_MARGIN_FLOOR:
        return None  # decisive basin — the winner routes, not a sample
    if eff < MIN_EFFECTIVE_N:
        return None  # stale/thin — kNN, not exploration theater
    total = 0.0
    clean: dict[str, float] = {}
    for prov, w in weights.items():
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if w > 0 and isinstance(prov, str) and prov:
            clean[prov] = w
            total += w
    if len(clean) < 2 or total <= 0:
        return None
    return clean


def thompson_eligible(entry: dict) -> bool:
    """Deterministic (no sampling) twin of `thompson_route`: True iff ask()
    would Thompson-EXPLORE this basin rather than route a winner or fall to
    kNN. The classification predicate for surfaces."""
    return _thompson_masses(entry) is not None


def classify_basins(rules: dict, basin_ids: set[str] | None = None) -> dict[str, int]:
    """Split a picks.json `rules` dict into the routing classes every surface
    reports — one source of truth so the launchpad card and the status
    liveness line can never disagree on how many basins actually route.
    `decisive`: ask() routes the winner (pick_routes). `explored`: ask()
    Thompson-samples a measured near-tie (thompson_eligible). `thin`: falls to
    kNN (stale, sub-count, or no weights).

    Pass `basin_ids` (the LIVE topics.json ids) to add a fourth class:
    `orphan` — a rule keyed to a basin that no longer exists. An orphan can
    never be reached, because `place_query` only ever returns a live basin id,
    so counting one as "decisive" overstates how much routing is actually
    wired. Measured 2026-07-31 on the founder's corpus: 6 of 31 rules were
    orphans and ONE of them (`b01d`, margin 0.35, effective_n 3.06) cleared the
    routing gate — so the surfaces read "4 basins route decisively" when only 3
    could ever fire.

    `basin_ids=None` means "the live basin set was not supplied", and the
    `orphan` key is then OMITTED rather than reported as 0. Reporting a
    reassuring zero for something we did not look at is the exact
    green-over-degenerate move this codebase keeps having to undo — and it
    keeps the default return shape byte-identical for existing callers."""
    if not isinstance(rules, dict):
        base = {"decisive": 0, "explored": 0, "thin": 0, "total": 0}
        return {**base, "orphan": 0} if basin_ids is not None else base
    decisive = explored = thin = orphan = 0
    for bid, entry in rules.items():
        if basin_ids is not None and str(bid) not in basin_ids:
            orphan += 1
            continue
        if isinstance(entry, dict) and pick_routes(entry):
            decisive += 1
        elif thompson_eligible(entry):
            explored += 1
        else:
            thin += 1
    out = {"decisive": decisive, "explored": explored, "thin": thin,
           "total": len(rules)}
    if basin_ids is not None:
        out["orphan"] = orphan
    return out


# ── Orphan pruning ─────────────────────────────────────────────────────
#
# picks.json is keyed by basin id; basin ids are assigned BY POSITION in
# `me/basins.py` (`b{i:02d}` after a size-descending sort) and re-drawn on every
# lens build. topics.json and picks.json are rebuilt by two INDEPENDENT kicks
# (`cold_start.maybe_kick_lens_refresh`, 30-min cooldown, vs
# `stale_pass.run_stale_pass`, 24-h window) with no ordering guarantee and no
# re-consolidate after a lens rebuild — so every lens build that lands between
# consolidates leaves rules pointing at ids that no longer exist.
#
# Pre-registered refusal floors. Pruning is a DELETE against the chairman's
# accumulated picks, so it must refuse rather than guess whenever the basin set
# it is checking against looks degenerate:
#   * fewer than MIN_BASINS_FOR_PRUNE live basins → the topology is unbuilt or
#     clobbered, and "not in the live set" would mean "not in an empty set".
#   * more than MAX_ORPHAN_DROP_FRACTION of the rules would go → that is not a
#     few stragglers, it is a whole-scheme change (renamed ids, a different
#     splitter). Keep everything and surface it; a human decides.
# Same shape as the #194 clobber guard on `save_routing_patterns`.
MIN_BASINS_FOR_PRUNE = 5
MAX_ORPHAN_DROP_FRACTION = 0.5

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




def prune_orphan_rules(
    rules: dict, basin_ids: set[str] | None,
    *,
    min_basins: int = MIN_BASINS_FOR_PRUNE,
    max_drop_fraction: float = MAX_ORPHAN_DROP_FRACTION,
) -> tuple[dict, list[str], str]:
    """Drop picks entries whose basin no longer exists in the lens topology.

    Returns ``(kept_rules, dropped_ids, reason)``. ``dropped_ids`` is empty
    whenever the prune refused, and ``reason`` always says which of the three
    outcomes happened — pruned / nothing to prune / refused — so a caller can
    never report "clean" off a refusal. Pure: the live basin set is injected."""
    if not isinstance(rules, dict):
        return {}, [], "picks rules are not a dict — nothing to prune"
    if not basin_ids:
        return dict(rules), [], (
            "no live basin set (topics.json missing, unreadable, or empty) — "
            "REFUSED: without a topology every rule would look orphaned"
        )
    if len(basin_ids) < min_basins:
        return dict(rules), [], (
            f"only {len(basin_ids)} live basin(s), below MIN_BASINS_FOR_PRUNE="
            f"{min_basins} — REFUSED: a degenerate topology cannot arbitrate "
            "which rules are dead"
        )
    orphans = sorted(str(bid) for bid in rules if str(bid) not in basin_ids)
    if not orphans:
        return dict(rules), [], (
            f"no orphans: all {len(rules)} rule(s) key a live basin "
            f"(of {len(basin_ids)})"
        )
    if rules and (len(orphans) / len(rules)) > max_drop_fraction:
        return dict(rules), [], (
            f"{len(orphans)}/{len(rules)} rules are orphaned, above "
            f"MAX_ORPHAN_DROP_FRACTION={max_drop_fraction} — REFUSED: that is a "
            "basin-id scheme change, not stale stragglers. Re-run "
            "`the retired consolidate verb` to rebuild the tally against the "
            "current topology instead of deleting most of it"
        )
    kept = {bid: entry for bid, entry in rules.items() if str(bid) in basin_ids}
    return kept, orphans, (
        f"pruned {len(orphans)} orphan rule(s) ({', '.join(orphans)}); "
        f"{len(kept)} of {len(rules)} kept against {len(basin_ids)} live basins"
    )




def pick_routes(entry: dict) -> bool:
    """THE routing gate for a picks.json entry — one predicate so ask(),
    get_picks, and every surface agree (the drift-by-two-copies trap).

    Routes iff margin >= WINNER_MARGIN_FLOOR AND the post-decay evidence mass
    clears MIN_EFFECTIVE_N (council_39e25084ea339099: margin alone false-greens
    a basin whose wins are all stale). Legacy entries written before the churn
    fields existed carry no effective_n — they fall back to the margin-only
    gate until the next consolidate rewrites them."""
    try:
        margin = float(entry.get("margin") or 0.0)
    except (TypeError, ValueError):
        return False
    if margin < WINNER_MARGIN_FLOOR:
        return False
    eff = entry.get("effective_n")
    if eff is None:
        return True  # legacy entry — margin-only until re-consolidated
    try:
        return float(eff) >= MIN_EFFECTIVE_N
    except (TypeError, ValueError):
        return False




def _to_epoch(iso: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def compute_basin_routing(
    councils: list[dict[str, Any]],
    basins: list[dict[str, Any]],
    embed_fn: Callable[[str], list[float]],
    *,
    match_floor: float = MATCH_FLOOR,
    margin_floor: float = MARGIN_FLOOR,
    min_count: int = MIN_COUNT,
    half_life_days: float = HALF_LIFE_DAYS,
    current_models: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-lens-basin recency-weighted chairman-winner tally.

    Args:
      councils: each ``{council_id, task_text, winner, substantive_members?,
        created_at?}``. A council with ``substantive_members < 2`` (a walkover,
        not a real contest) or no winner / no task_text is SKIPPED. The
        substantive-members gate stands in for the absent batch-vs-human
        provenance flag: batch sweeps are overwhelmingly single-target
        dispatches, so the real-contest filter drops most of them.
      basins: each ``{id, centroid}`` — the lens basins from topics.json. The
        centroid must be in the SAME embedding space as ``embed_fn`` (it is, by
        construction: both are the live lens embedder), so the #277 stale-space
        failure can't occur.
      embed_fn: ``text -> embedding``. Injected so tests are deterministic and
        the function is pure; production passes ``embeddings.embed``.

    Returns ``{basin_id: {winner, count, margin, n_episodes, evidence}}`` for
    basins that cleared ``min_count``. Basins with no/insufficient signal are
    OMITTED — the caller (ask) then falls through to kNN→heuristic, exactly as
    the inert cortex does today. Deterministic given its inputs (recency is
    measured relative to the newest council in the set, not wall-clock).
    """
    # Shape guard (#304 sibling): a corrupt/clobbered topics.json can hand us a
    # `basins` list whose ENTRIES are non-dicts; every access below is
    # `b.get(...)`. Filter to dicts at the canonical iteration point so
    # `consolidate` (which does NOT wrap this in try/except, unlike `ask`)
    # degrades gracefully — an empty tally — instead of crashing the CLI verb.
    basins = [b for b in basins if isinstance(b, dict)]
    # basin_id -> [(winner, weight, council_id, winner_model, fresh)]
    placed: dict[str, list[tuple[str, float, str, str, bool]]] = {}
    times = [_to_epoch(c.get("created_at") or "") for c in councils]
    t0 = max(times) if times else 0.0
    day = 86400.0

    # Same real-contest gate as the value-proof headline — one predicate so the
    # routing rules (picks.json) and the headline can't drift on the threshold.
    from .personal_routing import _is_real_contest

    for c in councils:
        if not _is_real_contest(c):
            continue  # walkover, not a real contest (stands in for the batch flag)
        # isinstance(..., str) shape-guards the STRING fields (Iter 257 class): a
        # corrupt non-string winner/task_text in a hand-edited council outcome would
        # hit `.strip()` on a non-str and crash basin-routing.
        winner_raw = c.get("winner")
        winner = winner_raw.strip() if isinstance(winner_raw, str) else ""
        task_raw = c.get("task_text")
        task = task_raw.strip() if isinstance(task_raw, str) else ""
        if not winner or not task:
            continue
        qv = embed_fn(task)
        if not qv:
            continue
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
            continue
        top1, bid = sims[0]
        top2 = sims[1][0] if len(sims) > 1 else 0.0
        if top1 < match_floor or (top1 - top2) < margin_floor:
            continue  # out-of-domain or ambiguous placement → no basin (kNN handles it)
        age_days = max(0.0, (t0 - _to_epoch(c.get("created_at") or "")) / day)
        weight = 0.5 ** (age_days / half_life_days) if half_life_days > 0 else 1.0
        # Model-churn decay (council_39e25084ea339099): an episode won by a
        # model that is NOT the provider's currently-configured one is stale
        # evidence — half weight. None winner_model = stale/unknown (the
        # agreed rule). When the current model for that provider is UNKNOWN
        # (no map entry — e.g. a provider removed from config), staleness
        # can't be assessed: no decay, matching pre-#churn behavior.
        wm_raw = c.get("winner_model")
        wm = wm_raw.strip() if isinstance(wm_raw, str) else ""
        cur = (current_models or {}).get(winner) or ""
        fresh = True
        if cur:
            fresh = bool(wm) and wm.lower() == cur.strip().lower()
            if not fresh:
                weight *= STALE_MODEL_DECAY
        placed.setdefault(bid, []).append(
            (winner, weight, str(c.get("council_id") or ""), wm or "unknown", fresh)
        )

    out: dict[str, dict[str, Any]] = {}
    for bid, rows in placed.items():
        if len(rows) < min_count:
            continue
        tally: dict[str, float] = {}
        models: dict[str, int] = {}
        # Identity-keyed weight mass (2026-07-14 — the founder's model x size x
        # effort fidelity requirement, additive: the slug `tally` above still
        # drives winner/margin so the routing gate is byte-unchanged; this only
        # ADDS a finer breakdown for the identity-aware surfaces + a future
        # Thompson-over-identity). Effort is "?" on councils dispatched before
        # the member-effort stamp, so cells accrue full fidelity forward.
        from .model_identity import parse_identity
        ident_weights: dict[str, float] = {}
        fresh_n = stale_n = 0
        for w, wt, _, wm, fresh in rows:
            tally[w] = tally.get(w, 0.0) + wt
            models[wm] = models.get(wm, 0) + 1
            ik = parse_identity(wm).label("family", "tier", "version", "effort")
            ident_weights[ik] = ident_weights.get(ik, 0.0) + wt
            if fresh:
                fresh_n += 1
            else:
                stale_n += 1
        # Highest recency-weighted tally wins the basin, tie-broken on the
        # provider slug so the stored/displayed winner is deterministic: two
        # providers with an EQUAL accumulated weight would otherwise resolve to
        # whichever appeared first in the council-scan-derived `tally` dict
        # order — so the basin WINNER written to picks.json (and shown on the
        # routing card / returned by get_picks) flipped on scan order. (A pure
        # tie yields margin 0 < WINNER_MARGIN_FLOOR so ask() abstains, but the
        # winner field is still surfaced.) Same canon as the chairman pick +
        # routing chip (b40807ec): max weight, lexically-smallest slug.
        ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(tally.values()) or 1.0
        runner_weight = ranked[1][1] if len(ranked) > 1 else 0.0
        out[bid] = {
            "winner": ranked[0][0],
            "count": len(rows),
            "margin": round((ranked[0][1] - runner_weight) / total, 3),
            "n_episodes": len(rows),
            # Post-decay weight mass — the honest sample size under model
            # churn. A basin whose wins are all stale keeps its margin but
            # loses effective_n; `pick_routes` refuses it below the floor.
            "effective_n": round(sum(wt for _, wt, _, _, _ in rows), 3),
            "fresh_n": fresh_n,
            "stale_n": stale_n,
            "models": models,
            # Identity-keyed masses (family·tier·version·effort) — the fidelity
            # breakdown for get_picks / the disagreement card / future
            # Thompson-over-identity. Additive; winner/margin unaffected.
            "identity_weights": {k: round(v, 3) for k, v in ident_weights.items()},
            # Per-provider post-decay masses (2026-07-14): the Thompson
            # exploration path samples these on coin-flip basins — the tally
            # computed them all along and used to discard them.
            "weights": {prov: round(w, 3) for prov, w in ranked},
            "evidence": [cid for _, _, cid, _, _ in rows if cid][:20],
        }
    return out


def place_query(
    query: str,
    basins: list[dict[str, Any]],
    embed_fn: Callable[[str], list[float]],
    *,
    match_floor: float = MATCH_FLOOR,
    margin_floor: float = MARGIN_FLOOR,
) -> str | None:
    """Place an incoming query into a lens basin (the ASK-time counterpart of the
    tally builder). Embed the query, take the nearest basin centroid, and apply
    the SAME placement gates the tally used: below ``match_floor`` the query is
    out-of-domain (nearest basin is noise); a top1−top2 gap below ``margin_floor``
    is an ambiguous placement. Either → return None so ``ask`` falls through to
    kNN→heuristic. Otherwise return the basin id whose winner tally routes the
    query. Pure (inject ``embed_fn``); production passes ``embeddings.embed``.

    This is increment B's ask-side primitive: building + testing it in isolation
    (like ``compute_basin_routing`` was for consolidate) means the live flip in
    the removed `ask` router was just `place_query(...) → routing[basin]['winner']`.
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


def _load_council_records() -> list[dict[str, Any]]:
    """Load council outcomes into the shape ``compute_basin_routing`` wants:
    ``{council_id, task_text, winner, substantive_members, created_at}``.

    Reuses ``council_analytics._scan_outcomes`` for the substantive-members count
    + chairman winner (the canonical real-contest logic), and pulls task_text
    from the outcome metadata. Best-effort; returns [] on any error so a missing
    corpus never breaks the caller.
    """
    try:
        from .council_analytics import _scan_outcomes
        from .council_runtime import load_council_outcome
    except Exception:
        return []
    records, _ = _scan_outcomes()
    out: list[dict[str, Any]] = []
    for r in records:
        cid = r.get("council_run_id")
        if not cid:
            continue
        task_text = ""
        oc = None
        try:
            oc = load_council_outcome(cid)
            task_text = (oc.metadata or {}).get("task_text") or ""
        except Exception:
            oc = None
        # winner_model: prefer the stamped outcome field; derive from the
        # winning member's model when absent (the browser-dispatch path
        # historically left it None — the enabler gap for model-churn decay).
        winner_slug = r.get("chairman_winner") or r.get("winner_provider") or ""
        winner_model = None
        try:
            if oc is not None:
                winner_model = getattr(oc, "winner_model", None)
                if not winner_model and winner_slug:
                    for m in (getattr(oc, "member_results", None) or []):
                        if getattr(m, "provider", None) == winner_slug and getattr(m, "model", None):
                            winner_model = m.model
                            break
        except Exception:
            winner_model = None
        out.append({
            "council_id": cid,
            "task_text": task_text,
            "winner_model": winner_model,
            "winner": winner_slug,
            "substantive_members": r.get("substantive_members", 2),
            # Carry the distinct-voice count through so picks.json shares the
            # value-proof's real-contest definition: a same-family chain
            # (claude·claude·claude — 3 substantive members, 1 distinct voice)
            # must be skipped by `_is_real_contest` here too, not just on the
            # value-proof headline. Defaults to 2 when absent (legacy records).
            "distinct_substantive_providers": r.get("distinct_substantive_providers", 2),
            # created_at lives on the CouncilOutcome top level (oc.created_at),
            # NOT on routing_label — CouncilRoutingLabel.to_dict() never emits it,
            # so the old `routing_label.created_at` binding was '' for EVERY
            # council (measured 634/634), silently zeroing every age and making
            # HALF_LIFE_DAYS inert: a basin's stale wins never decayed. `oc` is
            # already loaded above for winner_model; read the date off it too.
            "created_at": (getattr(oc, "created_at", None) or "") if oc is not None else "",
        })
    return out


def consolidate_via_lens_basins() -> dict[str, dict[str, Any]]:
    """Production wrapper: load the lens basins (topics.json) + council outcomes,
    embed each council's task_text, return the basin routing map. A cheap
    incremental READ — no LLM calls, NOT fused into the multi-minute daily lens
    build (the council's load-bearing caveat: routing must not lag fresh
    councils). Returns {} when the lens hasn't been built yet."""
    from .embeddings import embed

    basins = load_topics_basins()
    if not basins:
        return {}
    return compute_basin_routing(
        _load_council_records(), basins, embed,
        current_models=_current_models(),
    )


def _current_models() -> dict[str, str]:
    """provider slug → the model that CURRENTLY answers for it — the freshness
    reference for the model-churn decay. config.model per provider; for
    antigravity the agy CLI ignores config.model, so the live selection from
    agy's own settings wins when readable (read_agy_active_model_raw — the
    single honest source). Missing/None entries are omitted: unknown current
    model → staleness can't be assessed → no decay (documented in
    compute_basin_routing)."""
    out: dict[str, str] = {}
    try:
        from .config import load_config
        cfg = load_config(required=False)
        if cfg is None:
            return out
        for name, prov in cfg.providers.items():
            model = getattr(prov, "model", None)
            if name == "antigravity":
                try:
                    from .providers import read_agy_active_model_raw
                    model = read_agy_active_model_raw() or model
                except Exception:
                    pass
            if isinstance(model, str) and model.strip():
                out[name] = model.strip()
    except Exception:
        return {}
    return out
