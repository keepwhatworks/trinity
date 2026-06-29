"""Phase 2.5 — vocabulary distillation.

Reads ~/.trinity/prompts/prompt_nodes.jsonl and emits
~/.trinity/memories/vocabulary.md — the user's terminology overlay.

Three flavors of distinctive terminology surfaced (no LLM call):

1. **Anchors** (proper-noun recurrence): capitalized multi-word entities the
   user names across ≥3 distinct conversation threads. Pure regex + thread
   count; surfaces the projects/people/products that thread through the
   user's thinking but get stopword-stripped by the lowercase tokenizer.

2. **Homonyms** (one word → two meanings): tokens whose context-embedding
   distribution spreads across multiple regions of embedding space. Detected
   by the variance ratio between two clusters of the token's prompt
   embeddings — a high split-variance ratio signals the same token sitting in
   two semantic contexts.

3. **Synonyms** (two words → one meaning): pairs of distinct tokens whose
   mean context embeddings are cosine-similar above threshold. Each token's
   "mean context" is the centroid of every prompt embedding the token appears
   in. Near-identical centroids → the words are used in the same semantic
   territory.

The same machinery the cortex layer uses (bimodality flagging via geometric
prior) applied to the user's own terminology. Pure numpy; no chairman call;
runs ~1s on a 5k-prompt index.

Output schema is markdown, intentionally human-legible — chairman reads
vocabulary.md as one of the three thinking core memories before synthesizing.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .embeddings import is_finite_embedding


# Conservative defaults — surface only the most-overloaded terms; first run
# on a real corpus showed ~12 surfaces is the right ballpark before noise
# starts dominating signal.
DEFAULT_MIN_FREQ = 5
DEFAULT_TOP_HOMONYMS = 10
DEFAULT_TOP_SYNONYMS = 10
DEFAULT_TOP_ANCHORS = 15
DEFAULT_ANCHOR_MIN_THREADS = 3
# An anchor in more than this fraction of ALL threads is boilerplate, not a
# distinctive personal anchor (the IDF intuition). 0.40 cleanly separates
# Trinity's own captured prompt scaffolding (~70-75% prevalence) from real
# user vocabulary (a few %). See find_anchors (#196).
DEFAULT_ANCHOR_MAX_THREAD_FRACTION = 0.40
# The prevalence cap only kicks in once there are enough threads for "in
# most conversations" vs "in a few" to be a real distinction — IDF is
# meaningless on a tiny corpus, where a phrase in 100% of 4 threads is the
# signal, not boilerplate. Below this, the min_threads recurrence gate
# stands alone (a small/new user hasn't generated scaffolding pollution
# yet anyway — that needs many Trinity runs = many threads).
MIN_THREADS_FOR_PREVALENCE_CAP = 20
DEFAULT_SYNONYM_COSINE_THRESHOLD = 0.92
# #250 — a homonym needs evidence from enough DISTINCT conversations, not just
# ≥ min_freq occurrences. A token used 5 times in ONE thread can score high
# bimodality off noise (two phrasings within a single session); a real overload
# ("lens" the optic vs "lens" the taste model) shows up across separate threads.
DEFAULT_HOMONYM_MIN_THREADS = 3
# #250 — two tokens with cosine ≈ 1.0 are usually a CO-OCCURRENCE artifact: they
# appear in (nearly) the same prompts, so their mean context vectors collapse
# onto each other. That's not synonymy — it's two words that always travel
# together (template phrasing, a fixed bigram). A true synonym pair occupies
# DIFFERENT prompts with similar surrounding context. Drop pairs whose prompt
# sets overlap at or above this Jaccard.
DEFAULT_SYNONYM_MAX_JACCARD = 0.5
# #250 — the DOMINANT vocab pollution on a real corpus is template
# concentration: one automated agent loop (e.g. a floor-plan engine emitting
# the same JSON shape thousands of times) floods every token view with its
# field names (`availableRoomIds`, `appliedLayers`, …). Those tokens have high
# raw frequency but their prompts are near-DUPLICATES of each other — so they
# collapse to very few *distinct* contexts. A real vocabulary word recurs across
# genuinely different prompts. Require ≥ this many effectively-distinct contexts
# (near-dup-collapsed) before a token can surface as a homonym or synonym.
DEFAULT_MIN_DISTINCT_CONTEXTS = 4
# Two context vectors at or above this cosine are treated as the same prompt
# (near-duplicate template instance) when counting distinct contexts.
DEFAULT_CONTEXT_DUP_COSINE = 0.97
# #250 — a synonym candidate appearing in more than this fraction of ALL prompts
# is a high-frequency function/common word ("been", "within", "every", "state");
# its mean context vector collapses onto the corpus centroid, so it scores
# spuriously high cosine against every other ubiquitous word. The IDF intuition,
# tuned tight (real distinctive vocab sits well under 1% prevalence; the function
# words that polluted the synonym view sit at 3-5%).
DEFAULT_SYNONYM_MAX_PREVALENCE = 0.02

# Stopwords kept tight — only words a developer would reflexively skip,
# nothing that could plausibly be a load-bearing term. The whole point is
# to surface YOUR vocabulary, not strip it down to nouns.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for if in into is it no not of on or
    such that the their then there these they this to was were will with
    you your i me my we us our they them their it its he she his her
    so do does did just very also only really maybe just like get got
    have has had can could would should might must may
    """.split()
)

