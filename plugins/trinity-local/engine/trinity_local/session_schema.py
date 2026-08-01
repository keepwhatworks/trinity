from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionMessage:
    role: str
    text: str = ""
    timestamp: str | None = None
    model: str | None = None
    effort: str | None = None
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
    effort: str | None = None
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

    ``model`` / ``effort`` are the RAW provider strings for the model that
    answered this prompt — never normalised here. Feed them to
    ``model_identity.parse_identity(model, effort)`` to get the canonical
    family/tier/version/effort quadruple; that parse is total and returns the
    "?" sentinel for anything it can't read, which is the honest outcome for
    the provider vocabularies that don't map onto low/medium/high/xhigh/max
    (chatgpt's ``thinking_effort: extended|standard``, claude.ai's
    ``thinking_mode: auto|extended``). Both are OPTIONAL and are None on every
    surface that doesn't record them — notably gemini.google.com captures,
    which carry no model or effort field anywhere in the payload. A None here
    means "not recorded", never "default model".

    They are attributed as a PAIR from a single source message (see
    ``iter_prompt_turns``): taking the model from one message and the effort
    from another would synthesise an identity that never ran.
    """
    transcript_id: str
    provider: str
    source_path: str
    turn_index: int
    text: str
    timestamp: str | None = None
    preceding_assistant_text: str = ""
    following_assistant_text: str = ""
    model: str | None = None
    effort: str | None = None
