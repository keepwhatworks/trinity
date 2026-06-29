"""Node-based unit test for background.js `isLaunchpadSender` — the confused-
deputy defense on the ACTION path.

Trinity's extension exposes two inbound surfaces to the native host:
  • CAPTURES: page-hook (MAIN world) → content-script → onMessage → host. A
    malicious script on a provider page CAN forge these (it shares the MAIN-world
    context with page-hook), but the blast radius is bounded to corpus pollution,
    and a "token" there is theater — a MAIN-world secret can't be hidden from the
    page. Downstream sanitization (capture_host path-traversal guard, ingest
    filters) is the real defense.
  • ACTIONS: launchpad → onMessageExternal → host (council-launch, ingest-recent,
    …). THIS is the dangerous confused-deputy surface — it can drive the CLI. It
    is gated TWICE: the manifest's `externally_connectable` (file:// + localhost
    only, so a remote page's message never even reaches the worker) AND
    `isLaunchpadSender(sender)`, which re-checks the sender URL as defense in
    depth (Phase 8).

`isLaunchpadSender` was solid but UNTESTED in fast/CI — only a gated real-Chrome
ping smoke (`test_chrome_extension_smoke.py`, skipped without TRINITY_SLOW +
chrome) exercised the external path, and it never tried a HOSTILE sender. So a
refactor that loosened the origin check (e.g. `includes` instead of `startsWith`,
or dropping the query/hash strip) would weaken the action-path confused-deputy
defense with nothing failing. This pins it: every launchpad origin ACCEPTS, every
attack vector REJECTS. Mirrors [[capture_host_untrusted_boundary]] /
[[test_the_boundary_and_the_action]] — assert ACCEPT *and* REJECT.
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


def _is_launchpad_sender(urls: list[str]) -> list[bool]:
    script = f"""
    console.log = () => {{}}; console.warn = () => {{}};
    global.chrome = {{ runtime: {{
      onMessage: {{ addListener: () => {{}} }},
      onMessageExternal: {{ addListener: () => {{}} }},
      getManifest: () => ({{ version: "0.0.0" }}),
    }} }};
    const {{ isLaunchpadSender }} = require({json.dumps(str(BACKGROUND))});
    const urls = {json.dumps(urls)};
    process.stdout.write(JSON.stringify(urls.map((u) => isLaunchpadSender({{ url: u }}))));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


# The launchpad is served two ways: a file:// page under ~/.trinity, or
# `trinity-local serve` on 127.0.0.1/localhost. Only the exact Trinity page
# paths under those origins may dispatch actions.
ACCEPT = [
    "file:///Users/x/.trinity/portal_pages/launchpad.html",
    "file:///Users/someone/.trinity/review_pages/live_council.html",
    "http://localhost:8765/portal_pages/launchpad.html",
    "http://127.0.0.1:8765/portal_pages/launchpad.html",
    "http://localhost/review_pages/live_council.html",
    # query/hash are stripped before the suffix check, so a real launchpad URL
    # carrying a cache-buster still authenticates.
    "http://localhost:8765/portal_pages/launchpad.html?t=123",
]

REJECT = [
    # Remote origins — the dangerous case. A page can't dispatch CLI actions.
    "https://evil.com/portal_pages/launchpad.html",
    "https://claude.ai/portal_pages/launchpad.html",
    # localhost look-alikes (subdomain / userinfo) must NOT pass startsWith.
    "http://localhost.evil.com/portal_pages/launchpad.html",
    "http://localhost.evil.com:8765/portal_pages/launchpad.html",
    "http://localhost@evil.com/portal_pages/launchpad.html",
    # https-localhost: only http://localhost|127.0.0.1 is the local serve origin.
    "https://localhost/portal_pages/launchpad.html",
    # file:// outside ~/.trinity (e.g. a downloaded HTML file) can't dispatch.
    "file:///Users/x/Downloads/portal_pages/launchpad.html",
    # right origin, wrong page — only the two Trinity pages, not any html.
    "file:///Users/x/.trinity/evil.html",
    "http://localhost:8765/anything-else.html",
    # hash-tail trick: the real path is on evil.com; the launchpad suffix is only
    # in the fragment, which is stripped → no suffix match.
    "https://evil.com/x#/portal_pages/launchpad.html",
    # suffix present but NOT at the end (path-confusion).
    "http://localhost:8765/portal_pages/launchpad.html.evil.com/x",
    "",  # empty sender url
]


def test_launchpad_origins_accepted():
    results = _is_launchpad_sender(ACCEPT)
    bad = [u for u, ok in zip(ACCEPT, results) if not ok]
    assert not bad, f"legit launchpad senders were REJECTED (action dispatch broken): {bad}"


def test_hostile_senders_rejected():
    results = _is_launchpad_sender(REJECT)
    leaked = [u for u, ok in zip(REJECT, results) if ok]
    assert not leaked, (
        "confused-deputy hole — these senders were ACCEPTED and could dispatch "
        f"CLI actions to the native host: {leaked}"
    )