_TOKEN_RX = re.compile(r"[a-zA-Z][a-zA-Z_-]{2,}")  # 3+ chars, alpha/_/-, alpha start

# Anchors: capitalized words optionally chained with internal lowercase + hyphens
# (Trinity, GitHub, Claude Code, Antigravity). The runs of single capitalized
# tokens are merged at extraction time when adjacent in source text.
_PROPER_TOKEN_RX = re.compile(r"\b([A-Z][a-zA-Z][a-zA-Z0-9_-]*(?:\s+[A-Z][a-zA-Z][a-zA-Z0-9_-]*){0,3})\b")

# Words common at sentence start that aren't real anchors — filtered out
# because they always capitalize but rarely refer to entities.
_ANCHOR_BLACKLIST = frozenset({
    "the", "this", "that", "these", "those", "there", "here",
    "what", "when", "where", "which", "who", "why", "how",
    "i", "you", "we", "they", "he", "she", "it", "my", "your",
    "but", "and", "or", "so", "if", "yes", "no", "not",
    "can", "could", "would", "should", "will", "may", "might",
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had",
    "ok", "okay", "well", "now", "then", "just", "let", "lets",
    # Imperatives + emphasis + quantifiers that capitalize (sentence start,
    # ALL-CAPS emphasis, or instruction prose) but name no entity. These
    # dominate the anchor list because Trinity's own prompt scaffolding —
    # captured back into the corpus when its prompts run through the user's
    # CLI — is full of them ("RULES", "STRICT", "WRONG", "Output", "Skip",
    # "MUST", "DURABLE", "NON-OBVIOUS"). The prevalence cap in find_anchors
    # removes the most ubiquitous; this catches the lower-prevalence
    # residue (#196). Anchors are entities (Kitchen, LDK), not verbs.
    "read", "fix", "make", "use", "add", "get", "set", "run", "change",
    "update", "create", "build", "write", "show", "find", "give", "take",
    "note", "skip", "pack", "list", "output", "keep", "stop", "start",
    "must", "wrong", "rules", "strict", "only", "durable", "every", "new",
    "one", "all", "each", "more", "most", "also", "even", "for", "an",
    "non-obvious", "users", "downloads",
    # #250 follow-on (measured 2026-05-30): mixed/Title-case floor-plan template
    # words the ALL-CAPS `_is_template_section_header` ratio filter misses.
    "valid", "compare",
    # Common adverbs + a verb/gerund that capitalize at sentence start (or as a
    # UI label) but name no entity. Measured 2026-05-31 on the real corpus: the
    # anchor list surfaced "Currently" (1154 threads), "Edit" (1006), "Living"
    # (1171) alongside the genuine floor-plan anchors. Adverbs are a closed,
    # never-an-anchor class, so listing the common ones prevents the whole
    # family rather than whack-a-moling one. "edit" joins the existing verb
    # group (update/create/change). "living" is a SOFT lead (see below) so
    # "Living Room" survives while a bare "Living" drops.
    "currently", "recently", "actually", "basically", "essentially",
    "generally", "usually", "typically", "normally", "honestly", "literally",
    "ideally", "initially", "eventually", "finally", "originally", "previously",
    "edit", "living",
})


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN_RX.findall(text or ""):
        tok = raw.lower()
        if tok in _STOPWORDS:
            continue
        # #250 — drop tokens that end in a connector (`_`/`-`): no natural word
        # does, but pasted path/identifier fragments do (e.g. the macOS
        # screencapture temp dir `nsird_screencaptureui_`). Conservative — keeps
        # real compounds like `kitchen-sink` / `lounge_chair`.
        if tok.endswith(("_", "-")):
            continue
        # Drop multi-part snake_case CODE IDENTIFIERS (>=2 underscores, e.g.
        # `test_doc_count_consistency`, `run_in_background`). A homonym is "one
        # WORD, multiple meanings"; a 3+-segment identifier is code, not a word.
        # The corpus carries the user's own dev sessions, so these recur across
        # many threads with high bimodality (varied edit contexts) and otherwise
        # rank as top "homonyms" in linguistic memory (live 2026-06-01: the memory
        # viewer's #1 homonym was `test_doc_count_consistency`, 0.98 score). One
        # underscore is kept — real compounds like `lounge_chair` survive; two+ is
        # unambiguously an identifier, never a natural word.
        if tok.count("_") >= 2:
            continue
        out.append(tok)
    return out


