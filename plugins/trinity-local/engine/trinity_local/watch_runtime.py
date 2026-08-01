"""Transcript-source utilities.

The watcher loop (`watch_once` / `watch_loop`) and its task-recommendation
machinery were retired 2026-05-17 — the MCP `ask` tool now fires
`incremental_ingest.ingest_recent()` on every call with a 1s deadline,
so cursor-based ingestion is automatic and passive on the live product
path. The watcher CLI surfaces (`watch-once`, `watch-loop`) were the
v1.0 explicit-mode path; in v1.7+ they're redundant with MCP-triggered
ingest.

This module is preserved as the source-path resolver shared by
`incremental_ingest.ingest_recent()` and `cold_start.kick_cold_start_scan()`
— both walk transcripts under per-provider roots and need a single
source of truth for "where is provider X's history on disk".

Public utilities:
- `_source_root(source)` → per-provider transcript directory
- `_iter_recent_paths(source, since_mtime)` → walk fresh files only
- `_parse_source_path(source, path)` → adapter dispatch by source name
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .ingest import (
    parse_claude_code_session,
    parse_codex_session,
    parse_cowork_session,
    parse_gemini_cli_session,
)


def _source_root(source: str) -> Path:
    home = Path.home()
    if source == "claude":
        return home / ".claude" / "projects"
    if source == "codex":
        return home / ".codex" / "sessions"
    if source == "gemini":
        return home / ".gemini" / "tmp"
    if source == "antigravity":
        # agy CLI writes per-conversation transcripts under
        # brain/<conv_id>/.system_generated/logs/transcript.jsonl
        return home / ".gemini" / "antigravity-cli" / "brain"
    if source == "cowork":
        return home / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
    if source == "browser_claude":
        # v1.6: capture_host writes here when the user sends a message on
        # claude.ai with the Trinity browser extension installed. The
        # directory may not exist on installs without the extension —
        # _iter_recent_paths bails cleanly in that case.
        from .state_paths import conversations_provider_dir
        return conversations_provider_dir("claude")
    if source == "browser_chatgpt":
        from .state_paths import conversations_provider_dir
        return conversations_provider_dir("chatgpt")
    if source == "browser_gemini":
        # v1.8 (task #135): captures from gemini.google.com via the
        # browser extension's adapters/gemini.js. Same lifecycle as
        # browser_claude / browser_chatgpt — capture_host writes to
        # conversations/gemini/<conv_id>.stream.json; we read on the
        # MCP-ingest hot path.
        from .state_paths import conversations_provider_dir
        return conversations_provider_dir("gemini")
    raise ValueError(f"Unknown source: {source}")


def _is_conversation_capture(path: Path) -> bool:
    """Is this browser-capture file a CONVERSATION (vs. provider metadata)?

    ``capture_host.iter_capture_files`` is the documented single source of truth
    for that question and skips two shapes the extension also writes into
    ``conversations/<provider>/``:

      * ``_``-prefixed sentinels (``_sidebar.json``) — the recent-conversations
        snapshot the sync diff reads, written by a ``sidebar_list`` capture;
      * ``stream-<urlhash>.json`` — raw-fallback orphans with no conv_id,
        written when no adapter exists for the domain.

    The ingest walker did NOT apply that filter, so it handed a known
    non-conversation to a conversation parser and booked the inevitable None as
    a PARSE FAILURE. Measured 2026-07-31 on the live corpus: exactly one
    ``_sidebar.json`` per provider, so browser_claude / browser_chatgpt /
    browser_gemini each reported a permanent, unclearable ``skipped_parse`` of
    1 — the same "reads as breakage, isn't" defect the empty/parse split fixes,
    on the same counter. (No ``stream-`` orphan exists on that corpus; it is
    filtered here because ``capture_host`` writes the shape, not because one
    was observed.)
    """
    return not (path.name.startswith("_") or path.name.startswith("stream-"))


def _iter_recent_paths(source: str, since_mtime: float) -> Iterator[Path]:
    root = _source_root(source)
    if not root.exists():
        return
    if source == "claude":
        paths = root.rglob("*.jsonl")
    elif source == "codex":
        paths = root.rglob("rollout-*.jsonl")
    elif source == "gemini":
        paths = root.rglob("session-*.json")
    elif source == "antigravity":
        paths = root.glob("*/.system_generated/logs/transcript.jsonl")
    elif source in ("browser_claude", "browser_chatgpt"):
        # Exclude .stream.json sidecars — they're adapter outputs without
        # the canonical structure (chat_messages for claude, mapping for
        # chatgpt) and the captured parsers return None for them.
        # Saves the parse attempt + skipped_parse increment.
        paths = (p for p in root.glob("*.json")
                 if not p.name.endswith(".stream.json") and _is_conversation_capture(p))
    elif source == "browser_gemini":
        # Gemini has NO canonical full-conversation fetch — the
        # batchexecute RPC is reply-only — so the .stream.json files
        # ARE the data. Include them (opposite of the claude/chatgpt
        # filter above). All *.json files under conversations/gemini/
        # are adapter outputs from adapters/gemini.js.
        paths = (p for p in root.glob("*.json") if _is_conversation_capture(p))
    else:
        paths = root.rglob("local_*.json")
    recent = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # Inclusive boundary (>=, not >). Batch-written files (a Takeout
        # import, a sync that touches many files in one second) share an
        # mtime. If a deadline-bounded ingest commits the cursor at that
        # mtime mid-batch, a strict `>` on the next run would skip every
        # remaining sibling at that exact mtime — permanent silent loss.
        # `>=` re-includes the boundary; the id-dedup in ingest_recent
        # (existing_ids) skips turns already written, so re-scan is free of
        # double-writes and the batch eventually drains.
        if mtime >= since_mtime:
            recent.append((mtime, path))
    for _, path in sorted(recent):
        yield path


def _parse_source_path(source: str, path: Path):
    if source == "claude":
        return parse_claude_code_session(path)
    if source == "codex":
        return parse_codex_session(path)
    if source == "gemini":
        project_name = path.parent.parent.name if path.parent.name == "chats" else None
        return parse_gemini_cli_session(path, project_name=project_name)
    if source == "antigravity":
        from .ingest import parse_antigravity_session
        return parse_antigravity_session(path)
    if source == "cowork":
        return parse_cowork_session(path)
    if source == "browser_claude":
        from .ingest import parse_captured_claude_conversation
        return parse_captured_claude_conversation(path)
    if source == "browser_chatgpt":
        from .ingest import parse_captured_chatgpt_conversation
        return parse_captured_chatgpt_conversation(path)
    if source == "browser_gemini":
        from .ingest import parse_captured_gemini_conversation
        return parse_captured_gemini_conversation(path)
    raise ValueError(f"Unknown source: {source}")


def _parse_source_path_classified(source: str, path: Path):
    """``_parse_source_path`` + WHY nothing came back — ``(session, reason)``
    with reason in ``ingest.PARSE_OK`` / ``PARSE_EMPTY`` / ``PARSE_UNREADABLE``.

    A bare `None` cannot distinguish "this file is broken" from "this file is
    fine and carries nothing yet", so the caller that counts skips has to pick
    one and be wrong about the other. Sources whose parser can tell the
    difference answer here; the rest fall back to UNREADABLE, which is what
    the counter meant before this split (no source silently becomes 'empty'
    without a parser that actually checked)."""
    from .ingest import PARSE_OK, PARSE_UNREADABLE

    if source == "browser_gemini":
        from .ingest import parse_captured_gemini_conversation_classified
        return parse_captured_gemini_conversation_classified(path)
    session = _parse_source_path(source, path)
    return session, (PARSE_OK if session is not None else PARSE_UNREADABLE)
