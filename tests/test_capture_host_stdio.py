"""Stdio round-trip test for the v1.6 capture host.

Spawns trinity_local.capture_host as a subprocess, writes a length-
prefixed JSON capture payload to its stdin, reads the ack, and asserts
the captured conversation landed in the expected ``~/.trinity/
conversations/<provider>/<conv_id>.json`` location.

Validates:
* The 4-byte little-endian length prefix
* UTF-8 JSON envelope shape
* Atomic-rename write target (no leftover .tmp file)
* Both canonical and stream payload kinds
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path



def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def _read_frame(stream) -> dict:
    raw_len = stream.read(4)
    assert raw_len and len(raw_len) == 4, f"missing length prefix; got {raw_len!r}"
    length = struct.unpack("<I", raw_len)[0]
    body = stream.read(length)
    assert len(body) == length, f"short read: expected {length} bytes, got {len(body)}"
    return json.loads(body.decode("utf-8"))


def _spawn_host(tmp_trinity_home: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["TRINITY_HOME"] = str(tmp_trinity_home)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    return subprocess.Popen(
        [sys.executable, "-m", "trinity_local.capture_host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def test_canonical_payload_writes_conversation_file(tmp_path):
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    payload = {
        "kind": "captured",
        "payload": {
            "provider": "claude",
            "kind": "canonical",
            "url": "https://claude.ai/api/organizations/org/chat_conversations/abc-123",
            "method": "GET",
            "conversation": {
                "uuid": "abc-123",
                "name": "Test conversation",
                "chat_messages": [
                    {"uuid": "m1", "sender": "human", "text": "hi", "parent_message_uuid": None},
                    {"uuid": "m2", "sender": "assistant", "text": "hello", "parent_message_uuid": "m1"},
                ],
            },
            "captured_at": "2026-05-14T23:00:00Z",
        },
    }

    proc.stdin.write(_frame(payload))
    proc.stdin.flush()

    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True, f"host returned error: {ack}"
    assert ack["provider"] == "claude"
    assert ack["conv_id"] == "abc-123"

    written = Path(ack["path"])
    assert written.exists(), f"expected file at {written}"
    assert written == trinity_home / "conversations" / "claude" / "abc-123.json"

    saved = json.loads(written.read_text())
    assert saved["uuid"] == "abc-123"
    assert len(saved["chat_messages"]) == 2
    assert not list(written.parent.glob("*.tmp")), "atomic rename should leave no .tmp files"


def test_stream_payload_keyed_by_url_hash(tmp_path):
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    payload = {
        "kind": "captured",
        "payload": {
            "provider": "chatgpt",
            "kind": "stream",
            "url": "https://chatgpt.com/backend-api/conversation",
            "method": "POST",
            "body_text": "data: {\"delta\": \"hi\"}\n\ndata: [DONE]\n\n",
            "captured_at": "2026-05-14T23:00:00Z",
        },
    }

    proc.stdin.write(_frame(payload))
    proc.stdin.flush()

    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    assert ack["provider"] == "chatgpt"
    assert ack["conv_id"].startswith("stream-")

    written = Path(ack["path"])
    assert written.exists()
    saved = json.loads(written.read_text())
    assert "_raw_stream_body" in saved
    assert "data: [DONE]" in saved["_raw_stream_body"]


def test_adapter_stream_writes_with_stream_suffix(tmp_path):
    """``kind: "adapter_stream"`` payloads land under ``<conv_id>.stream.json``
    so they don't overwrite the canonical conversation file when both
    arrive for the same conv_id.
    """
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    payload = {
        "kind": "captured",
        "payload": {
            "provider": "claude",
            "kind": "adapter_stream",
            "conv_id": "conv-xyz",
            "message_uuid": "msg-aa11",
            "url": "https://claude.ai/api/organizations/o/chat_conversations/conv-xyz/completion",
            "method": "POST",
            "assistant_text": "Hello world.",
            "events_count": 5,
        },
    }

    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    assert ack["conv_id"] == "conv-xyz.stream"

    written = Path(ack["path"])
    assert written.exists()
    assert written == trinity_home / "conversations" / "claude" / "conv-xyz.stream.json"

    saved = json.loads(written.read_text())
    assert saved["assistant_text"] == "Hello world."
    assert saved["message_uuid"] == "msg-aa11"


def test_adapter_stream_file_stem_overrides_conv_id_for_filename(tmp_path):
    """When the adapter provides `file_stem`, the capture host uses it
    as the filename stem instead of conv_id. conv_id keeps its semantic
    meaning. Closes #145: gemini fires multiple RPCs per turn (and
    multiple turns per conversation) all sharing the same conv_id —
    without file_stem they overwrite each other. The adapter provides
    `<conv_id>__<msg_id>` as file_stem so each capture lands distinctly.
    """
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    payload = {
        "kind": "captured",
        "payload": {
            "provider": "gemini",
            "kind": "adapter_stream",
            "conv_id": "conv-xyz",
            "file_stem": "conv-xyz__msg-deadbeef9999",
            "message_id": "msg-deadbeef9999",
            "url": "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
            "method": "POST",
            "assistant_text": "Gemini reply.",
            "events_count": 3,
        },
    }
    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    # Filename uses file_stem, not conv_id
    assert ack["conv_id"] == "conv-xyz__msg-deadbeef9999.stream"
    written = Path(ack["path"])
    assert written == trinity_home / "conversations" / "gemini" / "conv-xyz__msg-deadbeef9999.stream.json"
    saved = json.loads(written.read_text())
    # Semantic conv_id preserved in payload — ingest can still group by it
    assert saved["conv_id"] == "conv-xyz"
    assert saved["file_stem"] == "conv-xyz__msg-deadbeef9999"


def test_adapter_stream_without_file_stem_uses_conv_id(tmp_path):
    """Back-compat: claude/chatgpt adapters don't provide file_stem
    (one stream per turn; overwrite is fine since the canonical fetch
    carries the full tree). Filename falls back to conv_id."""
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    payload = {
        "kind": "captured",
        "payload": {
            "provider": "claude",
            "kind": "adapter_stream",
            "conv_id": "conv-abc",
            "url": "https://claude.ai/api/organizations/o/chat_conversations/conv-abc/completion",
            "method": "POST",
            "assistant_text": "Hi.",
            "events_count": 2,
        },
    }
    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    assert ack["conv_id"] == "conv-abc.stream"
    written = Path(ack["path"])
    assert written == trinity_home / "conversations" / "claude" / "conv-abc.stream.json"


def test_query_sync_status_returns_diff(tmp_path):
    """The sync_status query computes sidebar_conv_ids - on_disk_conv_ids
    for one provider and returns the missing count + ids. Used by the
    in-provider sync pill to decide whether to show + at what count.
    """
    trinity_home = tmp_path / "trinity"
    # Pre-populate captures: _sidebar.json with 3 items, 1 already on disk
    p_dir = trinity_home / "conversations" / "chatgpt"
    p_dir.mkdir(parents=True)
    (p_dir / "_sidebar.json").write_text(json.dumps({
        "kind": "sidebar_list",
        "sidebar": {"items": [
            {"id": "conv-A", "title": "A"},
            {"id": "conv-B", "title": "B"},
            {"id": "conv-C", "title": "C"},
        ]},
    }))
    (p_dir / "conv-A.json").write_text("{}")  # only conv-A is on disk

    proc = _spawn_host(trinity_home)
    proc.stdin.write(_frame({"kind": "query", "query_kind": "sync_status",
                              "provider": "chatgpt"}))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    assert ack["provider"] == "chatgpt"
    assert ack["sidebar_count"] == 3
    assert ack["on_disk_count"] == 1
    assert ack["missing_count"] == 2
    assert set(ack["missing_ids"]) == {"conv-B", "conv-C"}


def test_query_sync_status_extracts_org_id_for_claude(tmp_path):
    """Claude canonical URLs need the org_id. The pill can't synthesize
    it (it's not in any conv_id) so capture-host extracts it from the
    _sidebar.json's stored URL and returns it inline."""
    trinity_home = tmp_path / "trinity"
    p_dir = trinity_home / "conversations" / "claude"
    p_dir.mkdir(parents=True)
    (p_dir / "_sidebar.json").write_text(json.dumps({
        "kind": "sidebar_list",
        "url": "https://claude.ai/api/organizations/38cce2de-a428-421c-b45d-2beebd4c4a27/chat_conversations_v2?limit=10",
        "sidebar": {"data": [{"uuid": "u-A"}]},
    }))

    proc = _spawn_host(trinity_home)
    proc.stdin.write(_frame({"kind": "query", "query_kind": "sync_status",
                              "provider": "claude"}))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    assert ack["org_id"] == "38cce2de-a428-421c-b45d-2beebd4c4a27"


def test_query_sync_status_rejects_invalid_provider(tmp_path):
    """Defense-in-depth: only known provider slugs are accepted."""
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)
    proc.stdin.write(_frame({"kind": "query", "query_kind": "sync_status",
                              "provider": "../etc/passwd"}))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)
    assert ack["ok"] is False
    assert "invalid_provider" in ack["error"]


