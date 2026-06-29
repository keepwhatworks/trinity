"""Semantic noise filter — separate signal from noise by GEOMETRY, not regex.

The boundary filter in `ingest._is_user_facing_prompt` is a growing pile of
hand-written patterns (scaffolding prefixes, slash-command bodies, "the human"
extraction prompts, agent-ops control). The audit warned it won't generalize —
a new phrasing of the same noise slips through. But the 768-d embeddings
already separate these: agent-ops / harness / extraction prompts cluster into
tight regions far from real taste. A handful of *prototype* vectors plus a
cosine threshold catches held-out phrasings no rule was written for.

The non-obvious part (validated on the real corpus): a pure distance-to-noise
threshold ALSO flags the user's terse *taste* ("lever:on/off", "MAKE EYES AND A
NOESE", "better") because short imperatives sit near agent-ops geometrically. So
the classifier is DUAL: a prompt is noise only when it is more noise-like than
taste-like — closer to a noise prototype than to the user's own taste regions,
by a margin. That dropped the flag rate from 7.7% (with false positives on real
terse taste) to 0.6% (clean), sparing "terse and meaningful" while catching
"terse and empty".

This is one of three geometric axes that replace hand-tuned heuristics:
  - distance-to-noise-prototype  → THIS (noise vs taste)
  - cluster cohesion             → #255 (real topic vs catch-all)
  - chapter-spread + recency     → #256 (durable trait vs passing project)
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# Universal noise prototypes — the same harness/agent-ops/extraction shapes
# show up for every user, so these are not user-specific. Add an EXAMPLE here,
# never a regex, to teach the filter a new noise category.
NOISE_PROTOTYPES: dict[str, list[str]] = {
    "agent_ops": [
        "respond with the word HELLO and nothing else",
        "continue with the plan if currently paused",
        "output only OK",
        "just reply YES nothing more",
        "resume the previous task",
    ],
    "harness": [
        "# AGENTS.md instructions for the project",
        "<environment_context><cwd>/Users/x</cwd></environment_context>",
        "You are extracting durable facts about the user",
    ],
    "extraction": [
        "Find the idiosyncratic words this human introduces",
        "Compose a taste profile about this person in third person",
    ],
}

# Validated on the live corpus (see module docstring): a prompt is noise when
# its similarity to the nearest noise prototype exceeds its similarity to the
# nearest taste region by MARGIN, AND clears an absolute floor (so a prompt that
# is merely "not very taste-like" isn't condemned as noise).
NOISE_FLOOR = 0.45
NOISE_MARGIN = 0.05


def _l2(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else list(vec)


def _max_cosine(unit_vec: Sequence[float], unit_matrix: Sequence[Sequence[float]]) -> float:
    best = -1.0
    for row in unit_matrix:
        a, b = unit_vec, row
        # Matryoshka safety: prototypes embed at 768 but a stored vector can be
        # a truncated dim. A bare zip() would silently dot mismatched prefixes
        # and return a wrong (non-unit) cosine. Align to the common prefix and
        # re-normalize so the score stays a true cosine.
        if len(a) != len(b):
            m = min(len(a), len(b))
            a, b = _l2(list(a[:m])), _l2(list(b[:m]))
        dot = sum(x * y for x, y in zip(a, b))
        if dot > best:
            best = dot
    return best


def noise_prototype_vectors() -> list[list[float]]:
    """Embed the curated noise prototypes (L2-normalized). Best-effort: returns
    [] if the embedder is unavailable, which disables semantic filtering (the
    regex pre-filter still runs)."""
    try:
        from ..embeddings import embed_batch, mlx_actually_loaded
    except Exception:
        return []
    # Abstain under the TF-IDF fallback: it can't separate meaning (0/5 on the
    # triplet test), so the dual noise-vs-taste geometry would misclassify.
    # Returning [] disables semantic filtering (the regex pre-filter still runs).
    if not mlx_actually_loaded():
        return []
    texts = [t for cat in NOISE_PROTOTYPES.values() for t in cat]
    try:
        raw = embed_batch(texts)
    except Exception:
        return []
    return [_l2(v) for v in raw if v]


def is_semantic_noise(
    embedding: Sequence[float],
    noise_unit: Sequence[Sequence[float]],
    taste_unit: Sequence[Sequence[float]],
    *,
    floor: float = NOISE_FLOOR,
    margin: float = NOISE_MARGIN,
) -> bool:
    """Dual-criterion: noise iff nearer a noise prototype than any taste region
    (by `margin`) AND over the absolute `floor`. `taste_unit` is a set of
    L2-normalized taste-region centroids (e.g. existing basin centroids); when
    empty, the filter abstains (returns False) rather than guess."""
    if not embedding or not noise_unit or not taste_unit:
        return False
    v = _l2(embedding)
    noise_s = _max_cosine(v, noise_unit)
    if noise_s < floor:
        return False
    taste_s = _max_cosine(v, taste_unit)
    return noise_s > taste_s + margin


def semantic_noise_report(limit: int | None = None) -> dict:
    """Introspection: score the live corpus and report how much is semantic
    noise, with examples. Uses existing basin centroids as the taste reference
    (non-circular — the previous build's topology). For validating the filter
    against the regex before it replaces any rules."""
    from ..me.basins import load_basins
    from ..memory.store import iter_prompt_nodes

    noise_unit = noise_prototype_vectors()
    basins = load_basins()
    taste_unit = [_l2(b.centroid) for b in basins if getattr(b, "centroid", None)]
    if not noise_unit or not taste_unit:
        return {"ready": False, "reason": "no embedder or no basins"}

    total = 0
    flagged = 0
    examples: list[str] = []
    for node in iter_prompt_nodes(limit=limit):
        emb = getattr(node, "embedding", None)
        if not emb:
            continue
        total += 1
        if is_semantic_noise(emb, noise_unit, taste_unit):
            flagged += 1
            if len(examples) < 12:
                examples.append((getattr(node, "text", "") or "")[:80])
    return {
        "ready": True,
        "total": total,
        "flagged": flagged,
        "fraction": round(flagged / total, 4) if total else 0.0,
        "examples": examples,
    }
