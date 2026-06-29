from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionMessage:
    role: str
    text: str = ""
    timestamp: str | None = None
    model: str | None = None
    tokens: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_type: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionRecord:
    provider: str
    session_id: str
    source_path: str
    native_id: str
    started_at: str | None = None
    ended_at: str | None = None
    cwd: str | None = None
    project_hint: str | None = None
    title: str | None = None
    model: str | None = None
    cli_name: str | None = None
    cli_version: str | None = None
    source_format: str | None = None
    source_format_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[SessionMessage] = field(default_factory=list)


@dataclass
class PromptTurn:
    """A single user-facing prompt extracted from a transcript, ready for embedding.

    Sidechain (subagent) turns and API-error responses are excluded by iter_prompt_turns.
    """
    transcript_id: str
    provider: str
    source_path: str
    turn_index: int
    text: str
    timestamp: str | None = None
    preceding_assistant_text: str = ""
    following_assistant_text: str = ""
