"""The launchpad page makes no outbound telemetry request, ever.

Its own sender (maybeSendTelemetry) could POST launchpad_view and elo_snapshot events to a
self-configured collector (TRINITY_TELEMETRY_ENDPOINT). That path was removed on 2026-10-15
(founder-approved); CLI and MCP events go to Google Analytics from Python. This drives the real
page in a browser and captures every non-local request.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# A reserved-ish host that won't resolve and dodges the page's own
# example/localhost/invalid abort guard (so the send actually fires in test B).
_CUSTOM_ENDPOINT = "https://collector.trinity-telemetry-probe.test/collect"


def _render_launchpad(home: Path, extra_env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Default OFF so the default-install case is deterministic regardless of the
    # CI host's environment.
    for k in ("TRINITY_GA4_MEASUREMENT_ID", "TRINITY_GA4_API_SECRET", "TRINITY_TELEMETRY_ENDPOINT"):
        env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "launchpad.html").exists()
    return pages


def _load_and_capture_external(pages: Path, probe: str | None = None):
    """Serve the home over loopback, load the launchpad with every non-local
    request aborted+captured, return the list of external (method, url)."""
    import functools
    import http.server
    import socketserver
    import threading

    from playwright.sync_api import sync_playwright

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(pages.parent))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)  # ephemeral port (no collision between tests)
    httpd.allow_reuse_address = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    external: list[tuple[str, str]] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context().new_page()

                def route(r):
                    url = r.request.url
                    if (url.startswith(f"http://127.0.0.1:{port}")
                            or url.startswith(("data:", "blob:", "about:"))):
                        r.continue_()
                    else:
                        external.append((r.request.method, url))
                        r.abort()

                page.route("**/*", route)
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle")
                page.wait_for_timeout(2500)  # let any deferred send fire
                if probe:   # positive control: a request the page makes must be captured
                    page.evaluate("u => fetch(u).catch(() => null)", probe)
                    page.wait_for_timeout(500)
            finally:
                browser.close()
    finally:
        httpd.shutdown()
    return external


def test_default_launchpad_sends_no_telemetry_from_browser():
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_launchpad(home)  # no creds → endpoint withheld
    external = _load_and_capture_external(pages)
    assert external == [], (
        "the default launchpad made outbound external request(s) on load — the "
        f"#231 no-browser-send invariant regressed: {external[:5]}"
    )


def test_the_page_sends_nothing_even_with_the_old_collector_variable_and_ga4_creds():
    """The launchpad's own sender and its collector (TRINITY_TELEMETRY_ENDPOINT) were removed
    2026-10-15: whatever the environment, the page makes no outbound request. The probe is the
    positive control: a request the page does make is captured, so silence is not a blind spot."""
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_launchpad(home, {"TRINITY_TELEMETRY_ENDPOINT": _CUSTOM_ENDPOINT,
                                     "TRINITY_GA4_MEASUREMENT_ID": "G-PROBE",
                                     "TRINITY_GA4_API_SECRET": "probe-secret"})
    probe = "https://capture-control.trinity-telemetry-probe.test/x"
    external = _load_and_capture_external(pages, probe=probe)
    urls = [u for _, u in external]
    assert probe in urls, f"the capture harness missed the control request: {urls[:5]}"
    assert [u for u in urls if u != probe] == [], f"the page sent telemetry: {urls[:5]}"
