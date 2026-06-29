"""Find questions the user asked across providers, group them by semantic
similarity, and surface as bootstrap material for the cortex.

Every PromptNode the seed pipeline produces carries the user's prompt
text, the assistant's response (preceding/following_assistant_text), AND
an embedding. The data needed to compare "what claude said when I asked
X" vs "what gemini said when I asked the same X" already exists on disk
— Trinity just hasn't been mining it.

This module does the mining: O(N²) cosine sim within each task_type
bucket (cheap at ~thousands of nodes per bucket), groups embeddings
above a configurable threshold into clusters, drops clusters that don't
span ≥ 2 providers, returns the cross-provider clusters as candidates
for synthetic-council generation.

Why not k-NN graph + connected components: the bootstrap pass runs
once or rarely; O(N²) finishes in seconds at the basin sizes that
matter, and the simpler code makes the discovery semantics auditable
("this pair clustered because cosine = 0.87" is trivially explainable).
"""
from __future__ import annotations

from dataclasses import dataclass

from .embeddings import is_finite_embedding
from .memory.schemas import PromptNode


# Two prompts whose embeddings have cosine similarity >= this are treated
# as "the same question" for pairing purposes. 0.85 is empirically the
# floor for nomic-embed-text-v1.5 semantic equivalence — below that you
# start including topically-related but distinct questions.
DEFAULT_SIMILARITY_THRESHOLD = 0.85
# Drop clusters whose representative prompt is conversational filler.
# Greedy clustering on the real corpus surfaces "10 more", "Thank you",
# "comma separated" first because short generic prompts match across
# providers trivially. Worth zero as synthetic-council seeds.
DEFAULT_MIN_PROMPT_WORDS = 6


@dataclass
class ProviderResponse:
    """One provider's answer to a clustered question."""
    provider: str
    prompt_text: str
    response_text: str
    node_id: str
    timestamp: str | None


@dataclass
class CrossProviderCluster:
    """A bundle of (provider, response) pairs all answering effectively the
    same question. Ready to feed `_synthesize_responses` as a synthetic
    council."""
    representative_prompt: str
    members: list[ProviderResponse]
    # Average pairwise similarity within the cluster — a quality signal.
    # Used to sort clusters (tightest first) and to surface the score in
    # the bootstrap output so the user can audit borderline clusters.
    coherence: float

    @property
    def providers(self) -> set[str]:
        return {m.provider for m in self.members}

    @property
    def n_providers(self) -> int:
        return len(self.providers)


def _node_response_text(node: PromptNode) -> str:
    """Pick the assistant text most likely to be the answer to this prompt.

    PromptNodes carry both preceding and following assistant text because
    we don't always know which is the response (depends on transcript
    layout). `following_assistant_text` is the standard case (the user
    asked, the model answered immediately after); fall back to preceding
    if the following is empty (some Gemini Takeout cells flip the order).
    """
    if node.following_assistant_text:
        return node.following_assistant_text
    return node.preceding_assistant_text


