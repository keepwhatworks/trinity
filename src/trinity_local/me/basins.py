"""Stage 1 — topology via numpy k-means over PromptNode embeddings.

Produces ~20 basins per `lens-build`. Each basin carries:
- id (b00..bNN)
- size (count of prompts in cluster)
- top_terms (top-3 TF-IDF residual phrases vs full corpus)
- centroid (768d numpy vector)
- prompt_ids (sample for stage 2 to walk)

Two consumers:
1. Stage 2 tags each extracted decision with its basin id.
2. Stage 4 post-filter drops accepted pairs whose tension evidence
   all sits in a single basin (topic-local virtue, not real lens).

NOT a chairman input — the council ratified that passing basin ids
through the prompt is dead-code-prone unless deterministic code uses
them. Per-decision tagging + post-filter together fix that.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..embeddings import is_finite_embedding
from ..memory.store import iter_prompt_nodes
from ..state_paths import state_dir


# k-LLMmeans hook contract (tick #52). The relabel hook receives, per
# basin, the basin id + size + a list of the K depth-ranked
# representative thread prompts (verbatim, not summaries). It returns
# a list of replacement centroid vectors, one per basin in the same
# order. The vectors should be the same dimensionality as the input
# embeddings — production callers will embed a chairman-produced
# label and pass the embedding.
#
# When hook is None or iterations=1, compute_basins behaves exactly
# as before (single k-means pass). The interface is dependency-free
# so tests can pass a deterministic Python callable + production
# can wire the actual chairman via lens-build's existing provider
# plumbing.
KLLMmeansHook = Callable[
    [list[dict[str, Any]]],   # one dict per current basin
    list[Sequence[float]],     # replacement centroids, same order
]


_DEFAULT_K = 20
_DEFAULT_SEED = 42
_TOP_TERMS_PER_BASIN = 3

# Corpus-size-aware k (#245 follow-on). A fixed k=20 junk-drawers on a large
# corpus: once the 2026-05-12 scaffolding pollution was purged, the clean
# 28.6k-prompt corpus packed 29.6% of prompts into one b00 basin at k=20 —
# above the 20% junk-drawer ceiling the real_corpus guard enforces. k must
# scale with the corpus so no single basin dominates as history grows.
# Empirically (clean corpus): k=20→29.6%, k=40→19.9%, k=50→16.1%. Sized by
# PROMPT count (what the junk-drawer guard measures), ~one basin per
# _PROMPTS_PER_BASIN prompts.
#
# #255 retune: clustering input is the DEDUPED corpus (v1.7.71 dedup-at-
# clustering), so the old 650 ratio — calibrated against the pre-dedup count —
# came out ~30% too coarse on the deduped count (20,966 deduped → k=32 → b00
# 24.7%, a junk-drawer). k=40 sits at 19.9% (razor-thin against the 20%
# ceiling); 440 targets ~k=48 on this corpus → ~16% top basin, a real safety
# margin. Verified by re-clustering the live corpus (b00 dropped under 20%).
_PROMPTS_PER_BASIN = 440
_MAX_K = 60  # cap: more basins than this fragments the topic map past usefulness


def auto_k(n_prompts: int) -> int:
    """Pick a basin count that scales with the corpus, clamped to
    [_DEFAULT_K, _MAX_K]. Below ~13k prompts this floors at the historical
    k=20 (behaviour-preserving for small/fresh installs); it only grows k
    once the corpus is large enough that k=20 would junk-drawer. Sized by
    prompt count, not thread count — the junk-drawer guard measures a basin's
    share of PROMPTS, and a few long multi-turn export threads otherwise let
    one basin dominate prompt-share while looking small in thread-share."""
    target = round(n_prompts / _PROMPTS_PER_BASIN)
    return max(_DEFAULT_K, min(_MAX_K, target))
# Stop words that crowd out distinctive vocabulary in tiny corpora.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were",
    "be", "been", "being", "of", "to", "in", "on", "at", "for", "with",
    "by", "from", "as", "this", "that", "these", "those", "it", "its",
    "i", "you", "he", "she", "we", "they", "me", "us", "them", "my",
    "your", "his", "her", "our", "their", "do", "does", "did", "have",
    "has", "had", "will", "would", "should", "could", "can", "may",
    "might", "must", "shall", "not", "no", "yes", "so", "up", "out",
    "about", "over", "than", "then", "there", "what", "when", "where",
    "who", "how", "why", "all", "any", "some", "each", "every", "more",
    "most", "less", "least", "very", "just", "only", "also", "even",
    "still", "yet", "now", "here", "into", "onto", "upon", "off",
    "down", "back", "again", "before", "after", "during", "while",
    "because", "though", "although", "since", "until", "unless",
}


@dataclass
class Basin:
    id: str
    size: int   # total turn count across all threads in this basin
    top_terms: list[str]
    centroid: list[float]
    prompt_ids: list[str] = field(default_factory=list)
    # Top-K threads closest to the basin centroid — the most semantically
    # representative conversations. Per-thread shape:
    #   {transcript_id, turn_count, headline, turns: [{id, snippet, turn_index}]}
    # Stored under `representatives` for backward-compat with the older
    # per-turn shape; the viewer detects the new shape by the presence of
    # `transcript_id` + `turns`.
    representatives: list[dict[str, Any]] = field(default_factory=list)
    # NEW for thread-aware topology: how many distinct sessions (threads)
    # landed in this basin. `size` is the total turn count.
    thread_count: int = 0
    # Chairman-labeled semantics (tick #49). Empty when lens-build was run
    # before the labeler stage existed OR the chairman call failed. UI
    # consumers prefer `label` when non-empty, fall back to top_terms.
    label: str = ""
    intent_type: str = ""    # e.g. automation / creative / learning / debugging / decision
    language: str = ""       # ISO-639-1 code or "mixed" — chairman call, not unicode-block heuristic

    def to_dict(self) -> dict[str, Any]:
        # No truncation on prompt_ids. Earlier code capped at 50 "for readable
        # JSON" — but `load_basins()` round-trips the file back into Basin
        # dataclasses, and `basin_for_prompt(basins, id)` checks membership
        # against the loaded list. With a 50-id cap, any prompt beyond the
        # first 50 in a basin was silently mis-tagged as "no basin" after
        # the round-trip, breaking Stage 2/4 of the lens pipeline. Keeping
        # the full list keeps topics.json a faithful serialization.
        return {
            "id": self.id,
            "size": self.size,
            "thread_count": self.thread_count,
            "top_terms": self.top_terms,
            "centroid": self.centroid,
            "prompt_ids": self.prompt_ids,
            "representatives": self.representatives,
            "label": self.label,
            "intent_type": self.intent_type,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Basin":
        """Tolerant load — drops unknown keys so a schema-drifted topics.json
        (a field added by a NEWER builder, or the viewer's slimmed
        `prompt_id_count` shape) still loads instead of raising
        `TypeError: unexpected keyword argument`. Mirrors PromptNode/TurnWindow.
        A record still missing a REQUIRED field (`id`/`size`/`top_terms`/
        `centroid`) raises here so `load_basins` can skip just that one basin
        rather than blinding every downstream consumer (the noise self-test
        went silently dark with a raw `TypeError` repr leaked into the user's
        lens-health report)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


