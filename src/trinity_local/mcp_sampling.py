"""MCP host-side sampling primitive — get a Claude completion via the
MCP client's `sampling/createMessage` channel instead of subprocessing
`claude -p`.

WHY this exists. As of 2026-06-15 Anthropic moves `claude -p` (Agent
SDK) usage to a separate monthly credit pool ($20 Pro, $100 Max 5x,
$200 Max 20x). MCP host sampling — when Trinity-MCP is loaded by a
chat client like Claude Desktop and asks the client to do a Claude
completion — counts against the host's *regular* plan, not the Agent
SDK pool. So for the 1-member-from-Claude-Desktop case the user pays
nothing beyond their existing Claude Desktop subscription.

DESIGN.
* This module exposes one primitive — ``request_claude_sample`` — that
  the future Claude provider wrapper will try BEFORE shelling out to
  ``claude -p``. If sampling isn't available (no session, no
  capability, client refuses) it returns ``None`` and the caller falls
  back to the subprocess path.
* The "current session" is tracked in a ``ContextVar`` so worker
  threads can find it without plumbing it through every layer. The
  MCP tool handlers ``set_active_session(session)`` at the start of
  each call, then ``clear_active_session()`` after.
* The sampling call is async; provider code is sync (council_runner
  uses a ThreadPoolExecutor). The bridge: ``run_coroutine_threadsafe``
  against the event loop that owns the session. We capture that loop
  at registration time too.

INVARIANTS.
* Returns ``None`` (never raises) when sampling isn't available or
  fails. Caller always has a working subprocess fallback.
* Never logs the sampled content — privacy posture matches the rest
  of Trinity (no prompt content leaves the user's machine).
* Other providers (Codex, Gemini) are NOT touched here — they don't
  penalize ``-p`` calls today, so the subprocess path remains correct
  and cheap for them.
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _ActiveSampling:
    """The pair we need to call into the host: the session itself, plus
    the asyncio event loop that owns it (worker threads need both to
    use ``run_coroutine_threadsafe``)."""
    session: Any  # mcp.server.session.ServerSession at runtime
    loop: asyncio.AbstractEventLoop


_active: contextvars.ContextVar[_ActiveSampling | None] = contextvars.ContextVar(
    "trinity_mcp_active_sampling", default=None
)


def set_active_session(session: Any) -> None:
    """Register the active MCP server session for the current request.

    Call this at the top of every MCP tool handler that may dispatch a
    council member. Pairs with ``clear_active_session()`` in a try/
    finally — leaking the session across requests would be benign
    (we'd just sample from the prior session) but is sloppy.
    """
    loop = asyncio.get_running_loop()
    _active.set(_ActiveSampling(session=session, loop=loop))


def clear_active_session() -> None:
    _active.set(None)


def current_session_supports_sampling() -> bool:
    """True iff an active MCP session is registered AND its client
    advertised the ``sampling`` capability during the initialize
    handshake. False otherwise.

    This is a cheap check — providers consult it before deciding
    whether to attempt sampling or go straight to subprocess.
    """
    state = _active.get()
    if state is None:
        return False
    return _client_has_sampling(state.session)


def _client_has_sampling(session: Any) -> bool:
    """Inspect the session's recorded client capabilities for the
    ``sampling`` field. Format: ``ClientCapabilities`` has a
    ``sampling`` attr that's an empty object when the client supports
    it, or None when it doesn't. Older SDK versions may stash this
    differently — guard everything in a try/except so a SDK quirk
    can't break the live path."""
    try:
        params = getattr(session, "client_params", None)
        if params is None:
            return False
        caps = getattr(params, "capabilities", None)
        if caps is None:
            return False
        sampling = getattr(caps, "sampling", None)
        return sampling is not None
    except Exception:
        return False


def request_claude_sample(
    prompt: str,
    *,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    timeout_seconds: float = 60.0,
) -> str | None:
    """Ask the host MCP client to perform a Claude completion via
    ``sampling/createMessage``. Returns the text response, or ``None``
    when sampling isn't available or fails.

    Safe to call from a worker thread — internally schedules the async
    sampling call onto the loop captured by ``set_active_session``.
    """
    state = _active.get()
    if state is None or not _client_has_sampling(state.session):
        return None

    future = asyncio.run_coroutine_threadsafe(
        _do_sample(
            state.session,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        state.loop,
    )
    try:
        return future.result(timeout=timeout_seconds)
    except Exception:
        # Any failure — timeout, client refusal, transport error — falls
        # back to subprocess. Never raise: the contract is "try sampling,
        # quietly degrade if it doesn't work."
        return None


async def _do_sample(
    session: Any,
    *,
    prompt: str,
    system_prompt: str | None,
    max_tokens: int,
    temperature: float | None,
) -> str | None:
    """The async half of ``request_claude_sample``. Constructs the
    sampling request, awaits the host, extracts the text content from
    the result. Returns None when the client refuses or the result
    doesn't contain text."""
    from mcp.types import ModelPreferences, ModelHint, SamplingMessage, TextContent

    messages = [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=prompt),
        )
    ]
    # Bias the host toward Claude. The MCP spec says hints are advisory —
    # a host that only runs Claude (Claude Desktop) will honor; others
    # may pick whatever they have. The Trinity contract is "Claude
    # voice" so we DO want Claude specifically; if the host can't
    # provide it, we'd rather fall back to subprocess `claude -p`
    # which we know is Claude.
    preferences = ModelPreferences(hints=[ModelHint(name="claude")])

    try:
        result = await session.create_message(
            messages=messages,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            temperature=temperature,
            model_preferences=preferences,
        )
    except Exception:
        return None

    # Result content is either a TextContent or a list-like — extract
    # the first text payload defensively.
    content = getattr(result, "content", None)
    if content is None:
        return None
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    # Some SDK versions wrap content as a list of content parts.
    if isinstance(content, list) and content:
        first = content[0]
        first_text = getattr(first, "text", None)
        if isinstance(first_text, str):
            return first_text
    return None
