"""Heuristic search over PromptNodes — no embedding model required.

`search_prompt_nodes(query, top_k)` ranks past prompts by:
  - substring/token overlap with the query (0..1)
  - recency (exponential decay, 30-day half-life)
  - theme tags (HIGH_VALUE_THEMES bonus)
  - council count (already-evaluated prompts have demonstrated value)
  - hardness (replay_value heuristics)
  - staleness penalty (recently-run prompts get pushed down)

Empty query = lens-build sampling mode (rank by replay-value heuristics only).

This is the embedding-free fast path. Trinity's product surface (launchpad
autofill, MCP search_prompts, lens-build sampling, replay-history candidates)
does not load nomic-embed-v1.5 or any ML model. Net: 22s cold-start → <100ms,
RSS drops by ~150MB. Embeddings stay available in the `embeddings/` package
for research/k-NN tooling that explicitly opts in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..ingest import is_user_facing_text
from .replay_value import (
    diversify_mmr,
    infer_hardness,
    replay_value_score,
    staleness_score,
    theme_score,
)
from .schemas import PromptNode
from .store import iter_prompt_nodes, iter_prompt_nodes_no_embedding


@dataclass
class SearchResult:
    """A retrieval hit ready for display in the autofill dropdown."""
    prompt_id: str
    text: str
    score: float
    prompt_similarity: float  # repurposed: substring/token-overlap score (0..1)
    window_similarity: float
    transcript_similarity: float
    hardness: float
    reasons: list[str]
    chairman_winner: str | None = None
    council_count: int = 0
    provider: str = ""
    timestamp: str | None = None
    # Thread context: the immediately-preceding assistant turn from the same
    # transcript. Critical for replaying short turns like "continue." or
    # "Let me restart." against fresh models — without this the new model has
    # no idea what the user is responding to.
    preceding_assistant_text: str = ""
    transcript_id: str = ""
    turn_index: int = 0


def _build_reasons(
    *,
    prompt_similarity: float,
    transcript_similarity: float,
    hardness: float,
    council_count: int,
    chairman_winner: str | None,
    themes: list[str],
) -> list[str]:
    reasons: list[str] = []
    if prompt_similarity > 0.78:
        reasons.append("Match")
    elif prompt_similarity > 0.4:
        reasons.append("Partial match")
    if hardness > 0.5:
        reasons.append("Uncertain")
    if council_count >= 1:
        reasons.append("Repeated")
    if themes:
        reasons.append("Known theme")
    if not reasons:
        reasons.append("Recent")
    return reasons


# Common English stopwords — substring matching on these is meaningless. A
# query of "the and what" would otherwise match almost every prompt with
# `prompt_similarity > 0.78`, which the MCP confidence band treats as "high"
# and the launchpad shows as the top suggestion. Keep the set tight; only
# add words that have ZERO discriminative value as substrings.
_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "but", "for", "with", "from", "this", "that",
    "have", "has", "had", "are", "was", "were", "been", "being",
    "what", "when", "where", "which", "who", "how", "why",
    "you", "your", "yours", "they", "them", "their",
    "can", "will", "would", "could", "should",
    "all", "any", "some", "only", "just", "also", "than", "then",
    "out", "off", "now", "not", "yet",
})


def _substring_score(query_text: str, node_text: str) -> float:
    """0..1 — how well the query matches the node text via substring/token overlap.

    1.0  = full query phrase appears verbatim in node text.
    >0.7 = most substantive query tokens appear in node text.
    0.0  = no substantive query tokens, or all-stopword query.

    Tokens shorter than 3 chars or in the stopword set don't count toward
    the match. A stopword-only query returns 0 — surfacing arbitrary prompts
    on a query like "the and what" was the bug.
    """
    if not query_text:
        return 0.0
    q = query_text.lower().strip()
    if not q:
        return 0.0
    t = node_text.lower()

    # Full-phrase match: max score (skip the stopword filter — if the user
    # typed the literal phrase, they want exact matches even if it's
    # stopword-heavy).
    if q in t:
        return 1.0

    # Substantive tokens only: > 2 chars AND not a stopword
    q_tokens = [w for w in q.split() if len(w) > 2 and w not in _QUERY_STOPWORDS]
    if not q_tokens:
        return 0.0
    hits = sum(1 for w in q_tokens if w in t)
    return hits / len(q_tokens)


def _recency_score(timestamp: str | None) -> float:
    """0..1 — recent prompts score higher. 30-day half-life."""
    if not timestamp:
        return 0.0
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    if days < 0:
        return 1.0
    return max(0.0, 0.5 ** (days / 30.0))


def search_prompt_nodes(
    query_text: str,
    *,
    top_k: int = 8,
    candidate_pool: int = 50,
    dim: int = 768,  # accepted for backward compat, ignored
) -> list[SearchResult]:
    """Heuristic search over PromptNodes — no embedding model load.

    Query mode (non-empty query): rank by substring × 2.5 + replay-value signals.
    Empty-query mode (lens-build / replay-history): rank by replay-value alone.

    Returns top_k diversified results via MMR (token-jaccard, no embeddings).
    """
    # MMR diversification uses token-jaccard, scoring uses substring +
    # replay-value heuristics — embeddings are unused on this path.
    # Skip embedding-array parsing to save ~1.85s of json.loads on
    # cold-cache renders (1GB / 38K-node real corpus).
    nodes = list(iter_prompt_nodes_no_embedding())
    if not nodes:
        return []

    has_query = bool(query_text.strip())
    scored: list[tuple[PromptNode, float, float]] = []

    for node in nodes:
        # Skip scaffolding prompts that leaked through an OLDER pre-filter ingest
        # (Trinity's extractor, "You are ..." council prompts, per-source harness
        # blocks like <goal_context> / "Used an Assistant feature" — all captured
        # role=user in CLI/web transcripts). The index is append-only, so nodes
        # ingested under a weaker filter persist until a re-seed; re-applying the
        # FULL filter here keeps already-poisoned PromptNodes out of agent search
        # WITHOUT a re-ingest. Completes the original `you are`/`you will` guard.
        if not is_user_facing_text(node.text):
            continue

        substring = _substring_score(query_text, node.text) if has_query else 0.0

        # When a query is given, drop prompts that share zero query tokens —
        # they're not what the user is looking for. Without this filter, the
        # ranker would surface high-recency prompts that don't match the query.
        if has_query and substring == 0.0:
            continue

        recency = _recency_score(node.timestamp or node.created_at)
        themes_val = theme_score(node.themes)
        council_count = len(node.council_run_ids)
        council_signal = min(1.0, council_count / 3.0)
        hardness = infer_hardness(node)
        recently_run = 1.0 if staleness_score(node.last_replayed_at) < 0.25 else 0.0

        if has_query:
            # Query-driven: substring dominates, plus value heuristics
            score = (
                2.5 * substring
                + 0.3 * recency
                + 0.4 * themes_val
                + 0.4 * council_signal
                + 0.3 * hardness
                - 0.4 * recently_run
            )
        else:
            # Empty query (lens-build sampling): replay-value heuristics
            score = replay_value_score(
                prompt_similarity=0.0,
                known_theme=themes_val,
                uncertainty=hardness,
                importance=node.importance or 0.0,
                staleness=staleness_score(node.last_replayed_at),
                recently_run=recently_run,
            )
            # Mild recency boost so the chairman sees fresh signal alongside
            # high-replay-value classics.
            score += 0.2 * recency

        scored.append((node, score, substring))

    # Relevance score DESC, node id ASC as a stable tie-break so the
    # candidate_pool cut is a TOTAL order: two nodes with an equal score
    # straddling the candidate_pool boundary would otherwise keep the upstream
    # scan order, so WHICH memory the chairman sees would flip on that order.
    scored.sort(key=lambda row: (-row[1], row[0].id))
    candidates = scored[:candidate_pool]

    results: list[SearchResult] = []
    for node, score, substring in candidates:
        hardness = infer_hardness(node)
        results.append(
            SearchResult(
                prompt_id=node.id,
                text=node.text,
                score=score,
                prompt_similarity=substring,
                window_similarity=0.0,
                transcript_similarity=0.0,
                hardness=hardness,
                reasons=_build_reasons(
                    prompt_similarity=substring,
                    transcript_similarity=0.0,
                    hardness=hardness,
                    council_count=len(node.council_run_ids),
                    chairman_winner=node.chairman_winner,
                    themes=node.themes,
                ),
                chairman_winner=node.chairman_winner,
                council_count=len(node.council_run_ids),
                provider=node.provider,
                timestamp=node.timestamp,
                preceding_assistant_text=node.preceding_assistant_text or "",
                transcript_id=node.transcript_id,
                turn_index=node.turn_index,
            )
        )
    return diversify_mmr(results, top_k=top_k)


def search(
    query_text: str,
    *,
    top_k: int = 8,
    candidate_pool: int = 50,
    dim: int = 768,
) -> list[SearchResult]:
    """Backward-compat alias for `search_prompt_nodes`.

    The previous TurnWindow + TranscriptNode hierarchical search was
    embedding-based. With the embedding-free path, both tiers collapse to
    the prompt-only heuristic search; the alias is preserved so older
    callers (tests, scripts) keep working.
    """
    return search_prompt_nodes(query_text, top_k=top_k, candidate_pool=candidate_pool, dim=dim)