# "new" / "one" are blacklisted (a lone capitalized "New"/"One" is
# scaffolding, and find_anchors still treats them as noise), but they also
# lead genuine compound proper nouns — "New Relic", "New York Times",
# "One Drive". So they're SOFT leads: stripped only when they stand alone or
# when the word after them is itself blacklisted ("For New Users" → the
# "Users" follower is blacklist, so "New" still goes, collapsing the
# scaffolding prefix #196 guards against). A soft lead followed by a real
# entity word is kept, preserving the brand/project name (#206).
_SOFT_ANCHOR_LEAD = frozenset({"new", "one", "living"})


def _extract_proper_phrases(text: str) -> list[str]:
    """Pull capitalized multi-word entity candidates from text.

    Lowercases the first word against the blacklist (catches "The …" /
    "When …" sentence starts that wear capital letters but aren't anchors).
    Returns the raw matched phrases — caller decides what counts.
    """
    out: list[str] = []
    if not text:
        return out
    for raw in _PROPER_TOKEN_RX.findall(text):
        # Strip ALL leading blacklisted words, not just the first — a
        # compound scaffolding prefix ("For New Users", "Output Downloads")
        # would otherwise survive after only its first word was removed,
        # leaking exactly the noise #196 set out to filter. Exception:
        # SOFT leads ("new"/"one") are kept when they head a real compound
        # entity — i.e. only stripped when sole token or followed by another
        # blacklisted word (#206, "New Relic" / "One Drive" survive).
        words = raw.strip().split()
        while words and words[0].lower() in _ANCHOR_BLACKLIST:
            if words[0].lower() in _SOFT_ANCHOR_LEAD and len(words) > 1 and words[1].lower() not in _ANCHOR_BLACKLIST:
                break
            words = words[1:]
        if not words:
            continue
        out.append(" ".join(words))
    return out


