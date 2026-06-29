"""Node-based unit test for browser-extension/content-script.js readGeminiSidebar().

content-script.js bridges page-hook (MAIN world) → background (service worker),
and on gemini.google.com it DOM-scrapes the recent-conversations sidebar (gemini
exposes no network list endpoint). readGeminiSidebar extracts conv_id from each
`a[href*="/app/"]` anchor — it feeds the `_sidebar.json` the sync-pill reads for
its "N new to sync" count.

The load-bearing + non-obvious invariant: the conv_id regex is deliberately
NARROW (`/app/<hex 8+>`), UNLIKE the gemini ADAPTER's broad `[A-Za-z0-9_-]{6,}`.
The adapter parses a KNOWN-conversation page URL; this scrapes ARBITRARY DOM
/app/ anchors, which include nav links (/app/settings, /app/new). Hex-only
rejects those while matching real gemini conv_ids (hex-16; verified 173/173 real
sidebar items matched). Broadening it (to "fix" the apparent inconsistency with
the adapter) would capture /app/settings as a PHANTOM conversation and the
sync-pill would over-count — this test pins the deliberate narrowness.

Loads the REAL content-script through node with browser-global shims (window /
location set to a NON-gemini host so the polling block is skipped + document).
Skips when node is absent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_SCRIPT = REPO_ROOT / "browser-extension" / "content-script.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(not _node_available(), reason="node not on PATH")


def _scrape(anchors: list[dict]) -> list[dict]:
    """Run readGeminiSidebar against synthetic anchors ({href, text} dicts)."""
    script = f"""
    console.log = () => {{}}; console.warn = () => {{}};
    global.window = {{ addEventListener: () => {{}} }};
    global.location = {{ hostname: "example.com" }};  // not gemini → skip poller
    const {{ readGeminiSidebar }} = require({json.dumps(str(CONTENT_SCRIPT))});
    const specs = {json.dumps(anchors)};
    const anchors = specs.map(s => ({{
      getAttribute: (k) => k === "href" ? s.href : (k === "title" ? (s.title || "") : null),
      textContent: s.text || "",
    }}));
    global.document = {{ querySelectorAll: () => anchors }};
    process.stdout.write(JSON.stringify(readGeminiSidebar()));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def test_real_hex_conversations_captured():
    items = _scrape([
        {"href": "/app/04f4e5eaa1a6a530", "text": "Telugu practice"},
        {"href": "https://gemini.google.com/app/087a73a78d0e878f", "text": "Parser redesign"},
    ])
    assert items == [
        {"conv_id": "04f4e5eaa1a6a530", "title": "Telugu practice"},
        {"conv_id": "087a73a78d0e878f", "title": "Parser redesign"},
    ]


def test_nav_links_rejected_by_hex_regex():
    """The deliberate narrowness: nav-link /app/ anchors (non-hex slugs) must
    NOT become phantom conversations. Broadening the regex breaks this."""
    items = _scrape([
        {"href": "/app/settings", "text": "Settings"},
        {"href": "/app/new", "text": "New chat"},
        {"href": "/app/MixedCaseSlug", "text": "Some page"},  # uppercase → not hex
        {"href": "/app/04f4e5eaa1a6a530", "text": "Real conversation"},
    ])
    assert items == [{"conv_id": "04f4e5eaa1a6a530", "title": "Real conversation"}], (
        "nav-link /app/ anchors leaked into the sidebar — the conv_id regex was "
        "broadened past hex-only and now captures phantom conversations"
    )


def test_duplicate_conv_ids_deduped():
    items = _scrape([
        {"href": "/app/04f4e5eaa1a6a530", "text": "First"},
        {"href": "/app/04f4e5eaa1a6a530", "text": "Same conv, different link"},
    ])
    assert items == [{"conv_id": "04f4e5eaa1a6a530", "title": "First"}]


def test_titleless_anchor_skipped():
    # Falls back to the title attribute, then skips entirely when both empty.
    items = _scrape([
        {"href": "/app/04f4e5eaa1a6a530", "text": "", "title": ""},
        {"href": "/app/087a73a78d0e878f", "text": "", "title": "From title attr"},
    ])
    assert items == [{"conv_id": "087a73a78d0e878f", "title": "From title attr"}]


def test_short_hex_below_floor_rejected():
    # The {8,} floor rejects short ids (avoids matching truncated/garbage slugs).
    items = _scrape([
        {"href": "/app/abc123", "text": "too short (6 hex)"},
        {"href": "/app/abc12345", "text": "exactly 8 hex"},
    ])
    assert items == [{"conv_id": "abc12345", "title": "exactly 8 hex"}]