def test_query_launchpad_data_returns_page_data(tmp_path):
    """The `launchpad_data` query lets the in-extension launchpad fetch its
    page-data over Native Messaging instead of reading a generated
    launchpad.html. Round-trips over real stdio against a fresh (cold-start)
    home: returns ok + the pageData dict the Vue app consumes, and stays under
    the host→extension size budget so Chrome won't drop the frame."""
    import struct as _struct

    trinity_home = tmp_path / "trinity"
    env = os.environ.copy()
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"  # no model calls / scan in the host
    proc_env = {**env, "TRINITY_HOME": str(trinity_home),
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "trinity_local.capture_host"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=proc_env,
    )
    proc.stdin.write(_frame({"kind": "query", "query_kind": "launchpad_data"}))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=30)

    assert ack["ok"] is True, ack
    assert "pageData" in ack and isinstance(ack["pageData"], dict)
    # A stable key the Vue app relies on — proves it's the real page-data, not a stub.
    # (order is config-derived, so compare as a set.)
    assert set(ack["pageData"].get("defaultMembers") or []) == {"claude", "codex", "antigravity"}
    assert "recentSidebarHtml" in ack
    # Must stay under Chrome's 1 MB host→extension cap (we budget 900 KB).
    assert len(_struct.pack("<I", 0) + json.dumps(ack).encode("utf-8")) < 900 * 1024


