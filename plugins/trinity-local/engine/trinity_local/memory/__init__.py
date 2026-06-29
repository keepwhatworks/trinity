"""Hierarchical memory index for Trinity routing.

Public API:
    upsert_prompt_node(node)        — write a PromptNode to disk
    upsert_turn_window(window)      — write a TurnWindow (local context)
    iter_prompt_nodes()             — stream all PromptNodes
    search(query_text, top_k)       — vector search across tiers, replay-ranked
    record_council_outcome(...)     — attach a CouncilRun id + chairman winner to a PromptNode

The index is the closed-loop spine: prompts -> embeddings -> replay-ranked search
-> council -> outcome -> updated PromptNode -> better future ranking.
"""
from __future__ import annotations

from .schemas import PromptNode, TurnWindow
from .store import (
    upsert_prompt_node,
    upsert_turn_window,
    iter_prompt_nodes,
    iter_turn_windows,
    load_prompt_node,
    record_council_outcome,
    load_cursor,
    save_cursor,
)
from .index import search, search_prompt_nodes, SearchResult
from .replay_value import replay_value_score, infer_hardness

__all__ = [
    "PromptNode",
    "TurnWindow",
    "SearchResult",
    "upsert_prompt_node",
    "upsert_turn_window",
    "iter_prompt_nodes",
    "iter_turn_windows",
    "load_prompt_node",
    "record_council_outcome",
    "load_cursor",
    "save_cursor",
    "search",
    "search_prompt_nodes",
    "replay_value_score",
    "infer_hardness",
]
