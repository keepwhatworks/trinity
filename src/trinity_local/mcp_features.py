"""MCP protocol features beyond tools/resources/sampling: structured
**logging**, **progress** notifications, **roots** discovery, and
**elicitation** — all host-side, all best-effort, all safe to call from a
worker thread.

Design mirrors ``mcp_sampling``:

* The active session + event loop are captured by ``mcp_sampling.set_active_session``
  at the top of every tool handler. We reuse that ContextVar here rather than
  duplicating the plumbing — one source of truth for "is there a live client".
* Council / lens work runs in ``ThreadPoolExecutor`` / ``threading.Thread``
  workers (sync code). The session's async methods must be scheduled onto the
  captured loop via ``run_coroutine_threadsafe`` — never awaited inline.
* Every function degrades to a quiet no-op (returns None / False) when no
  session is active, the client didn't advertise the capability, or the
  transport fails. The contract is "try the protocol feature, quietly degrade
  if it isn't there." A logging call must never break a council.

The per-request bits that aren't part of the sampling context — the
``request_id`` (for ``related_request_id``) and the ``progressToken`` the
client attached — are captured separately by ``set_request_context`` at tool
entry and cleared in the same ``finally`` as the session.
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import Any

# Reuse the sampling session/loop capture — set in handle_call_tool.
from .mcp_sampling import _active as _sampling_active

LogLevel = str  # one of: debug info notice warning error critical alert emergency

_LOG_RANK = {
    "debug": 0, "info": 1, "notice": 2, "warning": 3,
    "error": 4, "critical": 5, "alert": 6, "emergency": 7,
}

# Client-requested minimum log level (logging/setLevel). Default "info":
# emit info and above, drop debug, until the client asks otherwise. Module
# global (not a ContextVar) — the level is a connection-wide setting.
_min_log_level: LogLevel = "info"


def set_min_log_level(level: LogLevel) -> None:
    """Record the client's logging/setLevel request. Unknown levels are
    ignored (keep the prior threshold)."""
    global _min_log_level
    if level in _LOG_RANK:
        _min_log_level = level


def _level_enabled(level: LogLevel) -> bool:
    return _LOG_RANK.get(level, 1) >= _LOG_RANK.get(_min_log_level, 1)


@dataclass
class _RequestContext:
    request_id: Any
    progress_token: Any  # str | int | None


_request: contextvars.ContextVar[_RequestContext | None] = contextvars.ContextVar(
    "trinity_mcp_request", default=None
)


def set_request_context(request_id: Any, progress_token: Any) -> None:
    """Capture the current request's id + progress token at tool entry, so
    worker-thread log/progress calls can attribute themselves correctly."""
    _request.set(_RequestContext(request_id=request_id, progress_token=progress_token))


def clear_request_context() -> None:
    _request.set(None)


def _session_and_loop():
    """The active (session, loop) pair, or (None, None) when no client."""
    state = _sampling_active.get()
    if state is None:
        return None, None
    return state.session, state.loop


def _run_on_loop(coro, loop, timeout: float = 5.0):
    """Schedule a coroutine onto the captured loop from any thread, swallow
    every failure. Returns the result or None."""
    if loop is None:
        return None
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    except Exception:
        return None


# ─── Logging (notifications/message) ────────────────────────────────


def mcp_log(level: LogLevel, message: str, *, logger: str = "trinity") -> bool:
    """Send a structured log line to the client. Returns True if dispatched,
    False on any degrade (no session / level filtered / transport error).
    Safe from worker threads."""
    if not _level_enabled(level):
        return False
    session, loop = _session_and_loop()
    if session is None:
        return False
    req = _request.get()
    rid = req.request_id if req else None
    coro = session.send_log_message(
        level=level, data=message, logger=logger, related_request_id=rid
    )
    # send_log_message returns None on success, so the result can't tell us
    # success from failure — _run_on_loop swallows transport errors. Report
    # "dispatched"; logging is fire-and-forget by contract.
    _run_on_loop(coro, loop)
    return True


# ─── Progress (notifications/progress) ──────────────────────────────


def mcp_progress(
    progress: float, total: float | None = None, *, message: str | None = None
) -> bool:
    """Report progress for the in-flight request. No-op unless the client
    attached a progressToken to this tool call. Safe from worker threads."""
    req = _request.get()
    if req is None or req.progress_token is None:
        return False
    session, loop = _session_and_loop()
    if session is None:
        return False
    coro = session.send_progress_notification(
        progress_token=req.progress_token,
        progress=progress,
        total=total,
        message=message,
        related_request_id=req.request_id,
    )
    _run_on_loop(coro, loop)
    return True


# ─── Roots (roots/list) ─────────────────────────────────────────────


def discover_roots() -> list[str]:
    """Ask the client for its filesystem roots (roots/list). Returns a list of
    local paths (file:// URIs resolved to paths), or [] when the client didn't
    advertise roots or none are exposed. Safe from worker threads."""
    session, loop = _session_and_loop()
    if session is None:
        return []
    result = _run_on_loop(session.list_roots(), loop)
    if result is None:
        return []
    paths: list[str] = []
    for root in getattr(result, "roots", []) or []:
        uri = str(getattr(root, "uri", "") or "")
        if uri.startswith("file://"):
            paths.append(uri[len("file://"):])
        elif uri:
            paths.append(uri)
    return paths


# ─── Elicitation (elicitation/create) ───────────────────────────────


def elicit(message: str, requested_schema: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the user for structured input mid-tool. Returns the content dict on
    accept, or None on decline / cancel / no-client / unsupported. Callers MUST
    treat None as "proceed with the default" — elicitation is an enhancement,
    never a hard dependency (most harnesses don't support it yet). Safe from
    worker threads."""
    session, loop = _session_and_loop()
    if session is None:
        return None
    req = _request.get()
    rid = req.request_id if req else None
    result = _run_on_loop(
        session.elicit(
            message=message, requestedSchema=requested_schema, related_request_id=rid
        ),
        loop,
        timeout=300.0,  # a human is answering — give them time
    )
    if result is None:
        return None
    if getattr(result, "action", None) != "accept":
        return None
    return getattr(result, "content", None) or None