def test_query_launchpad_data_fails_soft_when_oversized(tmp_path, monkeypatch):
    """If a pathological corpus ever pushes the payload past the budget, the
    query returns a typed error (the page falls back to the file:// launchpad)
    rather than letting Chrome silently drop an oversized frame."""
    from trinity_local import capture_host

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
    monkeypatch.setattr(capture_host, "_MAX_LAUNCHPAD_BYTES", 10)  # force the breach
    result = capture_host._query_launchpad_data({"kind": "query", "query_kind": "launchpad_data"})
    assert result["ok"] is False
    assert result["error"] == "launchpad_payload_too_large"
    assert result["bytes"] > 10


def test_sidebar_list_writes_to_sentinel_filename(tmp_path):
    """`kind: "sidebar_list"` payloads land under `<provider>/_sidebar.json`
    — sentinel filename used by the auto-sync diff pipeline to compute
    "what's in the sidebar that we don't have on disk." Distinct from
    per-thread captures so it can be re-fetched/overwritten without
    touching real conversation files.
    """
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    payload = {
        "kind": "captured",
        "payload": {
            "provider": "claude",
            "kind": "sidebar_list",
            "url": "https://claude.ai/api/organizations/o/chat_conversations?limit=10",
            "method": "GET",
            "sidebar": [
                {"uuid": "conv-1", "name": "First chat"},
                {"uuid": "conv-2", "name": "Second chat"},
            ],
            "captured_at": "2026-05-23T22:00:00Z",
        },
    }
    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    proc.stdin.close()
    proc.wait(timeout=5)

    assert ack["ok"] is True
    assert ack["conv_id"] == "_sidebar"
    written = Path(ack["path"])
    assert written == trinity_home / "conversations" / "claude" / "_sidebar.json"
    saved = json.loads(written.read_text())
    assert saved["kind"] == "sidebar_list"
    assert len(saved["sidebar"]) == 2
    assert saved["sidebar"][0]["uuid"] == "conv-1"


