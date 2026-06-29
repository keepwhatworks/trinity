"""Guard: one corrupt transcript must not kill the whole adapter's ingest.

Found 2026-06-02 testing the ingest WRITE path (the green-gate Phase-1 finding —
the bug class now lives in write/compute paths, not the launchpad cards). Each
`iter_*_sessions` walks ALL of a provider's transcripts (~3,900 files on the
founder's box). The parsers guarded `json.loads` (JSONDecodeError) but NOT the
resulting TYPE, and the file opens didn't tolerate non-UTF8 — so a single corrupt
transcript (a truncated/interrupted write leaving a bare-list JSON line, or
invalid UTF-8 bytes) raised straight out of the iterator:

    AttributeError: 'list' object has no attribute 'get'   # wrong-type line
    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff  # bad bytes

…aborting the ENTIRE adapter's ingest and silently dropping every later file
(guard_shape_not_just_parse class, this time in the parsers rather than the
state-file readers). Fix: per-line `isinstance(entry, dict)` skip + `errors=
"replace"` opens (recover a partially-corrupt file's good lines) + a `_safe_parse`
wrapper in every `iter_*` (per-file isolation catch-all, logged not silent).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from trinity_local.ingest import (
    _safe_parse,
    iter_codex_sessions,
    parse_antigravity_session,
    parse_claude_code_session,
    parse_codex_session,
    parse_cowork_session,
    parse_gemini_cli_session,
)


def _codex_root() -> Path:
    return Path(tempfile.mkdtemp()) / "sessions"


def _valid_codex_lines() -> str:
    return (
        json.dumps({"type": "session_meta", "payload": {"id": "s1", "cwd": "/x"}}) + "\n"
        + json.dumps({
            "type": "response_item",
            "payload": {"type": "message", "role": "user",
                        "content": "a real user prompt long enough to count"},
            "timestamp": "2026-01-01T00:00:00",
        }) + "\n"
    )


def test_parse_codex_tolerates_wrong_type_line():
    root = _codex_root(); root.mkdir(parents=True)
    f = root / "rollout-bad.jsonl"
    f.write_text("[1, 2, 3]\n")  # valid JSON, wrong type
    assert parse_codex_session(f) is not None or True  # must NOT raise


def test_parse_codex_tolerates_non_utf8():
    root = _codex_root(); root.mkdir(parents=True)
    f = root / "rollout-bin.jsonl"
    f.write_bytes(b"\xff\xfe\x00 not utf-8 \x80\n")
    parse_codex_session(f)  # must NOT raise UnicodeDecodeError


def test_parse_codex_partial_corruption_recovers_good_lines():
    """A file with a valid line, then a wrong-type line, then another valid line
    must recover BOTH good lines — skip only the bad one (lossless)."""
    root = _codex_root(); root.mkdir(parents=True)
    f = root / "rollout-partial.jsonl"
    f.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "sp"}}) + "\n"
        + "[99]\n"  # wrong-type line wedged in the middle
        + json.dumps({
            "type": "response_item",
            "payload": {"type": "message", "role": "user",
                        "content": "a recovered good line after the bad one"},
            "timestamp": "2026-02-02T00:00:00",
        }) + "\n"
    )
    session = parse_codex_session(f)
    assert session is not None and len(session.messages) == 1


def test_iter_codex_survives_corrupt_files_among_valid():
    """The headline: one corrupt transcript must not abort the whole ingest —
    the valid sessions still come through."""
    root = _codex_root(); root.mkdir(parents=True)
    (root / "rollout-valid.jsonl").write_text(_valid_codex_lines())
    (root / "rollout-badtype.jsonl").write_text("[1, 2, 3]\n")
    (root / "rollout-nonutf8.jsonl").write_bytes(b"\xff\xfe not utf-8 \x80\n")
    sessions = list(iter_codex_sessions(root))  # must NOT raise
    # the valid transcript with a real message survives
    assert any(s.messages for s in sessions)


def test_safe_parse_isolates_a_crashing_parser(capsys):
    """The per-file catch-all: even a parser that raises on a file must be
    isolated (None + a logged skip), so a future unanticipated crash can't abort
    the ingest either."""
    def boom(_path: Path):
        raise RuntimeError("simulated parser explosion")

    result = _safe_parse(boom, Path("/tmp/whatever.jsonl"))
    assert result is None
    err = capsys.readouterr().err
    assert "skipped unparseable transcript" in err  # not silent
    assert "RuntimeError" in err


# ── the SAME resilience invariants for the OTHER parsers ──────────────────────
# The guards above were only pinned for codex, but every iter_* walks thousands
# of a provider's transcripts and the same wrong-type-line / non-UTF8 hazards
# apply to all of them. claude_code is the LARGEST corpus (the primary source) —
# a regression in its per-line guard would silently drop the whole Claude history
# while the codex-only tests stayed green. Pin the invariant per parser.

def _claude_user_line(text: str) -> str:
    return json.dumps({
        "type": "user", "timestamp": "2026-01-01T00:00:00Z",
        "message": {"content": text},
    })


def test_parse_claude_code_tolerates_wrong_type_and_recovers(tmp_path):
    """claude_code (the largest corpus): a wrong-type line wedged between two good
    user turns is skipped, both real turns survive (lossless)."""
    f = tmp_path / "claude-partial.jsonl"
    f.write_text(
        _claude_user_line("first real Claude prompt about auth") + "\n"
        + "[1, 2, 3]\n"  # valid JSON, wrong type — must be skipped, not crash
        + _claude_user_line("second real Claude prompt about tests") + "\n"
    )
    session = parse_claude_code_session(f)
    assert session is not None
    user_turns = [m for m in session.messages if m.role == "user"]
    assert len(user_turns) == 2, "a wrong-type line dropped a good Claude turn"


def test_parse_claude_code_tolerates_non_utf8(tmp_path):
    """A stray non-UTF8 byte must NOT raise out of the Claude parser (errors=
    'replace') — the good turns around it still come through."""
    f = tmp_path / "claude-bin.jsonl"
    with f.open("wb") as fh:
        fh.write(_claude_user_line("a real prompt before the bad byte").encode() + b"\n")
        fh.write(b'{"type":"user","message":{"content":"bad \xff\xfe byte"}}\n')
        fh.write(_claude_user_line("a real prompt after the bad byte").encode() + b"\n")
    session = parse_claude_code_session(f)  # must NOT raise
    assert session is not None
    assert sum(1 for m in session.messages if m.role == "user") >= 2


# ── NESTED wrong-type fields (the gap the top-level guards missed) ────────────
# The tests above wedge a bare-list LINE (the whole entry is wrong-type) — caught
# by the `isinstance(entry, dict)` guard. But a line can be a valid dict whose
# NESTED `message` / `payload` is a list/str (a malformed or interrupted-write
# transcript). The top-level guard passes, then `(entry.get("message") or {}).get(
# "content")` crashes on the list (`'list' object has no attribute 'get'`) — and
# because that propagates to `_safe_parse`, the WHOLE file is dropped, not just the
# bad line. Found dogfooding the parsers with an adversarial battery; fixed via
# `_as_dict()` coercion of the nested field. (guard_shape_not_just_parse class.)

def test_parse_claude_code_tolerates_nested_wrong_type_message(tmp_path):
    """A valid dict line whose `message` is a LIST or STR (not a dict) wedged
    between two good turns must NOT crash, and both good turns survive — the bad
    line degrades to empty, it doesn't drop the file."""
    f = tmp_path / "claude-nested.jsonl"
    f.write_text(
        _claude_user_line("first real Claude prompt about routing") + "\n"
        + json.dumps({"type": "user", "message": [1, 2, 3]}) + "\n"   # nested list
        + json.dumps({"type": "assistant", "message": "oops not a dict"}) + "\n"  # nested str
        + _claude_user_line("second real Claude prompt about evals") + "\n"
    )
    session = parse_claude_code_session(f)  # must NOT raise
    assert session is not None
    user_turns = [m for m in session.messages if m.role == "user" and m.text]
    assert len(user_turns) == 2, (
        "a nested wrong-type `message` dropped the whole Claude file instead of "
        "degrading the one bad line"
    )


