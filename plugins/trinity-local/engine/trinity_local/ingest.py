from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .session_schema import PromptTurn, SessionMessage, SessionRecord


def _safe_parse(
    parse_fn: Callable[[Path], "SessionRecord | None"], path: Path
) -> "SessionRecord | None":
    """Isolate a single transcript's parse failure so it can't kill the whole
    adapter's ingest.

    Each `iter_*_sessions` walks ALL of a provider's transcripts (~/.codex,
    ~/.claude/projects, …) — thousands of files. A single corrupt one (a
    truncated/interrupted write leaving a wrong-shape or non-UTF8 line) used to
    raise straight out of the iterator (e.g. `AttributeError: 'list' object has no
    attribute 'get'` on a non-dict JSON line, or `UnicodeDecodeError` on bad
    bytes), aborting ingest and silently dropping every later file. Skip the bad
    file, log it (visible, not silent — guard_shape_not_just_parse class), keep
    going. The per-line parsers also guard shape/encoding internally so a
    *partially* corrupt file still recovers its good lines; this is the catch-all
    for anything they miss."""
    try:
        return parse_fn(path)
    except Exception as exc:  # noqa: BLE001 — one bad file must not abort ingest
        print(
            f"trinity ingest: skipped unparseable transcript {path}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a value to a dict, or {} if it's any other type.

    The per-line parsers guard the TOP-LEVEL JSON entry with `isinstance(entry,
    dict)`, but a NESTED field (`message`, `payload`) can independently be a
    list/str/number in a malformed or interrupted-write transcript line. The
    `x = entry.get("message") or {}` idiom only catches FALSY values — a non-empty
    list slips through and crashes the next `.get` (`'list' object has no attribute
    'get'`, the guard_shape_not_just_parse class). That crash propagates out of the
    per-line loop to `_safe_parse`, which then drops the WHOLE file — so a single
    odd line silently discards every good turn in that transcript. Coerce instead,
    so the bad line degrades to empty and the rest of the file still ingests."""
    return value if isinstance(value, dict) else {}


def _message_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        parts: list[str] = []
        for block in payload:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _extract_gemini_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls = message.get("toolCalls")
    if not isinstance(tool_calls, list):
        return []
    out: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        entry = {
            "id": call.get("id"),
            "name": call.get("name"),
            "args": call.get("args"),
        }
        result = call.get("result")
        if isinstance(result, list) and result:
            entry["result"] = result
        out.append(entry)
    return out


def parse_gemini_cli_session(path: Path, *, project_name: str | None = None) -> SessionRecord | None:
    try:
        # errors="replace" for parity with the other parsers — a stray non-UTF8
        # byte (UnicodeDecodeError is NOT an OSError) shouldn't drop the whole
        # session file; replace it and let json.loads decide if the file is usable.
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None

    session_id = raw.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None

    messages: list[SessionMessage] = []
    model: str | None = None
    cwd = None
    project_hint = project_name

    for item in raw.get("messages", []):
        if not isinstance(item, dict):
            continue
        msg_type = item.get("type")
        timestamp = item.get("timestamp")
        if msg_type == "user":
            text = _message_text(item.get("content"))
            messages.append(
                SessionMessage(
                    role="user",
                    text=text,
                    timestamp=timestamp,
                    raw_type=msg_type,
                )
            )
        elif msg_type == "gemini":
            text = item.get("content") if isinstance(item.get("content"), str) else ""
            model = item.get("model") or model
            messages.append(
                SessionMessage(
                    role="assistant",
                    text=text,
                    timestamp=timestamp,
                    model=item.get("model"),
                    tokens=item.get("tokens") if isinstance(item.get("tokens"), dict) else {},
                    tool_calls=_extract_gemini_tool_calls(item),
                    raw_type=msg_type,
                )
            )
        elif msg_type == "info":
            text = item.get("content") if isinstance(item.get("content"), str) else ""
            messages.append(
                SessionMessage(
                    role="system",
                    text=text,
                    timestamp=timestamp,
                    raw_type=msg_type,
                )
            )

    return SessionRecord(
        provider="gemini",
        session_id=session_id,
        source_path=str(path),
        native_id=session_id,
        started_at=raw.get("startTime"),
        ended_at=raw.get("lastUpdated"),
        cwd=cwd,
        project_hint=project_hint,
        title=None,
        model=model,
        cli_name="gemini",
        cli_version=None,
        source_format="gemini_cli_chat",
        source_format_version="1",
        metadata={
            "kind": raw.get("kind"),
            "project_hash": raw.get("projectHash"),
        },
        messages=messages,
    )


def iter_gemini_cli_sessions(root: Path | None = None) -> Iterator[SessionRecord]:
    root = root or (Path.home() / ".gemini" / "tmp")
    if not root.exists():
        return
    for project_dir in sorted(root.iterdir()):
        chats_dir = project_dir / "chats"
        if not chats_dir.is_dir():
            continue
        for session_file in sorted(chats_dir.glob("session-*.json")):
            session = _safe_parse(
                lambda p, _n=project_dir.name: parse_gemini_cli_session(p, project_name=_n),
                session_file,
            )
            if session is not None:
                yield session


def parse_claude_code_session(path: Path) -> SessionRecord | None:
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    session_id = path.stem
    started_at = None
    ended_at = None
    cwd = None
    cli_version = None
    model = None
    git_branch = None
    permission_mode = None
    messages: list[SessionMessage] = []

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A valid-JSON-but-wrong-type line (a bare list/str/number) would
            # crash `.get` (guard_shape_not_just_parse). Skip it, keep the file.
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            if ts:
                started_at = started_at or ts
                ended_at = ts
            cwd = cwd or entry.get("cwd")
            cli_version = cli_version or entry.get("version")
            git_branch = git_branch or entry.get("gitBranch")
            permission_mode = permission_mode or entry.get("permissionMode")
            entry_type = entry.get("type")
            is_sidechain = bool(entry.get("isSidechain"))
            if entry_type == "user":
                content = _as_dict(entry.get("message")).get("content")
                text = content if isinstance(content, str) else _message_text(content)
                extra: dict[str, Any] = {}
                if is_sidechain:
                    extra["is_sidechain"] = True
                messages.append(
                    SessionMessage(
                        role="user",
                        text=text,
                        timestamp=ts,
                        raw_type=entry_type,
                        extra=extra,
                    )
                )
            elif entry_type == "assistant":
                message = _as_dict(entry.get("message"))
                candidate_model = message.get("model")
                is_synthetic = candidate_model == "<synthetic>"
                is_api_error = bool(entry.get("isApiErrorMessage"))
                if isinstance(candidate_model, str) and not is_synthetic:
                    model = candidate_model or model
                text = _message_text(message.get("content"))
                tool_calls: list[dict[str, Any]] = []
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "id": block.get("id"),
                                    "name": block.get("name"),
                                    "args": block.get("input") if isinstance(block.get("input"), dict) else {},
                                    "result": [],
                                }
                            )
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                tokens = {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "cached": (usage.get("cache_read_input_tokens", 0) or 0)
                    + (usage.get("cache_creation_input_tokens", 0) or 0),
                }
                extra = {}
                if is_sidechain:
                    extra["is_sidechain"] = True
                if is_synthetic:
                    extra["is_synthetic"] = True
                if is_api_error:
                    extra["is_api_error"] = True
                messages.append(
                    SessionMessage(
                        role="assistant",
                        text=text,
                        timestamp=ts,
                        model=candidate_model if isinstance(candidate_model, str) and not is_synthetic else None,
                        tokens=tokens,
                        tool_calls=tool_calls,
                        raw_type=entry_type,
                        extra=extra,
                    )
                )
            elif entry_type == "system":
                text = _message_text(_as_dict(entry.get("message")).get("content"))
                messages.append(
                    SessionMessage(
                        role="system",
                        text=text,
                        timestamp=ts,
                        raw_type=entry_type,
                    )
                )

    return SessionRecord(
        provider="claude",
        session_id=session_id,
        source_path=str(path),
        native_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        cwd=cwd,
        project_hint=path.parent.name,
        title=None,
        model=model,
        cli_name="claude",
        cli_version=cli_version,
        source_format="claude_code_jsonl",
        source_format_version="1",
        metadata={
            "git_branch": git_branch,
            "permission_mode": permission_mode,
        },
        messages=messages,
    )


def iter_claude_code_sessions(root: Path | None = None) -> Iterator[SessionRecord]:
    root = root or (Path.home() / ".claude" / "projects")
    if not root.exists():
        return
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        for session_file in sorted(project_dir.glob("*.jsonl")):
            session = _safe_parse(parse_claude_code_session, session_file)
            if session is not None:
                yield session


def parse_codex_session(path: Path) -> SessionRecord | None:
    try:
        # errors="replace": a transcript with invalid UTF-8 bytes (a corrupted/
        # interrupted write, odd tool output) must recover its good lines, not
        # raise UnicodeDecodeError mid-iteration (a ValueError, NOT caught by the
        # per-line JSONDecodeError guard below).
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    session_id = path.stem
    started_at = None
    ended_at = None
    cwd = None
    cli_version = None
    model = None
    model_provider = None
    messages: list[SessionMessage] = []

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A valid-JSON-but-wrong-type line (a bare list/str/number) would
            # crash `.get` (guard_shape_not_just_parse). Skip it, keep the file.
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            if ts:
                started_at = started_at or ts
                ended_at = ts
            entry_type = entry.get("type")
            payload = _as_dict(entry.get("payload"))
            if entry_type == "session_meta":
                session_id = payload.get("id") or session_id
                cwd = payload.get("cwd") or cwd
                cli_version = payload.get("cli_version") or cli_version
                model_provider = payload.get("model_provider") or model_provider
            elif entry_type == "turn_context":
                model = payload.get("model") or model
            elif entry_type == "response_item":
                payload_type = payload.get("type")
                if payload_type == "message":
                    role = payload.get("role")
                    if role == "developer":
                        role = "system"
                    if role not in ("user", "assistant", "system"):
                        continue
                    text = _message_text(payload.get("content"))
                    messages.append(
                        SessionMessage(
                            role=role,
                            text=text,
                            timestamp=ts,
                            model=model if role == "assistant" else None,
                            raw_type=payload_type,
                        )
                    )
                elif payload_type == "reasoning":
                    summary = payload.get("summary")
                    text = ""
                    if isinstance(summary, list):
                        text = "\n".join(
                            item.get("text", "") for item in summary if isinstance(item, dict)
                        ).strip()
                    messages.append(
                        SessionMessage(
                            role="assistant",
                            text=text,
                            timestamp=ts,
                            model=model,
                            raw_type=payload_type,
                        )
                    )
                elif payload_type == "function_call":
                    args = payload.get("arguments")
                    try:
                        parsed_args = json.loads(args) if isinstance(args, str) else {}
                    except json.JSONDecodeError:
                        parsed_args = {"_raw": args}
                    messages.append(
                        SessionMessage(
                            role="assistant",
                            text="",
                            timestamp=ts,
                            model=model,
                            raw_type=payload_type,
                            tool_calls=[
                                {
                                    "id": payload.get("call_id"),
                                    "name": payload.get("name"),
                                    "args": parsed_args if isinstance(parsed_args, dict) else {},
                                    "result": [],
                                }
                            ],
                        )
                    )
                elif payload_type == "function_call_output":
                    messages.append(
                        SessionMessage(
                            role="tool",
                            text=str(payload.get("output") or ""),
                            timestamp=ts,
                            raw_type=payload_type,
                        )
                    )

    return SessionRecord(
        provider="codex",
        session_id=session_id,
        source_path=str(path),
        native_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        cwd=cwd,
        project_hint=cwd,
        title=None,
        model=model,
        cli_name="codex",
        cli_version=cli_version,
        source_format="codex_rollout_jsonl",
        source_format_version="1",
        metadata={"model_provider": model_provider},
        messages=messages,
    )


def iter_codex_sessions(root: Path | None = None) -> Iterator[SessionRecord]:
    root = root or (Path.home() / ".codex" / "sessions")
    if not root.exists():
        return
    for session_file in sorted(root.rglob("rollout-*.jsonl")):
        session = _safe_parse(parse_codex_session, session_file)
        if session is not None:
            yield session


_AGY_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _antigravity_user_text(content: str) -> str:
    """Extract the user's actual request from an antigravity USER_INPUT line.

    The `content` wraps the prompt in `<USER_REQUEST>…</USER_REQUEST>` followed
    by harness-injected `<ADDITIONAL_METADATA>` / `<USER_SETTINGS_CHANGE>` tags
    (local time, model-selection notices). Only the USER_REQUEST body is the
    user's voice — the rest is scaffolding the lens must not learn from. When
    no wrapper is present (older format), fall back to the raw content with any
    angle-bracket tag blocks stripped."""
    m = _AGY_USER_REQUEST_RE.search(content)
    if m:
        return m.group(1).strip()
    # Fallback: strip any <TAG>…</TAG> blocks, keep the remainder.
    return re.sub(r"<[A-Z_]+>.*?</[A-Z_]+>", "", content, flags=re.DOTALL).strip()


def parse_antigravity_session(path: Path) -> SessionRecord | None:
    """Parse an antigravity (agy) CLI transcript.

    Each conversation lives at
    `~/.gemini/antigravity-cli/brain/<conv_id>/.system_generated/logs/transcript.jsonl`
    — one JSON object per line with a `type`: `USER_INPUT` (the user's turn,
    content wrapped in `<USER_REQUEST>`), `PLANNER_RESPONSE` (the agent's
    reply), plus CONVERSATION_HISTORY / ERROR_MESSAGE / tool steps we skip.
    """
    try:
        # errors="replace": a single non-UTF8 byte must NOT drop the whole multi-turn
        # conversation (it raises UnicodeDecodeError, uncaught by `except OSError`, so
        # _safe_parse would lose every turn) — recover the good lines, same lossless
        # behavior as the claude/codex/cowork parsers. (antigravity was added in #268
        # after the resilience pass and missed this.)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    # conv_id = the brain/<id>/ dir (path = …/<id>/.system_generated/logs/transcript.jsonl)
    try:
        conv_id = path.parents[2].name
    except IndexError:
        conv_id = path.stem

    messages: list[SessionMessage] = []
    started_at: str | None = None
    ended_at: str | None = None
    model: str | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict):
            continue
        ts = o.get("created_at")
        if isinstance(ts, str) and ts:
            started_at = started_at or ts
            ended_at = ts
        kind = o.get("type")
        content = o.get("content")
        if kind == "USER_INPUT" and isinstance(content, str):
            text = _antigravity_user_text(content)
            if text:
                messages.append(
                    SessionMessage(role="user", text=text, timestamp=ts, raw_type=kind)
                )
        elif kind == "PLANNER_RESPONSE" and isinstance(content, str) and content.strip():
            messages.append(
                SessionMessage(
                    role="assistant", text=content.strip(), timestamp=ts, raw_type=kind
                )
            )

    if not messages:
        return None

    return SessionRecord(
        provider="antigravity",
        session_id=conv_id,
        source_path=str(path),
        native_id=conv_id,
        started_at=started_at,
        ended_at=ended_at,
        cwd=None,
        project_hint=None,
        title=None,
        model=model,
        cli_name="antigravity",
        cli_version=None,
        source_format="antigravity_transcript_jsonl",
        source_format_version="1",
        metadata={},
        messages=messages,
    )


def iter_antigravity_sessions(root: Path | None = None) -> Iterator[SessionRecord]:
    root = root or (Path.home() / ".gemini" / "antigravity-cli" / "brain")
    if not root.exists():
        return
    for transcript in sorted(root.glob("*/.system_generated/logs/transcript.jsonl")):
        session = _safe_parse(parse_antigravity_session, transcript)
        if session is not None:
            yield session


def parse_cowork_session(meta_path: Path) -> SessionRecord | None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None

    session_id = meta.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None
    session_dir = meta_path.with_suffix("")
    audit_candidates = [session_dir / "audit.jsonl", session_dir / "audit1.jsonl"]
    audit_path = next((candidate for candidate in audit_candidates if candidate.exists()), None)
    if audit_path is None:
        return None

    messages: list[SessionMessage] = []
    model = meta.get("model")
    started_at: str | None = None
    ended_at: str | None = None
    try:
        fh = audit_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A valid-JSON-but-wrong-type line (a bare list/str/number) would
            # crash `.get` (guard_shape_not_just_parse). Skip it, keep the file.
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            if ts:
                started_at = started_at or ts
                ended_at = ts
            entry_type = entry.get("type")
            if entry_type == "user":
                content = _as_dict(entry.get("message")).get("content")
                text = content if isinstance(content, str) else _message_text(content)
                messages.append(SessionMessage(role="user", text=text, timestamp=ts, raw_type=entry_type))
            elif entry_type == "assistant":
                message = _as_dict(entry.get("message"))
                model = message.get("model") or model
                text = _message_text(message.get("content"))
                tool_calls: list[dict[str, Any]] = []
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "id": block.get("id"),
                                    "name": block.get("name"),
                                    "args": block.get("input") if isinstance(block.get("input"), dict) else {},
                                    "result": [],
                                }
                            )
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                tokens = {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "cached": (usage.get("cache_read_input_tokens", 0) or 0)
                    + (usage.get("cache_creation_input_tokens", 0) or 0),
                }
                messages.append(
                    SessionMessage(
                        role="assistant",
                        text=text,
                        timestamp=ts,
                        model=message.get("model"),
                        tokens=tokens,
                        tool_calls=tool_calls,
                        raw_type=entry_type,
                    )
                )
            elif entry_type == "system":
                text = _message_text(_as_dict(entry.get("message")).get("content"))
                messages.append(SessionMessage(role="system", text=text, timestamp=ts, raw_type=entry_type))

    mcp_servers = []
    for item in meta.get("remoteMcpServersConfig") or []:
        if isinstance(item, dict) and item.get("name"):
            mcp_servers.append(item["name"])

    return SessionRecord(
        provider="cowork",
        session_id=session_id,
        source_path=str(audit_path),
        native_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        cwd=meta.get("cwd"),
        project_hint=meta.get("cwd"),
        title=meta.get("title"),
        model=model,
        cli_name="claude-desktop-agent-mode",
        cli_version=None,
        source_format="cowork_audit_jsonl",
        source_format_version="1",
        metadata={
            "slash_commands": meta.get("slashCommands") if isinstance(meta.get("slashCommands"), list) else [],
            "mcp_servers": mcp_servers,
            "host_loop_mode": meta.get("hostLoopMode"),
            "web_fetch_allowed_urls": meta.get("webFetchAllowedUrls") if isinstance(meta.get("webFetchAllowedUrls"), list) else [],
            "user_selected_folders": meta.get("userSelectedFolders") if isinstance(meta.get("userSelectedFolders"), list) else [],
            "initial_message": meta.get("initialMessage"),
            "process_name": meta.get("processName"),
            "cli_session_id": meta.get("cliSessionId"),
        },
        messages=messages,
    )


def iter_cowork_sessions(root: Path | None = None) -> Iterator[SessionRecord]:
    root = root or (Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions")
    if not root.exists():
        return
    for meta_path in sorted(root.rglob("local_*.json")):
        session = _safe_parse(parse_cowork_session, meta_path)
        if session is not None:
            yield session


# Provider names appearing as a SPEAKER label — the tell that the founder pasted one
# model's answer in to compare across providers / get feedback (the cross-provider
# workflow). Distinguished from a genuine QUESTION about a model by the speaker-form
# (not "Model's keynote") plus a paste-sized body.
_PROVIDER_NAME = r"(claude|chat ?gpt|gpt-?\d[\w.]*|gpt|gemini|grok|deepseek|sonnet|opus|llama|mistral|qwen|perplexity)"
_CROSS_PROVIDER_PASTE_RE = re.compile(
    rf"(?:^|\n)\s*{_PROVIDER_NAME}\s*:"                                                   # "Gemini:" speaker label
    rf"|{_PROVIDER_NAME}\s+(?:says?|said|responded|replied|wrote)(?:\s+th(?:is|at))?\s*:"  # "Gemini says this:"
    rf"|\bfeedback\s+(?:for|on|to|from)\s+{_PROVIDER_NAME}\b"                              # "feedback for Gemini"
    rf"|\b(?:what|here'?s\s+what)\s+{_PROVIDER_NAME}\s+(?:said|says|thinks?|responded|wrote)\b"  # "what Gemini said"
    rf"|{_PROVIDER_NAME}'s\s+(?:response|answer|reply|take|verdict|critique|analysis|feedback|rebuttal)\b",  # "ChatGPT's response"
    re.IGNORECASE,
)


# Video/audio transcript outros captured as user turns ("Thank you for watching", "Please
# subscribe", …). Measured 2026-06-03: 30 such turns, all chatgpt-source — captured A/V
# content, NOT the founder's authored voice; the same artifact family as the gemini
# UI-button labels (#246). Anchored at the start + length-gated so a genuine "thanks for
# watching the demo, now do X" survives.
_VIDEO_OUTRO_RE = re.compile(
    r"^\s*(thank you for watching|thanks for watching|thank you so much for watching|"
    r"please (like and |like, comment and )?subscribe|don'?t forget to (like and )?subscribe|"
    r"see you (in the )?next (video|time|one)|thanks for listening|thank you for listening|"
    r"if you enjoyed this video)\b",
    re.IGNORECASE,
)


def is_cross_provider_paste(text: str) -> bool:
    """True when a role=user turn is a PASTED agent response the founder dropped in to
    compare across providers — labeled with a model as SPEAKER ("Gemini:", "Gemini says
    this:", "feedback for ChatGPT:", "Claude's response"). Another model's voice, NOT the
    founder's authored taste, so the lens must not learn from it (the #296 provenance
    finding 2026-06-03: the founder's real pasted-agent content is self-tagged
    cross-provider comparisons). PRECISE by design: requires the speaker-form AND a
    paste-sized body, so a genuine QUESTION about a model ("new releases from ChatGPT's
    keynote?", "what does Gemini think?") survives. Low-recall (caught ~0.10% of turns
    in the corpus check) with no measured false positives — a precise complement to the
    learned classifier, not the whole filter."""
    if not text or len(text) < 60:  # a bare question/mention is not a paste
        return False
    return bool(_CROSS_PROVIDER_PASTE_RE.search(text[:400]))


def _is_user_facing_prompt(message: SessionMessage, *, apply_dispatch_ledger: bool = True) -> bool:
    # `apply_dispatch_ledger` is True at INGEST (drop Trinity's replay before it
    # adds a duplicate node) and False on the RE-READ path (`is_user_facing_text`),
    # where the ledger would eat the user's ORIGINAL — see the clause at the end.
    # #260 do-operator invariant (LOAD-BEARING): only the user's OWN turns become
    # PromptNodes. Assistant/tool/system text is a model ACTION, never the user's
    # authored evidence — indexing it would train the lens on self-authored
    # tokens as if they were external observation ("taste echo chamber"). This
    # role gate is the single barrier; every ingest source routes through it.
    if message.role != "user":
        return False
    text = (message.text or "").strip()
    if not text:
        return False
    if message.extra.get("is_sidechain"):
        return False
    # Drop tool-scaffolding prompts that Trinity (or another agent harness)
    # fired through the user's CLI: extractor calls, "You are ..." system
    # prompts, etc. Captured transcripts label them role=user because the CLI
    # sees them as the input, but they are not authored by the human and
    # poison the autofill / replay / dream pipelines if indexed.
    lowered = text.lstrip().lower()
    if lowered.startswith(("you are ", "you will ")):
        return False
    # Structured-block markers a harness injects as role=user when its own
    # tool-use / automation machinery turns. These poison the preference ledger +
    # vocabulary if indexed as user voice. Per-source audit 2026-06-02 found each
    # source has its OWN block dialect the aggregate top-N missed: Codex emits
    # `<image …>` (43), `<turn_aborted>` (24), `<goal_context>` (21); Cowork emits
    # `<scheduled-task …>` (19) and `<uploaded_files>` (11). Verified pure blocks —
    # each turn's text IS the block (ends at the close tag), no user question
    # follows. `<uploaded_files>` additionally carries PII file paths (a legal-doc
    # filename) that must never reach the lens, so dropping it is privacy + purity.
    if lowered.startswith(("<task-notification>", "<task-id>", "<tool-use-id>",
                            "<command-name>", "<command-message>", "<local-command-stdout>",
                            "<local-command-stderr>", "<system-reminder>",
                            "<image", "<turn_aborted", "<goal_context",
                            "<scheduled-task", "<uploaded_files")):
        return False
    # Meta-narrative + session-frame markers — synthesized by the harness,
    # not the user.
    # "[Request interrupted by user]" AND its "[Request interrupted by user for
    # tool use]" variant (94 corpus copies measured 2026-06-02 — the closing-
    # bracket-anchored form missed the longer one). Drop the bracket so both
    # land; a human never opens a prompt with this Claude Code session marker.
    # "[image #" AND "[image: source: /var/folders/…]" — Claude Code image
    # attachment markers (the colon form leaked 166 corpus copies, 2026-06-02).
    if lowered.startswith(("[request interrupted by user", "[image #", "[image:",
                            "stop hook feedback:", "this session is being continued",
                            "caveat:")):
        return False
    # Trinity dispatch verbs that escape the "you are / you will " filter —
    # patterns observed in the audit (Subagent V tick).
    if lowered.startswith(("look at these recurring", "read the image at",
                            "for each durable fact")):
        return False
    # Trinity's OWN test/dispatch sentinels that reach a provider CLI as
    # role=user (e.g. the antigravity E2E probe and council-readiness checks).
    # `agy`'s transcript captures every prompt Trinity sends it, so these would
    # otherwise land in the user's lens as if the user typed them (#268).
    if "trinity_agy_e2e" in lowered or lowered.startswith(
        ("reply with exactly:", "respond with exactly:", "respond with the word")
    ):
        return False
    # Agent-harness instruction files + environment-context blocks captured as
    # role=user (Codex injects `# AGENTS.md instructions for <path>`; Claude
    # Code / Codex inject `<environment_context><cwd>…`). These slip the
    # corpus-wide purity floor (#245, 1.7% < 5%) but CONCENTRATE into near-pure
    # scaffolding basins (b19=94%, b22=90%) — #248. Paired with the per-basin
    # concentration guard.
    if lowered.startswith(("# agents.md", "<environment_context>", "<instructions>",
                            "<cwd>", "<env>")):
        return False
    # Slash-command skill bodies captured as role=user: when a user types
    # `/loop …`, the harness injects the expanded skill definition
    # (`# /loop — schedule a recurring or self-paced prompt\n\nParse …`) as the
    # turn text. That's the command DEFINITION, not the user's words — and it
    # repeats verbatim every invocation (the /loop body alone appeared 283× in
    # one basin, #248). Match a markdown heading whose first token is a slash
    # command.
    if re.match(r"#\s*/[a-z][\w-]*\b", lowered):
        return False
    # Trinity's OWN lens-extraction prompts refer to the user in the third
    # person ("Find the idiosyncratic words this human introduces", "Compose a
    # TASTE PROFILE about this person", "Find OPEN LOOPS in this session"). A
    # real prompt doesn't call its author "the human"/"this person" or talk
    # about "this session" in the third person; an imperative that does is the
    # extractor talking, not the user. (b21 — the mixed extraction-prompt basin.)
    if lowered.startswith(
        ("find ", "identify ", "extract ", "list ", "compose ", "summarize ")
    ) and any(
        marker in lowered[:280]
        for marker in ("the human", "this human", "this person", "this session")
    ):
        return False
    # A harness echoing the user's lens back into its OWN prompt preamble:
    # Trinity's councils historically opened with — and the founder's autonomous
    # loop tooling still opens with — "The user has the following lens
    # (cross-domain constraints they live by): …". That's a GENERATED prompt
    # template (the lens piped back into a dispatch), captured role=user but
    # never human-authored — the "generator over generated" recursion. 40 such
    # nodes were the representative threads of a live basin (2026-06-01 topology
    # audit). No human opens a prompt by narrating their own lens in 3rd person.
    if lowered.startswith("the user has the following lens"):
        return False
    # Agent-ops / dispatch-test / automation prompts — harness or loop control,
    # not human taste (#252 last-month sample: 16% of recent turns). Two shapes:
    # (a) output-format probes a human never types ("respond with the word HELLO
    # and nothing else", "output only OK") — used to test provider dispatch;
    # (b) automation continuations from a /loop-style driver ("continue with the
    # plan if currently paused", "continue from where you left off"). Bare
    # "continue"/"ok" are left alone (a human might type them) — dedup + the
    # cohesion guard handle those; here we match the distinctive control SHAPE.
    if (lowered.startswith(("respond with ", "reply with ", "output only", "output the word"))
            and any(s in lowered[:120] for s in ("nothing else", "single word", "the word", "only the"))):
        return False
    if lowered.startswith(("continue with the plan", "continue from where you left off",
                            "proceed with the plan", "continue the plan",
                            "continue until the goal is done")):  # Codex goal-loop driver (12)
        return False
    # The autonomous-loop DRIVER prompt: a `/loop` cron fires the SAME directive
    # verbatim every interval ("start new end to end flow to test the prod setup
    # in various environments … Improve usefulness."). It's harness automation,
    # never the human's authored taste, yet it lands role=user once per firing —
    # measured 2026-06-02 at 14 copies in the corpus + 150 prompt turns across 15
    # recent loop sessions, growing every firing (the "generator over generated"
    # recursion, same class as "the user has the following lens" above). The
    # opener is distinctive automation phrasing no human types to start a genuine
    # prompt; require the "test the prod setup" co-occurrence so a real prompt
    # that merely begins "start …" can't be eaten.
    if lowered.startswith("start new end to end flow") and "test the prod setup" in lowered[:160]:
        return False
    # A bare conversation-id hash captured as a prompt. gemini.google.com's
    # adapter over-captures Gemini's internal batchexecute RPCs (feature flags,
    # settings sync, telemetry) alongside real chat; for some of those the
    # extracted `user_text` is a `c_<hex>` conversation id, and 10 of them
    # reached the corpus as role=user prompts (measured 2026-06-01: gemini raw
    # captures are ~98% config RPCs; dedup + the empty-assistant-text skip catch
    # the rest, leaving these hashes as the one filterable residual). A 16-hex
    # conversation id is never something a human types — the purity barrier is
    # exactly where it gets dropped. Verified zero real prompts start with
    # `c_`+hex, so this can't eat a genuine prompt.
    if re.fullmatch(r"c_[0-9a-f]{6,}", lowered.strip()):
        return False
    # Gemini's web adapter over-captures the page's ACTION-BUTTON labels as if
    # they were typed prompts (same over-capture family as the `c_<hex>` ids and
    # the config RPCs). These are exact UI strings a human never types as a
    # standalone prompt — measured 2026-06-02 as the top kept gemini-source
    # texts: "Used an Assistant feature" ×414, "Start research" ×116, "Generate
    # Audio Overview" ×21, "Edit the research plan" ×14. Exact-match only, so a
    # genuine "start research on X" / "edit the research plan to add Y" survives
    # (the button label carries no object). The root fix is the adapter capturing
    # only chat RPCs (founder-gated); this is the same ingest-layer stopgap as
    # the c_<hex> drop above.
    _GEMINI_UI_ARTIFACTS = frozenset({
        "used an assistant feature",
        "start research",
        "generate audio overview",
        "edit the research plan",
    })
    if lowered.strip() in _GEMINI_UI_ARTIFACTS:
        return False
    # Video/audio transcript outros captured as user turns (not the founder's voice). The
    # captured ones are SHORT standalone outros; a genuine "thanks for watching the demo,
    # now do X" continues with a task and runs longer, so the length gate spares it.
    if len(text) < 60 and _VIDEO_OUTRO_RE.match(text):
        return False
    # A pasted cross-provider response (another model's voice the founder dropped in for
    # comparison/feedback) is not the founder's authored taste — drop it from the lens.
    if is_cross_provider_paste(text):
        return False
    # Trinity's OWN dispatches echoing back (2026-06-09). Every council member /
    # chairman / eval-target / eval-judge prompt Trinity fires lands in that
    # CLI's transcript as role=user — and the worst case defeats every shape
    # heuristic above: eval items are the founder's ORIGINAL prompts replayed
    # verbatim (one 42-item run measured 40 verbatim replica nodes). No filter
    # can tell the founder's voice from its replay — but Trinity KNOWS what it
    # dispatched: providers record a normalized hash at dispatch time
    # (dispatch_ledger), and this gate drops matching turns within the TTL
    # window. Last check on purpose — it's the only one that stats a file.
    # READ-PATH GUARD (2026-06-15): the ledger is an INGEST-time dedup — it stops
    # Trinity's replay of a prompt from ADDING a duplicate node. At READ time it
    # CANNOT tell the user's ORIGINAL from Trinity's replay (both are the same
    # text → same hash), so re-applying it here DELETES the user's original from
    # the lens. The user's own cross-provider workflow (ask one model, run a
    # council) dispatches their question, which lands in the ledger, which then
    # eats their authored prompt — measured at 383 legit cross-domain prompts.
    # The original is already indexed + legitimate, and exact-text replicas are
    # collapsed by the clustering-input dedup, so the ledger is both redundant
    # AND harmful on re-read. Apply it only at ingest.
    if apply_dispatch_ledger:
        from .dispatch_ledger import is_trinity_dispatched

        if is_trinity_dispatched(text):
            return False
    return True


def is_user_facing_text(text: str) -> bool:
    """Re-apply the role=user scaffolding filter to a bare PromptNode text.

    `_is_user_facing_prompt` is the ingest-time gate, but the index it feeds is
    append-only: nodes captured under an OLDER, weaker filter persist until a
    full re-seed (cursor reset). The LENS-bearing read paths — agent search
    (`search_prompt_nodes`) and lens-build Stage 0 (`iter_turn_pairs`) — call
    this to drop already-poisoned scaffolding at *read* time, so every
    ingest-filter improvement becomes retroactively effective WITHOUT a forbidden
    re-ingest. This completes the partial `you are`/`you will` guard the search
    path opened ("guards already-poisoned PromptNodes until the user re-seeds").

    Text-only: a PromptNode is, by construction, a captured role=user turn, so we
    run the text-shape checks via a synthetic user message — the role /
    `is_sidechain` gates are already resolved at ingest and irrelevant on re-read.

    Skips the dispatch-ledger gate (`apply_dispatch_ledger=False`): that gate is
    ingest-only dedup and at read time it can't distinguish the user's original
    from Trinity's replay — applying it here eats the user's own prompts (see
    `_is_user_facing_prompt`).
    """
    return _is_user_facing_prompt(
        SessionMessage(role="user", text=text or ""), apply_dispatch_ledger=False
    )


def _is_substantive_assistant(message: SessionMessage) -> bool:
    if message.role != "assistant":
        return False
    if message.extra.get("is_sidechain") or message.extra.get("is_api_error") or message.extra.get("is_synthetic"):
        return False
    return bool(message.text and message.text.strip())


def iter_prompt_turns(session: SessionRecord) -> Iterator[PromptTurn]:
    """Yield clean user-facing prompts from a session, ready for embedding.

    Excludes sidechain (subagent) turns, API-error responses, and empty messages.
    Each yielded turn includes the substantive assistant text immediately before
    and after, for use when constructing TurnWindow embeddings.
    """
    messages = session.messages
    user_indices = [i for i, m in enumerate(messages) if _is_user_facing_prompt(m)]
    turn_index = 0
    for msg_idx in user_indices:
        msg = messages[msg_idx]
        preceding = ""
        for j in range(msg_idx - 1, -1, -1):
            if _is_substantive_assistant(messages[j]):
                preceding = messages[j].text
                break
        following = ""
        for j in range(msg_idx + 1, len(messages)):
            if _is_substantive_assistant(messages[j]):
                following = messages[j].text
                break
        yield PromptTurn(
            transcript_id=session.session_id,
            provider=session.provider,
            source_path=session.source_path,
            turn_index=turn_index,
            text=msg.text.strip(),
            timestamp=msg.timestamp,
            preceding_assistant_text=preceding,
            following_assistant_text=following,
        )
        turn_index += 1


# ---------------------------------------------------------------------------
# Claude.ai webapp export
# ---------------------------------------------------------------------------
# Format: single JSON array of conversations.
# Each conversation: {uuid, name, summary, created_at, updated_at, account, chat_messages[]}
# Each chat_message: {uuid, text, content[], sender ("human" or null), created_at, updated_at}
# Each content block: {type:"text", text, start_timestamp, ...}


def _claude_conversation_dict_to_session(
    conv: dict[str, Any],
    *,
    source_path: str,
    source_format: str,
) -> SessionRecord | None:
    """Shared shape: one claude.ai conversation dict → SessionRecord.

    Used by both ``parse_claude_ai_export`` (bulk export — array of these)
    and ``parse_captured_claude_conversation`` (v1.6 browser capture — one
    per file). The wire shape is identical because v1.6 captures the same
    canonical endpoint Anthropic's export tool uses.
    """
    session_id = conv.get("uuid")
    if not isinstance(session_id, str) or not session_id:
        return None
    messages: list[SessionMessage] = []
    for msg in conv.get("chat_messages", []):
        if not isinstance(msg, dict):
            continue
        sender = msg.get("sender")
        role = "user" if sender == "human" else "assistant"
        text = _message_text(msg.get("content")) or (
            msg.get("text") if isinstance(msg.get("text"), str) else ""
        )
        messages.append(SessionMessage(
            role=role,
            text=text,
            timestamp=msg.get("created_at"),
            raw_type=sender or "assistant",
        ))
    return SessionRecord(
        provider="claude_ai",
        session_id=session_id,
        source_path=source_path,
        native_id=session_id,
        started_at=conv.get("created_at"),
        ended_at=conv.get("updated_at"),
        cwd=None,
        project_hint=None,
        title=(conv.get("name") or conv.get("summary") or "").strip() or None,
        model=conv.get("model") if isinstance(conv.get("model"), str) else None,
        cli_name="claude_ai_webapp",
        cli_version=None,
        source_format=source_format,
        source_format_version="1",
        metadata={},
        messages=messages,
    )


def parse_claude_ai_export(path: Path) -> Iterator[SessionRecord]:
    """Parse a Claude.ai webapp export (conversations.json).

    Yields one SessionRecord per conversation. Multi-turn structure preserved.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, list):
        return

    source_path = str(path)
    for conv in raw:
        if not isinstance(conv, dict):
            continue
        rec = _claude_conversation_dict_to_session(
            conv, source_path=source_path, source_format="claude_ai_export_json"
        )
        if rec is not None:
            yield rec