def test_unrecognized_payload_errors_but_keeps_host_alive(tmp_path):
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)

    # Missing provider — should error but not crash.
    proc.stdin.write(_frame({"kind": "captured", "payload": {"junk": True}}))
    proc.stdin.flush()
    ack = _read_frame(proc.stdout)
    assert ack["ok"] is False

    # Send a valid payload after the bad one — host must still be running.
    proc.stdin.write(_frame({
        "kind": "captured",
        "payload": {
            "provider": "claude",
            "kind": "canonical",
            "conversation": {"uuid": "after-error"},
        },
    }))
    proc.stdin.flush()
    ack2 = _read_frame(proc.stdout)
    assert ack2["ok"] is True
    assert ack2["conv_id"] == "after-error"

    proc.stdin.close()
    proc.wait(timeout=5)


# 100-persona audit D6 fix: path traversal blocker.
def test_provider_path_traversal_rejected(tmp_path, monkeypatch):
    """Compromised extension sends provider='../../etc' — host MUST refuse
    instead of writing outside ~/.trinity/conversations/."""
    import pytest
    from trinity_local.capture_host import _write_capture

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="unsafe provider"):
        _write_capture("../../etc", "innocent_conv", {"chat_messages": []})

    # Confirm nothing was written outside the conversations dir.
    forbidden = tmp_path / ".." / "etc"
    assert not forbidden.exists(), "path traversal succeeded — should have raised"


def test_conv_id_path_traversal_rejected(tmp_path, monkeypatch):
    """Same shape on conv_id — adversarial JSON in conv.uuid can't escape."""
    import pytest
    from trinity_local.capture_host import _write_capture

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="unsafe conv_id"):
        _write_capture("claude", "../../../etc/passwd_attempt", {"chat_messages": []})


def test_conv_id_with_slash_rejected(tmp_path, monkeypatch):
    import pytest
    from trinity_local.capture_host import _write_capture

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="unsafe conv_id"):
        _write_capture("claude", "abc/def", {"chat_messages": []})


def test_overlong_id_rejected(tmp_path, monkeypatch):
    """DoS guard — 80-char cap blocks filename-length attacks
    (cap is 80 to fit `<uuid>.stream` adapter suffix)."""
    import pytest
    from trinity_local.capture_host import _write_capture

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    long_id = "x" * 81
    with pytest.raises(ValueError, match="unsafe conv_id"):
        _write_capture("claude", long_id, {"chat_messages": []})


def test_valid_uuid_passes(tmp_path, monkeypatch):
    """Legitimate UUIDs (the common case) MUST keep working."""
    from trinity_local.capture_host import _write_capture
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    target = _write_capture("claude", "abc123-def456_xyz-uuid", {"chat_messages": []})
    assert target.exists()


