"""Data-purity guard: a Claude Code `tool_result` (returned to the model as a
user-role message) must NEVER become a user prompt the lens trains on.

This is the #260 do-operator invariant at its single biggest poison vector. In
Claude Code JSONL, tool OUTPUTS are delivered as role=user entries whose content
is a `tool_result` block — and in agentic sessions those vastly outnumber the
human's typed turns. The lens is supposed to learn the user's OWN authored taste;
if a tool_result's payload (a file dump, a grep result, a stack trace) were
extracted as a "user prompt", the corpus would be flooded with model/tool tokens
masquerading as the user's voice — a far larger leak than the scaffolding markers
test_ingest_scaffolding_filter already catches.

It's prevented by `_message_text` extracting ONLY a block's top-level ``text``
(a tool_result carries its payload under ``content``, not ``text``) → the
tool_result user message yields empty text → `_is_user_facing_prompt` drops it.
The unit tests for `_message_text` cover a string / text blocks / an image block /
None / int — but nothing covers a tool_result block, and nothing covers it at the
SESSION-PARSE level (a real Claude Code user message that is a tool_result). A
plausible future "also fold in tool context" change to `_message_text` would pass
every existing ingest test while poisoning the lens with every tool output.

This builds a realistic Claude Code session — typed prompts interleaved with
assistant tool_use, tool_result-as-user in BOTH content shapes (a list of text
blocks AND a bare string), and a sidechain sub-agent turn — parses it, applies the
production gate, and asserts ONLY the human's two typed prompts survive: no tool
output, no sub-agent text. Mutation-proven: make `_message_text` recurse into
``tool_result``/``content`` → the SECRET_TOOL markers leak → this reds.
"""
from __future__ import annotations

import json
from pathlib import Path

from trinity_local.ingest import (
    _is_user_facing_prompt,
    parse_claude_ai_export,
    parse_claude_code_session,
    parse_codex_session,
)

_TYPED_1 = "How do I fix this null pointer in the parser?"
_TYPED_2 = "Thanks — now refactor it to be null-safe."
_TOOL_LEAK_LIST = "SECRET_TOOL_OUTPUT_LEAKED_LIST def foo(): return None"
_TOOL_LEAK_STR = "SECRET_TOOL_OUTPUT_LEAKED_STRING contents of x.py"
_SIDECHAIN = "SIDECHAIN_SUBAGENT_PROMPT run the subtask"


def _write_session(path: Path) -> None:
    lines = [
        {"type": "user", "timestamp": "2026-06-01T00:00:00Z",
         "message": {"role": "user", "content": _TYPED_1}},
        {"type": "assistant", "timestamp": "2026-06-01T00:00:01Z",
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [{"type": "text", "text": "Let me read the file."},
                                 {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file": "x.py"}}]}},
        # tool_result returned as a user-role message — content is a LIST of text blocks
        {"type": "user", "timestamp": "2026-06-01T00:00:02Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1",
              "content": [{"type": "text", "text": _TOOL_LEAK_LIST}]}]}},
        # tool_result returned as a user-role message — content is a bare STRING
        {"type": "user", "timestamp": "2026-06-01T00:00:03Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": _TOOL_LEAK_STR}]}},
        {"type": "user", "timestamp": "2026-06-01T00:00:04Z",
         "message": {"role": "user", "content": _TYPED_2}},
        # a sidechain (sub-agent) user turn — a model action, not the human's voice
        {"type": "user", "isSidechain": True, "timestamp": "2026-06-01T00:00:05Z",
         "message": {"role": "user", "content": _SIDECHAIN}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_tool_result_and_sidechain_never_become_user_prompts(tmp_path):
    session_path = tmp_path / "session.jsonl"
    _write_session(session_path)

    record = parse_claude_code_session(session_path)
    assert record is not None, "the Claude Code session failed to parse"

    prompts = [m.text for m in record.messages if _is_user_facing_prompt(m)]

    # Exactly the human's two typed prompts — nothing else.
    assert prompts == [_TYPED_1, _TYPED_2], (
        f"the user-facing prompts are not exactly the two typed turns: {prompts!r}"
    )
    blob = "\n".join(prompts)
    for leak in (_TOOL_LEAK_LIST, _TOOL_LEAK_STR, "def foo", "contents of x.py", _SIDECHAIN):
        assert leak not in blob, (
            f"tool output / sub-agent text leaked into the lens corpus as a user "
            f"prompt: {leak!r} — the #260 do-operator invariant is broken (the lens "
            "would train on model/tool tokens as if they were the user's voice)"
        )


_CODEX_TOOL_LEAK = "CODEX_TOOL_OUTPUT_LEAKED total 48 drwxr-xr-x auth.py"


def _write_codex_session(path: Path) -> None:
    # Codex represents tool output differently from Claude Code: a
    # `function_call_output` response_item, which the parser maps to role="tool"
    # (vs Claude Code's role=user tool_result). The role gate then drops it. But
    # that mapping IS the load-bearing step — if it regressed to role="user" the
    # raw tool output (which carries no scaffolding marker) would sail past the
    # filter, and the role-gate test (which only proves a role=tool MESSAGE is
    # dropped) wouldn't catch it. Tool output is the largest volume of non-human
    # text in an agentic Codex session, same as Claude Code.
    lines = [
        {"type": "session_meta", "timestamp": "2026-06-01T00:00:00Z",
         "payload": {"id": "s1", "cwd": "/x", "cli_version": "1", "model_provider": "openai"}},
        {"type": "turn_context", "timestamp": "2026-06-01T00:00:00Z", "payload": {"model": "gpt-5.5"}},
        {"type": "response_item", "timestamp": "2026-06-01T00:00:01Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": _TYPED_1}]}},
        {"type": "response_item", "timestamp": "2026-06-01T00:00:02Z",
         "payload": {"type": "function_call", "name": "shell", "call_id": "c1", "arguments": "{}"}},
        {"type": "response_item", "timestamp": "2026-06-01T00:00:03Z",
         "payload": {"type": "function_call_output", "call_id": "c1", "output": _CODEX_TOOL_LEAK}},
        {"type": "response_item", "timestamp": "2026-06-01T00:00:04Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": _TYPED_2}]}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_codex_function_call_output_is_role_tool_and_never_a_prompt(tmp_path):
    session_path = tmp_path / "rollout-1.jsonl"
    _write_codex_session(session_path)

    record = parse_codex_session(session_path)
    assert record is not None, "the Codex session failed to parse"

    # The parser must label tool output role="tool" — the mapping the role gate
    # relies on. A regression to role="user" here is exactly what leaks.
    tool_msgs = [m for m in record.messages if _CODEX_TOOL_LEAK in (m.text or "")]
    assert tool_msgs, "the function_call_output didn't parse into a message at all"
    assert all(m.role == "tool" for m in tool_msgs), (
        "Codex function_call_output was NOT labeled role='tool' "
        f"(got {[m.role for m in tool_msgs]}) — relabeling it role='user' would leak "
        "raw tool output into the lens past the user-prompt gate"
    )
    # And the production gate keeps it (and only it) out: only the two typed turns.
    prompts = [m.text for m in record.messages if _is_user_facing_prompt(m)]
    assert prompts == [_TYPED_1, _TYPED_2], f"unexpected user prompts from Codex: {prompts!r}"
    assert _CODEX_TOOL_LEAK not in "\n".join(prompts), "Codex tool output leaked as a user prompt"


