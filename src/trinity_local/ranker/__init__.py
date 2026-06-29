"""Routing policy layer: unified interface for provider recommendations."""
from __future__ import annotations

from .base import Ranker
from .chairman_picker import chairman_pick_reason, predict_strongest_chairman
from .fallback import FallbackRanker
from .heuristic import prompt_calls_for_council
from .types import RoutingContext, RoutingDecision

__all__ = [
    "Ranker",
    "RoutingContext",
    "RoutingDecision",
    "build_default_ranker",
    "predict_strongest_chairman",
    "chairman_pick_reason",
    "prompt_calls_for_council",
]


def build_default_ranker() -> Ranker:
    """Factory for the default ranker.

    Returns FallbackRanker: tries k-NN advisory (heuristic + embeddings),
    falls back to pure heuristic if k-NN unavailable.

    This is the main backend for watcher routing (step 5+).
    """
    return FallbackRanker()