def test_file_stem_path_traversal_rejected(tmp_path, monkeypatch):
    """The adapter_stream `file_stem` field OVERRIDES conv_id for the on-disk
    filename (gemini fires one RPC per turn so each needs a distinct stem). It is
    a DISTINCT untrusted input — a compromised extension could send
    `file_stem='../../etc/passwd'`. It's currently safe ONLY because
    `_extract_target` folds it into the `f"{stem}.stream"` conv_id that
    `_write_capture` runs `_sanitize_id` on. The provider/conv_id traversal tests
    above never exercise file_stem, so a refactor that built the filename from
    file_stem directly (skipping the conv_id sanitizer) would silently reopen the
    path-traversal primitive with no failing test. This pins the END-TO-END
    contract through `_extract_target`. (`test_adapter_stream_file_stem_overrides_
    conv_id_for_filename` covers the benign-accept side; this is its adversarial
    twin — [[test_the_boundary_and_the_action]].)"""
    import pytest
    from trinity_local.capture_host import _extract_target, _write_capture

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    for bad in ("../../etc/passwd", "..__evil", ".hidden", "a/b/c", "x" * 90):
        provider, conv_id, conv = _extract_target(
            {"payload": {"provider": "antigravity", "kind": "adapter_stream",
                         "conv_id": "realconv", "file_stem": bad, "assistant_text": "hi"}}
        )
        with pytest.raises(ValueError, match="unsafe conv_id"):
            _write_capture(provider, conv_id, conv)

    # Nothing escaped the conversations dir.
    assert not (tmp_path / ".." / "etc" / "passwd").exists()

    # Accept side: a benign file_stem still produces the override filename.
    provider, conv_id, conv = _extract_target(
        {"payload": {"provider": "antigravity", "kind": "adapter_stream",
                     "conv_id": "realconv", "file_stem": "realconv__msg1", "assistant_text": "hi"}}
    )
    written = _write_capture(provider, conv_id, conv)
    assert written.name == "realconv__msg1.stream.json"


# ─── Phase 1: action-dispatch messages (Chrome extension transition) ──

def test_launch_council_action_dispatches_to_cli(monkeypatch, tmp_path):
    """An action message with kind=launch-council must dispatch via
    subprocess.Popen to `trinity-local council-launch --task <task>` and
    return immediately (fire-and-forget). The host builds the argv from
    the ACTION_ALLOWLIST spec — this test verifies the argv shape, the
    detached response contract, AND the status_token passthrough
    introduced for the popup's incremental status polling.
    """
    from trinity_local import capture_host

    captured_argv: list[list[str]] = []
    captured_kwargs: list[dict] = []

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            captured_argv.append(list(argv))
            captured_kwargs.append(kwargs)
            self.pid = 4242

    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "Popen", _FakePopen)

    result = capture_host._run_action({
        "kind": "launch-council",
        "task": "Compare Rust vs Go for a CLI",
        "primary_provider": "codex",
        "status_token": "launch_abc_123def",
    })

    # Detached contract: ok:true with detached:true + pid + status_token
    # echo. No `returncode` / `stdout` because the child is still running.
    assert result["ok"] is True
    assert result["detached"] is True
    assert result["pid"] == 4242
    assert result["action"] == "launch-council"
    assert result["status_token"] == "launch_abc_123def"

    assert captured_argv, "_run_action did not invoke subprocess.Popen"
    argv = captured_argv[0]
    # capture_host._trinity_local_bin() resolves to either the bare name
    # (PATH lookup path) or an absolute sibling path (Chrome minimal-PATH
    # workaround — the production path on real installs). Both are
    # correct; pin the basename so the test passes under both.
    assert Path(argv[0]).name == "trinity-local"
    assert argv[1] == "council-launch"
    assert "--task" in argv
    assert argv[argv.index("--task") + 1] == "Compare Rust vs Go for a CLI"
    assert "--primary-provider" in argv
    assert argv[argv.index("--primary-provider") + 1] == "codex"
    assert "--status-token" in argv
    assert argv[argv.index("--status-token") + 1] == "launch_abc_123def"

    # Popen must redirect stdio + start a new session so Chrome closing
    # the Native Messaging pipe (and SIGHUP'ing capture_host) doesn't
    # take the council child down with it.
    kwargs = captured_kwargs[0]
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("stdout") == real_subprocess.DEVNULL
    assert kwargs.get("stderr") == real_subprocess.DEVNULL
    assert kwargs.get("stdin") == real_subprocess.DEVNULL

    # PATH augmentation — without this the spawned `trinity-local
    # council-launch` inherits Chrome's minimal PATH and can't find
    # `claude` / `codex` / `gemini` in ~/.local/bin or Homebrew dirs.
    # First real council from the popup failed with "Provider binary
    # not found: claude" within 10s before this guard.
    env = kwargs.get("env")
    assert env is not None, "Popen must pass an augmented env, not inherit Chrome's"
    path = env.get("PATH", "")
    expected_bins = ["/usr/local/bin", "/opt/homebrew/bin"]
    found = [b for b in expected_bins if b in path]
    assert found, (
        f"PATH must include Homebrew + /usr/local/bin so provider "
        f"binaries resolve. Got PATH={path!r}"
    )


