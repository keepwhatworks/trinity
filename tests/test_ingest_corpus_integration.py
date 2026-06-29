"""End-to-end moat read-path: real transcript → parse → filter → prompt turns.

The moat is "Trinity reads your transcripts." Two halves are unit-tested in
isolation — the PARSERS (`parse_*_session`, test_ingest_parsers / _resilience) and
the corpus-purity FILTER (`_is_user_facing_prompt`, test_ingest_scaffolding_filter)
— but the filter test feeds HAND-BUILT `SessionMessage`s, never real parser output.
So a parser whose message shape the filter mishandles (a role set differently, text
wrapped/prefixed unexpectedly — e.g. antigravity's `_antigravity_user_text`
extraction) would slip past BOTH unit tests while silently corrupting or starving
the corpus.

This pins the parser↔filter CONTRACT per provider, the way prod wires it
(`iter_prompt_turns` applies `_is_user_facing_prompt` to each parsed message):
a realistic transcript with (a) a real authored prompt, (b) a provider-specific
scaffolding turn the filter must drop, (c) an assistant reply →
`parse_<provider>_session → iter_prompt_turns` must yield the real prompt ONLY,
tagged with the right provider. A future parser change that breaks the shape the
filter expects fails here even if the per-component unit tests stay green.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local.ingest import (
    iter_prompt_turns,
    parse_antigravity_session,
    parse_claude_code_session,
    parse_codex_session,
    parse_gemini_cli_session,
)

REAL = "REAL-PROMPT-MARKER"  # the authored prompt that MUST survive
# Each scaffolding turn below is a marker `_is_user_facing_prompt` drops, fed
# through the REAL parser (not a hand-built SessionMessage) to prove the parser's
# output shape and the filter agree.


def _build_claude(tmp: Path) -> Path:
    f = tmp / "session.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"content": f"{REAL} refactor auth to remove duplication"}}),
        json.dumps({"type": "user",  # scaffolding: "You are ..." system prompt
                    "message": {"content": "You are a helpful coding assistant."}}),
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Sure, here's the refactor."}]}}),
    ]))
    return f


def _build_codex(tmp: Path) -> Path:
    f = tmp / "rollout-x.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"id": "c1", "cwd": "/x"}}),
        json.dumps({"type": "response_item", "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {"type": "message", "role": "user",
                                "content": f"{REAL} write a test for the parser"}}),
        json.dumps({"type": "response_item",  # scaffolding: codex <image …> block
                    "payload": {"type": "message", "role": "user", "content": "<image foo.png>"}}),
    ]))
    return f


def _build_antigravity(tmp: Path) -> Path:
    logs = tmp / "brain" / "c1" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    f = logs / "transcript.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "USER_INPUT", "created_at": "2026-01-01T00:00:00",
                    "content": f"<USER_REQUEST>{REAL} remove selenium use stagehand</USER_REQUEST>"}),
        json.dumps({"type": "USER_INPUT", "created_at": "2026-01-01T00:01:00",  # Trinity agy probe
                    "content": "<USER_REQUEST>reply with exactly: PONG</USER_REQUEST>"}),
    ]))
    return f


def _build_gemini(tmp: Path) -> Path:
    chats = tmp / "proj" / "chats"
    chats.mkdir(parents=True)
    f = chats / "session-1.json"
    f.write_text(json.dumps({"sessionId": "g1", "messages": [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "content": f"{REAL} explain the floor plan engine"},
        {"type": "user", "timestamp": "2026-01-01T00:01:00Z",  # scaffolding
         "content": "You are an assistant."},
    ]}))
    return f


_PROVIDERS = {
    "claude": (_build_claude, lambda f: parse_claude_code_session(f)),
    "codex": (_build_codex, lambda f: parse_codex_session(f)),
    "antigravity": (_build_antigravity, lambda f: parse_antigravity_session(f)),
    "gemini": (_build_gemini, lambda f: parse_gemini_cli_session(f, project_name="proj")),
}


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_real_prompt_survives_scaffolding_dropped(provider, tmp_path):
    build, parse = _PROVIDERS[provider]
    session = parse(build(tmp_path))
    assert session is not None, f"{provider}: parser returned None on a valid transcript"
    turns = list(iter_prompt_turns(session))

    texts = [t.text for t in turns]
    # the authored prompt survives the parse→filter chain
    assert any(REAL in t for t in texts), (
        f"{provider}: the real authored prompt was dropped by the parse→filter chain "
        f"(corpus STARVED) — got {texts}"
    )
    # NO scaffolding turn leaks through as a corpus prompt
    assert all(REAL in t for t in texts), (
        f"{provider}: a scaffolding turn survived into the corpus (corpus POLLUTED) — "
        f"the parser's message shape and _is_user_facing_prompt disagree. Got {texts}"
    )
    # provenance is tagged with the right provider
    assert all(t.provider == provider for t in turns), (
        f"{provider}: a turn carried the wrong provider tag — {[t.provider for t in turns]}"
    )


def test_all_providers_share_the_same_filter_contract(tmp_path):
    """Cross-provider sanity: every adapter yields EXACTLY the one real prompt — no
    provider is silently dropping all turns (a shape mismatch that returns nothing
    would pass a per-provider 'real survives' check vacuously only if it also failed,
    but a provider that yields ZERO turns is the starvation failure to catch here)."""
    counts = {}
    for provider, (build, parse) in _PROVIDERS.items():
        d = tmp_path / provider
        d.mkdir()
        session = parse(build(d))
        counts[provider] = len(list(iter_prompt_turns(session))) if session else 0
    assert all(c == 1 for c in counts.values()), (
        f"a provider yielded != 1 prompt turn (expected exactly the one real prompt "
        f"per adapter, scaffolding filtered): {counts}"
    )
