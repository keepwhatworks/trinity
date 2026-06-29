"""Memory index schemas.

PromptNode is the atomic retrieval unit — one per user-facing turn.
TurnWindow is local context (~1.5k tokens) for prompts that need framing.
(The TranscriptNode centroid tier was retired — its only call site was the
search re-ranker, where TurnWindow + PromptNode similarity already covered
the same retrieval surface.)
CouncilRun ids are stored on PromptNode as references; the source of truth is
council_outcomes (council_runtime), not memory.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PromptNode:
    """A single user-facing prompt, embedded and indexed for retrieval."""
    id: str
    transcript_id: str
    provider: str
    source_path: str
    turn_index: int
    text: str
    embedding: list[float]
    created_at: str
    timestamp: str | None = None
    preceding_assistant_text: str = ""
    following_assistant_text: str = ""
    cluster_id: str | None = None
    themes: list[str] = field(default_factory=list)
    council_run_ids: list[str] = field(default_factory=list)
    chairman_winner: str | None = None
    uncertainty: float | None = None
    importance: float | None = None
    last_replayed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PromptNode:
        # Tolerant of unknown fields so older records still load
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in raw.items() if k in known}
        return cls(**filtered)


@dataclass
class TurnWindow:
    """Local context window around a PromptNode, for framing-dependent retrieval."""
    id: str
    transcript_id: str
    center_prompt_id: str
    text: str
    embedding: list[float]
    turn_start: int
    turn_end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TurnWindow:
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in raw.items() if k in known}
        return cls(**filtered)