_CLAUDEAI_ASSISTANT_LEAK = "CLAUDEAI_ASSISTANT_LEAKED let me think about that"
_CLAUDEAI_TOOL_LEAK = "CLAUDEAI_TOOL_OUTPUT_LEAKED 42 rows returned"


def _write_claude_ai_export(path: Path) -> None:
    # Claude.ai's parser maps role by a BINARY sender check: human → user, every
    # other sender → assistant (then the role gate drops it). That map IS the
    # load-bearing step — unlike ChatGPT, Claude.ai doesn't inject hidden
    # human-role nodes, so the sender map is the whole defense. Inverting it (or
    # mapping an assistant turn to user) would leak Claude's responses + tool
    # output into the lens, and the role gate wouldn't help (it'd be role=user).
    conv = {
        "uuid": "u1", "name": "Cache design",
        "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-06-01T00:01:00Z",
        "chat_messages": [
            {"sender": "human", "created_at": "t0",
             "content": [{"type": "text", "text": _TYPED_1}]},
            {"sender": "assistant", "created_at": "t1", "content": [
                {"type": "text", "text": _CLAUDEAI_ASSISTANT_LEAK},
                {"type": "tool_use", "name": "repl", "id": "tu1", "input": {"code": "q"}},
                {"type": "tool_result", "tool_use_id": "tu1",
                 "content": [{"type": "text", "text": _CLAUDEAI_TOOL_LEAK}]}]},
            {"sender": "human", "created_at": "t2",
             "content": [{"type": "text", "text": _TYPED_2}]},
        ],
    }
    path.write_text(json.dumps([conv]), encoding="utf-8")


def test_claude_ai_assistant_and_tool_text_never_become_prompts(tmp_path):
    export_path = tmp_path / "conversations.json"
    _write_claude_ai_export(export_path)

    sessions = list(parse_claude_ai_export(export_path))
    assert len(sessions) == 1, "the Claude.ai export failed to parse"
    record = sessions[0]

    # The sender map must label the assistant turn role="assistant" (the gate's
    # input). A regression to role="user" is what leaks Claude's own replies.
    assistant_msgs = [m for m in record.messages if _CLAUDEAI_ASSISTANT_LEAK in (m.text or "")]
    assert assistant_msgs and all(m.role == "assistant" for m in assistant_msgs), (
        "Claude.ai assistant turn was not labeled role='assistant' — the binary "
        f"sender map regressed (got {[m.role for m in assistant_msgs]})"
    )
    prompts = [m.text for m in record.messages if _is_user_facing_prompt(m)]
    assert prompts == [_TYPED_1, _TYPED_2], f"unexpected Claude.ai user prompts: {prompts!r}"
    blob = "\n".join(prompts)
    assert "CLAUDEAI_ASSISTANT_LEAKED" not in blob and "CLAUDEAI_TOOL_OUTPUT_LEAKED" not in blob, (
        "Claude.ai assistant reply / tool output leaked into the lens as a user prompt"
    )