def find_anchors(
    nodes: Iterable,
    *,
    min_threads: int,
    top_n: int,
    max_thread_fraction: float = DEFAULT_ANCHOR_MAX_THREAD_FRACTION,
) -> list[tuple[str, int, int]]:
    """Rank proper-noun phrases by distinct-thread count.

    Returns [(phrase, n_threads, n_mentions), ...] descending by thread
    recurrence then mention count. Filters to phrases appearing in
    ≥ min_threads distinct transcripts — the recurrence signal is what
    makes a token an anchor instead of a one-off proper noun.

    Two correctness guards (#196):

    - **Thread attribution**: a node with no transcript_id is skipped for
      anchor purposes. The old `transcript_id or node.id` fallback counted
      every unattributed node as its OWN thread, inflating recurrence past
      the real conversation count. A node we can't place in a thread can't
      establish cross-thread recurrence — the whole point of an anchor.

    - **Prevalence cap (IDF)**: a phrase appearing in more than
      `max_thread_fraction` of ALL threads is boilerplate, not a
      distinctive anchor. Trinity's own prompt scaffolding, captured back
      into the corpus, recurs in 70–75% of conversations ("RULES",
      "STRICT JSON", "WRONG", …); a real personal anchor recurs in a few
      percent. Ubiquity = no signal, so we drop it — the same reason
      TF-IDF down-weights common terms.
    """
    # phrase → {transcript_ids}, mention count
    threads: dict[str, set] = defaultdict(set)
    mentions: dict[str, int] = defaultdict(int)
    all_threads: set = set()
    for node in nodes:
        tid = getattr(node, "transcript_id", None)
        if not tid:
            continue
        all_threads.add(tid)
        for phrase in _extract_proper_phrases(getattr(node, "text", "") or ""):
            threads[phrase].add(tid)
            mentions[phrase] += 1
    total_threads = len(all_threads)
    cap = (
        int(total_threads * max_thread_fraction)
        if total_threads >= MIN_THREADS_FOR_PREVALENCE_CAP
        else 0
    )
    ranked = [
        (phrase, len(tids), mentions[phrase])
        for phrase, tids in threads.items()
        if len(tids) >= min_threads
        and (cap == 0 or len(tids) <= cap)
        and not _is_template_section_header(phrase, len(tids), mentions[phrase])
    ]
    # Sort by (thread recurrence DESC, mention count DESC, phrase ASC).
    # Recurrence first because "appeared in 8 different conversations" is the
    # load-bearing signal — anchors are projects/people, not single-thread
    # mentions. The phrase tie-break makes this a TOTAL order: two phrases tied
    # on BOTH thread count AND mention count would otherwise keep the upstream
    # `threads` dict order, so which one survives the `top_n` cut would flip on
    # ingest order (re-ingest reorders the dict). Phrase ASC is the stable cut.
    ranked.sort(key=lambda r: (-r[1], -r[2], r[0]))
    return ranked[:top_n]


def _is_template_section_header(phrase: str, n_threads: int, n_mentions: int) -> bool:
    """True for floor-plan / spec TEMPLATE section headers that masquerade as
    anchors (#250 follow-on, measured 2026-05-30).

    The user pastes a fixed floor-plan template into many threads, so its
    ALL-CAPS section headers ("SPEC", "ISSUE", "AREA", "ROOMS", "ACTIONABLE
    FIXES", "CURRENT FLOOR PLAN") recur across hundreds of threads — but
    exactly ONCE per templated doc, so mentions ≈ threads. A real anchor is
    either mixed-case (Kitchen, Bath, Deck) or recurs many times *within* a
    thread (Deck: 9973 mentions / 1385 threads = 7.2×). The signature that
    separates them: ALL-CAPS *and* a near-1.0 mentions/threads ratio."""
    if phrase.isupper() and n_threads > 0 and n_mentions <= n_threads * 1.2:
        return True
    return False