def me_dir() -> Path:
    """Output directory for lens-discovery artifacts."""
    path = state_dir() / "me"
    path.mkdir(parents=True, exist_ok=True)
    return path


def basins_path() -> Path:
    """The topology file. Renamed from `me/basins.json` → `memories/topics.json`
    per the brand axis (topics = semantic memory; basins was math-jargon).
    Migration handled by state_paths.memories_dir() on first access."""
    from ..state_paths import topics_path
    return topics_path()


def _kmeans_pp_init(matrix, k: int, seed: int):
    """Pure-numpy k-means++ centroid init. Deterministic with seed."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n, _ = matrix.shape
    first = int(rng.integers(0, n))
    centroids = [matrix[first]]
    for _ in range(1, k):
        diffs = matrix[:, None, :] - np.array(centroids)[None, :, :]
        sq_dists = np.sum(diffs * diffs, axis=2).min(axis=1)
        total = sq_dists.sum()
        if not np.isfinite(total) or total <= 0:
            # All remaining points are exactly on existing centroids
            # (duplicate embeddings). Fall back to uniform random.
            next_idx = int(rng.integers(0, n))
        else:
            probs = sq_dists / total
            # Replace any NaN that slipped through (extreme float edge cases)
            probs = np.nan_to_num(probs, nan=0.0)
            if probs.sum() <= 0:
                next_idx = int(rng.integers(0, n))
            else:
                probs = probs / probs.sum()
                next_idx = int(rng.choice(n, p=probs))
        centroids.append(matrix[next_idx])
    return np.array(centroids)


def _kmeans(matrix, k: int = _DEFAULT_K, *, seed: int = _DEFAULT_SEED, max_iter: int = 50):
    """Lightweight k-means. Returns (labels, centroids)."""
    import numpy as np

    n, _ = matrix.shape
    if n <= k:
        # Degenerate: each row is its own cluster.
        labels = np.arange(n)
        return labels, matrix.copy()
    centroids = _kmeans_pp_init(matrix, k, seed)
    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        diffs = matrix[:, None, :] - centroids[None, :, :]
        sq_dists = np.sum(diffs * diffs, axis=2)
        new_labels = np.argmin(sq_dists, axis=1)
        if (new_labels == labels).all():
            break
        labels = new_labels
        for c in range(k):
            members = matrix[labels == c]
            if len(members) > 0:
                centroids[c] = members.mean(axis=0)
    return labels, centroids


def _top_terms_for_cluster(texts: list[str], all_texts: list[str], top_n: int = _TOP_TERMS_PER_BASIN) -> list[str]:
    """TF-IDF residual: terms common in cluster but rare globally."""
    cluster_words = Counter()
    global_words = Counter()
    for text in texts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-_]{2,}", text.lower()):
            if w in _STOPWORDS:
                continue
            cluster_words[w] += 1
    for text in all_texts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-_]{2,}", text.lower()):
            if w in _STOPWORDS:
                continue
            global_words[w] += 1
    if not cluster_words:
        return []
    cluster_size = max(sum(cluster_words.values()), 1)
    global_size = max(sum(global_words.values()), 1)
    scored: list[tuple[str, float]] = []
    for word, count in cluster_words.most_common(50):
        cluster_freq = count / cluster_size
        global_freq = global_words.get(word, 0) / global_size
        # Residual: how much more frequent in cluster than globally.
        residual = cluster_freq - global_freq
        if residual > 0:
            scored.append((word, residual))
    # Residual DESC, word ASC as a stable tie-break so the top_n cut is a
    # TOTAL order: two words with an equal residual straddling the top_n
    # boundary would otherwise keep the upstream dict-iteration order, so which
    # term survives into the basin's top_terms would flip on that order.
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [w for w, _ in scored[:top_n]]


def compute_basins(
    *,
    k: int | None = None,
    seed: int = _DEFAULT_SEED,
    iterations: int = 1,
    relabel_hook: Optional[KLLMmeansHook] = None,
    split_megas: bool | None = None,
) -> list[Basin]:
    """Cluster threads (sessions) into k basins.

    Each thread's centroid is the mean embedding of its turns. We cluster
    threads, not turns — a multi-turn conversation about tweet-drafting
    becomes ONE point in basin-space, not N points scattered across
    "drafting" / "shortening" / "polishing" basins.

    Single-turn sources (Gemini Takeout) naturally become threads of
    size 1 — same code path, no special casing.

    Skips nodes without embeddings. Returns basins sorted by total turn
    count descending so b00 is the most prevalent.

    **k-LLMmeans iteration (tick #52):** when `iterations > 1` AND
    `relabel_hook` is provided, the function loops:
      1. Run k-means / assign threads to nearest centroid
      2. Build minimal basin summaries (id, size, top-3 representative
         verbatim prompts)
      3. Call relabel_hook → get replacement centroid per basin (in
         production these are embeddings of chairman-produced labels)
      4. Re-assign each thread to nearest replacement centroid
      5. Repeat until iterations exhausted

    Reference: ClusterLLM (EMNLP 2023, arXiv:2305.14871) +
    k-LLMmeans (arXiv:2502.09667). The chairman steers centroid
    drift toward semantic coherence — fixes the b11-style failure
    where surface keywords pull semantically distant threads into
    one basin. With iterations=1 (default) or no hook, behaviour
    is identical to vanilla k-means.

    **Mega-basin split post-process (combined splitter, #308):** when
    `split_megas` is True (or the `TRINITY_SPLIT_MEGA_BASINS` env knob is set),
    the global topology is refined by `me.basin_split.split_mega_basins` AFTER
    clustering — it splits ONLY the oversized + incoherent mega-basins into
    coherent sub-basins (fresh ids like b05a/b05b) and leaves every small or
    coherent basin untouched. Split-only: never merges, never moves prompts
    across basins, never re-clusters globally. OFF by default
    (`split_megas=None` reads the env knob, which itself defaults off), so the
    default path is behaviour-preserving. See `me/basin_split.py`.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("numpy is required for basin clustering") from exc

    # Use `iter_prompt_nodes(limit=None)` to lift the 5000-node hot-path
    # cap. Recent ingest skips embedding to keep the launchpad/search
    # path fast, so embeddings sit on the older seeded prompts BELOW
    # the cap.
    # Re-apply the user-facing gate at basin-build READ time. The ingest filter
    # (`_is_user_facing_prompt`) only screens NEW captures; the append-only index
    # still holds nodes captured under older/weaker filters. Stage 0 (iter_turn_pairs)
    # and agent search already call this — but the basin build did not, so already-
    # poisoned scaffolding (e.g. Trinity's own "The user has the following lens…"
    # dispatch preamble — 40 such nodes) clustered into a near-pure scaffolding
    # basin (b26, 71%), tripping the #248 concentration guard. Re-applying it here
    # closes that read path with no forbidden re-ingest. (#316)
    from ..ingest import is_user_facing_text

    nodes: list = []
    skipped_unusable = 0
    skipped_duplicate = 0
    skipped_scaffolding = 0
    _seen_texts: set[str] = set()
    for node in iter_prompt_nodes(limit=None):
        emb = getattr(node, "embedding", None)
        # is_finite_embedding rejects None, [], NaN, and Inf in one
        # call. Counter name is "unusable" rather than "nan" because
        # the rejection class is broader than NaN alone — but the
        # NaN case is the load-bearing one that poisons k-means
        # centroids via column-wise matmul propagation.
        if not is_finite_embedding(emb):
            skipped_unusable += 1
            continue
        # Dedup by exact text before clustering (#248/#15). Repeated prompts
        # ("continue" ×130, an automation prompt ×188) carry identical vectors;
        # left in, they form a dense pseudo-cluster that dominates a basin and
        # trips the junk-drawer guard while saying nothing about taste. One
        # unique text = one clustering point. The STORE keeps every node
        # (provenance, prompt_id refs); only the clustering INPUT is deduped.
        text_key = (getattr(node, "text", "") or "").strip()
        if not is_user_facing_text(text_key):
            skipped_scaffolding += 1
            continue
        if text_key in _seen_texts:
            skipped_duplicate += 1
            continue
        _seen_texts.add(text_key)
        nodes.append(node)

    if not nodes:
        return []

    # Group turns by transcript_id. A missing transcript_id falls back to
    # a synthetic per-turn thread (the node's own id) so single-turn
    # sources still cluster.
    threads: dict[str, list[int]] = {}
    for idx, node in enumerate(nodes):
        tid = getattr(node, "transcript_id", None) or node.id
        threads.setdefault(tid, []).append(idx)

    thread_ids = list(threads.keys())
    thread_centroids = np.zeros((len(thread_ids), len(nodes[0].embedding)), dtype=np.float32)
    for ti, tid in enumerate(thread_ids):
        thread_centroids[ti] = np.mean(
            [nodes[i].embedding for i in threads[tid]], axis=0, dtype=np.float32,
        )

    # Cluster THREADS, not turns. `labels[ti]` is the basin id for thread `tid`.
    # k=None → corpus-size-aware (avoids the junk-drawer a fixed k=20 produces
    # on large corpora, #245); an explicit k always wins (CLI --k-basins / tests).
    # Size k by TURN count (len(nodes)) — that's what the junk-drawer guard
    # measures (a basin's share of prompts), and what the k-sweep that picked
    # _THREADS_PER_BASIN was measured against. Still capped at len(thread_ids)
    # since k-means can't make more clusters than there are points.
    effective_k = auto_k(len(nodes)) if k is None else k
    labels, basin_centroids = _kmeans(thread_centroids, k=min(effective_k, len(thread_ids)), seed=seed)

    REPRESENTATIVE_K = 5
    REPRESENTATIVE_MAX_CHARS = 280
    TURNS_PER_REP = 10  # cap turns rendered per representative thread

    # k-LLMmeans refinement loop. Each iteration: the hook receives
    # the current basin's top-3 representative thread prompts, returns
    # a replacement centroid (typically the embedding of a chairman-
    # produced semantic label). Threads re-assign to nearest new
    # centroid. The geometry settles toward labels-as-anchors instead
    # of pure-geometric-mean centroids. Stop early if hook returns
    # the wrong number of centroids (defensive — defective hook can't
    # corrupt the basin output).
    if relabel_hook is not None and iterations > 1:
        for _ in range(iterations - 1):
            # Build the hook input: per-basin summary the hook can
            # reason over. K reps = 3 here (the hook should keep its
            # chairman prompts tight); not the same K as the final
            # render's REPRESENTATIVE_K.
            HOOK_REPS_K = 3
            payload: list[dict[str, Any]] = []
            for cluster_idx in range(len(basin_centroids)):
                cluster_thread_indices = [
                    ti for ti, lbl in enumerate(labels) if lbl == cluster_idx
                ]
                if not cluster_thread_indices:
                    payload.append({"basin_id": f"b{cluster_idx:02d}", "size": 0, "reps": []})
                    continue
                centroid_vec = basin_centroids[cluster_idx]
                # Closest threads to current centroid → most-representative.
                sorted_by_distance = sorted(
                    cluster_thread_indices,
                    key=lambda ti: float(np.linalg.norm(thread_centroids[ti] - centroid_vec)),
                )
                reps_for_hook: list[str] = []
                for ti in sorted_by_distance[:HOOK_REPS_K]:
                    turn_idx_in_nodes = threads[thread_ids[ti]]
                    # Use the first turn (or single turn) as the rep prompt.
                    if turn_idx_in_nodes:
                        text = (nodes[turn_idx_in_nodes[0]].text or "").strip()
                        if text:
                            reps_for_hook.append(text[:280])
                payload.append({
                    "basin_id": f"b{cluster_idx:02d}",
                    "size": sum(len(threads[thread_ids[ti]]) for ti in cluster_thread_indices),
                    "reps": reps_for_hook,
                })
            try:
                new_centroids = relabel_hook(payload)
            except Exception:
                # Hook failure must not corrupt the clustering — fall
                # back to current centroids and break the loop.
                break
            # Defensive shape check: hook must return one centroid per
            # input basin, same dimensionality. If anything's off,
            # break + keep the prior iteration's centroids.
            if not isinstance(new_centroids, (list, tuple)) or len(new_centroids) != len(basin_centroids):
                break
            try:
                new_arr = np.array(new_centroids, dtype=np.float32)
            except Exception:
                break
            if new_arr.shape != basin_centroids.shape:
                break
            basin_centroids = new_arr
            # Re-assign every thread to nearest replacement centroid.
            # Plain euclidean since embeddings are nomic-normalized.
            distances = np.linalg.norm(
                thread_centroids[:, None, :] - basin_centroids[None, :, :], axis=2,
            )
            labels = distances.argmin(axis=1).tolist()

    basins: list[Basin] = []
    for cluster_idx in range(len(basin_centroids)):
        thread_indices = [ti for ti, lbl in enumerate(labels) if lbl == cluster_idx]
        if not thread_indices:
            continue

        # All turns across all threads in this basin (for top_terms,
        # prompt_ids, total-size).
        member_turn_indices: list[int] = []
        for ti in thread_indices:
            member_turn_indices.extend(threads[thread_ids[ti]])

        cluster_texts = [(nodes[i].text or "") for i in member_turn_indices]
        all_texts = [(n.text or "") for n in nodes]
        top_terms = _top_terms_for_cluster(cluster_texts, all_texts)

        # Pick representative THREADS: closest to basin centroid in
        # thread-centroid space.
        basin_centroid = basin_centroids[cluster_idx]
        thread_distances = [
            (ti, float(np.linalg.norm(thread_centroids[ti] - basin_centroid)))
            for ti in thread_indices
        ]
        # Distance ASC, thread index ASC as a stable tie-break so the
        # representatives[:REPRESENTATIVE_K] cut is a TOTAL order: two threads
        # equidistant from the centroid straddling the K boundary would
        # otherwise keep `thread_indices` order, so WHICH thread is shown as a
        # representative would flip on that order.
        thread_distances.sort(key=lambda pair: (pair[1], pair[0]))

        reps: list[dict[str, Any]] = []
        for ti, dist in thread_distances[:REPRESENTATIVE_K]:
            tid = thread_ids[ti]
            turn_idx_in_nodes = threads[tid]
            # Sort the thread's turns by their original turn_index so
            # the viewer renders them in conversational order.
            turn_idx_in_nodes = sorted(
                turn_idx_in_nodes,
                key=lambda i: getattr(nodes[i], "turn_index", 0),
            )
            # Headline = the single turn closest to the BASIN centroid
            # within this thread (= the turn most central to "why this
            # thread is here").
            headline_idx = min(
                turn_idx_in_nodes,
                key=lambda i: float(np.linalg.norm(
                    np.array(nodes[i].embedding, dtype=np.float32) - basin_centroid
                )),
            )

            def _snippet(text: str) -> str:
                s = (text or "").strip()
                if len(s) > REPRESENTATIVE_MAX_CHARS:
                    return s[:REPRESENTATIVE_MAX_CHARS].rstrip() + "…"
                return s

            turns_payload = [
                {
                    "id": nodes[i].id,
                    "snippet": _snippet(nodes[i].text or ""),
                    "turn_index": getattr(nodes[i], "turn_index", 0),
                }
                for i in turn_idx_in_nodes[:TURNS_PER_REP]
            ]
            reps.append({
                "transcript_id": tid,
                "turn_count": len(turn_idx_in_nodes),
                "headline": _snippet(nodes[headline_idx].text or ""),
                "turns": turns_payload,
            })

        # Compute a substantive label from the representative turns — pick the
        # longest non-greeting snippet across the reps' first turn. Without
        # this, persona audit Theme B #1 reproduces: 100% of real-corpus
        # basins have empty `label`, and viewers fall back to `headline`
        # which is whichever turn is closest to the basin centroid — that's
        # often a short greeting ("Hello.") for greeting-heavy basins, so
        # the largest cluster of 3,408 prompts renders as "Hello." in the
        # topology graph. Pure regex/length heuristic, no LLM call; the
        # chairman labeler stage stays a forward-arc item.
        basin_label = _pick_label_snippet(reps)
        basins.append(Basin(
            id=f"b{cluster_idx:02d}",
            size=len(member_turn_indices),  # total turn count
            thread_count=len(thread_indices),
            top_terms=top_terms,
            centroid=basin_centroids[cluster_idx].tolist(),
            prompt_ids=[nodes[i].id for i in member_turn_indices],
            representatives=reps,
            label=basin_label,
        ))

    # Size DESC, then (label, top_terms) ASC as a stable tie-break. Basin IDs
    # are assigned BY POSITION here (b00, b01, …) and referenced downstream by
    # picks.json basin_id / topics.json / memory-viewer deep-links, so two
    # basins of EQUAL size must not swap IDs across re-runs. Without the
    # tie-break, equal-size basins keep the k-means cluster-index order, which
    # is fragile to input ordering / numpy argmin tie-handling — the SAME basin
    # could get b01 one run and b02 the next, silently re-pointing every stored
    # basin_id. (label, top_terms) is a stable per-basin content identity.
    basins.sort(key=lambda b: (-b.size, b.label, tuple(b.top_terms)))
    for i, basin in enumerate(basins):
        basin.id = f"b{i:02d}"

    # Combined mega-basin splitter (#308) — opt-in post-process. With the knob
    # off (the default) this returns `basins` unchanged, so the vanilla k-means
    # contract above is byte-for-byte preserved. `split_megas=None` defers to the
    # TRINITY_SPLIT_MEGA_BASINS env knob (default off).
    from .basin_split import split_mega_basins
    basins = split_mega_basins(basins, enabled=split_megas, seed=seed)
    return basins


_LABEL_GREETINGS = {
    "hi", "hello", "hey", "yo", "sup", "thanks", "thank you", "ok", "okay",
    "yes", "no", "sure", "got it", "cool", "nice", "great", "awesome",
    "continue", "go on", "next", "more", "again", "?", "??", "...",
}

_LABEL_MAX_CHARS = 80


def _pick_label_snippet(reps: list[dict]) -> str:
    """Pick the most substantive turn across reps to use as the basin's
    human label. Skips short greetings; prefers longer, multi-word turns
    closer to the centroid (reps come pre-sorted that way).

    Returns "" if nothing substantive — the viewer then falls back to
    top_terms display, which at least surfaces what tokens dominate the
    cluster instead of misleading with a stale greeting.
    """
    best: tuple[int, str] = (0, "")
    for rep in reps[:5]:  # top 5 representatives only
        headline = (rep.get("headline") or "").strip()
        candidates = [headline]
        for turn in rep.get("turns") or []:
            snippet = (turn.get("snippet") or "").strip()
            if snippet and snippet not in candidates:
                candidates.append(snippet)
        for cand in candidates:
            if not cand:
                continue
            normalized = cand.lower().rstrip("?.!,;:'\" ")
            if normalized in _LABEL_GREETINGS:
                continue
            words = cand.split()
            if len(words) < 3:
                continue
            score = len(cand)
            if score > best[0]:
                truncated = cand if len(cand) <= _LABEL_MAX_CHARS else cand[:_LABEL_MAX_CHARS].rstrip() + "…"
                best = (score, truncated)
    return best[1]


def save_basins(basins: list[Basin], *, allow_shrink: bool = False) -> Path:
    """Persist topics.json atomically, carrying the #194 clobber guard:
    refuse to overwrite a populated topology with a cliff-drop (empty when
    >= _CLOBBER_MIN_EXISTING basins exist, or below _CLOBBER_MIN_FRACTION
    of the existing count). A degenerate `compute_basins` (corpus failed to
    load, embeddings all stripped) would otherwise blow away a good
    topology that Stage 2/4 + the topology viewer depend on. The live
    file is preserved and the would-be result is stashed to a `.degenerate`
    sidecar. `allow_shrink=True` is the escape hatch.

    Previously a plain (non-atomic) `write_text`; now atomic so a crash
    mid-write can't leave a truncated topics.json that `load_basins` then
    fails to parse."""
    from ..utils import atomic_write_text
    from .turn_pairs import (
        _CLOBBER_MIN_EXISTING,
        _CLOBBER_MIN_FRACTION,
        DegenerateExtractionError,
    )

    path = basins_path()
    existing = len(load_basins())
    floor = max(1, int(existing * _CLOBBER_MIN_FRACTION))
    payload = {"basins": [b.to_dict() for b in basins]}
    if not allow_shrink and existing >= _CLOBBER_MIN_EXISTING and len(basins) < floor:
        sidecar = path.parent / (path.name + ".degenerate")
        try:
            sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass
        raise DegenerateExtractionError(
            f"Refusing to overwrite {existing} basins with {len(basins)} "
            f"(cliff-drop below {floor}). Live topology preserved; degenerate "
            f"result written to {sidecar.name}. Pass allow_shrink=True only "
            f"if the corpus genuinely shrank."
        )
    atomic_write_text(path, json.dumps(payload, indent=2))
    return path


def load_basins() -> list[Basin]:
    path = basins_path()
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        return []
    out: list[Basin] = []
    for b in payload.get("basins", []):
        if not isinstance(b, dict):
            continue
        try:
            out.append(Basin.from_dict(b))
        except TypeError:
            # One basin record is missing a required field (e.g. centroid not
            # written because the embedder was down at build) — skip it, don't
            # abort the whole load and blind every consumer (lens-health's noise
            # self-test was leaking the raw `TypeError` repr and going dark).
            continue
    return out


def basin_for_prompt(basins: list[Basin], prompt_id: str) -> str | None:
    """Lookup which basin a prompt belongs to. Used during stage 2 to
    tag each decision with its basin_id for the stage 4 post-filter."""
    for basin in basins:
        if prompt_id in basin.prompt_ids:
            return basin.id
    return None
