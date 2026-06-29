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