def _l2_normalize(vec):
    import numpy as np
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _effective_distinct_contexts(
    vecs, *, dup_cosine: float = DEFAULT_CONTEXT_DUP_COSINE, max_check: int = 80
) -> int:
    """Count effectively-distinct context vectors (near-duplicates collapsed).

    Greedy: walk the vectors, keep one only if it's below `dup_cosine` to every
    already-kept vector. A token from a repeated template (the same JSON shape
    emitted thousands of times by one agent loop) collapses to a tiny count even
    at high raw frequency — that's the #250 template-concentration signal. A
    real vocabulary word recurs across genuinely different prompts, so its
    distinct count stays high. Capped at `max_check` vectors so a high-frequency
    token stays O(max_check²), not O(freq²)."""
    import numpy as np
    kept: list = []
    for v in vecs[:max_check]:
        a = _l2_normalize(np.asarray(v, dtype=float))
        if not np.all(np.isfinite(a)):
            continue
        if any(float(a @ k) >= dup_cosine for k in kept):
            continue
        kept.append(a)
    return len(kept)


def _two_means_split_variance(vectors, *, max_samples: int = 200) -> float:
    """K=2 split silhouette — bimodality score on [0, 1].

    Single-pass two-means seeded by the two farthest-apart points, then
    silhouette: mean per-point (b - a) / max(a, b) where a = mean distance
    to own cluster, b = mean distance to other cluster.

    - Unimodal (tight homogeneous cluster, forced split): silhouette ≈ 0.
    - Bimodal (real two-cluster structure): silhouette ≈ 0.7+.

    Pure numpy, no scipy/sklearn. Caps inputs at `max_samples` because
    the pairwise distance matrices are O(n²·d) memory — a token appearing
    in 5000 prompts with 768-d embeddings would otherwise allocate
    ~19GB. 200 samples is comfortably above the statistical floor for
    "is this bimodal or not."
    """
    import numpy as np
    n = len(vectors)
    if n < 4:
        return 0.0
    # Subsample large context sets — distance-matrix memory is O(n²·d).
    # Deterministic by stride so repeated runs return the same score.
    if n > max_samples:
        idx = np.linspace(0, n - 1, max_samples, dtype=int)
        vectors = [vectors[i] for i in idx]
        n = len(vectors)
    arr = np.asarray(vectors, dtype=float)
    # Seed centroids: two farthest-apart points.
    if n <= 50:
        diffs = arr[:, None, :] - arr[None, :, :]
        dists = np.linalg.norm(diffs, axis=-1)
        i, j = divmod(int(dists.argmax()), n)
    else:
        idx = np.linspace(0, n - 1, 50, dtype=int)
        sub = arr[idx]
        diffs = sub[:, None, :] - sub[None, :, :]
        dists = np.linalg.norm(diffs, axis=-1)
        ii, jj = divmod(int(dists.argmax()), 50)
        i, j = int(idx[ii]), int(idx[jj])
    c1, c2 = arr[i], arr[j]
    d1 = np.linalg.norm(arr - c1, axis=1)
    d2 = np.linalg.norm(arr - c2, axis=1)
    a1_mask = d1 <= d2
    if a1_mask.sum() < 2 or (~a1_mask).sum() < 2:
        return 0.0
    g1 = arr[a1_mask]
    g2 = arr[~a1_mask]

    # Silhouette: for each point, compute a (avg dist to own cluster) and
    # b (avg dist to other cluster). Vectorize via pairwise-distance matrices.
    def _pairwise(a, b):
        return np.linalg.norm(a[:, None, :] - b[None, :, :], axis=-1)
    d11 = _pairwise(g1, g1)
    d22 = _pairwise(g2, g2)
    d12 = _pairwise(g1, g2)
    # Own-cluster mean excludes self → divide by (n-1).
    a_g1 = (d11.sum(axis=1) / max(len(g1) - 1, 1))
    a_g2 = (d22.sum(axis=1) / max(len(g2) - 1, 1))
    b_g1 = d12.mean(axis=1)
    b_g2 = d12.mean(axis=0)
    s_g1 = (b_g1 - a_g1) / np.maximum(a_g1, b_g1).clip(min=1e-12)
    s_g2 = (b_g2 - a_g2) / np.maximum(a_g2, b_g2).clip(min=1e-12)
    silhouette = float(np.concatenate([s_g1, s_g2]).mean())  # in [-1, 1]
    # Unimodal (tight cluster, forced k=2 split): silhouette ≈ 0.
    # Bimodal (genuine 2-cluster structure): silhouette ≈ 0.7–1.0.
    # Clip negatives to 0 (means clusters are worse than no clustering — not
    # bimodal). Threshold heuristic for callers: ≥0.5 = bimodal candidate.
    return max(0.0, min(1.0, silhouette))


