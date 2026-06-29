"""Fallback ranker: tries k-NN, gracefully falls back to heuristic."""
from __future__ import annotations

from .base import Ranker
from .heuristic import HeuristicRanker
from .knn_ranker import KnnRanker
from .types import RoutingContext, RoutingDecision


class FallbackRanker(Ranker):
    """Two-tier routing with graceful fallback.

    Strategy:
      1. Try KnnRanker (heuristic + k-NN advisory)
      2. If it fails, fall back to HeuristicRanker
      3. Backend annotation reflects which tier succeeded

    This is the default ranker for production watcher use. It combines
    the best of both worlds: k-NN advisory when available, heuristic
    when embeddings or corpus are unavailable.
    """

    def __init__(self):
        self._knn = KnnRanker()
        self._heuristic = HeuristicRanker()

    def advise(self, context: RoutingContext) -> RoutingDecision:
        """Advise via k-NN with heuristic fallback."""
        try:
            decision = self._knn.advise(context)
            # KnnRanker always succeeds (internal fallback to heuristic)
            # If it returned knn backend, k-NN was available
            # If it returned heuristic backend, k-NN was not available
            return decision
        except Exception:
            # Safety: if k-NN ranker fails for any reason, use pure heuristic
            try:
                return self._heuristic.advise(context)
            except Exception:
                # Last resort: return minimal fallback decision
                return RoutingDecision(
                    recommended_provider=context.current_provider,
                    top_k=[],
                    needs_council=False,
                    confidence=0.0,
                    evidence=["Routing failed; staying with current provider."],
                    backend="fallback",
                )
