"""Tests for scripts/_runtime.py — the shared shebang-script runtime.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).
The dual-interface contract (shebang + importable) hinges on this
module; if it breaks, every script in scripts/ breaks.

Coverage:
- audit_log writes one JSONL line per call
- audit_log sanitizes non-primitive args without crashing
- audit_log failure paths (unwritable disk) don't crash the caller
- bootstrap_or_continue short-circuits when sentinel env var set
- bootstrap_or_continue short-circuits on opt-out env var
- read_input_json / write_output_json round-trip via stdin/stdout
- read_input_json / write_output_json round-trip via file paths

Doesn't cover the actual venv creation path — that requires real
filesystem + pip and only fires on first run. The bootstrap_or_continue
sentinel test exercises the short-circuit (the "second-run" code path),
which is the hot path for every invocation after first install.
"""
from __future__ import annotations

import io
import json

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_audit_log_writes_jsonl_line(isolated_home):
    from scripts._runtime import audit_log

    audit_log(script="embed", operation="embed_batch",
              args={"n": 5}, outcome="ok")
    audit_log_path = isolated_home / "audit.log"
    assert audit_log_path.exists()
    lines = audit_log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["script"] == "embed"
    assert record["operation"] == "embed_batch"
    assert record["outcome"] == "ok"
    assert record["args"] == {"n": 5}
    # Required fields per spec.
    assert "ts" in record
    assert record["tier"] == "skill"
    assert record["trust_mode"] == "default"


def test_audit_log_appends_multiple_calls(isolated_home):
    from scripts._runtime import audit_log

    for i in range(3):
        audit_log(script="embed", operation=f"call_{i}")
    lines = (isolated_home / "audit.log").read_text().splitlines()
    assert len(lines) == 3


def test_audit_log_sanitizes_non_primitive_args(isolated_home):
    """Don't crash if a caller passes a list/dict/object as an arg
    value — stringify it. Prevents the audit-log path from being
    fragile to upstream API changes."""
    from scripts._runtime import audit_log

    class Thing:
        pass

    audit_log(script="embed", operation="x", args={
        "ok_str": "hello",
        "ok_int": 5,
        "ok_bool": True,
        "ok_none": None,
        "weird_list": [1, 2, 3],
        "weird_obj": Thing(),
    })
    record = json.loads((isolated_home / "audit.log").read_text().splitlines()[0])
    assert record["args"]["ok_str"] == "hello"
    assert record["args"]["ok_int"] == 5
    assert record["args"]["ok_bool"] is True
    assert record["args"]["ok_none"] is None
    # Non-primitives stored as type name
    assert record["args"]["weird_list"] == "list"
    assert record["args"]["weird_obj"] == "Thing"


def test_audit_log_caps_long_strings(isolated_home):
    """Args with very long string values (e.g. an entire prompt) get
    capped so the audit record fits in PIPE_BUF (~512 bytes) for
    atomic append. The cap is 120 chars per arg-string."""
    from scripts._runtime import audit_log

    audit_log(script="embed", operation="x", args={"task": "x" * 1000})
    record = json.loads((isolated_home / "audit.log").read_text().splitlines()[0])
    assert len(record["args"]["task"]) == 120


def test_audit_log_failure_emits_stderr_warning_then_silences(
    monkeypatch, tmp_path, capsys
):
    """council c18f739a verdict: audit-log failures MUST be loud, not
    silent. First failure emits a stderr warning naming the OSError;
    subsequent failures in the same process are rate-limited to avoid
    spam. The caller still doesn't crash — operations continue."""
    from scripts import _runtime
    # Reset the rate-limit flag if a prior test set it
    if hasattr(_runtime.audit_log, "_failure_warned"):
        del _runtime.audit_log._failure_warned

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    # Point at an unwritable path (directory doesn't exist + parent
    # is read-only).
    bad = tmp_path / "readonly"
    bad.mkdir()
    bad.chmod(0o500)
    monkeypatch.setattr(_runtime, "_audit_log_path",
                        lambda: bad / "audit.log")
    # First call: must not raise, must warn
    _runtime.audit_log(script="embed", operation="x")
    captured = capsys.readouterr()
    assert "audit log write FAILED" in captured.err
    # Second call: must not raise, must NOT warn again (rate-limited)
    _runtime.audit_log(script="embed", operation="y")
    captured2 = capsys.readouterr()
    assert captured2.err == ""


def test_audit_log_reads_origin_tier_from_env(monkeypatch, tmp_path):
    """council c18f739a verdict: TRINITY_ORIGIN_TIER env var stamps the
    audit record with the originating tier (skill / pip / extension)
    even when the actual runtime is the pip CLI."""
    from scripts import _runtime
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_ORIGIN_TIER", "extension")
    monkeypatch.setenv("TRINITY_ORIGIN_ACTION", "launch-council")
    monkeypatch.setenv("TRINITY_INVOCATION_ID", "abc-123")

    _runtime.audit_log(script="embed", operation="embed_batch")

    audit = (tmp_path / "audit.log").read_text().splitlines()
    record = json.loads(audit[-1])
    assert record["tier"] == "extension"
    assert record["origin_action"] == "launch-council"
    assert record["invocation_id"] == "abc-123"


def test_bootstrap_short_circuits_on_sentinel(monkeypatch):
    """When TRINITY_SCRIPT_BOOTSTRAPPED matches, the function returns
    immediately. This is the second-run hot path — every invocation
    after the first hits this."""
    from scripts._runtime import bootstrap_or_continue

    monkeypatch.setenv("TRINITY_SCRIPT_BOOTSTRAPPED", "embed")
    # Must NOT attempt to create a venv or re-exec.
    bootstrap_or_continue(script_name="embed",
                          requirements=["nonexistent-package-zzz"])
    # If we reach here, the function returned without trying to install.


def test_bootstrap_short_circuits_on_opt_out(monkeypatch):
    """TRINITY_SKIP_VENV_BOOTSTRAP=1 lets dev / CI users opt out."""
    from scripts._runtime import bootstrap_or_continue

    monkeypatch.delenv("TRINITY_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setenv("TRINITY_SKIP_VENV_BOOTSTRAP", "1")
    bootstrap_or_continue(script_name="embed",
                          requirements=["nonexistent-package-zzz"])


def test_read_input_json_from_stdin(monkeypatch):
    from scripts._runtime import read_input_json

    payload = {"texts": ["hello", "world"]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert read_input_json() == payload


def test_read_input_json_from_file(tmp_path):
    from scripts._runtime import read_input_json

    path = tmp_path / "input.json"
    payload = {"texts": ["a", "b"]}
    path.write_text(json.dumps(payload))
    assert read_input_json(str(path)) == payload


def test_write_output_json_to_stdout(capsys):
    from scripts._runtime import write_output_json

    write_output_json({"vectors": [[1.0, 2.0]]})
    out = capsys.readouterr().out
    assert json.loads(out) == {"vectors": [[1.0, 2.0]]}


def test_write_output_json_to_file(tmp_path):
    from scripts._runtime import write_output_json

    out_path = tmp_path / "out.json"
    write_output_json({"vectors": [[1.0]]}, str(out_path))
    assert json.loads(out_path.read_text()) == {"vectors": [[1.0]]}