def _gather_token_index(
    nodes: Iterable, *, min_freq: int
) -> tuple[dict[str, list], dict[str, set], dict[str, set]]:
    """Map each token to (context embeddings, prompt-id set, thread-id set)
    from the prompts where it appears.

    The prompt-id and thread-id sets power the #250 guards: Jaccard
    co-occurrence detection for synonyms (shared prompts) and the
    distinct-thread floor for homonyms (separate conversations). Only tokens
    in ≥ min_freq distinct prompts are kept (cheap long-tail/typo filter).
    """
    contexts: dict[str, list] = defaultdict(list)
    prompts: dict[str, set] = defaultdict(set)
    threads: dict[str, set] = defaultdict(set)
    for node in nodes:
        emb = getattr(node, "embedding", None)
        # NaN-or-Inf embeddings poison the k=2 silhouette downstream
        # (numpy distance computations propagate NaN). Same shape as
        # the filter in me/depth.py and me/basins.py.
        if not is_finite_embedding(emb):
            continue
        pid = getattr(node, "id", None)
        tid = getattr(node, "transcript_id", None)
        text = getattr(node, "text", "") or ""
        for tok in set(_tokenize(text)):
            contexts[tok].append(emb)
            if pid:
                prompts[tok].add(pid)
            if tid:
                threads[tok].add(tid)
    keep = {tok for tok, vecs in contexts.items() if len(vecs) >= min_freq}
    return (
        {tok: contexts[tok] for tok in keep},
        {tok: prompts[tok] for tok in keep},
        {tok: threads[tok] for tok in keep},
    )


def _gather_token_contexts(nodes: Iterable, *, min_freq: int) -> dict[str, list]:
    """Map token → list of embeddings (back-compat wrapper over
    `_gather_token_index`; callers that don't need the prompt/thread index
    keep their old signature)."""
    contexts, _prompts, _threads = _gather_token_index(nodes, min_freq=min_freq)
    return contexts


def find_homonyms(
    token_contexts: dict[str, list],
    *,
    top_n: int,
    token_threads: dict[str, set] | None = None,
    min_threads: int = 0,
    min_distinct: int = 0,
) -> list[tuple[str, float, int]]:
    """Rank tokens by k=2 bimodality score over their context embeddings.

    Returns [(token, score, n_contexts), ...] descending by score, top_n only.

    Ranking detail — silhouette saturates at 1.0 for genuinely bimodal
    tokens, so on a real corpus the top is `code`, `find`, `which` —
    common verbs that legitimately span many contexts but are too
    generic to surface as actionable overloads. We secondary-sort by
    INVERSE frequency: among tokens with equally-high bimodality,
    prefer the rarer one. Domain-specific overloads ("react", "lens",
    "thread") rank above generic English ("which", "find") even when
    their raw scores are identical.

    #250 distinct-thread floor: when `token_threads` is supplied and
    `min_threads > 0`, a token is skipped unless it appears in at least
    `min_threads` distinct conversations — a high bimodality score off
    ≥min_freq occurrences that all live in ONE thread is intra-session
    phrasing noise, not a real cross-context overload.
    """
    scored: list[tuple[str, float, int]] = []
    for tok, vecs in token_contexts.items():
        if token_threads is not None and min_threads:
            if len(token_threads.get(tok, ())) < min_threads:
                continue
        # #250 template-concentration floor: a bimodal score off near-duplicate
        # template prompts (one agent loop's JSON) isn't a real overload. Require
        # enough effectively-distinct contexts first.
        if min_distinct and _effective_distinct_contexts(vecs) < min_distinct:
            continue
        score = _two_means_split_variance(vecs)
        scored.append((tok, score, len(vecs)))
    # Sort by (bimodality DESC, frequency ASC, token ASC) — high score + rare =
    # most actionable. `-len(vecs)` would put high-frequency on top; we want the
    # OPPOSITE so the secondary key is len(vecs) ascending. The token tie-break
    # makes this a TOTAL order: two tokens tied on BOTH score AND frequency
    # would otherwise keep the upstream `token_contexts` dict order, so which
    # one survives the `top_n` cut would flip on ingest order. Token ASC = the
    # stable cut.
    scored.sort(key=lambda r: (-r[1], r[2], r[0]))
    return scored[:top_n]


