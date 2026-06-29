"""Browser guard: a STALE dispatch-failure banner must not survive into the NEXT
operation on the SAME mounted launchpad.

THE SEQUENCE BUG (found 2026-06-22 in the multi-step / temporal-composition sweep —
single-cell states were all clean; this only appears when a real user does TWO
dispatches in a row on the same mount):

  Step A — the user clicks Launch with no host wired. The dispatcher returns
           ``{tier:'install-prompt', ok:false}`` → ``handleDispatchResult`` rolls back
           and opens the "No dispatch path is wired up" banner. CORRECT.
  Step B — the user wires the native host (``trinity-local install-extension``),
           types a new task, and clicks Launch AGAIN — WITHOUT dismissing the banner
           (the panel picks up the host live; no forced reload). This dispatch now
           SUCCEEDS (``{tier:'extension', ok:true}``) and the optimistic
           "Council in progress…" spinner appears.

THE LEAK: ``beginOperation`` cleared ``launchError`` + ``stopRequested`` but NOT
``dispatchBannerOpen``, and the SUCCESS path never closes the banner. So step B
rendered its running spinner RIGHT BELOW the stale "No dispatch path is wired up"
banner — a self-contradiction (the council is provably running, yet a banner claims
no dispatch path exists). Every SINGLE-dispatch state-machine test
(``test_launchpad_dispatch_result_state_machine_browser.py``) stays green because the
banner is correct WITHIN one operation; only the A-fail → B-succeed SEQUENCE on one
mount exposes it.

THE FIX: ``beginOperation`` (the single entry point every dispatch — council + ingest
— flows through) clears ``dispatchBannerOpen``. The banner stays an honest reactive
surface: if the NEW dispatch ALSO fails no-route, ``handleDispatchResult`` re-opens it
("a failed click reopens this").

This guard DRIVES the real sequence with a stubbed ``window.__TRINITY_DISPATCH__`` (set
before any click) that returns no-route FIRST then success SECOND, and asserts the
banner is gone — and crucially NOT co-present with the success spinner — after step B.
A single-state assertion cannot bite this; the bite requires the two-dispatch sequence.

Stubs the dispatcher before any click so it never touches the real extension / a real
council. Serves an isolated, PII-free synthetic home over http. Slow-marked; skips
without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_TASK_A = "First task: compare three caching strategies for the write path"
_TASK_B = "Second task: design a token-bucket rate limiter"

# Sequence dispatcher: result[0] = no-route FAILURE (opens the banner),
# result[1] = SUCCESS (optimistic spinner). __trinityReload is a no-op counter so a
# SUCCESS path can't navigate the page away mid-assert.
_STUB = (
    "window.__TRINITY_DISPATCH_CALLS__=[];"
    "window.__trinityReload=function(){{window.__TRINITY_RELOAD_CALLED__=(window.__TRINITY_RELOAD_CALLED__||0)+1;}};"
    "window.__SEQ_RESULTS__=[{{tier:'install-prompt', ok:false}},{{tier:'extension', ok:true}}];"
    "window.__TRINITY_DISPATCH__={{dispatch:function(o){{"
    "  var idx=window.__TRINITY_DISPATCH_CALLS__.length;"
    "  window.__TRINITY_DISPATCH_CALLS__.push(o&&o.extensionAction?o.extensionAction.kind:null);"
    "  var res=window.__SEQ_RESULTS__[idx]||{{tier:'extension', ok:true}};"
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


_BANNER_RE = (
    r"No dispatch path is wired up|host not registered"
    r"|Install the Trinity browser extension|capture host just isn"
)

_SNAP_JS = (
    "() => {"
    "  const banner = [...document.querySelectorAll('section.card')]"
    "    .find(c => /" + _BANNER_RE + "/.test(c.innerText));"
    "  const spin = document.querySelector('.launch-status .spinner');"
    "  const launchBtn = [...document.querySelectorAll('button')]"
    "    .find(b => /Launch Council|Council in progress/i.test(b.textContent));"
    "  return {"
    "    bannerVisible: !!banner && banner.offsetParent !== null,"
    "    spinnerVisible: !!spin && spin.offsetParent !== null,"
    "    launchBtnLabel: launchBtn ? launchBtn.textContent.trim() : null,"
    "    calls: window.__TRINITY_DISPATCH_CALLS__ || [],"
    "  };"
    "}"
)


def test_dispatch_banner_does_not_leak_across_operations(tmp_path, monkeypatch):
    """A-fail (no-route → banner) → B-success (spinner) on one mount: the stale banner
    must be CLEARED, never co-present with the running spinner."""
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

                # --- Step A: dispatch a council; the dispatcher returns no-route ---
                page.fill("textarea", _TASK_A)
                page.click("button:has-text('Launch Council')", timeout=5000)
                page.wait_for_timeout(400)
                a = page.evaluate(_SNAP_JS)
                # Precondition: the no-route failure opened the banner (the spinner
                # rolled back). If this isn't true the sequence is mis-set-up.
                assert a["bannerVisible"] and not a["spinnerVisible"], (
                    f"step A (no-route) must show the banner and NO spinner; got {a!r}"
                )

                # --- Step B: retry WITHOUT dismissing the banner; this one succeeds ---
                page.fill("textarea", _TASK_B)
                page.click("button:has-text('Launch Council')", timeout=5000)
                page.wait_for_timeout(400)
                b = page.evaluate(_SNAP_JS)

                assert not errs, f"JS pageerrors during sequence: {errs[:3]}"
                # Both dispatches fired.
                assert b["calls"] == ["launch-council", "launch-council"], (
                    f"both Launch clicks must dispatch; got {b['calls']!r}"
                )
                # The success spinner is up (step B genuinely launched).
                assert b["spinnerVisible"] and b["launchBtnLabel"] == "Council in progress…", (
                    f"step B must show the optimistic running spinner; got {b!r}"
                )
                # THE BITE: the stale dispatch-failure banner from step A must NOT be
                # visible alongside the step-B success spinner. A council provably
                # running BELOW a "No dispatch path is wired up" banner is the
                # cross-operation stale-error leak.
                assert not b["bannerVisible"], (
                    "the 'No dispatch path is wired up' banner from a PRIOR no-route "
                    "dispatch leaked into the NEXT (successful) operation — it renders "
                    "right below the 'Council in progress…' spinner, a self-contradiction "
                    f"(running council + 'no dispatch path' banner). state={b!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
