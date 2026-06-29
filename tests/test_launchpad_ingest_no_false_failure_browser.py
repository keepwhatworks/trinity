"""A successful "Ingest transcripts" must NOT be reported as a failure.

Found 2026-06-17 driving the ingest path: clicking "Ingest transcripts" (settings
modal) fires `ingestOnce`, which calls `beginOperation({kind:'ingest'})` — and
beginOperation starts the COUNCIL status poller. But ingest-recent is
fire-and-forget: the host runs the bare command and writes NO
`council_status_<token>.js` (its allowlist entry passes no status_token). So the
poller 404s every tick and, at the 30s give-up cap, FALSELY flips a SUCCESSFUL scan
to "Transcript ingest failed — Council status unavailable — the dispatch may not
have started" (council wording on an ingest op, success reported as failure).

The CLASS: an operation that writes no status file must not be driven by the
status-poller, which guarantees a timeout-failure. The root-cause fix gives ingest
its own result handler — on a successful dispatch it stops polling and shows a
brief honest "scan started" confirmation; only a real dispatch FAILURE routes to
the shared rollback/banner.

This DRIVES a real successful ingest and asserts that within ~1.5s the operation is
the completed confirmation (NOT "running", NOT "failed"), with no council-worded
error — so it bites the bug WITHOUT waiting the 30s cap. Mutation-provable: rewire
`onResult` back to `handleDispatchResult` and at 1.5s the op is still "Transcript
ingest running" (heading != the confirmation) → reds.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_ingest_result_handler_is_wired_not_the_council_path():
    """CI-runnable canary: ingestOnce must use its own fire-and-forget result
    handler, not the council handleDispatchResult (which polls to a false fail)."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    assert "handleIngestResult" in src, "ingest lost its dedicated fire-and-forget result handler"
    assert "onResult: (r) => this.handleIngestResult(r)" in src, "ingestOnce no longer routes to handleIngestResult"
    assert "Transcript scan started" in src, "ingest completion heading missing"


pytestmark_browser = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_successful_ingest_shows_confirmation_not_false_failure(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(), encoding="utf-8")
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
                # A SUCCESSFUL ingest dispatch (extension present, ok).
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__={dispatch:(o)=>{"
                    " if(o&&o.onResult) setTimeout(()=>o.onResult("
                    "{tier:'extension', ok:true, response:{ok:true}}),10); }};"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                page.click("button[aria-label='Open settings']", timeout=5000)
                page.wait_for_timeout(300)
                page.click("button[aria-label='Ingest transcripts once now']", timeout=5000)
                page.wait_for_timeout(1500)  # well under the 30s give-up cap

                s = page.evaluate(
                    "() => { const ls = document.querySelector('.launch-status');"
                    " const b = document.querySelector('.actions button.button.primary');"
                    " return { text: ls ? ls.innerText : '', busyBtn: b ? b.disabled : null }; }"
                )
                # The fixed behavior: an immediate honest confirmation.
                assert "Transcript scan started" in s["text"], (
                    f"ingest didn't confirm — still polling toward the 30s false-fail? text={s['text']!r}"
                )
                # And NEVER the council-worded false failure.
                assert "ingest failed" not in s["text"].lower(), f"successful ingest reported as FAILED: {s['text']!r}"
                assert "Council status unavailable" not in s["text"], (
                    f"ingest surfaced the council-worded poll-timeout error: {s['text']!r}"
                )
                assert "running" not in s["text"].lower(), f"ingest still spinning (not resolved): {s['text']!r}"
                # busy clears → the Launch button is usable again, not stuck.
                assert s["busyBtn"] is False, "Launch button stuck disabled after a completed ingest"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