def render_vocabulary_md(
    *, homonyms: list[tuple[str, float, int]],
    synonyms: list[tuple[str, str, float, int, int]],
    anchors: list[tuple[str, int, int]],
    corpus_size: int,
    total_corpus: int | None = None,
) -> str:
    """Compose the markdown output. Chairman reads it as one of the three
    thinking core memories — keep it human-legible, no JSON dump.

    `corpus_size` is the EMBEDDED prompt count the homonym/synonym scan ran
    over; `total_corpus` (when given) is the full prompt count the anchor
    scan saw. Naming both keeps the "Scanned N" line honest — it used to
    print only the embedded count as if it were the whole corpus (#250
    staleness: 18,270 embedded shown while 27,225 were ingested)."""
    if total_corpus is not None and total_corpus != corpus_size:
        scan_line = (
            f"_Scanned {total_corpus} prompts for anchors; {corpus_size} embedded "
            f"prompts for homonyms. Tokens require ≥5 occurrences; "
            f"anchors require ≥3 distinct threads._"
        )
    else:
        scan_line = (
            f"_Scanned {corpus_size} prompts. Tokens require ≥5 occurrences; "
            f"anchors require ≥3 distinct threads._"
        )
    lines = [
        "# Your vocabulary",
        "",
        "*Pure-geometric scan of your prompt corpus. Two views of distinctive",
        "terminology — anchors and homonyms — surfaced as one of your",
        "three thinking core memories.*",
        "",
        scan_line,
        "",
        "## Anchors — proper nouns you return to across threads",
        "",
    ]
    if anchors:
        lines.append("Projects, people, products, and named ideas that recur across distinct conversations. Each row: anchor, distinct threads, total mentions.")
        lines.append("")
        lines.append("| anchor | threads | mentions |")
        lines.append("|---|---|---|")
        for phrase, n_threads, n_mentions in anchors:
            lines.append(f"| {phrase} | {n_threads} | {n_mentions} |")
    else:
        lines.append("_(none yet — no capitalized phrase recurs across enough distinct threads)_")
    lines.append("")
    lines.append("## Homonyms — one word, multiple meanings")
    lines.append("")
    if homonyms:
        lines.append("Words you use across distinct semantic contexts. Each row: token, bimodality score (0–1), times used.")
        lines.append("")
        lines.append("| token | bimodality | uses |")
        lines.append("|---|---|---|")
        for tok, score, n in homonyms:
            lines.append(f"| `{tok}` | {score:.2f} | {n} |")
    else:
        lines.append("_(none yet — your terms are sitting in coherent contexts)_")
    lines.append("")
    # #250 follow-on (cut 2026-05-30): the Synonyms section is removed. Measured
    # ~0% useful — top pairs were template co-occurrence (`opensintoroomid`/
    # `widthft`, `timed`/`wet`), not synonymy. Near-identical context vectors
    # mean "these tokens travel together in templated content", which neither
    # the Jaccard nor centroid-margin guards (#250) reliably separate from real
    # synonymy. Distance ≠ synonymy; the section produced noise, so it's gone.
    # `synonyms` stays in the signature for back-compat (always passed empty).
    return "\n".join(lines)


