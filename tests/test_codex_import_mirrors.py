"""Codex's imported copies of Claude sessions are not Codex usage.

Codex imports Claude Code transcripts into its own thread store as ordinary
rollout files. Measured 2026-09-26: 8,428 of 10,164 prompt nodes filed under
`codex` (82%) were those mirrors -- the founder's Claude conversations ingested
twice and labelled as a second lab, which is the exact shape of the
cross-provider pairs the deep build mines.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local import ingest
from trinity_local.ingest import parse_codex_session

THREAD = "01a08dc2-2ae7-74d3-b7fb-ea47d81c3456"


def _rollout(home: Path, thread: str, *, marker: bool = False, prompt: str = "hello") -> Path:
    d = home / "sessions" / "2026" / "09" / "10"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-09-10T19-58-27-{thread}.jsonl"
    rows = [
        {"timestamp": "2026-09-10T23:58:27Z", "type": "session_meta",
         "payload": {"id": thread, "cwd": "/tmp/x", "model_provider": "openai"}},
        {"timestamp": "2026-09-10T23:58:28Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": prompt}},
        {"timestamp": "2026-09-10T23:58:28Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": prompt}]}},
        {"timestamp": "2026-09-10T23:58:29Z", "type": "event_msg",
         "payload": {"type": "agent_message",
                     "message": "<EXTERNAL SESSION IMPORTED>" if marker else "an answer"}},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def _manifest(home: Path, *threads: str) -> None:
    (home / "external_agent_session_imports.json").write_text(json.dumps(
        {"records": [{"imported_thread_id": t, "source_path": "/x.jsonl"} for t in threads]}))


def setup_function(_):
    ingest._imported_cache.clear()


def test_a_native_codex_session_is_ingested(tmp_path):
    f = _rollout(tmp_path, "01a0ffff-0000-7000-8000-000000000001")
    assert parse_codex_session(f) is not None, "the filter must not eat real Codex usage"


def test_a_thread_in_the_import_manifest_is_skipped(tmp_path):
    f = _rollout(tmp_path, THREAD)
    _manifest(tmp_path, THREAD)
    assert parse_codex_session(f) is None


def test_the_in_file_marker_is_the_fallback_without_a_manifest(tmp_path):
    f = _rollout(tmp_path, THREAD, marker=True)
    assert parse_codex_session(f) is None


def test_a_manifest_naming_other_threads_leaves_this_one_alone(tmp_path):
    f = _rollout(tmp_path, "01a0ffff-0000-7000-8000-000000000002")
    _manifest(tmp_path, THREAD)
    assert parse_codex_session(f) is not None


def test_a_corrupt_manifest_skips_nothing_rather_than_crashing(tmp_path):
    f = _rollout(tmp_path, THREAD)
    (tmp_path / "external_agent_session_imports.json").write_text("{not json")
    assert parse_codex_session(f) is not None


def test_the_manifest_is_reread_when_it_changes(tmp_path):
    f = _rollout(tmp_path, THREAD)
    _manifest(tmp_path, "someone-else")
    assert parse_codex_session(f) is not None
    import os
    _manifest(tmp_path, THREAD)
    st = (tmp_path / "external_agent_session_imports.json").stat()
    os.utime(tmp_path / "external_agent_session_imports.json", (st.st_atime, st.st_mtime + 5))
    assert parse_codex_session(f) is None, "a stale cache would keep ingesting a new mirror"


def test_a_short_manifest_id_never_suffix_matches_a_native_file(tmp_path):
    """verify's codex reader, 2026-09-27: `endswith` let a malformed short id
    skip any native rollout whose filename happened to end with it."""
    native = "01a0ffff-0000-7000-8000-000000000abc"
    f = _rollout(tmp_path, native)
    _manifest(tmp_path, "abc", "0abc", "000000000abc")
    assert parse_codex_session(f) is not None


def test_a_same_mtime_rewrite_of_the_manifest_is_still_seen(tmp_path):
    """mtime alone missed a rewrite inside one timestamp tick."""
    import os
    f = _rollout(tmp_path, THREAD)
    m = tmp_path / "external_agent_session_imports.json"
    _manifest(tmp_path, "x")
    assert parse_codex_session(f) is not None
    st = m.stat()
    _manifest(tmp_path, THREAD)                       # different size
    os.utime(m, ns=(st.st_atime_ns, st.st_mtime_ns))  # SAME mtime as before
    assert parse_codex_session(f) is None


def test_a_same_size_same_mtime_rewrite_is_still_seen(tmp_path):
    """verify's codex reader, second pass: (mtime_ns, size) still missed a
    same-size rewrite inside one timestamp tick. Any stat key has that hole."""
    import os
    f = _rollout(tmp_path, THREAD)
    m = tmp_path / "external_agent_session_imports.json"
    other = THREAD[:-1] + ("0" if THREAD[-1] != "0" else "1")   # same length
    _manifest(tmp_path, other)
    assert parse_codex_session(f) is not None
    st = m.stat()
    _manifest(tmp_path, THREAD)                                  # SAME size
    assert m.stat().st_size == st.st_size
    os.utime(m, ns=(st.st_atime_ns, st.st_mtime_ns))             # SAME mtime
    assert parse_codex_session(f) is None



@pytest.mark.parametrize("doc", ['{"records": 1}', '{"records": "abc"}', '[1, 2]',
                                 '{"records": [1, null, {"imported_thread_id": 7}]}', '"x"', 'null'])
def test_a_wrong_shape_manifest_skips_nothing_and_never_crashes(tmp_path, doc):
    """Valid JSON, wrong shape. verify's codex reader, third pass: `{"records": 1}`
    raised an uncaught TypeError -- a corrupt manifest could crash ingest."""
    f = _rollout(tmp_path, THREAD)
    (tmp_path / "external_agent_session_imports.json").write_text(doc)
    assert parse_codex_session(f) is not None


def _meta_rollout(home, thread, meta_extra):
    f = _rollout(home, thread)
    rows = [json.loads(line) for line in f.read_text().splitlines()]
    rows[0]["payload"].update(meta_extra)
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


@pytest.mark.parametrize("meta", [
    {"source": {"subagent": {"thread_spawn": {"parent_thread_id": "p"}}}, "thread_source": "subagent"},
    {"source": {"subagent": {"other": "guardian"}}, "thread_source": "guardian_review"},
    {"source": {"subagent": "review"}, "thread_source": "subagent"},
])
def test_an_agent_in_the_user_seat_is_not_the_founder(tmp_path, meta):
    """Peer report 2026-09-28: 45 subagent / approval-reviewer sessions had put
    257 agent-written prompts into the founder's prompt index."""
    f = _meta_rollout(tmp_path, "01a0ffff-0000-7000-8000-00000000aaaa", meta)
    assert parse_codex_session(f) is None


@pytest.mark.parametrize("meta", [
    {"source": "exec", "thread_source": "user"},
    {"source": "vscode"},
    {"source": "cli", "thread_source": "user"},
])
def test_a_session_the_founder_started_is_kept(tmp_path, meta):
    f = _meta_rollout(tmp_path, "01a0ffff-0000-7000-8000-00000000bbbb", meta)
    assert parse_codex_session(f) is not None