def test_get_council_status_reads_status_json_in_process(monkeypatch, tmp_path):
    """The popup polls every ~1.5s; subprocess startup per poll is
    wasteful. capture_host handles `get-council-status` in-process by
    calling council_status.load_council_status directly."""
    from trinity_local import capture_host

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    # No status written yet — should return ok:true with status:null,
    # NOT an error (the runner may not have flushed the first record).
    r = capture_host._run_action({
        "kind": "get-council-status",
        "status_token": "launch_xyz_999",
    })
    assert r["ok"] is True
    assert r["status"] is None
    assert r["status_token"] == "launch_xyz_999"

    # Now write a status JSON the way the runner would and verify
    # the in-process reader picks it up. Pass our own pid as runner_pid
    # so the staleness coercion (running + dead runner → failed) doesn't
    # rewrite the record before we read it.
    import os
    from trinity_local.council_status import init_council_run_state
    init_council_run_state(
        "launch_xyz_999",
        task_text="hello",
        bundle_id="b1",
        council_id="b1",
        members=["claude", "codex", "antigravity"],
        metadata={},
        runner_pid=os.getpid(),
    )
    r = capture_host._run_action({
        "kind": "get-council-status",
        "status_token": "launch_xyz_999",
    })
    assert r["ok"] is True
    assert r["status"] is not None
    assert r["status"]["status"] == "running"
    assert set(r["status"]["members"].keys()) == {"claude", "codex", "antigravity"}


def test_get_council_status_rejects_unsafe_token():
    """status_token must match _SAFE_ID_RX — defense against path
    traversal even though the consumer is in-process (the same id
    feeds load_council_status → trinity_home() / 'portal_pages' /
    'status' / f'{token}.json')."""
    from trinity_local import capture_host

    for bad in ["../etc/passwd", "a/b", "x" * 200, "", None, 42]:
        r = capture_host._run_action({
            "kind": "get-council-status",
            "status_token": bad,
        })
        assert r["ok"] is False, f"expected reject for {bad!r}"


def test_launch_council_non_zero_exit_surfaces_real_error(monkeypatch):
    """Pre-fix: capture_host returned `ok:false` with no `error` field
    when the CLI exited non-zero, so the popup rendered 'Failed: unknown
    error'. Post-fix: error == the (sanitized) last line of stderr — an
    already-clean diagnosable line survives intact — plus the actionable
    `trinity-local status` recovery hint (the popup paints this verbatim, so
    a crashed CLI's traceback line gets stripped of any absolute path / Errno /
    type name by `_safe_error`; see test_capture_host_error_no_leak_browser)."""
    from trinity_local import capture_host

    # Disable the detached path for this test so we exercise the
    # synchronous error-surface branch. The simplest hook: monkeypatch
    # the detached set to be empty.
    monkeypatch.setattr(capture_host, "_DETACHED_ACTIONS", set())

    class _FakeCompletedProcess:
        returncode = 2
        stdout = ""
        stderr = "first line\nERROR: bundle not found\n"

    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    result = capture_host._run_action({
        "kind": "launch-council",
        "task": "hi",
    })
    assert result["ok"] is False
    assert result["returncode"] == 2
    # The clean diagnosable stderr line is preserved (not redacted away)...
    assert "ERROR: bundle not found" in result["error"]
    # ...and the dispatch failure is now ACTIONABLE.
    assert "trinity-local status" in result["error"]


