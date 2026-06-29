"""Gated real-Chrome smoke for the launchpad → onMessageExternal path.

Council `bf1ab3f4dd70f75e` flagged this as the v1.0 must-add: the
runtime boundary most likely to break the whole transition. CI
doesn't have Chrome available, so the test is *gated* behind the
`TRINITY_CHROME_SMOKE=1` env var and a real Chrome binary.

To run manually:

  1. Install the optional Stagehand driver:
       cd browser-extension && npm install
  2. Export the 32-char extension ID for this repo's unpacked
     `browser-extension/` path.
  3. Run:
       export TRINITY_CHROME_SMOKE=1
       export TRINITY_EXTENSION_ID=<copied-id>
       trinity-local install-extension --extension-id "$TRINITY_EXTENSION_ID"
       trinity-local portal-html   # ensure launchpad.html exists
       pytest tests/test_chrome_extension_smoke.py -v

What the test verifies (the bare minimum codex flagged as acceptable):

  - A Chrome binary is launchable through Stagehand's local browser path.
  - The local launchpad page loads over http://127.0.0.1.
  - A `chrome.runtime.sendMessage(<extensionId>, {type:"trinity-ping"})`
    from that page returns `{ok: true, type: "trinity-pong"}` via the
    extension's onMessageExternal handler (Phase 4 implementation).

This is the one test we don't have that the council said we should.
It does NOT exercise the full action path (that would require the
native messaging host to be registered AND launchable — additional
setup). Pinging proves the extension/launchpad/onMessageExternal triangle
works; the action path is exercised by test_phase8_integration's
subprocess-based Native Messaging frame round-trip.
"""
from __future__ import annotations

import functools
import http.server
import os
import socketserver
import subprocess
import shutil
import threading
from pathlib import Path

import pytest


SMOKE_ENABLED = os.environ.get("TRINITY_CHROME_SMOKE") == "1"
EXTENSION_ID = os.environ.get("TRINITY_EXTENSION_ID", "")

pytestmark = pytest.mark.skipif(
    not SMOKE_ENABLED,
    reason="Real-Chrome smoke gated behind TRINITY_CHROME_SMOKE=1 "
           "(CI has no Chrome; run manually per file docstring).",
)


def _find_chrome() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib method name
        return


class _ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class _LaunchpadServer:
    def __init__(self, root: Path):
        handler = functools.partial(_QuietHandler, directory=str(root))
        self._server = _ReusableTCPServer(("127.0.0.1", 0), handler)
        self.url = (
            f"http://127.0.0.1:{self._server.server_address[1]}"
            "/portal_pages/launchpad.html"
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="trinity-launchpad-smoke",
            daemon=True,
        )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)


def _stagehand_available(ext_dir: Path) -> bool:
    result = subprocess.run(
        [
            "node",
            "-e",
            (
                "import('@browserbasehq/stagehand')"
                ".then(()=>process.exit(0))"
                ".catch(()=>process.exit(1))"
            ),
        ],
        cwd=ext_dir,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def test_chrome_binary_is_available():
    """Sanity: a Chrome/Chromium binary must be present before the test
    body has anything to drive."""
    chrome = _find_chrome()
    assert chrome, "No Chrome/Chromium binary found. Install Chrome or set $PATH."


def test_extension_id_env_is_set():
    """TRINITY_EXTENSION_ID is the 32-char hash Chrome assigns to the
    unpacked extension at chrome://extensions. Tests can't infer it
    automatically — the user has to copy + export it before running."""
    assert EXTENSION_ID, (
        "Export TRINITY_EXTENSION_ID=<32-char id from chrome://extensions> "
        "before running this smoke."
    )
    import re
    assert re.fullmatch(r"[a-p]{32}", EXTENSION_ID), (
        f"TRINITY_EXTENSION_ID={EXTENSION_ID!r} does not match Chrome's "
        "32-char a-p extension-ID format."
    )


def test_launchpad_file_exists():
    """The smoke can't run if portal-html hasn't been called. Surface
    the precise hint."""
    home = Path(os.environ.get("TRINITY_HOME") or Path.home() / ".trinity")
    launchpad = home / "portal_pages" / "launchpad.html"
    assert launchpad.exists(), (
        f"Launchpad missing at {launchpad}. Run `trinity-local portal-html` "
        "first to generate it."
    )


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="This smoke uses Stagehand via node; install node first.",
)
def test_launchpad_can_ping_extension_via_stagehand():
    """Launch Chrome with the unpacked extension via Stagehand and assert
    the launchpad can reach background.js through onMessageExternal."""
    repo = Path(__file__).resolve().parent.parent
    ext_dir = repo / "browser-extension"
    if not _stagehand_available(ext_dir):
        pytest.skip(
            "Optional Stagehand driver not installed. Run "
            "`cd browser-extension && npm install`."
        )

    chrome = _find_chrome()
    assert chrome, "No Chrome/Chromium binary found. Install Chrome or set $PATH."

    home = Path(os.environ.get("TRINITY_HOME") or Path.home() / ".trinity")
    with _LaunchpadServer(home) as server:
        env = dict(os.environ)
        env.update({
            "TRINITY_CHROME_EXECUTABLE_PATH": chrome,
            "TRINITY_EXTENSION_DIR": str(ext_dir),
            "TRINITY_LAUNCHPAD_URL": server.url,
            "TRINITY_STAGEHAND_HEADLESS": os.environ.get(
                "TRINITY_STAGEHAND_HEADLESS",
                "0",
            ),
        })
        result = subprocess.run(
            ["node", str(ext_dir / "smoke-stagehand.mjs")],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert result.returncode == 0, (
        "Stagehand Chrome smoke failed.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
