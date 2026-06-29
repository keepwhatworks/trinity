"""k-NN advisory layer for routing recommendations.

Queries a pre-mined hard-example corpus using embedding similarity to
provide structured advice for the watcher:
  - Should this session trigger council?
  - Is there a better provider (reroute suggestion)?
  - What are the top-2 providers for this task shape?

This module is advisory only — it never makes routing decisions.
The watcher uses the advice to upgrade (never downgrade) its
heuristic recommendations.

Dependencies: only ``embeddings`` and ``config``. No research imports.
Graceful degradation: returns None when corpus or embeddings unavailable.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .state_paths import research_dir


@dataclass
class KnnAdvice:
    """Structured advice from the k-NN corpus lookup."""

    should_council: bool = False
    council_confidence: float = 0.0  # fraction of neighbors agreeing
    reroute_provider: str | None = None
    reroute_similarity: float = 0.0  # best cross-provider match score
    top2_providers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    neighbor_count: int = 0


@dataclass
class _CorpusEntry:
    """Minimal in-memory representation of a hard example."""

    example_id: str
    provider: str
    label: str  # "good_fit", "bad_fit", "needs_council"
    hard_type: str
    prompt: str
    vector: list[float] | None = None


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

_corpus_cache: list[_CorpusEntry] | None = None


def _corpus_dirs() -> list[Path]:
    base = research_dir()
    return [base / "hard_examples", base / "examples"]


def _load_corpus(*, force: bool = False) -> list[_CorpusEntry]:
    """Load hard examples from disk into memory. Cached after first load."""
    global _corpus_cache
    if _corpus_cache is not None and not force:
        return _corpus_cache

    corpus_dir: Path | None = None
    for candidate in _corpus_dirs():
        if candidate.exists() and any(candidate.glob("*.json")):
            corpus_dir = candidate
            break

    if corpus_dir is None:
        _corpus_cache = []
        return _corpus_cache

    entries: list[_CorpusEntry] = []
    for path in corpus_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            prompt = (raw.get("transcript", {}).get("first_user_text") or "").strip()
            if not prompt:
                continue
            entries.append(_CorpusEntry(
                example_id=raw["example_id"],
                provider=raw["chosen_provider"],
                label=raw["label"],
                hard_type=raw.get("hard_type", "replay"),
                prompt=prompt[:1500],
            ))
        except (json.JSONDecodeError, KeyError):
            continue

    _corpus_cache = entries
    return _corpus_cache


def corpus_size() -> int:
    """Number of entries in the hard-example corpus."""
    return len(_load_corpus())


# ---------------------------------------------------------------------------
# k-NN lookup
# ---------------------------------------------------------------------------

# Cosine via the canonical primitive (was one of four copies — unified so the
# dimension guard lives in exactly one place; embeddings/__init__ re-exports it
# and is light: heavy backends load lazily).
from .embeddings import cosine_similarity as _cosine_similarity


def advise(
    prompt: str,
    provider: str,
    *,
    k: int = 5,
    dim: int = 512,
    council_threshold: float = 0.6,
) -> KnnAdvice | None:
    """Query the hard-example corpus for k-NN advice on this prompt.

    Returns None if:
      - Embeddings package not available
      - Corpus is empty or too small (< k+1 entries)
      - Prompt is empty

    The advice is structured but never prescriptive. The caller decides
    whether to act on it.

    Args:
        prompt: the user's first message text
        provider: the current session's provider
        k: number of neighbors to consult
        dim: embedding dimension
        council_threshold: fraction of neighbors that must agree
            for should_council=True (default 0.6 = 3 of 5)
    """
    if not prompt or not prompt.strip():
        return None

    # MLX-or-TF-IDF: `embeddings.embed()` falls back to TF-IDF on any MLX
    # failure (network, missing model, no [mlx] extras). The previous
    # `is_available()` gate refused to run when only TF-IDF was available,
    # even though the downstream similarity math works on any cosine-
    # comparable vectors. TF-IDF on the advisory corpus is a coarser but
    # still useful signal; the downstream caller (knn_ranker) treats None
    # vs KnnAdvice as feature-flag-style — paying for the gate cost
    # only what it actually returns. Letting TF-IDF through gives every
    # install the k-NN advisory layer instead of just `[mlx]`-equipped ones.
    try:
        from . import embeddings as emb
    except ImportError:
        return None

    corpus = _load_corpus()
    if len(corpus) < k + 1:
        return None

    # Embed the query prompt
    query_vec = emb.embed(prompt, dim=dim)

    # Embed corpus entries (lazy, cached via shared embedding cache)
    for entry in corpus:
        if entry.vector is None:
            entry.vector = emb.embed(entry.prompt, dim=dim)

    # Find k nearest neighbors
    similarities: list[tuple[float, _CorpusEntry]] = []
    for entry in corpus:
        sim = _cosine_similarity(query_vec, entry.vector)
        similarities.append((sim, entry))
    # Similarity DESC, example_id ASC as a stable tie-break so the top-k cut is
    # a TOTAL order: two corpus entries equidistant from the query straddling
    # the k boundary would otherwise keep the corpus order, so which neighbor
    # (and its label/provider votes) is included would flip on that order.
    similarities.sort(key=lambda x: (-x[0], x[1].example_id))
    top_k = similarities[:k]

    if not top_k:
        return None

    # Vote on labels and providers
    label_votes: Counter[str] = Counter()
    provider_votes: Counter[str] = Counter()
    evidence_lines: list[str] = []

    for sim, entry in top_k:
        label_votes[entry.label] += 1
        provider_votes[entry.provider] += 1

    # 1. Should council?
    council_labels = {"needs_council", "bad_fit"}
    council_votes = sum(label_votes.get(l, 0) for l in council_labels)
    council_frac = council_votes / k
    should_council = council_frac >= council_threshold

    # 2. Reroute suggestion: best cross-provider match
    reroute_provider = None
    reroute_sim = 0.0
    for sim, entry in top_k:
        if entry.provider != provider and sim > reroute_sim:
            reroute_provider = entry.provider
            reroute_sim = sim

    # 3. Top-2 providers
    top2 = [p for p, _ in provider_votes.most_common(2)]

    # 4. Build evidence lines
    total_neighbors = len(top_k)
    avg_sim = sum(s for s, _ in top_k) / total_neighbors
    evidence_lines.append(
        f"k-NN ({total_neighbors} neighbors, avg sim {avg_sim:.2f}): "
        f"{', '.join(f'{l}={c}' for l, c in label_votes.most_common())}"
    )
    if should_council:
        evidence_lines.append(
            f"Embedding neighbors suggest council ({council_frac:.0%} agreement)."
        )
    if reroute_provider and reroute_sim > 0.7:
        evidence_lines.append(
            f"Similar session found in {reroute_provider} (sim={reroute_sim:.2f})."
        )
    if top2:
        evidence_lines.append(
            f"Top providers for similar tasks: {', '.join(top2)}."
        )

    return KnnAdvice(
        should_council=should_council,
        council_confidence=council_frac,
        reroute_provider=reroute_provider if reroute_sim > 0.7 else None,
        reroute_similarity=reroute_sim,
        top2_providers=top2,
        evidence=evidence_lines,
        neighbor_count=total_neighbors,
    )