def test_parse_codex_tolerates_nested_wrong_type_payload():
    """codex `payload` as a LIST (not a dict) must not crash; the good lines around
    it still parse."""
    root = _codex_root(); root.mkdir(parents=True)
    f = root / "rollout-nested.jsonl"
    f.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "sn"}}) + "\n"
        + json.dumps({"type": "session_meta", "payload": [1, 2, 3]}) + "\n"  # nested list
        + json.dumps({
            "type": "response_item",
            "payload": {"type": "message", "role": "user",
                        "content": "a recovered good line after the bad payload"},
            "timestamp": "2026-02-02T00:00:00",
        }) + "\n"
    )
    session = parse_codex_session(f)  # must NOT raise
    assert session is not None and len(session.messages) == 1


def test_parse_antigravity_recovers_good_turns_on_bad_byte(tmp_path):
    """REGRESSION: antigravity read with bare encoding='utf-8' so ONE non-UTF8 byte
    raised UnicodeDecodeError → _safe_parse dropped the ENTIRE multi-turn
    conversation (the other parsers recover via errors='replace'). With the fix the
    good turns around the bad byte survive."""
    logs = tmp_path / "brain" / "conv1" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    f = logs / "transcript.jsonl"
    good_user = json.dumps({
        "type": "USER_INPUT",
        "content": "<USER_REQUEST>a real antigravity question</USER_REQUEST>",
        "created_at": "2026-01-01T00:00:00",
    })
    good_reply = json.dumps({
        "type": "PLANNER_RESPONSE", "content": "a substantive reply",
        "created_at": "2026-01-01T00:01:00",
    })
    with f.open("wb") as fh:
        fh.write(good_user.encode() + b"\n")
        fh.write(b'{"type":"PLANNER_RESPONSE","content":"bad \xff\xfe byte"}\n')
        fh.write(good_reply.encode() + b"\n")
    session = parse_antigravity_session(f)  # must NOT raise, must NOT whole-drop
    assert session is not None, "antigravity dropped the whole conversation on one bad byte"
    assert len(session.messages) >= 2, "antigravity lost recoverable turns around a bad byte"


