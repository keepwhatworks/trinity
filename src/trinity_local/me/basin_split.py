"""Mega-basin sub-clustering — the combined splitter (council verdict #308).

A post-process that runs AFTER `compute_basins` produces the global topology.
It splits ONLY the few oversized, incoherent "mega" basins into coherent
sub-basins, and leaves every small/coherent basin untouched. It NEVER merges,
NEVER moves a prompt across basins, and NEVER re-clusters globally — the global
k-means in `basins.py` owns the cross-basin partition; this module only refines
within a single mega.

Why a separate module
---------------------
`compute_basins` clusters THREADS into a coarse topic map sized by `auto_k`.
On a large corpus a handful of basins still junk-drawer ~70% of the corpus into
9 mega-basins (size >= MEGA_SIZE_FLOOR) whose internal coherence is low — e.g.
a single basin mixing Claude-Code/MCP tooling threads with home-renovation
threads. Splitting those (and only those) gives Stage 2/4 + the routing layer
finer, single-domain basins to tag against.

The combined algorithm (best-of the experiment cohort)
------------------------------------------------------
Three stages, all numpy-only (no sklearn / hdbscan / scipy runtime dep):

  1. GUARD (from A10 `split_only_guarded`) — a basin is a split CANDIDATE only
     if  size >= MEGA_SIZE_FLOOR  AND  mean_pairwise_cosine < INCOHERENCE_FLOOR.
     Small or already-coherent basins pass straight through, untouched.

  2. DECIDE k (from A09 `bimodality_participation`) — reuse the repo's
     dependency-free `cortex_geometry` math (geometric median + participation
     ratio + leading-PC excess kurtosis) on a deterministic subsample to decide
     whether the mega is genuinely multi-modal and to derive a k CEILING. A
     mega that reads as a single cone (low participation ratio, no twin-peak
     kurtosis) is left whole even though it tripped the guard — geometry vetoes
     the split.

  3. EXECUTE (from A10 / A02) — silhouette-selected-k spherical k-means
     (cosine == dot on L2-normed rows), k swept in [K_MIN .. min(K_MAX,
     ceiling)], pick the k with the best cosine silhouette. Every prompt in the
     basin is assigned to its nearest final centroid: FULL COVERAGE, no point is
     dropped to "noise". (The two highest raw-tightness experiment approaches —
     DBSCAN-density A07 and agglomerative A03 — bought their score by demoting
     42–77% of items to noise AND needed sklearn; that was flagged as
     metric-gaming. This splitter keeps every item and stays numpy-only.)

  4. STOP-GATE — accept the split ONLY if it actually tightens the basin
     (size-weighted mean pairwise cosine rises by >= MIN_TIGHTEN) and the best
     silhouette is positive. A split that doesn't tighten is discarded and the
     mega is kept whole. A green (a "split") is only emitted when the invariant
     it attests (real coherence gain) holds — degenerate input is refused, not
     fabricated (CLAUDE.md green-gate discipline).

Abstain / no-op contract
------------------------
`split_mega_basins` is a pure transform `list[Basin] -> list[Basin]`. It returns
the input basins UNCHANGED (never crashes, never emits a wrong split) when:
  - the knob is off (default);
  - numpy is unavailable;
  - no basin trips the size+incoherence guard;
  - a candidate's prompt embeddings can't be loaded (< 2 distinct finite points);
  - geometry vetoes (unimodal), silhouette is non-positive, or the split fails
    the tighten stop-gate.

Determinism
-----------
Every random draw is seeded. The per-basin RNG seed is derived from the basin id
via `zlib.crc32` (NOT Python's `hash()`, which is salted per process by
PYTHONHASHSEED), so subsamples + k-means++ inits reproduce run-to-run.

Config knob
-----------
OFF by default — opt-in until the founder flips it. Enable via the
`TRINITY_SPLIT_MEGA_BASINS=1` env var, or pass `split_megas=True` to
`compute_basins` / `stage1_basins`. With the knob off, `compute_basins` behaves
exactly as before (behaviour-preserving).
"""

from __future__ import annotations

import os
import zlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle at module load
    from .basins import Basin


# --- config knob ----------------------------------------------------------- #
SPLIT_ENV_VAR = "TRINITY_SPLIT_MEGA_BASINS"


