"""A FAILED "Ingest transcripts" (Scan recent transcripts) dispatch must stay
CO-LOCATED with its own ingest context — NOT leak a bare, council-worded,
undismissable error into the council composer ribbon.

Class: #242(a) cross-surface-misattribution / NO-FEEDBACK-ON-FAILURE — the same
shape the Refresh-memory / Repair-extension (Iter 40) and lens-build Stop/Restart
(Iter 20) buttons were already fixed off `handleDispatchResult`. The ingest button
(settings modal) was the third leaker, found 2026-06-17 in the dispatch-handler
class sweep: driving "Ingest transcripts" with a FAILING extension dispatch,
`handleIngestResult` routed the failure through `handleDispatchResult`, which
`clearOperation()`s the ingest op and writes the raw error to `launchError` — a
string rendered ONLY in the COUNCIL composer `.launch-status` ribbon, with NO
heading (the ingest-aware `operationHeading` needs `operation`, now null) and NO
Dismiss button (`v-if="operation"`). So a failed transcript scan painted a bare
"capture host crashed (errno 13)" in the council composer, reading as if a COUNCIL
the user never launched had failed, with no way to clear it. The settings modal had
already closed (ingestOnce sets settingsOpen=false), so the user is on the home view
staring at an orphaned council-area error.

Root-cause fix (launchpad_template.handleIngestResult): on a genuine extension error
(tier==='extension' && !ok && reason !== 'native-host-unavailable'), KEEP the ingest
operation in a `status:'failed'` state with the error, so the ribbon shows the
ingest-aware "Transcript ingest failed" heading + the error + a working Dismiss —
co-located with its own context. The install-prompt / native-host-unavailable cases
(the dispatch couldn't route at all) still delegate to handleDispatchResult's
wire-up-the-extension banner.

Mutation-provable: reroute the extension-error path back through
`this.handleDispatchResult(r)` (the old behavior) → the ingest context + Dismiss
vanish and the bare error orphans in the council ribbon → the browser assert reds.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_ingest_failure_is_co_located_canary():
    """CI-runnable canary: the ingest-failure path must NOT collapse straight to
    handleDispatchResult — it must keep an ingest `failed` operation so the ribbon
    stays ingest-contextual (heading + Dismiss), not a bare council-ribbon leak."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    handler = src.split("handleIngestResult(r) {", 1)[1].split("\n        },", 1)[0]
    # The extension-error branch must set a failed ingest operation co-located in
    # the ribbon (NOT the bare-launchError path of handleDispatchResult).
    assert "isExtError" in handler, (
        "handleIngestResult no longer distinguishes the extension-error case — a "
        "failed ingest would route straight to handleDispatchResult and leak a "
        "council-worded error into the council composer ribbon"
    )
    assert "status: 'failed'" in handler, (
        "handleIngestResult no longer keeps a failed ingest operation — the ribbon "
        "loses its 'Transcript ingest failed' heading + Dismiss button"
    )
    assert "native-host-unavailable" in handler, (
        "handleIngestResult must still delegate the can't-route cases (install-prompt "
        "/ native-host-unavailable) to the wire-up banner — not swallow them as an "
        "ingest failure"
    )


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _render(tmp_path):
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(pp.parent)


@pytest.mark.slow
@pytest.mark.browser
def test_ingest_failure_stays_co_located_not_council_ribbon_leak(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    httpd, port = _render(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1100}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                # FAIL the ingest with a genuine extension error (NOT native-host /
                # install-prompt — those are the legit "wire up the extension" cases).
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__={dispatch:(o)=>{"
                    " if(o&&o.onResult) setTimeout(()=>o.onResult("
                    "  {tier:'extension', ok:false, response:{error:'capture host crashed (errno 13)'}}),10); }};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                page.click("button[aria-label='Open settings']", timeout=4000)
                page.wait_for_timeout(150)
                page.click("button[aria-label='Ingest transcripts once now']", timeout=4000)
                page.wait_for_timeout(500)  # past the 10ms onResult

                state = page.evaluate(
                    "() => {"
                    " const ribbon = document.querySelector('.launch-status');"
                    " const heading = [...document.querySelectorAll('.launch-status strong')]"
                    "   .map(s => s.innerText).join(' | ');"
                    " const dismiss = [...document.querySelectorAll('.launch-status button')]"
                    "   .some(b => /Dismiss/.test(b.innerText));"
                    " return {"
                    "   ribbon: ribbon ? ribbon.innerText : '',"
                    "   heading,"
                    "   hasDismiss: dismiss,"
                    " }; }"
                )

                # The error must be present AND carry the ingest-aware heading so it
                # reads as a transcript-scan failure, not a phantom council failure.
                assert "capture host crashed (errno 13)" in state["ribbon"], (
                    f"the ingest error is gone from the ribbon entirely: {state!r}"
                )
                assert "Transcript ingest failed" in state["heading"], (
                    "a FAILED 'Ingest transcripts' click showed NO ingest-context "
                    "heading — the council-worded error orphaned in the council "
                    "composer ribbon as if a council the user never launched had "
                    f"failed: {state!r}"
                )
                assert "Council failed" not in state["heading"], (
                    "the ingest failure mislabeled itself as a council failure: "
                    f"{state!r}"
                )
                # And the user must be able to clear it (the orphaned launchError
                # form had NO Dismiss button — operation was null).
                assert state["hasDismiss"], (
                    "a FAILED ingest left an undismissable error in the ribbon "
                    f"(no Dismiss button — operation was cleared): {state!r}"
                )
                assert not errs, f"console errors on a failed ingest: {errs}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
