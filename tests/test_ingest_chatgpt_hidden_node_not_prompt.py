"""Privacy + data-purity regression: ChatGPT-export HIDDEN nodes (custom
instructions / memory / system setup) must never become user prompts.

Bug found 2026-06-09 dogfooding the chatgpt.com export parser
(`parse_chatgpt_export`, a moat web-capture source). ChatGPT's `conversations.json`
mapping injects context the user never typed — custom instructions, memory, system
setup — as nodes its OWN UI hides (`metadata.is_visually_hidden_from_conversation`,
`metadata.is_user_system_message`). Crucially MANY carry author.role == "user", so
the downstream `_is_user_facing_prompt` role gate doesn't stop them. The parser only
keyed on author.role, so a hidden role=user node — typically the user's custom
instructions, which routinely hold PII (their name, profession, preferences) —
sailed straight into the lens corpus.

Impact was twofold and exactly the failure modes this project guards hardest:
  * data poison — the lens trains on injected non-authored tokens as if they were
    the user's voice (the #260 do-operator invariant);
  * PII leak — personal context flows into the lens and every surface it feeds
    (memory viewer, share cards), the "privacy nightmare" the product must avoid.

Fix: `_chatgpt_conversation_dict_to_session` skips any node whose metadata sets
`is_visually_hidden_from_conversation` or `is_user_system_message`. None of those
nodes is part of the VISIBLE conversation, so dropping them matches exactly what
the user saw and typed.

This builds a realistic export — a hidden role=user custom-instructions node (with a
PII marker) interleaved with two genuinely-typed prompts, plus assistant + tool
nodes — and asserts ONLY the two typed prompts survive the production gate.
Mutation-proven: reverting the hidden-node skip leaks CUSTOM_INSTRUCTIONS_PII back
into the prompts → reds.
"""
from __future__ import annotations

import json
from pathlib import Path

from trinity_local.ingest import _is_user_facing_prompt, parse_chatgpt_export

_PII = "CUSTOM_INSTRUCTIONS_PII The user is a tax attorney named Pat who prefers terse answers."
_TYPED_1 = "How do I defer the capital gain on this sale?"
_TYPED_2 = "What about a QOZ instead?"
_ASSISTANT = "CHATGPT_ASSISTANT_REPLY use a 1031 exchange"
_TOOL = "CHATGPT_TOOL_OUTPUT browsing result blob"


def _node(nid, role, text, parent, children, *, hidden=False, user_system=False):
    meta = {}
    if hidden:
        meta["is_visually_hidden_from_conversation"] = True
    if user_system:
        meta["is_user_system_message"] = True
    return nid, {
        "id": nid, "parent": parent, "children": children,
        "message": {
            "author": {"role": role}, "create_time": 1717200000.0,
            "content": {"content_type": "text", "parts": [text]}, "metadata": meta,
        },
    }


def _write_export(path: Path) -> None:
    mapping = dict([
        _node("root", "system", "", None, ["n1"]),
        # The trap: a HIDDEN role=user node — ChatGPT's injected custom
        # instructions (PII), which the role gate alone would let through.
        _node("n1", "user", _PII, "root", ["n2"], hidden=True, user_system=True),
        _node("n2", "user", _TYPED_1, "n1", ["n3"]),
        _node("n3", "assistant", _ASSISTANT, "n2", ["n4"]),
        _node("n4", "tool", _TOOL, "n3", ["n5"]),
        _node("n5", "user", _TYPED_2, "n4", []),
    ])
    conv = {
        "conversation_id": "c1", "title": "Tax", "current_node": "n5",
        "create_time": 1717200000.0, "update_time": 1717200100.0, "mapping": mapping,
    }
    path.write_text(json.dumps([conv]), encoding="utf-8")


def test_chatgpt_hidden_user_node_never_becomes_a_prompt(tmp_path):
    export_path = tmp_path / "conversations.json"
    _write_export(export_path)

    sessions = list(parse_chatgpt_export(export_path))
    assert len(sessions) == 1, "the ChatGPT export failed to parse"
    record = sessions[0]

    prompts = [m.text for m in record.messages if _is_user_facing_prompt(m)]
    assert prompts == [_TYPED_1, _TYPED_2], (
        f"ChatGPT user-facing prompts are not exactly the two typed turns: {prompts!r}"
    )
    blob = "\n".join(prompts)
    assert "CUSTOM_INSTRUCTIONS_PII" not in blob, (
        "ChatGPT custom-instructions PII (a hidden role=user node) leaked into the "
        "lens corpus as a user prompt — data poison + a personal-data leak"
    )
    assert "CHATGPT_ASSISTANT_REPLY" not in blob and "CHATGPT_TOOL_OUTPUT" not in blob, (
        "assistant / tool text leaked as a user prompt"
    )
