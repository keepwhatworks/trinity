"""Node-based unit tests for the extension's standalone capture engine
(browser-extension/background.js).

The extension is a good standalone "download your transcripts" tool: every
captured conversation is mirrored into chrome.storage.local so a user with ONLY
the extension (no CLI/host) still owns their history and can export it. These
pin the PURE helpers that back that flow — the storage key (dedup by conv_id),
the export bundle shape + filename, the live stats, and the onboarding tip ladder
(which mirrors src/trinity_local/tips.py: prereq-gated, one rung, seen-aware).

Loads the REAL background.js through node with a minimal chrome shim (same
pattern as test_background_provider_thread_url.py). Skips when node is absent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND = REPO_ROOT / "browser-extension" / "background.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _eval(expr: str):
    """Load background.js under a chrome shim and return JSON.stringify(<expr>)."""
    script = f"""
    console.log = () => {{}}; console.warn = () => {{}};
    global.chrome = {{ runtime: {{
      onMessage: {{ addListener: () => {{}} }},
      onMessageExternal: {{ addListener: () => {{}} }},
    }} }};
    const bg = require({json.dumps(str(BACKGROUND))});
    process.stdout.write(JSON.stringify({expr}));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def test_storage_key_dedups_by_conv_id():
    assert _eval('bg.captureStorageKey("claude", "abc-123")') == "cap:claude:abc-123"
    # Same provider+conv_id → same key → re-capture overwrites (no unbounded growth).
    a = _eval('bg.captureStorageKey("gemini", "0f1e")')
    b = _eval('bg.captureStorageKey("gemini", "0f1e")')
    assert a == b == "cap:gemini:0f1e"


def test_capture_stats_counts_per_provider():
    records = [
        {"provider": "claude", "conv_id": "a", "captured_at": "2026-06-16T01:00:00Z"},
        {"provider": "claude", "conv_id": "b", "captured_at": "2026-06-16T03:00:00Z"},
        {"provider": "chatgpt", "conv_id": "c", "captured_at": "2026-06-16T02:00:00Z"},
    ]
    stats = _eval(f"bg.extensionCaptureStats({json.dumps(records)})")
    assert stats["count"] == 3
    assert stats["providers"] == {"claude": 2, "chatgpt": 1}
    assert stats["last_at"] == "2026-06-16T03:00:00Z"  # the most recent


def test_transcript_bundle_shape_and_filename():
    records = [
        {"provider": "claude", "conv_id": "a", "captured_at": "t1", "payload": {"x": 1}},
        {"provider": "gemini", "conv_id": "b", "captured_at": "t2", "payload": {"y": 2}},
    ]
    out = _eval(f'bg.buildTranscriptBundle({json.dumps(records)}, "2026-06-16T03:04:05Z")')
    b = out["bundle"]
    assert b["schema"] == "trinity-transcripts-v1"
    assert b["count"] == 2 and b["providers"] == {"claude": 1, "gemini": 1}
    assert [c["conv_id"] for c in b["conversations"]] == ["a", "b"]
    assert b["conversations"][0]["payload"] == {"x": 1}
    # Visible `trinity/` subfolder (no dot), dated filename.
    assert out["filename"] == "trinity/transcripts-2026-06-16.json"


def test_tip_ladder_walks_the_funnel():
    nudge = _eval('bg.nextExtensionTip({captured: 0})')
    assert nudge["key"] == "capture-nudge"

    # captured but no CLI → push the install.
    install = _eval('bg.nextExtensionTip({captured: 4, hostPresent: false})')
    assert install["key"] == "install-cli"

    # CLI present, no lens → create-lens.
    lens = _eval('bg.nextExtensionTip({captured: 4, hostPresent: true, lensBuilt: false})')
    assert lens["key"] == "create-lens" and "lens-setup" in lens["cta"]

    # all adopted → nothing.
    assert _eval('bg.nextExtensionTip({captured: 4, hostPresent: true, lensBuilt: true})') is None


def test_tip_ladder_respects_seen():
    # capture-nudge eligible but dismissed → ladder yields nothing (no other rung fits yet).
    assert _eval('bg.nextExtensionTip({captured: 0, seen: ["capture-nudge"]})') is None
