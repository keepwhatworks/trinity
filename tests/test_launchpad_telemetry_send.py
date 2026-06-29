"""Browser-level guard for the #231 privacy invariant: the launchpad must make
NO outbound telemetry request from the browser unless an endpoint was explicitly
provisioned (GA4 creds or a custom collector).

`test_telemetry_no_pii.py` guards this at the DATA layer — it asserts the
`endpoint` is stripped from `launchpad_telemetry_state()` without creds and
`_browser_send_enabled()` is False. But the actual gate is a line of JS
(`if (!settings.sharing_enabled || !settings.endpoint) return;`) running in the
page. A refactor that constructed a fallback URL, moved the guard, or read the
endpoint from a different field would keep every data-level test green while the
browser silently started POSTing to a collector. This exercises the gate where
it executes: the default launchpad fires ZERO external requests on load.

Non-vacuous by construction: the second test provisions a custom endpoint and
asserts the browser DOES attempt the send (intercepted + aborted — nothing ever
leaves the machine). That proves the default silence is the WITHHELD endpoint,
not dead send code. Categorical-only payload (guaranteed by the data-layer
guards); the request is aborted before any network egress regardless.

Slow-marked (spawns portal-html + chromium); skips when Playwright/chromium are
absent. Isolated synthetic home — no founder data, no real telemetry.
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


def _load_and_capture_external(pages: Path):
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


def test_provisioned_endpoint_does_fire_the_browser_send():
    # Non-vacuous companion: prove the send path is REACHABLE, so the default
    # test's silence is the withheld endpoint, not dead code. With a custom
    # collector provisioned, the browser must attempt the POST (aborted here).
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_launchpad(home, {"TRINITY_TELEMETRY_ENDPOINT": _CUSTOM_ENDPOINT})
    external = _load_and_capture_external(pages)
    hit = [u for _, u in external if "trinity-telemetry-probe.test" in u]
    assert hit, (
        "a provisioned custom endpoint did NOT trigger any browser send — the "
        "send path may be dead, which would make the default-case guard vacuous. "
        f"external requests seen: {external[:5]}"
    )
