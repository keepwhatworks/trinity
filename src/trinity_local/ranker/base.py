"""Abstract base class for routing decisions."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import RoutingContext, RoutingDecision


class Ranker(ABC):
    """Routes a task to a provider based on context.

    Product boundary: all rankers (heuristic, k-NN, learned, conductor)
    implement this interface. This enables pluggable routing strategies
    and clean testing.

    Implementations should be stateless or cache-only; advise() must be
    deterministic given the same context.
    """

    @abstractmethod
    def advise(self, context: RoutingContext) -> RoutingDecision:
        """Advise which provider to use for this task.

        Args:
            context: Task and session context.

        Returns:
            RoutingDecision with recommended provider, confidence, and evidence.
        """
        pass