def split_enabled() -> bool:
    """True iff the mega-basin splitter is switched on via the env knob.

    Default OFF. Accepts the usual truthy spellings so a founder flip is
    forgiving. `compute_basins(split_megas=...)` overrides this per call."""
    return os.environ.get(SPLIT_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


# --- pre-registered tunables (deterministic) ------------------------------- #
# A basin is a split candidate only at/above this size. Matches the harness +
# task framing of a "mega" basin (the 9 basins holding ~70% of a 21.7k corpus).
MEGA_SIZE_FLOOR = 1000
# ...AND its mean pairwise cosine must be below this (incoherent enough to be
# worth splitting). A03/A10 swept this region; 0.35 keeps already-tight basins
# whole while admitting the diffuse megas (real ones sit at ~0.24–0.31).
INCOHERENCE_FLOOR = 0.35
# silhouette-selected-k search range. The ceiling is further capped per basin by
# the geometry decision (participation ratio), so K_MAX is just a hard safety lid.
K_MIN = 2
K_MAX = 8
# Geometry decision (A09): participation ratio is the effective number of spread
# directions. On 768-d mega embeddings the 1-D leading-PC kurtosis test is blind
# to modes that separate along different axes, so the PR floor is the real
# multi-modal evidence (single-cone megas sit at PR ~1–3; diffuse megas at 60+).
PARTICIPATION_MULTIMODAL_FLOOR = 25.0
# Map participation ratio -> a k ceiling: k ~ round(PR / PR_PER_MODE), clamped.
PARTICIPATION_PER_MODE = 24.0
# Stop-gate: a split must raise size-weighted mean pairwise cosine by at least
# this much, AND the chosen silhouette must be > 0, or the mega is kept whole.
MIN_TIGHTEN = 0.005
# Deterministic subsample caps (keep the O(n^2) / O(n*k*d) work bounded on the
# big megas; centroids are then applied to EVERY point so coverage is full).
GEOM_SUBSAMPLE = 400      # rows fed to the pure-Python cortex_geometry decision
KMEANS_SUBSAMPLE = 800    # rows fed to the k-means centroid fit per mega
SIL_SUBSAMPLE = 600       # rows fed to the silhouette used to PICK k
KMEANS_ITERS = 25
SEED = 0


# --------------------------------------------------------------------------- #
# numpy-only similarity + spherical k-means primitives                        #
# (rows are L2-normed embeddings, so cosine == dot and cosine k-means is just  #
#  Euclidean k-means on the unit sphere with re-normalized centroids)         #
# --------------------------------------------------------------------------- #
def _mean_pairwise_cosine(X) -> float:
    import numpy as np

    n = X.shape[0]
    if n < 2:
        return float("nan")
    Xd = X.astype(np.float64, copy=False)
    G = Xd @ Xd.T
    return float(G.sum() - np.trace(G)) / (n * (n - 1))


def _l2_normalize(X):
    import numpy as np

    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.clip(norms, 1e-12, None)


def _kmeanspp_init(X, k: int, rng):
    import numpy as np

    n = X.shape[0]
    first = int(rng.integers(0, n))
    centers = [X[first]]
    closest = np.clip(1.0 - X @ centers[0], 0.0, 2.0)
    for _ in range(1, k):
        total = float(closest.sum())
        if total <= 1e-12:
            idx = int(rng.integers(0, n))
        else:
            idx = int(rng.choice(n, p=closest / total))
        centers.append(X[idx])
        d = np.clip(1.0 - X @ centers[-1], 0.0, 2.0)
        closest = np.minimum(closest, d)
    return np.asarray(centers, dtype=np.float64)


def _spherical_kmeans(X, k: int, rng):
    """Lloyd iterations on the unit sphere. Returns (k, d) unit-norm centroids.

    Empty clusters are re-seeded to the point worst-served by the current
    centroids, so a returned centroid set always spans k live clusters when the
    data supports them."""
    import numpy as np

    Xd = X.astype(np.float64, copy=False)
    C = _kmeanspp_init(Xd, k, rng)
    for _ in range(KMEANS_ITERS):
        sims = Xd @ C.T
        assign = np.argmax(sims, axis=1)
        newC = np.zeros_like(C)
        empty = []
        for j in range(k):
            m = assign == j
            if m.any():
                v = Xd[m].sum(axis=0)
                nrm = np.linalg.norm(v)
                newC[j] = v / nrm if nrm > 1e-12 else C[j]
            else:
                empty.append(j)
        if empty:
            worst = np.argsort(-(1.0 - sims.max(axis=1)))
            for j, wi in zip(empty, worst):
                newC[j] = Xd[int(wi)]
        if np.allclose(newC, C, atol=1e-7):
            C = newC
            break
        C = newC
    return C


def _assign(X, C):
    import numpy as np

    sims = X.astype(np.float64, copy=False) @ C.T
    return np.argmax(sims, axis=1).astype(int)


def _cosine_silhouette(X, labels, rng) -> float:
    """Mean cosine silhouette on a deterministic subsample. nan if < 2 clusters."""
    import numpy as np

    uniq = np.unique(labels)
    if uniq.size < 2:
        return float("nan")
    n = X.shape[0]
    if n > SIL_SUBSAMPLE:
        sel = rng.choice(n, size=SIL_SUBSAMPLE, replace=False)
        sel.sort()
        Xv, lv = X[sel], labels[sel]
    else:
        Xv, lv = X, labels
    if np.unique(lv).size < 2:
        return float("nan")
    Xd = Xv.astype(np.float64, copy=False)
    D = np.clip(1.0 - (Xd @ Xd.T), 0.0, 2.0)
    uq = np.unique(lv)
    masks = {c: (lv == c) for c in uq}
    sizes = {c: int(mk.sum()) for c, mk in masks.items()}
    sil: list[float] = []
    for i in range(Xd.shape[0]):
        ci = lv[i]
        if sizes[ci] < 2:
            continue
        own = masks[ci].copy()
        own[i] = False
        a = D[i, own].mean()
        b = min(D[i, masks[c]].mean() for c in uq if c != ci)
        denom = max(a, b)
        sil.append(0.0 if denom == 0 else (b - a) / denom)
    return float(np.mean(sil)) if sil else float("nan")


# --------------------------------------------------------------------------- #
# geometry decision — reuse the repo's dependency-free cortex_geometry math    #
# --------------------------------------------------------------------------- #
def _decide_multimodal_and_ceiling(X, rng) -> tuple[bool, int, float]:
    """A09 decision: is this mega genuinely multi-modal, and what k ceiling?

    Returns (multimodal, k_ceiling, participation_ratio). Runs the pure-Python
    cortex_geometry helpers on a deterministic subsample (they're O(n^2 d) /
    O(i n d), so the subsample keeps them cheap on the big megas)."""
    from ..cortex_geometry import (
        BIMODALITY_KURTOSIS_THRESHOLD,
        excess_kurtosis,
        participation_ratio,
        project_onto_first_pc,
        weiszfeld_median,
    )

    n = X.shape[0]
    if n > GEOM_SUBSAMPLE:
        sel = rng.choice(n, size=GEOM_SUBSAMPLE, replace=False)
        sel.sort()
        pts = X[sel].astype(float).tolist()
    else:
        pts = X.astype(float).tolist()

    if len(pts) < 2:
        return False, 1, 0.0

    median = weiszfeld_median(pts)
    part = float(participation_ratio(pts, median))
    first_pc = project_onto_first_pc(pts, median)
    kurt = excess_kurtosis(first_pc) if first_pc else 0.0
    kurt_bimodal = bool(len(pts) >= 10 and first_pc and kurt < BIMODALITY_KURTOSIS_THRESHOLD)
    pr_multimodal = bool(part >= PARTICIPATION_MULTIMODAL_FLOOR)
    multimodal = kurt_bimodal or pr_multimodal
    if not multimodal:
        return False, 1, part
    ceiling = int(round(part / PARTICIPATION_PER_MODE))
    ceiling = max(K_MIN, min(K_MAX, ceiling))
    return True, ceiling, part


# --------------------------------------------------------------------------- #
# per-basin embedding load (read-only on ~/.trinity)                          #
# --------------------------------------------------------------------------- #
def _load_basin_matrix(basin: "Basin", *, out_info: dict | None = None):
    """Return an (N, d) L2-normed float32 matrix of the finite embeddings for
    this basin's prompt_ids, plus the parallel list of prompt_ids that survived.

    When `out_info` is provided it is filled `{prompt_id: (text, transcript_id,
    turn_index)}` during the same store pass — free text for medoid-rep synthesis,
    no second scan.

    Reads embeddings from the prompt store (read-only). Returns (None, []) when
    fewer than 2 distinct finite points are available — the caller then abstains
    (keeps the basin whole). Never raises."""
    import numpy as np

    from ..embeddings import is_finite_embedding
    from ..memory.store import iter_prompt_nodes

    wanted = set(basin.prompt_ids)
    if not wanted:
        return None, []

    by_id: dict[str, list] = {}
    try:
        for node in iter_prompt_nodes(limit=None):
            nid = getattr(node, "id", None)
            if nid not in wanted or nid in by_id:
                continue
            emb = getattr(node, "embedding", None)
            if not is_finite_embedding(emb):
                continue
            by_id[nid] = list(emb)
            if out_info is not None:
                out_info[nid] = (
                    getattr(node, "text", "") or "",
                    getattr(node, "transcript_id", nid) or nid,
                    getattr(node, "turn_index", 0) or 0,
                )
            if len(by_id) == len(wanted):
                break
    except Exception:
        return None, []

    if len(by_id) < 2:
        return None, []

    # Preserve the basin's prompt_ids order for those we found (deterministic).
    ids = [pid for pid in basin.prompt_ids if pid in by_id]
    dims = {len(by_id[pid]) for pid in ids}
    if len(dims) != 1:
        # Mixed dimensionality (e.g. a backend swap mid-corpus) — refuse rather
        # than fabricate a matmul over ragged rows. Keep to the dominant dim.
        from collections import Counter

        top_dim, _ = Counter(len(by_id[pid]) for pid in ids).most_common(1)[0]
        ids = [pid for pid in ids if len(by_id[pid]) == top_dim]
        if len(ids) < 2:
            return None, []

    X = np.asarray([by_id[pid] for pid in ids], dtype=np.float32)
    X = _l2_normalize(X)
    # Need at least 2 DISTINCT points or there is nothing to split.
    if np.unique(X, axis=0).shape[0] < 2:
        return None, []
    return X, ids


# --------------------------------------------------------------------------- #
# top-level transform                                                          #
# --------------------------------------------------------------------------- #
def split_mega_basins(
    basins: "list[Basin]",
    *,
    enabled: bool | None = None,
    seed: int = SEED,
) -> "list[Basin]":
    """Split ONLY oversized + incoherent mega-basins into coherent sub-basins.

    Pure transform `list[Basin] -> list[Basin]`. Split-only and #308-compliant:
    operates strictly within a single basin, never merges, never moves a prompt
    across basins, never re-clusters globally. Small/coherent basins pass through
    byte-identical.

    Sub-basins inherit the parent's id with a letter suffix (b05 -> b05a, b05b,
    …) and carry a recomputed centroid (sub-cluster mean, L2-normed), the correct
    prompt_ids subset, recomputed size/thread_count/top_terms, and the
    representatives nearest each sub-centroid.

    Returns `basins` unchanged when the knob is off (default), numpy is missing,
    no basin trips the guard, or every candidate abstains (degenerate / unimodal
    / no-tighten). The result is re-sorted by size descending and the parent's
    chairman label is cleared on children (it no longer describes the subset).

    `enabled` overrides the env knob for this call (None => read the env knob).
    """
    if enabled is None:
        enabled = split_enabled()
    if not enabled or not basins:
        return basins

    import importlib.util

    if importlib.util.find_spec("numpy") is None:
        # numpy-only by contract; without it there's nothing to do but no-op.
        return basins

    out: list = []
    changed = False

    for basin in basins:
        result = _maybe_split_one(basin, seed=seed)
        if result is None:
            out.append(basin)  # unchanged (guard / abstain / no-tighten)
        else:
            out.extend(result)
            changed = True

    if not changed:
        return basins

    # Re-sort by size desc and re-number top-level ids so b00 stays the most
    # prevalent — EXCEPT we must keep sub-basin ids stable/derivable. We keep the
    # letter-suffixed child ids as emitted (they're unique) and only re-sort the
    # list; renumbering would collide a child like "b05a" with a fresh "b05".
    # Tie-break on the (already-stable) basin id so two equal-size basins don't
    # swap DISPLAY position across re-runs (ids are not renumbered here, so the
    # id is the stable identity to order on).
    out.sort(key=lambda b: (-b.size, b.id))
    return out


def _maybe_split_one(basin: "Basin", *, seed: int):
    """Attempt to split ONE basin. Returns a list of child Basins on a real
    split, or None to keep the basin whole (guard fail / abstain / no-tighten)."""
    import numpy as np

    from .basins import Basin, _pick_label_snippet, _top_terms_for_cluster

    # --- GUARD: only oversized basins are candidates (cheap size check first) -
    if basin.size < MEGA_SIZE_FLOOR:
        return None

    info: dict = {}
    X, ids = _load_basin_matrix(basin, out_info=info)
    if X is None or X.shape[0] < 2:
        return None  # ABSTAIN: <2 distinct finite points / missing embeddings

    coh = _mean_pairwise_cosine(X)
    if np.isnan(coh) or coh >= INCOHERENCE_FLOOR:
        return None  # coherent enough -> keep whole

    # --- per-basin deterministic RNG (crc32, NOT salted hash()) --------------
    basin_seed = seed + (zlib.crc32(basin.id.encode()) % 1_000_000)
    rng = np.random.default_rng(basin_seed)

    # --- DECIDE k (geometry veto + ceiling) ----------------------------------
    multimodal, k_ceiling, _part = _decide_multimodal_and_ceiling(X, rng)
    if not multimodal:
        return None  # geometry says single cone -> keep whole

    # --- EXECUTE: silhouette-selected-k spherical k-means on a subsample ------
    if X.shape[0] > KMEANS_SUBSAMPLE:
        sub_sel = rng.choice(X.shape[0], size=KMEANS_SUBSAMPLE, replace=False)
        sub_sel.sort()
        Xsub = X[sub_sel]
    else:
        Xsub = X

    best_sil, best_C = float("-inf"), None
    for k in range(K_MIN, min(K_MAX, k_ceiling) + 1):
        if k >= Xsub.shape[0]:
            break
        C = _spherical_kmeans(Xsub, k, np.random.default_rng(basin_seed + k))
        sub_labels = _assign(Xsub, C)
        if np.unique(sub_labels).size < 2:
            continue
        sil = _cosine_silhouette(Xsub, sub_labels, np.random.default_rng(basin_seed + k + 777))
        if not np.isnan(sil) and sil > best_sil:
            best_sil, best_C = sil, C

    if best_C is None or best_sil <= 0:
        return None  # no positive-silhouette split -> keep whole

    # Assign EVERY prompt in the basin to its nearest final centroid (full
    # coverage — no point dropped to noise).
    full = _assign(X, best_C)
    live = [int(j) for j in np.unique(full)]
    if len(live) < 2:
        return None

    # --- STOP-GATE: the split must actually tighten the basin ----------------
    after_num = after_den = 0.0
    for j in live:
        Xs = X[full == j]
        if Xs.shape[0] >= 2:
            c = _mean_pairwise_cosine(Xs)
            if not np.isnan(c):
                after_num += c * Xs.shape[0]
                after_den += Xs.shape[0]
    after = after_num / after_den if after_den else coh
    if (after - coh) < MIN_TIGHTEN:
        return None  # didn't tighten -> refuse the split (green-gate discipline)

    # --- build child Basins ---------------------------------------------------
    # Map back from the per-prompt label to full prompt_ids and embeddings.
    id_to_label = {pid: int(lbl) for pid, lbl in zip(ids, full.tolist())}
    # Prompts whose embeddings we couldn't load keep with the basin's first live
    # sub-cluster so NO prompt is lost from the topology (coverage invariant).
    missing_ids = [pid for pid in basin.prompt_ids if pid not in id_to_label]
    fallback_label = live[0]

    # Order children by the canonical sorted label so suffixes are stable.
    label_order = sorted(live)

    children: list[Basin] = []
    for offset, lbl in enumerate(label_order):
        member_ids = [pid for pid in basin.prompt_ids
                      if id_to_label.get(pid, fallback_label if pid in missing_ids else -999) == lbl]
        if not member_ids:
            continue
        rows = X[full == lbl]
        centroid = rows.mean(axis=0)
        nrm = float(np.linalg.norm(centroid))
        centroid = centroid / nrm if nrm > 1e-12 else centroid

        # top_terms recomputed against the FULL basin (residual within-mega).
        sub_index_set = set(member_ids)
        # representatives: carry over the parent reps whose transcript turns fall
        # in this sub-cluster, else recompute from member_ids would need text we
        # don't hold here — keep parent reps that belong, capped.
        kept_reps = _reps_for_subset(basin, sub_index_set)
        if not kept_reps:
            # The mega had only ~5 reps spread across N children, so this
            # sub-cluster inherited none. Synthesize its MEDOID as a representative
            # (text is free from the load pass) so the detail panel isn't empty —
            # real-corpus invariant: every basin needs >=1 representative.
            sub = [(pid, X[k]) for k, pid in enumerate(ids) if int(full[k]) == lbl]
            if sub:
                # Closest-to-centroid wins, prompt id ASC as a stable tie-break
                # so an exact dot-product tie picks the same medoid regardless of
                # `sub` order. min over (-dot, pid) = max dot, smallest id.
                medoid_id = min(sub, key=lambda p: (-float(np.dot(p[1], centroid)), p[0]))[0]
                _ti = info.get(medoid_id)
                if _ti:
                    _txt, _tid, _turn = _ti
                    _snip = " ".join((_txt or "").split())[:200]
                    if _snip:
                        kept_reps = [{
                            "transcript_id": _tid,
                            "turn_count": 1,
                            "headline": _snip,
                            "turns": [{"id": medoid_id, "snippet": _snip,
                                       "turn_index": int(_turn)}],
                        }]

        child = Basin(
            id=f"{basin.id}{chr(ord('a') + offset)}",
            size=len(member_ids),
            thread_count=_distinct_threads(basin, sub_index_set),
            top_terms=list(basin.top_terms),  # placeholder; refined below
            centroid=centroid.tolist(),
            prompt_ids=member_ids,
            representatives=kept_reps,
            label=_pick_label_snippet(kept_reps) if kept_reps else "",
            intent_type=basin.intent_type,
            language=basin.language,
        )
        children.append(child)

    if len(children) < 2:
        return None

    # Refine top_terms: each child's distinctive terms vs the PARENT mega's text.
    _refine_child_top_terms(basin, children, _top_terms_for_cluster)
    return children


def _distinct_threads(basin: "Basin", member_id_set: set) -> int:
    """Count distinct transcript_ids among this sub-cluster's representatives.

    The Basin dataclass doesn't carry a prompt_id -> transcript_id map, so we
    approximate thread_count from the representatives that fall in the subset;
    fall back to the member count (each prompt its own thread) when reps are
    absent. Never larger than the sub-cluster size."""
    tids: set[str] = set()
    for rep in basin.representatives or []:
        rep_turn_ids = {t.get("id") for t in (rep.get("turns") or [])}
        if rep_turn_ids & member_id_set:
            tid = rep.get("transcript_id")
            if tid:
                tids.add(tid)
    if tids:
        return len(tids)
    # No rep overlap recorded — be conservative: at least 1, at most subset size.
    return max(1, min(len(member_id_set), 1))


def _reps_for_subset(basin: "Basin", member_id_set: set) -> list:
    """Keep the parent representatives whose turn ids land in this sub-cluster.

    Caps at 5 (the same REPRESENTATIVE_K the parent used). Representatives carry
    no embedding, so we route them by turn-id membership — a rep belongs to the
    sub-cluster that holds (most of) its turns."""
    kept: list = []
    for rep in basin.representatives or []:
        rep_turn_ids = [t.get("id") for t in (rep.get("turns") or [])]
        if not rep_turn_ids:
            continue
        hits = sum(1 for tid in rep_turn_ids if tid in member_id_set)
        if hits * 2 >= len(rep_turn_ids):  # majority of the rep's turns are here
            kept.append(rep)
        if len(kept) >= 5:
            break
    return kept


def _refine_child_top_terms(basin: "Basin", children: list, top_terms_fn) -> None:
    """Recompute each child's top_terms from its representative snippets, scored
    against the PARENT mega's representative text (within-mega residual).

    We only have text on the basin's representatives (not every prompt), so this
    is a best-effort refinement: children with no rep text retain the parent's
    top_terms (the safe default). Pure string heuristic — no LLM, no embedding."""
    parent_texts: list[str] = []
    for rep in basin.representatives or []:
        for turn in rep.get("turns") or []:
            s = (turn.get("snippet") or "").strip()
            if s:
                parent_texts.append(s)
    if not parent_texts:
        return
    for child in children:
        child_texts: list[str] = []
        for rep in child.representatives or []:
            for turn in rep.get("turns") or []:
                s = (turn.get("snippet") or "").strip()
                if s:
                    child_texts.append(s)
        if child_texts:
            terms = top_terms_fn(child_texts, parent_texts)
            if terms:
                child.top_terms = terms