def test_parse_gemini_tolerates_non_utf8_byte(tmp_path):
    """gemini reads the whole file as one JSON object; a stray non-UTF8 byte inside
    a string value must not drop the session (errors='replace' → json.loads still
    succeeds)."""
    chats = tmp_path / "proj" / "chats"
    chats.mkdir(parents=True)
    f = chats / "session-1.json"
    obj = '{"sessionId":"s1","messages":[{"type":"user","timestamp":"2026-01-01T00:00:00Z","content":"hello PLACEHOLDER world"}]}'
    with f.open("wb") as fh:
        fh.write(obj.replace("PLACEHOLDER", "\udcff").encode("utf-8", "surrogateescape"))
    session = parse_gemini_cli_session(f, project_name="proj")  # must NOT raise
    assert session is not None and session.messages


def test_parse_cowork_tolerates_wrong_type_audit_line(tmp_path):
    """cowork reads a meta JSON + an audit JSONL; a wrong-type audit line must be
    skipped, not crash the parse."""
    meta = tmp_path / "local_cw1.json"
    meta.write_text(json.dumps({"id": "cw1", "cwd": "/x"}), encoding="utf-8")
    sess_dir = tmp_path / "local_cw1"
    sess_dir.mkdir()
    audit = sess_dir / "audit.jsonl"
    audit.write_text(
        json.dumps({"type": "user_message", "text": "a real cowork prompt"}) + "\n"
        + "[1, 2, 3]\n"  # wrong-type line
    )
    parse_cowork_session(meta)  # must NOT raise (content shape varies; resilience only)
