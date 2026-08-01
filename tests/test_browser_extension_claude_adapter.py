"""Fixture-based unit test for browser-extension/adapters/claude.js.

Spec-v1.6 Day 5 deliverable: "Normalize Anthropic's SSE delta format
into Trinity's conversation schema. Pin with at least one fixture-
based unit test."

Strategy: run the adapter through node (which is available because
the extension itself is shipped as JS), feed it a saved SSE sample,
verify it reconstructs the assistant message text + extracts the
conversation/message ids. Skips when node isn't on PATH so contributors
without node still get a green test run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = REPO_ROOT / "browser-extension" / "adapters" / "claude.js"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "claude_sse_sample.txt"


@pytest.fixture(scope="module")
def adapter_result() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not available; the JS adapter can't run")
    assert ADAPTER_PATH.exists(), f"adapter missing: {ADAPTER_PATH}"
    assert FIXTURE_PATH.exists(), f"fixture missing: {FIXTURE_PATH}"

    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const fs = require('fs');
    const body = fs.readFileSync({json.dumps(str(FIXTURE_PATH))}, 'utf-8');
    const result = adapter.adapt({{
      url: 'https://claude.ai/api/organizations/org-test/chat_conversations/conv-fixture-xyz/completion',
      body_text: body,
      method: 'POST',
      captured_at: '2026-05-14T23:30:00Z',
    }});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def test_adapter_reports_correct_provider(adapter_result):
    assert adapter_result["provider"] == "claude"


def test_adapter_extracts_conv_id_from_url(adapter_result):
    assert adapter_result["conv_id"] == "conv-fixture-xyz"


def test_adapter_extracts_message_uuid_from_message_start(adapter_result):
    assert adapter_result["message_uuid"] == "msg-aa11bb22"


def test_adapter_concatenates_text_deltas_in_order(adapter_result):
    expected = (
        "Trinity Local is the cross-provider memory layer "
        "the labs are commercially prevented from building."
    )
    assert adapter_result["assistant_text"] == expected


def test_adapter_kind_is_adapter_stream(adapter_result):
    assert adapter_result["kind"] == "adapter_stream"


def test_adapter_counts_events(adapter_result):
    # Fixture has 9 SSE blocks (8 named events + 1 unnamed [DONE]).
    assert adapter_result["events_count"] >= 8


def test_adapter_does_not_crash_on_empty_body():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const result = adapter.adapt({{ url: '', body_text: '', method: 'POST' }});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["provider"] == "claude"
    assert result["assistant_text"] == ""
    assert result["events_count"] == 0


def test_adapter_skips_malformed_json_without_crashing():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    # Stream truncated mid-event — Anthropic shouldn't emit this but
    # an interrupted stream might leave a partial JSON payload.
    truncated = (
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
        "event: content_block_delta\n"
        "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"ty"  # truncated mid-key
    )
    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const result = adapter.adapt({{ url: 'https://claude.ai/api/organizations/org/chat_conversations/c-1/completion',
                                    body_text: {json.dumps(truncated)},
                                    method: 'POST' }});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    # First event parsed cleanly; second skipped silently.
    assert result["assistant_text"] == "hi"
    assert result["conv_id"] == "c-1"


def test_adapter_stamps_the_model_that_produced_the_reply(adapter_result: dict):
    """Anthropic's `message_start.message` carries `model` beside the `uuid` the
    adapter already read, so stamping it is free — and it has to happen at capture
    time or it can never happen.

    WHY THIS IS GUARDED (2026-07-25): captured councils recorded provider but not
    model, so 73% of member rows on disk had model=null and every per-model rollup
    silently collapsed to LAB granularity — the exact blending the trust ledger
    abandoned when it re-keyed to model x version, where Opus 4.8 at 77% had been
    hiding behind Opus 4.7 at 51% inside one "claude" column. A capture that ships
    unstamped is behavioural data that cannot gain model fidelity later."""
    assert adapter_result.get("model") == "claude-3-opus", (
        "the adapter must stamp the model from message_start.message.model; "
        f"got {adapter_result.get('model')!r}"
    )


def test_missing_model_yields_none_never_a_guess(tmp_path):
    """A stream without a model must produce null, not an inferred value. A wrong
    model stamp is worse than a missing one: missing is visibly missing, wrong
    silently corrupts every per-model tally built on it."""
    import json as _json
    import shutil as _shutil
    import subprocess as _subprocess

    node = _shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    # a valid stream whose message_start carries no model field
    body = (
        'event: message_start\n'
        'data: {"type":"message_start","message":{"uuid":"msg-nomodel"}}\n\n'
        'event: content_block_delta\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n\n'
    )
    script = (
        'const fs=require("fs");global.window=global;'
        f'eval(fs.readFileSync({_json.dumps(str(ADAPTER_PATH))},"utf8"));'
        f'const out=window.__TRINITY_ADAPTERS.claude.adapt({{url:"https://claude.ai/api/organizations/o/chat_conversations/c9/completion",body_text:{_json.dumps(body)},method:"POST"}});'
        'process.stdout.write(JSON.stringify(out));'
    )
    res = _subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    out = _json.loads(res.stdout)
    assert out["model"] is None, f"absent model must be null, got {out['model']!r}"
    assert out["message_uuid"] == "msg-nomodel", "the rest of the extraction must still work"
