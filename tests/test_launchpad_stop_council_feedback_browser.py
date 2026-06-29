"""Clicking "Stop council" must ACK immediately — not look like a no-op.

Found 2026-06-17 driving a running council: clicking "Stop council" dispatches
`stop-council` (confirmed firing) but the UI showed NO change — still "Council
running", the spinner kept cycling its witty messages, the button still read "Stop
council". The actual cancel only lands LATER, when the host writes a 'canceled'
status the poller is waiting on. So for the whole gap the user can't tell their
click did anything (they'd click again, or assume Stop is broken).

The CLASS: a user action whose RESULT is deferred to an async poller must give an
IMMEDIATE acknowledgment, or it reads as a no-op (the founder's NO-FEEDBACK
lineage). The root-cause fix is a `stopRequested` flag set the instant Stop is
clicked: the button flips to "Stopping…" (disabled, so no double-fire) and the
status message pins "Stopping the council…" until the poller finalizes to canceled
(begin/clear reset it).

This DRIVES a real running council, clicks Stop, and asserts the immediate ACK
(<1s). Mutation-provable: delete `this.stopRequested = true` and the status keeps
cycling a "running" message + the button stays "Stop council" → reds.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_stop_request_flag_is_wired():
    """CI-runnable canary: the immediate-ACK plumbing must exist."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    assert "stopRequested: false" in src, "lost the stopRequested data flag"
    assert "this.stopRequested = true" in src, "Stop click no longer sets the ACK flag"
    assert "Stopping the council" in src, "no 'Stopping…' status override"
    assert "stopRequested ? 'Stopping" in src, "Stop button label not bound to stopRequested"


pytestmark_browser = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_stop_council_acks_immediately(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    pd["activeOperation"] = {
        "kind": "council", "status": "running", "statusToken": "launch_runningX",
        "task_text": "Compare three rate-limiting strategies", "memberOrder": ["claude", "codex"],
    }
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1100}).new_page()
                # The stop dispatch succeeds but defers the actual cancel (the host
                # writes 'canceled' later) — exactly the gap the ACK fills.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__={dispatch:(o)=>{"
                    " if(o&&o.onResult) setTimeout(()=>o.onResult({tier:'extension', ok:true}),10); }};"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                page.click("button:has-text('Stop council')", timeout=5000)
                page.wait_for_timeout(500)  # well before any poller round-trip

                s = page.evaluate(
                    # NOTE: there are TWO .status-message nodes — the <strong>
                    # heading ("Council running") and the <p> currentStatusMessage.
                    # Read the WHOLE launch-status text so the assertion sees the
                    # cycling message line, not just the heading.
                    "() => { const ls = document.querySelector('.launch-status');"
                    " const stop = [...document.querySelectorAll('.launch-status-actions button')]"
                    ".find(b => /stop/i.test(b.textContent));"
                    " return { msg: ls ? ls.innerText : '', stopLabel: stop ? stop.textContent.trim() : null,"
                    " stopDisabled: stop ? stop.disabled : null }; }"
                )
                # Immediate honest status — NOT a cycling 'running' line.
                assert "Stopping the council" in s["msg"], (
                    f"Stop gave no immediate status — looks like a no-op: {s['msg']!r}"
                )
                # The button itself ACKs + locks against a double-fire.
                assert s["stopLabel"] == "Stopping…", f"Stop button didn't ACK the click: {s['stopLabel']!r}"
                assert s["stopDisabled"] is True, "Stop button still clickable after a stop request (double-fire)"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