def test_action_missing_required_field_rejected(monkeypatch):
    """launch-council requires `task`. Missing → reject without invoking CLI."""
    from trinity_local import capture_host

    invoked = []
    import subprocess as real_subprocess
    monkeypatch.setattr(
        real_subprocess, "run", lambda *a, **k: invoked.append(a) or None,
    )

    result = capture_host._run_action({"kind": "launch-council"})  # no task
    assert result["ok"] is False
    assert "missing required field" in result["error"]
    assert "task" in result["error"]
    assert not invoked, "subprocess.run should not be invoked when validation fails"


def test_action_not_in_allowlist_rejected(monkeypatch):
    """Defense in depth: unknown kind → reject. Even if a compromised
    extension sends arbitrary action names, the host won't run them."""
    from trinity_local import capture_host

    invoked = []
    import subprocess as real_subprocess
    monkeypatch.setattr(
        real_subprocess, "run", lambda *a, **k: invoked.append(a) or None,
    )

    result = capture_host._run_action({
        "kind": "rm-rf-everything",
        "task": "x",
    })
    assert result["ok"] is False
    assert "not in allowlist" in result["error"]
    assert not invoked


def test_action_non_primitive_field_rejected(monkeypatch):
    """Field values must be str/int/float — reject dicts/lists. Defends
    against payloads that could be misinterpreted as shell metacharacters."""
    from trinity_local import capture_host

    invoked = []
    import subprocess as real_subprocess
    monkeypatch.setattr(
        real_subprocess, "run", lambda *a, **k: invoked.append(a) or None,
    )

    result = capture_host._run_action({
        "kind": "launch-council",
        "task": {"nested": "payload"},
    })
    assert result["ok"] is False
    assert "primitive" in result["error"]
    assert not invoked


def test_is_action_message_recognizes_action_kinds():
    from trinity_local.capture_host import _is_action_message
    assert _is_action_message({"kind": "launch-council"}) is True
    assert _is_action_message({"kind": "ingest-recent"}) is True
    assert _is_action_message({"kind": "canonical"}) is False
    assert _is_action_message({"kind": "adapter_stream"}) is False
    assert _is_action_message({"kind": "stream"}) is False
    assert _is_action_message({}) is False


def test_oversized_message_length_rejected(tmp_path):
    """DoS hardening (2026-06-01): Chrome permits extension→host Native-Messaging
    messages up to 4 GB (only host→extension is capped at 1 MB). A 4-byte length
    prefix near that would make `read(length)` accumulate gigabytes into memory.
    The host must reject an oversized frame on the LENGTH alone — before reading
    (and buffering) the body. We send only an over-cap length prefix + 1 byte;
    the host must ack an error and exit WITHOUT trying to read the absent GB."""
    from trinity_local.capture_host import _MAX_MESSAGE_BYTES

    proc = _spawn_host(tmp_path / "trinity")
    proc.stdin.write(struct.pack("<I", _MAX_MESSAGE_BYTES + 1) + b"x")
    proc.stdin.flush()
    proc.stdin.close()
    # Must respond quickly with an error frame (not hang reading 64MB+ of body).
    ack = _read_frame(proc.stdout)
    proc.wait(timeout=10)
    assert ack["ok"] is False, ack
    assert "exceeds" in ack["error"] or "cap" in ack["error"], ack
    assert proc.returncode == 1, "host must exit after an oversized frame"


def test_normal_sized_message_still_accepted(tmp_path):
    """ACCEPT side of the cap (per the boundary-both-sides rule): a well-formed
    capture under the cap must still write its file — the cap must not regress
    into rejecting legitimate captures."""
    trinity_home = tmp_path / "trinity"
    proc = _spawn_host(trinity_home)
    payload = {
        "kind": "captured",
        "payload": {
            "provider": "claude", "kind": "canonical",
            "url": "https://claude.ai/api/.../chat_conversations/cap-ok",
            "method": "GET",
            "conversation": {"uuid": "cap-ok", "name": "n", "chat_messages": []},
            "captured_at": "2026-06-01T00:00:00Z",
        },
    }
    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    proc.stdin.close()
    ack = _read_frame(proc.stdout)
    proc.wait(timeout=10)
    assert ack["ok"] is True, ack
    assert (trinity_home / "conversations" / "claude" / "cap-ok.json").exists()
