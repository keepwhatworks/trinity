"""PromptTurn / PromptNode carry the model + effort that answered each turn.

Before 2026-07-31 the corpus was a MIX of Haiku, Fable and Opus turns with no
way to tell them apart. Measured baseline on the live store that day:
53,427 nodes in ~/.trinity/prompts/prompt_nodes.jsonl, 0 carrying a model,
0 carrying an effort — the keys did not exist in the record at all — even
though every raw record except gemini.google.com's carries one. Model
attribution is the cheapest handle on machine-text provenance — the
embedding-based machine/human classifier measured FNR 0.415 against unseen
generators, i.e. not usable as a wall — so the field has to be real.

Those 53,427 nodes do NOT gain a model retroactively: the index is
append-only and the fields fill going forward, on re-ingested turns only.
`test_legacy_prompt_node_json_loads_without_model_or_effort` is what keeps
them loadable in the meantime.

Every fixture here is the SHAPE observed on the live corpus (paths verified
2026-07-31), not an invented one. The two things this file defends:

  1. the field is POPULATED from the raw record on every surface that has one;
  2. the field is REFUSED — left None — where the provider records nothing, or
     records a word that only looks like an effort.

(2) is the load-bearing half. gemini.google.com has no model or effort field
anywhere in its payload; a parser that guessed "gemini-3-pro" there would look
like 100% coverage and be 100% fabricated.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.ingest import (
    _attributed_identity,
    iter_prompt_turns,
    parse_captured_chatgpt_conversation,
    parse_captured_claude_conversation,
    parse_captured_gemini_conversation,
    parse_antigravity_session,
    parse_claude_code_session,
    parse_codex_session,
)
from trinity_local.memory.schemas import PromptNode
from trinity_local.model_identity import is_known_effort, known_efforts, parse_identity
from trinity_local.session_schema import PromptTurn, SessionMessage, SessionRecord


# ---------------------------------------------------------------------------
# Surface 1 — claude code JSONL
# ---------------------------------------------------------------------------
# Live shape: `message.model` on the assistant entry, `effort` as a TOP-LEVEL
# sibling of `message` (NOT nested inside it). Verified 2026-07-31.

def _claude_code_file(tmp_path, lines):
    p = tmp_path / "sess-abc.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return p


CLAUDE_CODE_LINES = [
    {"type": "user", "timestamp": "2026-07-30T10:00:00Z",
     "message": {"content": "how should the ledger key on effort?"}},
    {"type": "assistant", "timestamp": "2026-07-30T10:00:09Z", "effort": "xhigh",
     "message": {"model": "claude-opus-5",
                 "content": [{"type": "text", "text": "Key it on model x version."}]}},
]


def test_claude_code_turn_carries_model_and_effort(tmp_path):
    session = parse_claude_code_session(_claude_code_file(tmp_path, CLAUDE_CODE_LINES))
    turns = list(iter_prompt_turns(session))
    assert len(turns) == 1
    assert turns[0].model == "claude-opus-5"
    assert turns[0].effort == "xhigh"


def test_claude_code_effort_is_read_from_the_top_level_not_the_message(tmp_path):
    """The exact bug an implementer would ship: reading message.effort.

    On the live corpus `effort` is NEVER inside `message` — a parser that
    looked there would return None on all 17,282 entries that have one while the
    model field kept working, which reads as "this surface has no effort".
    """
    lines = [
        CLAUDE_CODE_LINES[0],
        {"type": "assistant", "timestamp": "2026-07-30T10:00:09Z",
         "message": {"model": "claude-opus-5", "effort": "xhigh",
                     "content": [{"type": "text", "text": "answer"}]}},
    ]
    turns = list(iter_prompt_turns(parse_claude_code_session(_claude_code_file(tmp_path, lines))))
    assert turns[0].model == "claude-opus-5"
    assert turns[0].effort is None, (
        "effort nested inside `message` is not the live shape; reading it there "
        "would mean the top-level read is untested"
    )


def test_claude_code_synthetic_turn_contributes_no_identity(tmp_path):
    """`<synthetic>` is harness output, not a model answer — drop the PAIR."""
    lines = [
        CLAUDE_CODE_LINES[0],
        {"type": "assistant", "timestamp": "2026-07-30T10:00:09Z", "effort": "xhigh",
         "message": {"model": "<synthetic>",
                     "content": [{"type": "text", "text": "Caveat: ..."}]}},
    ]
    turns = list(iter_prompt_turns(parse_claude_code_session(_claude_code_file(tmp_path, lines))))
    assert turns[0].model is None
    assert turns[0].effort is None, "a synthetic turn must not donate an orphan effort"


def test_claude_code_synthetic_message_keeps_no_orphan_effort(tmp_path):
    """Asserted on the MESSAGE, not the turn.

    The turn-level test above passes either way: a `<synthetic>` message
    already gets model=None, and `_attributed_identity` skips any message
    without a model, so an orphan effort left on it never reaches a turn
    TODAY. That makes the turn-level assertion unable to see this mechanism
    (mutation-checked 2026-07-31: deleting the synthetic effort-drop left the
    whole file green). SessionMessage.effort is a public field other readers
    can pick up, so the half-identity is refused where it is written.
    """
    lines = [
        CLAUDE_CODE_LINES[0],
        {"type": "assistant", "timestamp": "2026-07-30T10:00:09Z", "effort": "xhigh",
         "message": {"model": "<synthetic>",
                     "content": [{"type": "text", "text": "Caveat: ..."}]}},
    ]
    session = parse_claude_code_session(_claude_code_file(tmp_path, lines))
    synthetic = [m for m in session.messages if m.role == "assistant"]
    assert len(synthetic) == 1, "fixture must produce the synthetic message"
    assert synthetic[0].model is None
    assert synthetic[0].effort is None, "no model, so no effort — a half-identity is not an identity"
    assert session.effort is None, "and it must not become the session-level effort either"


def test_claude_code_model_without_effort_yields_effort_none(tmp_path):
    """Most of the corpus: a model, no effort. Unknown, never defaulted."""
    lines = [
        CLAUDE_CODE_LINES[0],
        {"type": "assistant", "timestamp": "2026-07-30T10:00:09Z",
         "message": {"model": "claude-haiku-4-5-20251001",
                     "content": [{"type": "text", "text": "answer"}]}},
    ]
    turns = list(iter_prompt_turns(parse_claude_code_session(_claude_code_file(tmp_path, lines))))
    assert turns[0].model == "claude-haiku-4-5-20251001"
    assert turns[0].effort is None


# ---------------------------------------------------------------------------
# Surface 2 — codex rollout JSONL
# ---------------------------------------------------------------------------
# Live shape: `turn_context` payload carries BOTH model and effort as running
# state; assistant messages arrive later as `response_item`s with neither.

CODEX_LINES = [
    {"type": "session_meta", "timestamp": "2026-07-30T11:00:00Z",
     "payload": {"id": "roll-1", "cwd": "/tmp", "cli_version": "0.144.0"}},
    {"type": "turn_context", "timestamp": "2026-07-30T11:00:01Z",
     "payload": {"model": "gpt-5.5", "effort": "xhigh",
                 "collaboration_mode": {"settings": {"reasoning_effort": "xhigh"}}}},
    {"type": "response_item", "timestamp": "2026-07-30T11:00:02Z",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "which model should chair?"}]}},
    {"type": "response_item", "timestamp": "2026-07-30T11:00:20Z",
     "payload": {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "A fixed strong judge."}]}},
]


def _codex_file(tmp_path, lines, name="rollout-1.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return p


def test_codex_turn_carries_model_and_effort(tmp_path):
    turns = list(iter_prompt_turns(parse_codex_session(_codex_file(tmp_path, CODEX_LINES))))
    assert len(turns) == 1
    assert turns[0].model == "gpt-5.5"
    assert turns[0].effort == "xhigh"


def test_codex_reads_the_nested_reasoning_effort_when_payload_effort_absent(tmp_path):
    lines = [dict(x) for x in CODEX_LINES]
    lines[1] = {"type": "turn_context", "timestamp": "2026-07-30T11:00:01Z",
                "payload": {"model": "gpt-5.6",
                            "collaboration_mode": {"settings": {"reasoning_effort": "high"}}}}
    turns = list(iter_prompt_turns(parse_codex_session(_codex_file(tmp_path, lines))))
    assert turns[0].model == "gpt-5.6"
    assert turns[0].effort == "high"


def test_codex_turn_context_is_running_state_not_retroactive(tmp_path):
    """A mid-session model switch must not be back-applied to earlier turns."""
    lines = [
        CODEX_LINES[0], CODEX_LINES[1], CODEX_LINES[2], CODEX_LINES[3],
        {"type": "turn_context", "timestamp": "2026-07-30T11:05:00Z",
         "payload": {"model": "gpt-5.6", "effort": "medium"}},
        {"type": "response_item", "timestamp": "2026-07-30T11:05:01Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "and now with less effort?"}]}},
        {"type": "response_item", "timestamp": "2026-07-30T11:05:09Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Same answer."}]}},
    ]
    turns = list(iter_prompt_turns(parse_codex_session(_codex_file(tmp_path, lines))))
    assert len(turns) == 2
    assert (turns[0].model, turns[0].effort) == ("gpt-5.5", "xhigh")
    assert (turns[1].model, turns[1].effort) == ("gpt-5.6", "medium")


# ---------------------------------------------------------------------------
# Surface 3 — claude.ai browser capture
# ---------------------------------------------------------------------------
# Live shape: conversation-level `model` + `settings.effort_level`. NO
# chat_message carries a model key on any of the 152 live captures.

def _browser_claude_file(tmp_path, conv):
    p = tmp_path / "conv-1.json"
    p.write_text(json.dumps(conv), encoding="utf-8")
    return p


BROWSER_CLAUDE_CONV = {
    "uuid": "conv-browser-1",
    "name": "Effort split",
    "model": "claude-opus-4-8",
    "settings": {"effort_level": "max", "thinking_mode": "auto"},
    "effective_thinking_mode": "auto",
    "chat_messages": [
        {"uuid": "m1", "sender": "human", "created_at": "2026-07-30T09:00:00Z",
         "text": "does effort change the ranking?",
         "content": [{"type": "text", "text": "does effort change the ranking?"}]},
        {"uuid": "m2", "sender": None, "created_at": "2026-07-30T09:00:30Z",
         "text": "It split GPT-5.5 at 69 vs 48.",
         "content": [{"type": "text", "text": "It split GPT-5.5 at 69 vs 48."}]},
    ],
}


def test_browser_claude_turn_carries_conversation_level_pair(tmp_path):
    session = parse_captured_claude_conversation(_browser_claude_file(tmp_path, BROWSER_CLAUDE_CONV))
    turns = list(iter_prompt_turns(session))
    assert len(turns) == 1
    assert turns[0].model == "claude-opus-4-8"
    assert turns[0].effort == "max"


def test_browser_claude_stamps_the_pair_on_the_assistant_message(tmp_path):
    """Asserted on the MESSAGE, for the same reason as the synthetic case.

    `_attributed_identity` falls back to the session-level pair, which
    claude.ai also fills — so the turn-level test above stays green even if
    the per-message stamp is deleted (mutation-checked 2026-07-31). The stamp
    is what makes SessionMessage self-describing for every other reader.
    """
    session = parse_captured_claude_conversation(_browser_claude_file(tmp_path, BROWSER_CLAUDE_CONV))
    by_role = {m.role: m for m in session.messages}
    assert set(by_role) == {"user", "assistant"}, "fixture must produce both roles"
    assert (by_role["assistant"].model, by_role["assistant"].effort) == ("claude-opus-4-8", "max")
    assert (by_role["user"].model, by_role["user"].effort) == (None, None), (
        "the user did not answer anything; stamping the model on their turn "
        "would attribute the human's words to a model"
    )


def test_browser_claude_thinking_mode_is_not_stored_as_an_effort(tmp_path):
    """The category error this surface invites.

    `thinking_mode` / `effective_thinking_mode` are auto|extended — a
    different axis from the low..max effort ladder, and the ONLY effort-ish
    field on the 43 live conversations that lack `effort_level`. Copying it
    into `effort` would turn 43 unknowns into 43 rows that count as covered
    and parse to "?" anyway.
    """
    conv = dict(BROWSER_CLAUDE_CONV)
    conv["settings"] = {"thinking_mode": "extended"}
    conv["effective_thinking_mode"] = "extended"
    session = parse_captured_claude_conversation(_browser_claude_file(tmp_path, conv))
    turns = list(iter_prompt_turns(session))
    assert turns[0].model == "claude-opus-4-8"
    assert turns[0].effort is None, (
        "'extended' is not an effort level; storing it fakes effort coverage"
    )


# ---------------------------------------------------------------------------
# Surface 4 — chatgpt.com browser capture
# ---------------------------------------------------------------------------
# Live shape: per-message `mapping.<uuid>.message.metadata.model_slug`, and a
# per-message `thinking_effort` whose vocabulary is mostly NOT effort levels.

def _browser_chatgpt_file(tmp_path, conv):
    p = tmp_path / "gpt-1.json"
    p.write_text(json.dumps(conv), encoding="utf-8")
    return p


def _gpt_conv(assistant_meta):
    return {
        "conversation_id": "gpt-conv-1",
        "title": "Router",
        "current_node": "n2",
        "mapping": {
            "n1": {"id": "n1", "parent": None, "message": {
                "author": {"role": "user"}, "create_time": 1785000000.0,
                "content": {"content_type": "text", "parts": ["is the router dead?"]},
                "metadata": {}}},
            "n2": {"id": "n2", "parent": "n1", "message": {
                "author": {"role": "assistant"}, "create_time": 1785000030.0,
                "content": {"content_type": "text", "parts": ["Killed four times."]},
                "metadata": assistant_meta}},
        },
    }


def test_browser_chatgpt_turn_carries_model_slug(tmp_path):
    conv = _gpt_conv({"model_slug": "gpt-5-5-thinking", "thinking_effort": "xhigh"})
    turns = list(iter_prompt_turns(parse_captured_chatgpt_conversation(
        _browser_chatgpt_file(tmp_path, conv))))
    assert len(turns) == 1
    assert turns[0].model == "gpt-5-5-thinking"
    assert turns[0].effort == "xhigh"


def test_browser_chatgpt_model_switch_is_attributed_per_turn(tmp_path):
    """chatgpt records the model PER MESSAGE, and threads really do switch.

    Live corpus 2026-07-31: 7 distinct `model_slug` values across 5,765
    mapping nodes. Falling back to the conversation-level model — the first
    slug seen — would silently relabel every later turn as the earlier model,
    and the turn-level test above cannot see that with one assistant node
    (mutation-checked: deleting the per-message stamp left it green).
    """
    conv = {
        "conversation_id": "gpt-conv-switch",
        "title": "Switch",
        "current_node": "n4",
        "mapping": {
            "n1": {"id": "n1", "parent": None, "message": {
                "author": {"role": "user"}, "create_time": 1785000000.0,
                "content": {"content_type": "text", "parts": ["first question"]},
                "metadata": {}}},
            "n2": {"id": "n2", "parent": "n1", "message": {
                "author": {"role": "assistant"}, "create_time": 1785000010.0,
                "content": {"content_type": "text", "parts": ["first answer"]},
                "metadata": {"model_slug": "gpt-5-5-thinking"}}},
            "n3": {"id": "n3", "parent": "n2", "message": {
                "author": {"role": "user"}, "create_time": 1785000020.0,
                "content": {"content_type": "text", "parts": ["second question"]},
                "metadata": {}}},
            "n4": {"id": "n4", "parent": "n3", "message": {
                "author": {"role": "assistant"}, "create_time": 1785000030.0,
                "content": {"content_type": "text", "parts": ["second answer"]},
                "metadata": {"model_slug": "gpt-5-6-thinking"}}},
        },
    }
    turns = list(iter_prompt_turns(parse_captured_chatgpt_conversation(
        _browser_chatgpt_file(tmp_path, conv))))
    assert len(turns) == 2
    assert turns[0].model == "gpt-5-5-thinking"
    assert turns[1].model == "gpt-5-6-thinking", (
        "the second turn was answered by a different model; the conversation-level "
        "fallback would have called it gpt-5-5-thinking"
    )


@pytest.mark.parametrize("word", ["extended", "standard"])
def test_browser_chatgpt_product_tier_words_are_not_efforts(tmp_path, word):
    """4,816 of 4,819 live thinking_effort values are these two words.

    They are OpenAI product tiers, not points on the low..max ladder. If they
    landed in `effort`, this surface would report ~99% effort coverage that
    parse_identity then throws away — the exact green-over-degenerate-data
    shape this repo keeps re-shipping.
    """
    conv = _gpt_conv({"model_slug": "gpt-5-5-thinking", "thinking_effort": word})
    turns = list(iter_prompt_turns(parse_captured_chatgpt_conversation(
        _browser_chatgpt_file(tmp_path, conv))))
    assert turns[0].model == "gpt-5-5-thinking"
    assert turns[0].effort is None


# ---------------------------------------------------------------------------
# Surface 5 — gemini.google.com browser capture: NOTHING, and it stays nothing
# ---------------------------------------------------------------------------

def test_browser_gemini_turns_carry_no_model_or_effort(tmp_path):
    """gemini.google.com records neither. Verified by exhaustive grep over all
    4,321 live captures 2026-07-31: zero files contain a model / model_slug /
    effort / reasoning_effort / thinking_mode key. Every "model" substring in
    the corpus is prose or unrelated UI strings (a
    `GEMINI_MODEL_SELECTION_MENU_BANNER` feature flag, article titles).

    So the honest output is None, and this test exists to make inventing a
    default cost a red build.
    """
    payload = {
        "provider": "gemini",
        "conv_id": "c_abc123",
        "message_id": "r_def456",
        "user_text": "what does the chairman actually decide?",
        "assistant_text": "Which side of a disputed claim survives.",
        "captured_at": "2026-07-30T12:00:00Z",
    }
    p = tmp_path / "c_abc123__20260730120000000.stream.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    session = parse_captured_gemini_conversation(p)
    assert session is not None, "fixture must parse — a None session would make this vacuous"
    turns = list(iter_prompt_turns(session))
    # Assert the turn EXISTS before asserting its fields are None, or this test
    # passes over an empty list and proves nothing.
    assert len(turns) == 1
    assert turns[0].model is None, "gemini records no model — do not invent one"
    assert turns[0].effort is None, "gemini records no effort — do not invent one"


# ---------------------------------------------------------------------------
# Surface 6 — antigravity (agy) CLI transcript
# ---------------------------------------------------------------------------
# The ONLY model signal agy emits is the <USER_SETTINGS_CHANGE> notice, which
# _antigravity_user_text strips off the user turn. Present on 500/500 live
# transcripts, always at user turn 0.

AGY_NOTICE = (
    "<USER_REQUEST>\nwhich model do I actually trust?\n</USER_REQUEST>\n"
    "<ADDITIONAL_METADATA>\nLocal time: 14:00.\n</ADDITIONAL_METADATA>\n"
    "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from None to "
    "Gemini 3.1 Pro (High). No need to comment on this change.\n</USER_SETTINGS_CHANGE>"
)


def _agy_file(tmp_path, lines):
    d = tmp_path / "conv-x" / ".system_generated" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return p


def test_antigravity_model_recovered_from_settings_notice(tmp_path):
    lines = [
        {"type": "USER_INPUT", "created_at": "2026-07-30T14:00:00Z", "content": AGY_NOTICE},
        {"type": "PLANNER_RESPONSE", "created_at": "2026-07-30T14:00:20Z",
         "content": "Opus 4.8, on your resolved disagreements."},
    ]
    turns = list(iter_prompt_turns(parse_antigravity_session(_agy_file(tmp_path, lines))))
    assert len(turns) == 1
    assert turns[0].model == "Gemini 3.1 Pro (High)"
    # The effort rides inside the model string; parse_identity is what reads it,
    # so there is no second normalisation to keep in sync.
    assert parse_identity(turns[0].model, turns[0].effort).effort == "high"


def test_antigravity_without_a_notice_stays_unknown(tmp_path):
    """The notice fires on CHANGE. Absent means unknown, not "the default"."""
    lines = [
        {"type": "USER_INPUT", "created_at": "2026-07-30T14:00:00Z",
         "content": "<USER_REQUEST>\nplain prompt\n</USER_REQUEST>"},
        {"type": "PLANNER_RESPONSE", "created_at": "2026-07-30T14:00:20Z", "content": "reply"},
    ]
    turns = list(iter_prompt_turns(parse_antigravity_session(_agy_file(tmp_path, lines))))
    assert turns[0].model is None


def test_antigravity_notice_still_stripped_from_the_prompt_text(tmp_path):
    """Reading the notice must not put harness scaffolding back in the lens."""
    lines = [
        {"type": "USER_INPUT", "created_at": "2026-07-30T14:00:00Z", "content": AGY_NOTICE},
        {"type": "PLANNER_RESPONSE", "created_at": "2026-07-30T14:00:20Z", "content": "reply"},
    ]
    turns = list(iter_prompt_turns(parse_antigravity_session(_agy_file(tmp_path, lines))))
    assert turns[0].text == "which model do I actually trust?"
    assert "USER_SETTINGS_CHANGE" not in turns[0].text
    assert "Model Selection" not in turns[0].text


# ---------------------------------------------------------------------------
# Attribution: the pair comes from ONE message
# ---------------------------------------------------------------------------

def _session(messages, **kw):
    return SessionRecord(
        provider="claude", session_id="s1", source_path="/tmp/s1", native_id="s1",
        messages=messages, **kw,
    )


def test_pair_is_never_assembled_from_two_different_messages():
    """model-from-here + effort-from-there mints an identity that never ran.

    The ledger keys on (model, version, effort); a fabricated pair is a
    fabricated ledger row.

    BOTH borrow-sources are armed and set to DIFFERENT values here — the
    earlier assistant turn (effort='low') and the session-level pair
    (effort='max') — because a fixture that leaves them empty passes whether
    or not the borrow happens. That was the first version of this test: the
    mutation `messages[j].effort or session.effort` left it green.
    """
    messages = [
        SessionMessage(role="assistant", text="earlier", model="claude-fable-5", effort="low"),
        SessionMessage(role="user", text="the prompt"),
        SessionMessage(role="assistant", text="the answer", model="claude-opus-5", effort=None),
    ]
    session = _session(messages, model="claude-opus-4-8", effort="max")
    model, effort = _attributed_identity(session, messages, 1)
    assert model == "claude-opus-5"
    assert effort is None, (
        "effort must come from the SAME message as the model — borrowing 'low' "
        "from the earlier fable turn, or 'max' from the session record, would "
        "invent an opus-5 identity that never ran"
    )


def test_attribution_prefers_the_answer_over_the_previous_turn():
    messages = [
        SessionMessage(role="assistant", text="earlier", model="claude-fable-5", effort="low"),
        SessionMessage(role="user", text="the prompt"),
        SessionMessage(role="assistant", text="the answer", model="claude-opus-5", effort="xhigh"),
    ]
    assert _attributed_identity(_session(messages), messages, 1) == ("claude-opus-5", "xhigh")


def test_attribution_falls_back_to_the_previous_answer_then_the_session():
    trailing = [
        SessionMessage(role="assistant", text="earlier", model="claude-fable-5", effort="low"),
        SessionMessage(role="user", text="unanswered trailing prompt"),
    ]
    assert _attributed_identity(_session(trailing), trailing, 1) == ("claude-fable-5", "low")

    bare = [SessionMessage(role="user", text="only a prompt")]
    session = _session(bare, model="claude-opus-4-8", effort="max")
    assert _attributed_identity(session, bare, 0) == ("claude-opus-4-8", "max")


def test_attribution_returns_none_when_nothing_records_a_model():
    messages = [
        SessionMessage(role="user", text="prompt"),
        SessionMessage(role="assistant", text="answer"),
    ]
    assert _attributed_identity(_session(messages), messages, 0) == (None, None)


# ---------------------------------------------------------------------------
# Consumers tolerate None / legacy records
# ---------------------------------------------------------------------------

def test_prompt_turn_and_node_default_to_none():
    turn = PromptTurn(transcript_id="t", provider="claude", source_path="/x",
                      turn_index=0, text="hi")
    assert turn.model is None and turn.effort is None
    node = PromptNode(id="n", transcript_id="t", provider="claude", source_path="/x",
                      turn_index=0, text="hi", embedding=[], created_at="2026-07-30T00:00:00Z")
    assert node.model is None and node.effort is None


def test_legacy_prompt_node_json_loads_without_model_or_effort():
    """Every node written before 2026-07-31 lacks these keys."""
    legacy = {
        "id": "n1", "transcript_id": "t1", "provider": "claude",
        "source_path": "/x", "turn_index": 0, "text": "hi",
        "embedding": [0.1, 0.2], "created_at": "2026-01-01T00:00:00Z",
        "preceding_assistant_text": "", "following_assistant_text": "",
    }
    node = PromptNode.from_dict(legacy)
    assert node.model is None and node.effort is None
    assert node.to_dict()["model"] is None


def test_prompt_node_round_trips_model_and_effort():
    node = PromptNode(id="n", transcript_id="t", provider="codex", source_path="/x",
                      turn_index=0, text="hi", embedding=[], created_at="2026-07-30T00:00:00Z",
                      model="gpt-5.5", effort="xhigh")
    assert PromptNode.from_dict(node.to_dict()).model == "gpt-5.5"
    assert PromptNode.from_dict(node.to_dict()).effort == "xhigh"


def test_parse_identity_tolerates_none_on_both_legs():
    """The gemini path, end to end: no model, no effort, no exception, no guess."""
    identity = parse_identity(None, None)
    assert identity.family == "?" and identity.tier == "?"
    assert identity.version == "?" and identity.effort == "?"
    assert identity.is_full is False


# ---------------------------------------------------------------------------
# The effort vocabulary is one source of truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["low", "medium", "high", "xhigh", "max", "XHIGH", " High "])
def test_is_known_effort_accepts_the_ladder(word):
    assert is_known_effort(word)


@pytest.mark.parametrize("word", ["extended", "standard", "auto", "", None, "generic", "true"])
def test_is_known_effort_refuses_everything_else(word):
    assert not is_known_effort(word)


def test_known_efforts_is_what_parse_identity_actually_parses():
    """The gate and the parser must not drift apart — a gate that accepted a
    word parse_identity then dropped would be a green over degenerate data.
    """
    for word in known_efforts():
        assert parse_identity("claude-opus-5", word).effort == word