def find_cross_provider_clusters(
    nodes: list[PromptNode],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_providers: int = 2,
    max_cluster_size: int = 8,
    min_prompt_words: int = DEFAULT_MIN_PROMPT_WORDS,
) -> list[CrossProviderCluster]:
    """Discover clusters of PromptNodes that ask effectively the same
    question across ≥ ``min_providers`` providers.

    Algorithm:
      1. Filter to nodes WITH embeddings AND assistant response text.
      2. Stack embeddings + L2-normalize once. Each seed's cosine
         against all others is then a single BLAS matmul (microseconds
         per seed instead of milliseconds — 100× speedup over
         element-by-element Python).
      3. Greedy clustering: for each node, find all other nodes whose
         embedding similarity >= threshold. Form a cluster.
      4. Drop clusters that don't span ``min_providers`` distinct
         providers (intra-provider near-duplicates aren't useful).
      5. Keep one response per provider per cluster (the highest-similarity
         to the representative) — synthetic councils don't want 5 claude
         responses, they want one per provider.
      6. Cap cluster size for the chairman prompt budget.

    Returns clusters sorted by coherence descending (tightest first).
    """
    import numpy as np

    usable = [
        n for n in nodes
        # NaN-or-Inf embeddings poison the cosine matmul (a single bad
        # row contaminates every column it touches). Same shape as the
        # filter in me/depth.py:thread_lid and me/basins.py.
        if is_finite_embedding(n.embedding)
        and _node_response_text(n).strip()
        # Drop prompts too short to be useful synthesis seeds.
        # "Thank you", "10 more", "comma separated" cluster trivially
        # but yield zero signal as virtual councils.
        and len((n.text or "").split()) >= min_prompt_words
    ]
    n = len(usable)
    if n < 2:
        return []

    # Vectorize: stack + L2-normalize ONCE. Subsequent cosine against
    # any seed is just a single dot product against this matrix. Without
    # this, find_cross_provider_clusters runs in O(N²·d) pure Python
    # and a real-sized corpus (17k+ embeddings × 768 dims) takes hours.
    emb_matrix = np.asarray([u.embedding for u in usable], dtype=np.float32)
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # avoid /0 on a corrupt/empty embedding
    emb_norm = emb_matrix / norms

    assigned = np.zeros(n, dtype=bool)
    clusters: list[CrossProviderCluster] = []

    for i in range(n):
        if assigned[i]:
            continue
        seed = usable[i]
        # One BLAS matmul: cosine of seed against every other vector.
        sims_row = emb_norm @ emb_norm[i]  # shape (n,)
        candidates = np.where((sims_row >= similarity_threshold) & ~assigned)[0]
        if len(candidates) < 2:
            continue
        # Force seed to the front (its self-sim is 1.0; we want seed first
        # so the per-provider tie-breaker prefers the seed).
        members_idx = [int(i)] + [int(j) for j in candidates if int(j) != i]
        sims = [1.0] + [float(sims_row[j]) for j in candidates if int(j) != i]

        if len(members_idx) < 2:
            continue

        # Mark all as assigned so each node lands in exactly one cluster.
        for idx in members_idx:
            assigned[idx] = True

        # Build the per-provider winner (highest sim to seed) within
        # this cluster. Keeps the cluster compact + diverse.
        by_provider: dict[str, tuple[int, float]] = {}
        for idx, sim in zip(members_idx, sims):
            node = usable[idx]
            best = by_provider.get(node.provider)
            if best is None or sim > best[1]:
                by_provider[node.provider] = (idx, sim)

        if len(by_provider) < min_providers:
            continue

        members = []
        kept_sims = []
        for provider, (idx, sim) in by_provider.items():
            node = usable[idx]
            members.append(ProviderResponse(
                provider=provider,
                prompt_text=node.text,
                response_text=_node_response_text(node),
                node_id=node.id,
                timestamp=node.timestamp,
            ))
            kept_sims.append(sim)
        # Cap at max_cluster_size by best similarity to the seed.
        if len(members) > max_cluster_size:
            paired = sorted(zip(members, kept_sims), key=lambda x: -x[1])[:max_cluster_size]
            members = [m for m, _ in paired]
            kept_sims = [s for _, s in paired]

        coherence = sum(kept_sims) / len(kept_sims) if kept_sims else 0.0
        clusters.append(CrossProviderCluster(
            representative_prompt=seed.text,
            members=members,
            coherence=coherence,
        ))

    # Coherence DESC (tightest first), representative_prompt ASC as a stable
    # tie-break so the lead cluster (consumers feed clusters[0] to synthesis) is
    # deterministic when two clusters tie on coherence — else the build order
    # picks. representative_prompt is the cluster's content identity.
    clusters.sort(key=lambda c: (-c.coherence, c.representative_prompt))
    return clusters


def cluster_to_synthesis_args(cluster: CrossProviderCluster) -> dict:
    """Translate a CrossProviderCluster into the args shape
    `_synthesize_responses` expects."""
    return {
        "task": cluster.representative_prompt,
        "responses": [
            {"provider": m.provider, "content": m.response_text}
            for m in cluster.members
        ],
    }