def distill_vocabulary(
    *,
    min_freq: int = DEFAULT_MIN_FREQ,
    top_homonyms: int = DEFAULT_TOP_HOMONYMS,
    top_synonyms: int = DEFAULT_TOP_SYNONYMS,
    top_anchors: int = DEFAULT_TOP_ANCHORS,
    anchor_min_threads: int = DEFAULT_ANCHOR_MIN_THREADS,
    synonym_threshold: float = DEFAULT_SYNONYM_COSINE_THRESHOLD,
    homonym_min_threads: int = DEFAULT_HOMONYM_MIN_THREADS,
    min_distinct: int = DEFAULT_MIN_DISTINCT_CONTEXTS,
    synonym_max_jaccard: float = DEFAULT_SYNONYM_MAX_JACCARD,
    synonym_max_prevalence: float = DEFAULT_SYNONYM_MAX_PREVALENCE,
) -> dict:
    """End-to-end Phase 2.5: scan corpus → emit vocabulary.md.

    Returns a report dict (`{ok, path, homonyms, synonyms, corpus_size}`).
    Returns `skipped: True` cleanly when no embedded prompts exist yet.

    Uses `iter_prompt_nodes(limit=None)` to walk the full corpus. The
    default `iter_prompt_nodes()` caps at 5000 most-recent nodes (hot
    path for launchpad/search), but recent ingest skips embedding to keep
    that path fast — so embeddings sit on the older seeded prompts BELOW
    the cap. `limit=None` lifts the cap and is cached in-process by file
    mtime, so dream/vocabulary/basins all share the parse cost.
    """
    from .memory.store import iter_prompt_nodes
    from .state_paths import vocabulary_path

    nodes = list(iter_prompt_nodes(limit=None))
    nodes_with_emb = [n for n in nodes if getattr(n, "embedding", None)]
    if not nodes_with_emb:
        return {
            "ok": False, "skipped": True,
            "reason": "no embedded prompts yet — run `import-export` first",
        }

    contexts, token_prompts, token_threads = _gather_token_index(
        nodes_with_emb, min_freq=min_freq
    )
    # Anchors run on the FULL node set (not just embedded ones) — proper-noun
    # recurrence is pure-text and benefits from every transcript we have.
    # Doesn't share the embedding/min_freq gate; can surface signal even when
    # the corpus is too sparse for k=2 silhouette to be meaningful.
    anchors = find_anchors(nodes, min_threads=anchor_min_threads, top_n=top_anchors)
    if not contexts and not anchors:
        return {
            "ok": False, "skipped": True,
            "reason": f"no tokens meet min_freq={min_freq} and no anchors meet min_threads={anchor_min_threads}; corpus too small or too sparse",
        }

    # #250: distinct-thread floor for homonyms (kills intra-session phrasing
    # noise) + Jaccard co-occurrence guard for synonyms (kills cos≈1.0
    # always-together artifacts).
    homonyms = (
        find_homonyms(
            contexts, top_n=top_homonyms,
            token_threads=token_threads, min_threads=homonym_min_threads,
            min_distinct=min_distinct,
        ) if contexts else []
    )
    # #250 follow-on: the synonyms section was cut (measured ~0% useful —
    # template co-occurrence, not synonymy). No longer computed; `find_synonyms`
    # stays as a dormant, unit-tested function pending a redesign that separates
    # synonymy from "tokens that travel together".
    synonyms: list = []
    md = render_vocabulary_md(
        homonyms=homonyms, synonyms=synonyms, anchors=anchors,
        corpus_size=len(nodes_with_emb), total_corpus=len(nodes),
    )
    path = vocabulary_path()
    path.write_text(md, encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "corpus_size": len(nodes_with_emb),
        "tokens_scanned": len(contexts),
        "anchors_emitted": len(anchors),
        "homonyms_emitted": len(homonyms),
        "synonyms_emitted": len(synonyms),
    }