def parse_captured_claude_conversation(path: Path) -> SessionRecord | None:
    """Parse a v1.6 browser-captured claude.ai conversation file.

    Wire shape matches one element of the bulk export — the capture host
    writes the response from
    ``GET /api/organizations/<org>/chat_conversations/<conv_id>``
    as-is. Returns None for the ``.stream.json`` adapter outputs
    (those don't contain a ``chat_messages`` array — they're keyed by
    ``conv_id`` + ``assistant_text`` from the SSE accumulator).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    # Skip adapter_stream payloads — they don't have chat_messages, so
    # iter_prompt_turns has nothing to yield. The canonical fetch
    # (sibling file at <conv_id>.json) is preferred. Adapter outputs
    # become relevant only when the canonical never arrives (e.g. the
    # user never reloaded the conversation page).
    if "chat_messages" not in raw:
        return None
    return _claude_conversation_dict_to_session(
        raw, source_path=str(path), source_format="claude_browser_capture"
    )


# ---------------------------------------------------------------------------
# ChatGPT webapp export
# ---------------------------------------------------------------------------
# Format: single JSON array of conversations.
# Each conversation has `mapping` (node graph) + `current_node`.
# To get linear order: walk parent links from current_node back to root, then reverse.


def _chatgpt_extract_text(content: Any) -> str:
    """Extract user-facing text from a ChatGPT message.content payload.

    content is typically: {content_type, parts: [str|dict]} OR a multimodal list.
    """
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            stripped = part.strip()
            if stripped:
                chunks.append(stripped)
        elif isinstance(part, dict):
            text = part.get("text") or part.get("content") or ""
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks).strip()


def _chatgpt_conversation_dict_to_session(
    conv: dict[str, Any],
    *,
    source_path: str,
    source_format: str,
) -> SessionRecord | None:
    """Shared shape: one chatgpt conversation dict → SessionRecord.

    Used by both ``parse_chatgpt_export`` (bulk export — array of these)
    and ``parse_captured_chatgpt_conversation`` (v1.6 browser capture —
    one per file). Wire shape is identical because v1.6 captures the
    same ``GET /backend-api/conversation/<id>`` endpoint OpenAI's bulk
    export reads from.
    """
    conv_id = conv.get("conversation_id") or conv.get("id")
    if not isinstance(conv_id, str) or not conv_id:
        return None
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict):
        return None
    current_id = conv.get("current_node")
    if not isinstance(current_id, str) or current_id not in mapping:
        # Fall back: iterate mapping in insertion order
        ordered_ids = list(mapping.keys())
    else:
        ordered_ids = []
        seen: set[str] = set()
        cursor = current_id
        while cursor and cursor in mapping and cursor not in seen:
            seen.add(cursor)
            ordered_ids.append(cursor)
            node = mapping[cursor]
            cursor = node.get("parent") if isinstance(node, dict) else None
        ordered_ids.reverse()

    messages: list[SessionMessage] = []
    model: str | None = None
    for node_id in ordered_ids:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        msg = node.get("message")
        if not isinstance(msg, dict):
            continue
        author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
        role = author.get("role")
        if role not in ("user", "assistant", "system", "tool"):
            continue
        metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        # Skip context the user never typed. ChatGPT injects custom instructions,
        # memory, and system setup as mapping nodes its OWN UI hides
        # (`is_visually_hidden_from_conversation`) — and crucially many carry
        # role="user" (the `is_user_system_message` custom-instruction node), so
        # the downstream role gate doesn't stop them. That text is not the user's
        # authored prompt and routinely holds PII (their name, profession,
        # preferences), so indexing it both poisons the lens with non-authored
        # tokens AND leaks personal data into every surface (viewer, share cards).
        # Found 2026-06-09 dogfooding the chatgpt.com export parser. None of these
        # hidden nodes is part of the visible conversation, so dropping them
        # matches what the user actually saw + typed.
        if metadata.get("is_visually_hidden_from_conversation") or metadata.get("is_user_system_message"):
            continue
        text = _chatgpt_extract_text(msg.get("content"))
        if not text and role != "user":
            continue
        model_slug = metadata.get("model_slug") or metadata.get("default_model_slug")
        if isinstance(model_slug, str) and not model:
            model = model_slug
        create_time = msg.get("create_time")
        timestamp = None
        if isinstance(create_time, (int, float)):
            timestamp = datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()
        messages.append(SessionMessage(
            role=role,
            text=text,
            timestamp=timestamp,
            model=model_slug if isinstance(model_slug, str) else None,
            raw_type=role,
        ))

    create_time = conv.get("create_time")
    update_time = conv.get("update_time")
    started_at = (
        datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()
        if isinstance(create_time, (int, float))
        else None
    )
    ended_at = (
        datetime.fromtimestamp(update_time, tz=timezone.utc).isoformat()
        if isinstance(update_time, (int, float))
        else None
    )
    return SessionRecord(
        provider="chatgpt",
        session_id=conv_id,
        source_path=source_path,
        native_id=conv_id,
        started_at=started_at,
        ended_at=ended_at,
        cwd=None,
        project_hint=None,
        title=(conv.get("title") or "").strip() or None,
        model=model or conv.get("default_model_slug"),
        cli_name="chatgpt_webapp",
        cli_version=None,
        source_format=source_format,
        source_format_version="1",
        metadata={},
        messages=messages,
    )


def parse_chatgpt_export(path: Path) -> Iterator[SessionRecord]:
    """Parse a ChatGPT webapp export (conversations*.json).

    Yields one SessionRecord per conversation. Walks the node tree from
    current_node back to root via parent links, then reverses for linear order.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, list):
        return

    source_path = str(path)
    for conv in raw:
        if not isinstance(conv, dict):
            continue
        rec = _chatgpt_conversation_dict_to_session(
            conv, source_path=source_path, source_format="chatgpt_export_json"
        )
        if rec is not None:
            yield rec


