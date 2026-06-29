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


_TOPICS_BASINS_CACHE: tuple[str, float, list[dict]] | None = None


def load_topics_basins() -> list[dict]:
    """The SINGLE shape-guarded reader for `topics.json`'s basin list.

    Returns a list of dict basins (non-dict entries dropped), or ``[]`` for a
    missing / unreadable / non-JSON / wrong-shape file. Centralizes the guard
    `#304` added on the launchpad side but that `ask._try_cortex_route`,
    `consolidate_via_lens_basins`, and `milestones._count_basins` each open-coded
    differently (one with no top-level shape check at all) — a valid-JSON-but-
    wrong-shape `topics.json` (`{"basins": "x"}`) now degrades UNIFORMLY across
    every reader instead of crashing one and silently mis-routing another.

    Cached on the file's path + mtime so the repeat readers in one process
    (ask / consolidate / launchpad render / milestones) don't each re-parse.
    """
    global _TOPICS_BASINS_CACHE
    import json

    from .state_paths import topics_path

    path = topics_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _TOPICS_BASINS_CACHE = None
        return []
    if _TOPICS_BASINS_CACHE is not None and _TOPICS_BASINS_CACHE[:2] == (str(path), mtime):
        return _TOPICS_BASINS_CACHE[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _TOPICS_BASINS_CACHE = None
        return []
    raw = data.get("basins") if isinstance(data, dict) else None
    basins = [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
    _TOPICS_BASINS_CACHE = (str(path), mtime, basins)
    return basins


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
    placed: dict[str, list[tuple[str, float, str]]] = {}  # basin_id -> [(winner, weight, council_id)]
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
        placed.setdefault(bid, []).append((winner, weight, str(c.get("council_id") or "")))

    out: dict[str, dict[str, Any]] = {}
    for bid, rows in placed.items():
        if len(rows) < min_count:
            continue
        tally: dict[str, float] = {}
        for w, wt, _ in rows:
            tally[w] = tally.get(w, 0.0) + wt
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
            "evidence": [cid for _, _, cid in rows if cid][:20],
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
    ``ask._try_cortex_route`` is just `place_query(...) → routing[basin]['winner']`.
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

    Reuses ``personal_routing._scan_outcomes`` for the substantive-members count
    + chairman winner (the canonical real-contest logic), and pulls task_text
    from the outcome metadata. Best-effort; returns [] on any error so a missing
    corpus never breaks the caller.
    """
    try:
        from .personal_routing import _scan_outcomes
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
        try:
            oc = load_council_outcome(cid)
            task_text = (oc.metadata or {}).get("task_text") or ""
        except Exception:
            pass
        out.append({
            "council_id": cid,
            "task_text": task_text,
            "winner": r.get("chairman_winner") or r.get("winner_provider") or "",
            "substantive_members": r.get("substantive_members", 2),
            # Carry the distinct-voice count through so picks.json shares the
            # value-proof's real-contest definition: a same-family chain
            # (claude·claude·claude — 3 substantive members, 1 distinct voice)
            # must be skipped by `_is_real_contest` here too, not just on the
            # value-proof headline. Defaults to 2 when absent (legacy records).
            "distinct_substantive_providers": r.get("distinct_substantive_providers", 2),
            "created_at": (r.get("routing_label") or {}).get("created_at") or "",
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
    return compute_basin_routing(_load_council_records(), basins, embed)
