"""Node-based unit test for browser-extension/background.js providerThreadUrl().

background.js is the MV3 service worker — the last link in the capture+dispatch
chain (content-script → background → native host). `providerThreadUrl(provider,
conv_id)` builds the URL the CURRENT-TAB-SYNC navigates to when re-capturing a
missing conversation (the sync-pill "N to sync" flow). If a provider migrates its
conversation URL format, sync silently navigates to a 404, the conversation is
never re-captured, and "N to sync" never decreases — the same provider-migration
breakage the page-hook comments document for the capture endpoints. It was
untested.

The cross-surface invariant this pins: the gemini URL `/app/<conv_id>` MUST match
the path the content-script's gemini sidebar scrape re-finds (it extracts conv_id
from `a[href*="/app/"]`). If providerThreadUrl and the scrape diverge on the path
segment, sync navigates somewhere the scrape can't re-detect → the loop never
converges.

Loads the REAL background.js through node with a minimal chrome shim (the two
onMessage listeners it registers at load). Skips when node is absent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND = REPO_ROOT / "browser-extension" / "background.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not on PATH")


def _provider_thread_urls(cases: list[tuple[str, str]]) -> list:
    script = f"""
    console.log = () => {{}}; console.warn = () => {{}};
    global.chrome = {{ runtime: {{
      onMessage: {{ addListener: () => {{}} }},
      onMessageExternal: {{ addListener: () => {{}} }},
    }} }};
    const {{ providerThreadUrl }} = require({json.dumps(str(BACKGROUND))});
    const cases = {json.dumps(cases)};
    process.stdout.write(JSON.stringify(cases.map(([p, c]) => providerThreadUrl(p, c))));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def test_provider_thread_urls_match_real_conversation_url_formats():
    claude, chatgpt, gemini, unknown = _provider_thread_urls([
        ("claude", "abc-123"),
        ("chatgpt", "c-xyz-789"),
        ("gemini", "04f4e5eaa1a6a530"),
        ("nope", "whatever"),
    ])
    # The real per-provider conversation page URLs (a migration here breaks sync).
    assert claude == "https://claude.ai/chat/abc-123"
    assert chatgpt == "https://chatgpt.com/c/c-xyz-789"
    assert gemini == "https://gemini.google.com/app/04f4e5eaa1a6a530"
    assert unknown is None  # unknown provider → no nav target, not a bad URL


def test_gemini_thread_url_path_matches_content_script_scrape():
    """Cross-surface consistency: the gemini thread URL providerThreadUrl
    navigates to must use the SAME /app/<id> path the content-script sidebar
    scrape re-finds (a[href*="/app/"]). If these diverge, the sync→re-capture
    loop can't converge — sync navigates somewhere the scrape can't re-detect."""
    [gemini] = _provider_thread_urls([("gemini", "087a73a78d0e878f")])
    # Path segment the scrape keys on:
    assert "/app/087a73a78d0e878f" in gemini, (
        "gemini thread URL diverged from the content-script /app/ scrape path — "
        "current-tab-sync would navigate where readGeminiSidebar can't re-find it"
    )
    # And a hex conv_id round-trips through the scrape's regex (/app/<hex 8+>).
    import re
    m = re.search(r"/app/([0-9a-f]{8,})", gemini)
    assert m and m.group(1) == "087a73a78d0e878f"
