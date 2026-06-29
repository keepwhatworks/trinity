"""Replay-value scoring + hardness inference (§8.5).

The autofill ranking is *not* pure cosine similarity. Rank by usefulness to
re-run: similar + repeated + uncertain + stale + not-recently-replayed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .schemas import PromptNode


HIGH_VALUE_THEMES: set[str] = {
    "trinity",
    "router",
    "model_council",
    "verification",
    "evals",
    "capital_allocation",
    "housing_os",
    "agent_distribution",
    "closed_loop_learning",
}


def replay_value_score(
    *,
    prompt_similarity: float,
    window_similarity: float = 0.0,
    transcript_similarity: float = 0.0,
    cluster_density: float = 0.0,
    known_theme: float = 0.0,
    uncertainty: float = 0.0,
    importance: float = 0.0,
    staleness: float = 0.0,
    recently_run: float = 0.0,
) -> float:
    """Replay-value scoring. All inputs are 0..1 normalized."""
    return (
        0.30 * prompt_similarity
        + 0.14 * window_similarity
        + 0.06 * transcript_similarity
        + 0.14 * cluster_density
        + 0.14 * known_theme
        + 0.16 * uncertainty
        + 0.10 * importance
        + 0.06 * staleness
        - 0.16 * recently_run
    )


def infer_hardness(node: PromptNode) -> float:
    """Hardness inference without LLM calls (§8.5).

    Higher = more worth re-running through council.
    """
    score = 0.0
    council_count = len(node.council_run_ids)
    if council_count == 0:
        score += 0.15
    if council_count > 1:
        score += 0.10
    if node.themes and any(t in HIGH_VALUE_THEMES for t in node.themes):
        score += 0.20
    if (node.importance or 0.0) > 0.7:
        score += 0.15
    return min(score, 1.0)


def staleness_score(last_replayed_at: str | None) -> float:
    """0..1, where 1 means very stale and worth retesting."""
    if not last_replayed_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(last_replayed_at.replace("Z", "+00:00"))
    except ValueError:
        return 1.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days = (now - ts).total_seconds() / 86400.0
    if days < 2:
        return 0.0
    if days < 7:
        return 0.25
    if days < 30:
        return 0.6
    return 1.0


def theme_score(themes: list[str]) -> float:
    if not themes:
        return 0.0
    hits = sum(1 for t in themes if t in HIGH_VALUE_THEMES)
    return min(hits / 2.0, 1.0)


def diversify_mmr(
    items: list,
    *,
    top_k: int = 8,
    lambda_factor: float = 0.72,
    similarity_fn=None,
) -> list:
    """Maximal Marginal Relevance — diversify a ranked list.

    items must each have `.score` and `.text` (or `.id`) attributes.
    similarity_fn(a, b) -> 0..1; defaults to a cheap text-token jaccard
    so MMR works without re-embedding.
    """
    if similarity_fn is None:
        similarity_fn = _text_jaccard

    selected: list = []
    # Score DESC, then a stable id (or text) tie-break so the greedy MMR seed
    # order is deterministic: two items with an equal score would otherwise keep
    # the input order, so which one MMR picks first (and thus the whole diverse
    # selection) would flip on input order.
    remaining = sorted(
        items,
        key=lambda x: (-x.score, getattr(x, "id", "") or getattr(x, "text", "")),
    )

    while len(selected) < top_k and remaining:
        best_index = 0
        best_mmr = float("-inf")
        for i, item in enumerate(remaining):
            sim_to_selected = (
                max(similarity_fn(item, s) for s in selected) if selected else 0.0
            )
            mmr = lambda_factor * item.score - (1 - lambda_factor) * sim_to_selected
            if mmr > best_mmr:
                best_mmr = mmr
                best_index = i
        selected.append(remaining.pop(best_index))
    return selected


def _text_jaccard(a, b) -> float:
    text_a = (getattr(a, "text", None) or "").lower().split()
    text_b = (getattr(b, "text", None) or "").lower().split()
    if not text_a or not text_b:
        return 0.0
    set_a = set(text_a)
    set_b = set(text_b)
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0