def parse_captured_chatgpt_conversation(path: Path) -> SessionRecord | None:
    """Parse a v1.6 browser-captured chatgpt.com conversation file.

    Wire shape matches one element of the bulk export — the capture host
    writes the response from
    ``GET /backend-api/conversation/<conv_id>`` as-is. Returns None for
    the ``.stream.json`` adapter outputs (those don't contain a
    ``mapping`` graph — they're keyed by ``conv_id`` + ``assistant_text``
    from the SSE accumulator).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    # Skip adapter_stream payloads — they don't have mapping graph.
    if "mapping" not in raw:
        return None
    return _chatgpt_conversation_dict_to_session(
        raw, source_path=str(path), source_format="chatgpt_browser_capture"
    )


# ---------------------------------------------------------------------------
# Gemini Takeout HTML (Google "My Activity" Material Design Lite export)
# ---------------------------------------------------------------------------
# Each `outer-cell` is one prompt + response activity entry.
# Google flattens multi-turn conversations into independent entries — each
# one becomes a 2-message session (user prompt + assistant response).


import re as _re


_GEMINI_OUTER_RE = _re.compile(
    r'<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">(.*?)</div></div></div>',
    _re.DOTALL,
)
_GEMINI_CONTENT_CELL_RE = _re.compile(
    r'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">(.*?)</div>',
    _re.DOTALL,
)
_GEMINI_TAG_RE = _re.compile(r"<[^>]+>")
_GEMINI_WS_RE = _re.compile(r"\s+")
_GEMINI_TS_RE = _re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M(?:\s+[A-Z]{2,4})?)"
)
_GEMINI_TZ_OFFSETS = {
    "UTC": 0, "GMT": 0,
    "EDT": -4, "EST": -5,
    "CDT": -5, "CST": -6,
    "MDT": -6, "MST": -7,
    "PDT": -7, "PST": -8,
}


def _gemini_parse_takeout_timestamp(s: str) -> str | None:
    """Parse 'Apr 12, 2026, 3:34:31 PM EDT' → ISO8601 UTC."""
    import html as _html_mod
    from datetime import datetime, timedelta, timezone

    s = _html_mod.unescape(s).strip()
    tz_offset = 0
    for code, offset in _GEMINI_TZ_OFFSETS.items():
        if s.endswith(code):
            s = s[: -len(code)].strip()
            tz_offset = offset
            break
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y, %I:%M %p"):
        try:
            dt = datetime.strptime(s, fmt)
            dt = dt.replace(tzinfo=timezone.utc) - timedelta(hours=tz_offset)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def _gemini_strip_html(s: str) -> str:
    import html as _html_mod

    s = _GEMINI_TAG_RE.sub(" ", s)
    s = _html_mod.unescape(s)
    s = _GEMINI_WS_RE.sub(" ", s).strip()
    return s


# Group consecutive Gemini Takeout cells into one SessionRecord when they
# occur within this many seconds of each other. Google flattens multi-turn
# conversations into independent cells, but the timestamps on consecutive
# cells reveal the original threading: turns within a single conversation
# typically land seconds-to-minutes apart, while a new conversation usually
# starts hours later.
GEMINI_TAKEOUT_SESSION_GAP_SECONDS = 30 * 60  # 30 minutes


def _parse_gemini_takeout_cells(raw: str) -> list[dict]:
    """Extract every outer-cell as a dict: {prompt, response, ts_iso, native_id}."""
    cells: list[dict] = []
    for match in _GEMINI_OUTER_RE.finditer(raw):
        cell_html = match.group(1)
        content_cells = _GEMINI_CONTENT_CELL_RE.findall(cell_html)
        if not content_cells:
            continue
        main = content_cells[0]
        # Split prompt+timestamp (the head) from the response at the activity
        # TIMESTAMP, not at a <p> tag. The response is whatever the model
        # rendered — multi-paragraph <p>, attributed <p class="…">, <ol>/<ul>
        # lists, or a mix — and a <p>-tag boundary both TRUNCATED multi-part
        # responses AND (worse) spilled <p class>/list responses INTO the prompt
        # the lens learns from, because no bare <p> matched so the whole cell
        # became "head" (verified 2026-06-06: <p class>, <ol>, and <p>+<ol> all
        # corrupted the prompt or dropped the list). Everything after the
        # timestamp is the response, tag-stripped — uniform across all forms.
        # Anchor the timestamp search PAST the prompt link's </a> so a date
        # inside the prompt text isn't mistaken for the activity timestamp.
        a_end = main.find("</a>")
        ts_search_from = a_end + len("</a>") if a_end >= 0 else 0
        ts_match = _GEMINI_TS_RE.search(main, ts_search_from)
        if ts_match:
            head = main[: ts_match.end()]
            response_text = _gemini_strip_html(main[ts_match.end():])
        else:
            # No timestamp — fall back to the <p>-based split (legacy shape).
            p_matches = list(_re.finditer(r"<p>(.*?)</p>", main, _re.DOTALL))
            response_text = (
                _gemini_strip_html(" ".join(m.group(1) for m in p_matches))
                if p_matches else ""
            )
            head = main[: p_matches[0].start()] if p_matches else main

        ts_iso = _gemini_parse_takeout_timestamp(ts_match.group(1)) if ts_match else None

        prompt_html = head
        if ts_match:
            prompt_html = prompt_html.replace(ts_match.group(1), "")
        prompt_text = _gemini_strip_html(prompt_html)
        if prompt_text.startswith("Prompted "):
            prompt_text = prompt_text[len("Prompted "):]
        prompt_text = prompt_text.replace("Audio included.", "").strip()

        if not prompt_text and not response_text:
            continue

        native_id_match = _re.search(r'href="https?://[^"]+/([^/"]+)"', head)
        native_id = native_id_match.group(1) if native_id_match else (ts_iso or "")
        if not native_id:
            continue

        cells.append({
            "prompt": prompt_text,
            "response": response_text,
            "ts_iso": ts_iso,
            "native_id": native_id,
        })
    return cells


def _group_cells_into_sessions(cells: list[dict]) -> list[list[dict]]:
    """Group time-adjacent cells into sessions.

    Cells in the Takeout HTML are in reverse-chronological order. Sort
    ascending by timestamp, then start a new group whenever the gap to the
    previous cell exceeds GEMINI_TAKEOUT_SESSION_GAP_SECONDS or whenever
    a timestamp is missing.
    """
    from datetime import datetime

    def parse_ts(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    timed = [(parse_ts(c["ts_iso"]), c) for c in cells]
    timed.sort(key=lambda pair: pair[0] or datetime.max)

    groups: list[list[dict]] = []
    current: list[dict] = []
    last_ts: object = None
    for ts, cell in timed:
        if not current:
            current = [cell]
            last_ts = ts
            continue
        gap = None
        if ts is not None and last_ts is not None:
            gap = (ts - last_ts).total_seconds()
        if gap is None or gap > GEMINI_TAKEOUT_SESSION_GAP_SECONDS:
            groups.append(current)
            current = [cell]
        else:
            current.append(cell)
        last_ts = ts
    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# v="3" — embedding+time hybrid grouper (task #107)
# ---------------------------------------------------------------------------
# The pure time-proximity grouper above (v="2") doesn't reconstruct threads
# correctly on real Takeout corpora:
#   1. Cells with missing timestamps fragment (new group started on every None).
#   2. User resumes a topic after >30 min — wrongly split into 2 sessions.
#   3. User multitasks <30 min across unrelated topics — wrongly merged.
#
# The v="3" path embeds each cell's (prompt + " " + response) text via
# nomic-768d, then sliding-window clusters cells in time order. A cell
# attaches to the most-recent open cluster when BOTH:
#   (a) cosine(cell, cluster_centroid) >= GEMINI_TAKEOUT_EMBED_SIMILARITY
#   (b) time gap to cluster's last cell <= GEMINI_TAKEOUT_EMBED_MAX_WINDOW_SECONDS
# Otherwise a new cluster is seeded.
#
# Threshold choice (0.55): synthetic-data validated. nomic-768d cosines on
# topic-related prose typically land in 0.65–0.85; cross-topic cosines
# land in 0.2–0.45. 0.55 is the midpoint of "clearly related" vs "clearly
# unrelated" and tolerates the natural drift within a single conversation.
# May need re-tuning once we see real-corpus distributions — flagged in
# the task #107 commit.
#
# Time bound (24h): much larger than the v="2" 30-min gap so the embedding
# signal can override "long gap" on resumed topics. Beyond 24h, even
# strongly related cells are almost always a fresh session that happens
# to be on a similar topic — the time bound is a safety net, not the
# primary cluster signal.
#
# Embedding fallback: if MLX isn't installed, embeddings.embed_batch falls
# back to a deterministic TF-IDF projection. TF-IDF cosines aren't directly
# comparable to nomic cosines (different distributions), so when the
# backend is "tfidf" we fall through to the v="2" path entirely (caller
# checks embeddings.get_backend()).

GEMINI_TAKEOUT_EMBED_SIMILARITY = 0.55
GEMINI_TAKEOUT_EMBED_MAX_WINDOW_SECONDS = 24 * 3600


def _group_cells_by_embedding(cells: list[dict]) -> list[list[dict]]:
    """Embedding+time hybrid grouper (v="3" path, task #107).

    Embeds each cell's (prompt + " " + response) text in ONE batched call,
    then sliding-window clusters cells in time order using cosine similarity
    against a running cluster centroid plus a generous time bound.

    Cells with missing timestamps are filled with last_seen + 1 second so
    they ride along with adjacent cells instead of fragmenting.
    """
    from datetime import datetime, timedelta

    if not cells:
        return []

    # 1) Parse timestamps and assign placeholder timestamps to None values.
    # Sort by parsed timestamp first (None goes to the end), then walk and
    # fill None with last_seen + 1s. This keeps the time order stable while
    # ensuring missing-timestamp cells join the most-recent group.
    def parse_ts(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    timed = [(parse_ts(c["ts_iso"]), c) for c in cells]
    # Sort ascending; None timestamps push to the end of the corpus, but we
    # patch them inline below so they join their physical neighbors instead.
    timed.sort(key=lambda pair: pair[0] or datetime.max)

    last_seen: datetime | None = None
    filled: list[tuple[datetime, dict]] = []
    for ts, cell in timed:
        if ts is None:
            # No timestamp on this cell — anchor it to last_seen + 1s so
            # it joins the previous cluster's time window. If last_seen is
            # also None (first cell missing ts), use epoch as the anchor.
            ts = (last_seen + timedelta(seconds=1)) if last_seen else datetime(1970, 1, 1)
        filled.append((ts, cell))
        last_seen = ts

    # Re-sort after filling so any "patched" timestamps land in proper order.
    filled.sort(key=lambda pair: pair[0])

    # 2) Embed every cell in ONE batched call (meta-principle: batch at
    # the boundary; 10k cells × per-cell embed() would be ~10× slower).
    from .embeddings import embed_batch
    from .embeddings.backend_tfidf import cosine_similarity

    texts = [
        (cell["prompt"] + " " + cell["response"]).strip() or cell["native_id"]
        for _, cell in filled
    ]
    vectors = embed_batch(texts)

    # 3) Sliding-window cluster. Each cluster carries its centroid (running
    # mean of member vectors) + the timestamp of its most-recent cell.
    groups: list[list[dict]] = []
    centroids: list[list[float]] = []
    last_ts_per_group: list[datetime] = []

    for (ts, cell), vec in zip(filled, vectors):
        attached = False
        # Scan clusters in reverse-chronological order (most-recent first)
        # so resumed topics attach to the freshest matching cluster.
        for idx in range(len(groups) - 1, -1, -1):
            time_gap = (ts - last_ts_per_group[idx]).total_seconds()
            if time_gap > GEMINI_TAKEOUT_EMBED_MAX_WINDOW_SECONDS:
                continue
            sim = cosine_similarity(vec, centroids[idx])
            if sim >= GEMINI_TAKEOUT_EMBED_SIMILARITY:
                groups[idx].append(cell)
                # Update centroid as running mean.
                n = len(groups[idx])
                centroids[idx] = [
                    (centroids[idx][i] * (n - 1) + vec[i]) / n
                    for i in range(len(vec))
                ]
                last_ts_per_group[idx] = ts
                attached = True
                break
        if not attached:
            groups.append([cell])
            centroids.append(list(vec))
            last_ts_per_group.append(ts)

    # 4) Emit groups in time order (sort by first-cell timestamp).
    def first_ts(group: list[dict]) -> datetime:
        for cell in group:
            ts = parse_ts(cell["ts_iso"])
            if ts is not None:
                return ts
        return datetime.max
    groups.sort(key=first_ts)
    return groups


def parse_gemini_takeout_html(
    path: Path,
    *,
    use_embedding_grouping: bool = True,
) -> Iterator[SessionRecord]:
    """Parse a Gemini Takeout MyActivity.html file.

    Each outer-cell is one prompt+response activity entry. Google flattens
    multi-turn conversations into independent cells, but the original threading
    has to be reconstructed.

    Two grouping strategies are available:

    * ``use_embedding_grouping=True`` (default, v="3") — embed each cell via
      nomic-768d and sliding-window cluster by cosine similarity + a 24h
      time bound. Reconstructs threads correctly across (a) missing
      timestamps, (b) >30min gaps with topic continuity, (c) topic-switch
      multitasking inside a short window. Used when MLX embeddings are
      available; falls back to v="2" when the active backend is TF-IDF
      (whose cosines aren't directly comparable to nomic cosines, so the
      0.55 threshold doesn't transfer).

    * ``use_embedding_grouping=False`` (v="2") — pure time-proximity:
      gap > 30 min OR missing timestamp starts a new session. Preserved
      for back-compat / tests / embed-less environments.

    The emitted SessionRecord carries source_format_version + a
    ``reconstruction`` metadata key (``embedding+time`` or
    ``time_proximity``) so downstream consumers can see which path
    produced each session.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    source_path = str(path)
    cells = _parse_gemini_takeout_cells(raw)
    if not cells:
        return

    # Backend probe: nomic cosines and TF-IDF cosines aren't comparable, so
    # the 0.55 threshold only transfers under the nomic backend. When the
    # active backend is TF-IDF (MLX not installed / fallback), the v="3"
    # path silently falls through to v="2" — back-compat parity for
    # embed-less environments.
    use_v3 = use_embedding_grouping
    if use_v3:
        try:
            from .embeddings import get_backend
            if get_backend() != "mlx":
                use_v3 = False
        except Exception:
            use_v3 = False

    if use_v3:
        groups = _group_cells_by_embedding(cells)
        version_tag = "3"
        reconstruction_kind = "embedding+time"
    else:
        groups = _group_cells_into_sessions(cells)
        version_tag = "2"
        reconstruction_kind = "time_proximity"

    for group in groups:
        if not group:
            continue
        first_native_id = group[0]["native_id"]
        session_id = f"gemini_takeout_{first_native_id}"
        first_ts = group[0]["ts_iso"]
        last_ts = group[-1]["ts_iso"]
        title_source = group[0]["prompt"] or group[0]["response"] or ""

        messages: list[SessionMessage] = []
        for cell in group:
            if cell["prompt"]:
                messages.append(SessionMessage(
                    role="user",
                    text=cell["prompt"],
                    timestamp=cell["ts_iso"],
                    raw_type="user",
                ))
            if cell["response"]:
                messages.append(SessionMessage(
                    role="assistant",
                    text=cell["response"],
                    timestamp=cell["ts_iso"],
                    raw_type="assistant",
                ))
        if not messages:
            continue

        metadata: dict[str, Any] = {
            "cell_count": len(group),
            "reconstruction": reconstruction_kind,
        }
        if version_tag == "3":
            metadata["reconstruction_threshold"] = GEMINI_TAKEOUT_EMBED_SIMILARITY
            metadata["reconstruction_window_seconds"] = GEMINI_TAKEOUT_EMBED_MAX_WINDOW_SECONDS

        yield SessionRecord(
            provider="gemini",
            session_id=session_id,
            source_path=source_path,
            native_id=first_native_id,
            started_at=first_ts,
            ended_at=last_ts,
            cwd=None,
            project_hint=None,
            title=(title_source[:80] if title_source else None),
            model=None,
            cli_name="gemini_takeout",
            cli_version=None,
            source_format="gemini_takeout_html",
            source_format_version=version_tag,
            metadata=metadata,
            messages=messages,
        )


# ---------------------------------------------------------------------------
# Gemini browser-capture parser (v1.8, task #135)
# ---------------------------------------------------------------------------
# Captures from gemini.google.com arrive via the browser extension's
# `adapters/gemini.js`. Unlike claude.ai / chatgpt.com (whose canonical
# state endpoints return the full message tree), Google's `batchexecute`
# RPC is reply-only on the response side — the user's prompt only lives
# in the outbound REQUEST body. The adapter best-effort extracts both:
#   * `user_text` from the form-encoded `f.req` body (request_body in
#     page-hook.js)
#   * `assistant_text` from the chunked length-prefixed JSON frames
#     (wrb.fr rows in the response body)
#
# Raw bodies are preserved in `_raw_body` + `_raw_request_body` so a
# future ingest run with an updated adapter can re-parse without
# re-capturing. Gemini's frame shape rotates across Google's frontend
# releases — keeping the raw decouples ingest from adapter shape.


def parse_captured_gemini_conversation(path: Path) -> SessionRecord | None:
    """Parse a v1.8 browser-captured gemini.google.com conversation file.

    Wire shape is the adapter_stream payload written by
    ``browser-extension/adapters/gemini.js`` — see module docstring above.
    Returns None if the file isn't a recognizable Gemini capture (no
    provider field, wrong provider, no conv_id, no assistant_text).

    Each captured file is ONE turn (one batchexecute RPC = one user
    prompt + one assistant reply). Multi-turn conversations land as
    multiple files, each keyed by conv_id — overwrite-by-conv-id keeps
    only the latest turn on disk by design, mirroring the claude.ai /
    chatgpt.com canonical-write semantics.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("provider") != "gemini":
        return None
    conv_id = raw.get("conv_id")
    if not isinstance(conv_id, str) or not conv_id:
        return None
    assistant_text = raw.get("assistant_text")
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        # No prose extracted — adapter shape may have moved. Caller
        # (incremental_ingest) treats this as skipped_parse; the raw
        # capture stays on disk so a future ingest run with an
        # updated extractor can pick it up.
        return None
    captured_at = raw.get("captured_at") if isinstance(raw.get("captured_at"), str) else None
    message_id = raw.get("message_id") if isinstance(raw.get("message_id"), str) else None
    user_text = raw.get("user_text") if isinstance(raw.get("user_text"), str) else None

    messages: list[SessionMessage] = []
    # User turn — only present when the adapter could extract it from the
    # batchexecute REQUEST body (page-hook.js snapshots init.body for
    # gemini.google.com fetches). Without this, the capture contributes
    # zero PromptTurn entries because iter_prompt_turns only yields
    # user-facing turns. Older captures (pre-v1.8 page-hook) won't have
    # this field; the assistant turn alone is still recorded as context
    # but yields no PromptTurn — that's the documented gemini reply-only
    # limitation.
    if user_text and user_text.strip():
        messages.append(SessionMessage(
            role="user",
            text=user_text.strip(),
            timestamp=captured_at,
            raw_type="user",
        ))
    messages.append(SessionMessage(
        role="assistant",
        text=assistant_text.strip(),
        timestamp=captured_at,
        raw_type="assistant",
    ))

    return SessionRecord(
        provider="gemini",
        session_id=conv_id,
        source_path=str(path),
        native_id=message_id or conv_id,
        started_at=captured_at,
        ended_at=captured_at,
        cwd=None,
        project_hint=None,
        # First ~80 chars of the user prompt (or assistant text fallback)
        # as a stand-in title — the batchexecute RPC doesn't carry one.
        title=((user_text.strip()[:80] if user_text and user_text.strip() else assistant_text.strip()[:80]) or None),
        model=None,
        cli_name="gemini_webapp",
        cli_version=None,
        source_format="gemini_browser_capture",
        source_format_version="1",
        metadata={
            "frames_count": raw.get("frames_count") if isinstance(raw.get("frames_count"), int) else None,
            "events_count": raw.get("events_count") if isinstance(raw.get("events_count"), int) else None,
        },
        messages=messages,
    )
