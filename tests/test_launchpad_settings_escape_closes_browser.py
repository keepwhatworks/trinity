"""The settings modal must close on Escape — keyboard parity with the × button.

Founder context: a "can't close the modal" bug already bit when the × button was
pushed off-screen in the narrow side panel (the no-mouse-path failure). Pressing
Escape is the KEYBOARD sibling — a true modal overlay (z-index 1000, backdrop) that
can ONLY be dismissed by clicking the × or the backdrop is mouse-only and strands a
keyboard user. Standard modals close on Escape.

Before the fix, the global keydown(Escape) handler called ONLY `closeRail()` (the
mobile councils drawer) and never touched `settingsOpen`, so Escape did nothing
while the settings modal was open — confirmed by driving the real launchpad: opened
via the gear button, still present after `keyboard.press('Escape')`.

This drives the REAL petite-vue launchpad over http: opens the settings modal via
the gear @click, presses Escape, and asserts the modal element is GONE (v-if
unmounts when settingsOpen → false). A class-level assertion — any future
dismissible overlay that forgets its Escape path reds here.

Mutation-proof: revert the handler to `if (e.key === 'Escape') this.closeRail();`
(drop the `if (this.settingsOpen) { this.settingsOpen = false; ... }` branch) and
this test reds with "settings modal STILL OPEN after Escape" — the founder symptom.
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_settings_modal_closes_on_escape(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    html = render_launchpad_html(page_data=build_launchpad_payload())

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 720, "height": 1200}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )

                # Open the settings modal via the gear button.
                page.click("button[aria-label='Open settings']")
                page.wait_for_selector(".settings-modal", state="visible", timeout=3000)
                assert page.evaluate(
                    "() => !!document.querySelector('.settings-modal')"
                ), "settings modal did not open via the gear button"

                # Press Escape — the modal must close (keyboard parity with ×).
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                still_open = bool(
                    page.evaluate("() => !!document.querySelector('.settings-modal')")
                )
                assert not still_open, (
                    "settings modal STILL OPEN after Escape — the × is the only way out "
                    "(the keyboard sibling of the founder's 'can't close the modal' bug). "
                    "The global keydown(Escape) handler must close settingsOpen, not just "
                    "the rail drawer."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
