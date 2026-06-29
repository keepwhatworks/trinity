"""Browser guard: a FAILED Stop-council dispatch must NOT vanish the still-running
council display — it must keep the running ribbon, recover the Stop button, and say
honestly that the council is still running.

THE BUG (found 2026-06-22 driving the launchpad-home running-council STOP path with a
failing stop dispatch — a state a real user reaches via the init() resume path: open
the launchpad, find a council already running from disk, click Stop while the native
host happens to be unwired / unreachable):

  Step A — a council is running; the user clicks "Stop council". ``stopCurrentCouncil``
           sets ``stopRequested=true`` (button → disabled "Stopping…") and dispatches
           ``stop-council``.
  Step B — the stop dispatch FAILS (``{tier:'extension', ok:false,
           reason:'native-host-unavailable'}`` — extension present, host not wired; or
           ``{tier:'install-prompt', ok:false}`` on the file:// launchpad with no
           extension).

THE DEFECT: the stop dispatch routed its failure through ``handleDispatchResult`` —
whose rollback is the LAUNCH semantics (``clearOperation`` drops the optimistic
operation that never started). On a failed STOP the council is REAL and STILL RUNNING,
so ``clearOperation`` made the spinner + "Open council page" + "Stop council" VANISH and
re-enabled Launch — the UI silently read as "the council stopped", while the council
kept running on the backend (the stop never reached it). NO-FEEDBACK + wrong-state: the
only thing that surfaced was a generic "host not registered" banner about wiring up
dispatch, which says nothing about the stop having failed.

THE FIX: ``stopCurrentCouncil`` routes its result through a STOP-specific
``handleStopResult`` that, on failure, KEEPS the running operation, resets
``stopRequested`` (so "Stop council" recovers from "Stopping…" and is retryable), and
sets a launchError that says the council is still running.

Single-dispatch launch tests stay green (the launch rollback is correct); only the
RUNNING-council → failed-STOP path exposes this. Mutation-proven: reverting the fix
(routing stop failure back through handleDispatchResult) makes the running display
vanish and reds the bite below.

Stubs ``window.__TRINITY_DISPATCH__`` before any click so it never touches the real
extension / a real council. Serves an isolated, PII-free synthetic home over http.
Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_TASK = "Should we ship the new router this week or wait for more eval data?"

# The dispatcher: a council LAUNCH succeeds (so the optimistic operation is NOT rolled
# back and a real running display appears), then the STOP dispatch FAILS with
# native-host-unavailable. __trinityReload is a no-op so no path navigates the page away
# mid-assert.
_STUB = (
    "window.__TRINITY_DISPATCH_CALLS__=[];"
    "window.__trinityReload=function(){{}};"
    "window.__TRINITY_DISPATCH__={{dispatch:function(o){{"
    "  var kind=o&&o.extensionAction?o.extensionAction.kind:null;"
    "  window.__TRINITY_DISPATCH_CALLS__.push(kind);"
    "  var res=(kind==='stop-council')"
    "    ?{{tier:'extension', ok:false, reason:'native-host-unavailable', response:{{}}}}"
    "    :{{tier:'extension', ok:true, response:{{ok:true}}}};"
    "  if(o&&o.onResult)setTimeout(function(){{o.onResult(res);}},15);"
    "}}}};"
)


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    serve_root = tmp_path / "serve"
    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)
    return serve_root


_SNAP_JS = (
    "() => {"
    "  const sec = document.querySelector('.launch-status');"
    "  const spin = document.querySelector('.launch-status .spinner');"
    "  const stopBtn = [...document.querySelectorAll('.launch-status button')]"
    "    .find(b => /Stop council|Stopping/.test(b.textContent));"
    "  const launchBtn = [...document.querySelectorAll('button.primary')]"
    "    .find(b => /Launch Council|Council in progress/i.test(b.textContent));"
    "  return {"
    "    runningRibbonVisible: !!sec && sec.offsetParent !== null,"
    "    spinnerVisible: !!spin && spin.offsetParent !== null,"
    "    stopBtnLabel: stopBtn ? stopBtn.textContent.trim() : null,"
    "    stopBtnDisabled: stopBtn ? !!stopBtn.disabled : null,"
    "    launchBtnLabel: launchBtn ? launchBtn.textContent.trim() : null,"
    "    launchBtnDisabled: launchBtn ? !!launchBtn.disabled : null,"
    "    statusText: sec ? sec.innerText : '',"
    "    calls: window.__TRINITY_DISPATCH_CALLS__ || [],"
    "  };"
    "}"
)


def test_failed_stop_keeps_running_council_and_says_so(tmp_path, monkeypatch):
    """A running council + a FAILED Stop dispatch: the running ribbon must STAY, the Stop
    button must recover (retryable), and the UI must say the council is still running."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    serve_root = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1024, "height": 1300}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.add_init_script(_STUB.format())
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

                # --- Step A: launch a council (succeeds → real running display) ---
                page.fill("textarea", _TASK)
                page.click("button:has-text('Launch Council')", timeout=5000)
                page.wait_for_timeout(400)
                a = page.evaluate(_SNAP_JS)
                # Precondition: the council is visibly running with a Stop button.
                assert a["runningRibbonVisible"] and a["spinnerVisible"], (
                    f"step A (launch) must show the running ribbon + spinner; got {a!r}"
                )
                assert a["stopBtnLabel"] == "Stop council", (
                    f"step A must offer a 'Stop council' button; got {a!r}"
                )

                # --- Step B: click Stop; the stop dispatch FAILS ---
                page.evaluate(
                    "() => { const b = [...document.querySelectorAll('.launch-status button')]"
                    ".find(b => /Stop council/.test(b.textContent)); if (b) b.click(); }"
                )
                page.wait_for_timeout(500)
                b = page.evaluate(_SNAP_JS)

                assert not errs, f"JS pageerrors during stop sequence: {errs[:3]}"
                assert b["calls"] == ["launch-council", "stop-council"], (
                    f"launch then stop must both dispatch; got {b['calls']!r}"
                )

                # THE BITE (defect #1 — the running display VANISHED on a failed stop):
                # the council is still running on the backend (the stop never reached it),
                # so the running ribbon must NOT disappear. On the un-fixed code
                # handleDispatchResult.clearOperation() nulled the operation → the whole
                # ribbon vanished and Launch re-enabled, silently reading as "stopped".
                assert b["runningRibbonVisible"], (
                    "a FAILED Stop-council dispatch vanished the still-running council "
                    "display (clearOperation) — the council keeps running on the backend "
                    "while the UI claims nothing is there. The running ribbon must STAY. "
                    f"state={b!r}"
                )
                # Launch must STAY disabled — the council is still running.
                assert b["launchBtnLabel"] == "Council in progress…" and b["launchBtnDisabled"], (
                    "Launch must stay disabled while the (un-stopped) council still runs; "
                    f"got {b!r}"
                )

                # THE BITE (defect #2 — the Stop button stuck / un-recoverable + mute):
                # the Stop button must recover from disabled 'Stopping…' so the user can
                # retry, and the UI must SAY the council is still running.
                assert b["stopBtnLabel"] == "Stop council" and not b["stopBtnDisabled"], (
                    "after a FAILED stop the 'Stop council' button must recover from "
                    "disabled 'Stopping…' so the user can retry; "
                    f"got label={b['stopBtnLabel']!r} disabled={b['stopBtnDisabled']!r}"
                )
                assert "still running" in b["statusText"].lower(), (
                    "a FAILED stop must say the council is STILL RUNNING (NO-FEEDBACK "
                    "otherwise — the user has no signal the stop didn't take); "
                    f"statusText={b['statusText']!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
