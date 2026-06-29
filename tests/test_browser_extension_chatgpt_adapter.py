"""Fixture-based unit test for browser-extension/adapters/chatgpt.js.

Spec-v1.6 Week 2 Day 6-7: chatgpt.com adapter, same shape as claude.js.
Different endpoint (``POST /backend-api/conversation``), different SSE
event shape (``message.content.parts[]`` cumulative instead of
``content_block_delta`` incremental), different conv_id source
(``conversation_id`` field on event payloads).

Runs the JS adapter through node against a saved SSE fixture; asserts
the reconstructed ``assistant_text`` matches verbatim plus correct
conv_id and message_id extraction. Skips when node isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = REPO_ROOT / "browser-extension" / "adapters" / "chatgpt.js"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "chatgpt_sse_sample.txt"


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
      url: 'https://chatgpt.com/backend-api/conversation',
      body_text: body,
      method: 'POST',
      captured_at: '2026-05-15T00:30:00Z',
    }});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def test_adapter_reports_correct_provider(adapter_result):
    assert adapter_result["provider"] == "chatgpt"


def test_adapter_extracts_conv_id_from_event_payload(adapter_result):
    """OpenAI ships conversation_id as a top-level field on most
    events (also nested in message.metadata). Adapter picks the first
    one it sees."""
    assert adapter_result["conv_id"] == "conv-abc-xyz"


def test_adapter_extracts_assistant_message_id(adapter_result):
    assert adapter_result["message_id"] == "msg-7ab2"


def test_adapter_returns_final_cumulative_text(adapter_result):
    """OpenAI's parts[] is cumulative — each event carries the FULL
    text so far. The adapter must keep the last-observed (longest)
    parts payload, not concatenate them."""
    expected = "Trinity Local is the cross-provider memory layer the labs are commercially prevented from building."
    assert adapter_result["assistant_text"] == expected


def test_adapter_kind_is_adapter_stream(adapter_result):
    assert adapter_result["kind"] == "adapter_stream"


def test_adapter_counts_events(adapter_result):
    # 4 message events + [DONE]
    assert adapter_result["events_count"] >= 4


def test_adapter_handles_delta_shape():
    """Newer OpenAI responses use ``delta`` chunks instead of
    cumulative parts. Adapter must accumulate delta strings AND
    return the longer of (cumulative-parts, accumulated-deltas)."""
    if shutil.which("node") is None:
        pytest.skip("node not available")
    delta_body = "\n".join([
        'data: {"delta":{"content":"Hello "}}',
        '',
        'data: {"delta":{"content":"from "}}',
        '',
        'data: {"delta":{"content":"the delta path."}}',
        '',
        'data: [DONE]',
        '',
    ])
    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const result = adapter.adapt({{
      url: 'https://chatgpt.com/backend-api/conversation',
      body_text: {json.dumps(delta_body)},
      method: 'POST',
    }});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["assistant_text"] == "Hello from the delta path."


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
    assert result["provider"] == "chatgpt"
    assert result["assistant_text"] == ""
    assert result["events_count"] == 0


def test_adapter_skips_malformed_json_without_crashing():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    truncated = (
        'data: {"message":{"id":"a","author":{"role":"assistant"},"content":{"content_type":"text","parts":["hi"]},"metadata":{"conversation_id":"c-1"}}}\n\n'
        'data: {"message":{"id":"a","author":{"role":"assist'  # truncated mid-string
    )
    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const result = adapter.adapt({{ url: 'https://chatgpt.com/backend-api/conversation',
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


def test_adapter_falls_back_to_message_metadata_for_conv_id():
    """When top-level conversation_id isn't on the event, adapter
    must fall back to message.metadata.conversation_id."""
    if shutil.which("node") is None:
        pytest.skip("node not available")
    body = "\n".join([
        'data: {"message":{"id":"m","author":{"role":"assistant"},"content":{"content_type":"text","parts":["hi"]},"metadata":{"conversation_id":"only-in-metadata"}}}',
        '',
        'data: [DONE]',
        '',
    ])
    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const result = adapter.adapt({{ url: 'https://chatgpt.com/backend-api/conversation',
                                    body_text: {json.dumps(body)},
                                    method: 'POST' }});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["conv_id"] == "only-in-metadata"
