"""Routing decision types: pure data, immutable, no policy logic."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class RoutingContext:
    """Input to a routing decision.

    Contains only task and session context needed for ranking,
    not internal watcher state or transcript paths.
    """

    task_text: str
    task_type: str
    current_provider: str
    session_id: str

    task_id: str | None = None
    cwd: str | None = None
    source: str | None = None

    switched_from_provider: str | None = None
    switched_from_task_id: str | None = None

    has_web: bool = False
    has_tools: bool = False
    has_edits: bool = False
    message_count: int = 0

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    """Output of a routing decision.

    Pure data: no formatting, no analytics helpers, no message rendering.
    Semantics:
    - confidence: 0.0–1.0, confidence in the full decision (including council)
    - top_k[0] == recommended_provider when both present
    - needs_council=True does not preclude top_k
    - backend: which ranker made this decision
    """

    recommended_provider: str | None
    top_k: list[str] = field(default_factory=list)
    needs_council: bool = False
    confidence: float = 0.5

    evidence: list[str] = field(default_factory=list)
    backend: Literal["heuristic", "knn", "fallback", "learned"] = "heuristic"

    metadata: dict[str, Any] = field(default_factory=dict)
