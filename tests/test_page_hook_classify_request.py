"""Node-based unit test for browser-extension/page-hook.js classifyRequest().

classifyRequest is the capture-DECISION: for each page fetch/XHR it decides
provider + kind (canonical / stream / sidebar_list / skip). It's load-bearing
AND provider-format-coupled — the comments in page-hook.js record repeated
PRODUCTION breakage from provider endpoint migrations ("caught live 2026-05-23:
original pattern matched nothing"; the chatgpt `/f/conversation` move; the
claude `/chat_conversations_v2` upgrade; the "no-stars sidebar wiped the list"
incident). Yet the logic was untested (only credential-safety read page-hook).

The most fragile invariant: the chatgpt plural sidebar `/backend-api/conversations`
must classify as sidebar_list, NOT be swallowed by the singular
`/backend-api/conversation` stream `includes()` over-match — which works ONLY
because the `/f/conversation` pattern is listed FIRST (so pattern-1's
endsWith-sidebar check fires before pattern-2's over-match). Reorder the patterns
and chatgpt sidebar capture silently breaks. This pins it.

Runs the REAL page-hook.js through node with browser-global shims (window /
location / XMLHttpRequest), via the module.exports guard. Skips when node is
absent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE_HOOK = REPO_ROOT / "browser-extension" / "page-hook.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _classify(cases: list[tuple[str, str]]) -> list:
    """Run classifyRequest in node against (url, method) pairs."""
    script = f"""
    // Stub console so page-hook's load-time "[trinity-hook] installed" log
    // doesn't pollute stdout (we parse stdout as JSON).
    console.log = () => {{}}; console.warn = () => {{}};
    global.window = {{}};
    global.location = {{ href: "https://example.com/", hostname: "example.com" }};
    function XHR() {{}}
    XHR.prototype = {{ open() {{}}, send() {{}}, addEventListener() {{}} }};
    global.XMLHttpRequest = XHR;
    const {{ classifyRequest }} = require({json.dumps(str(PAGE_HOOK))});
    const cases = {json.dumps(cases)};
    process.stdout.write(JSON.stringify(cases.map(([u, m]) => classifyRequest(u, m))));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not on PATH")


def test_page_hook_exposes_classify_request_for_node():
    # The module.exports guard must survive in node (browser-global shims +
    # the IIFE loading cleanly) or the whole capture-decision is untestable.
    result = _classify([("https://example.com/nope", "GET")])
    assert result == [None]


def test_claude_endpoint_classification():
    canon, stream, sb_v2, sb_v1 = _classify([
        ("https://claude.ai/api/organizations/o/chat_conversations/abc123", "GET"),
        ("https://claude.ai/api/organizations/o/chat_conversations/abc123/completion", "POST"),
        ("https://claude.ai/api/organizations/o/chat_conversations_v2?limit=30", "GET"),
        ("https://claude.ai/api/organizations/o/chat_conversations?limit=30", "GET"),
    ])
    assert canon == {"provider": "claude", "kind": "canonical"}
    assert stream == {"provider": "claude", "kind": "stream"}
    # Both the v2 and the legacy sidebar endpoints classify as sidebar_list —
    # the v2 upgrade (2026-05-23) and the no-star variant both matter.
    assert sb_v2 == {"provider": "claude", "kind": "sidebar_list"}
    assert sb_v1 == {"provider": "claude", "kind": "sidebar_list"}


def test_chatgpt_endpoint_classification():
    canon, stream_f, sidebar = _classify([
        ("https://chatgpt.com/backend-api/conversation/abc-xyz", "GET"),
        ("https://chatgpt.com/backend-api/f/conversation", "POST"),
        ("https://chatgpt.com/backend-api/conversations?limit=20", "GET"),
    ])
    assert canon == {"provider": "chatgpt", "kind": "canonical"}
    assert stream_f == {"provider": "chatgpt", "kind": "stream"}
    assert sidebar == {"provider": "chatgpt", "kind": "sidebar_list"}


def test_chatgpt_plural_sidebar_not_swallowed_by_singular_stream_overmatch():
    """THE fragile invariant: `/backend-api/conversations` (plural, the sidebar
    list) CONTAINS `/backend-api/conversation` (singular, the stream pattern) as
    a substring, so the stream `includes()` check would over-match it — UNLESS
    the `/f/conversation` pattern is checked first (its endsWith-sidebar branch
    fires before the singular pattern's over-match). A pattern reorder breaks
    chatgpt sidebar capture silently; this guards it."""
    [sidebar] = _classify([("https://chatgpt.com/backend-api/conversations?limit=28", "GET")])
    assert sidebar == {"provider": "chatgpt", "kind": "sidebar_list"}, (
        "chatgpt plural sidebar mis-classified — the /f/conversation pattern "
        "must precede the singular /backend-api/conversation pattern so the "
        "sidebar endsWith check wins over the stream includes() over-match"
    )


def test_gemini_streamgenerate_classified_as_stream():
    # StreamGenerate is the real conversation endpoint (added 2026-05-23 to fix
    # empty assistant_text); it must classify as a stream so the adapter runs.
    [stream] = _classify([
        ("https://gemini.google.com/_/BardChatUi/data/"
         "assistant.lamda.BardFrontendService/StreamGenerate?bl=x&rt=c", "POST"),
    ])
    assert stream == {"provider": "gemini", "kind": "stream"}


def test_unrelated_requests_not_captured():
    results = _classify([
        ("https://example.com/api/whatever", "GET"),
        ("https://claude.ai/static/main.js", "GET"),
        ("https://chatgpt.com/favicon.ico", "GET"),
    ])
    assert results == [None, None, None]
