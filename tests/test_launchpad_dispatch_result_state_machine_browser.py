"""Browser guard: the launchpad LAUNCH-COUNCIL dispatch-RESULT state machine must
reach the CORRECT terminal UI state for each result shape the dispatcher returns.

THE GAP (found 2026-06-20 in the UX state-machine sweep): ``handleDispatchResult``
is the optimistic-UI rollback handler — it owns three outcomes:

  * ``{tier:'extension', ok:true}``  → SUCCESS: keep the optimistic "Council
    running" spinner + the disabled "Council in progress…" button (the status
    poller takes over). The prompt stays cleared.
  * ``{tier:'install-prompt', ok:false}`` → no-route FAILURE: ROLL BACK — stop the
    spinner, re-enable the Launch button, RESTORE the typed prompt, and open the
    "No dispatch path is wired up" banner.
  * ``{tier:'extension', ok:false, response:{error}}`` → extension FAILURE: roll
    back the same way + surface the error detail in the ``.status-error`` ribbon.

It was guarded ONLY by ``test_launchpad_dispatch_failure_rollback.py``, which is a
pure STRING-PRESENCE check (``"this.clearOperation()" in html`` etc.). That survives
a STATE-MACHINE INVERSION: flip the ``failed`` predicate (e.g. ``!result.ok`` →
``result.ok``) so SUCCESS rolls back and FAILURE optimistically proceeds, and every
asserted string is still literally present in the template — the string test stays
GREEN while the launch flow is backwards (a successful launch loses its spinner; a
no-route failure sticks "Council in progress…" forever — the founder's stuck-launch
lineage, ``launch_mpm0bght_gx1y9v``).

This guard DRIVES the real handler with a stubbed ``window.__TRINITY_DISPATCH__`` (set
before any click) returning each shape, and asserts the EXACT reached state — so a
state inversion / dropped rollback REDS here with the wrong-STATE symptom while the
old string test stays green.

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

_TASK = "Compare three caching strategies for the write path"

# Stub dispatcher: records the dispatched extensionAction.kind so we can assert the
# RIGHT dispatch fired, and resolves onResult with a configurable result shape after a
# tiny delay (so the optimistic UI is observable before the result lands). __trinityReload
# is stubbed to a no-op counter so a SUCCESS path can't navigate the page away mid-assert.
_STUB = (
    "window.__TRINITY_DISPATCH_CALLS__=[];"
    "window.__trinityReload=function(){{window.__TRINITY_RELOAD_CALLED__=(window.__TRINITY_RELOAD_CALLED__||0)+1;}};"
    "window.__TRINITY_DISPATCH__={{dispatch:function(o){{"
    "  window.__TRINITY_DISPATCH_CALLS__.push(o&&o.extensionAction?o.extensionAction.kind:null);"
    "  if(o&&o.onResult)setTimeout(function(){{o.onResult({result});}},15);"
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


def _drive(port: int, result_js: str):
    """Launch a council with the dispatcher stubbed to return ``result_js``; return the
    reached UI state ~400ms after the click (after onResult fires, before any poller
    round-trip)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1024, "height": 1200}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.add_init_script(_STUB.format(result=result_js))
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
            page.fill("textarea", _TASK)
            page.click("button:has-text('Launch Council')", timeout=5000)
            page.wait_for_timeout(400)
            state = page.evaluate(
                "() => {"
                " const ls = document.querySelector('.launch-status');"
                " const spin = document.querySelector('.launch-status .spinner');"
                " const launchBtn = [...document.querySelectorAll('button')]"
                "   .find(b => /Launch Council|Council in progress/i.test(b.textContent));"
                " const errEl = document.querySelector('.launch-status .status-error');"
                " const banner = [...document.querySelectorAll('section.card')]"
                "   .find(c => /No dispatch path is wired up|Install the Trinity browser extension/.test(c.innerText));"
                " return {"
                "   launchStatusVisible: !!ls && ls.offsetParent !== null,"
                "   spinnerVisible: !!spin && spin.offsetParent !== null,"
                "   launchBtnLabel: launchBtn ? launchBtn.textContent.trim() : null,"
                "   launchBtnDisabled: launchBtn ? launchBtn.disabled : null,"
                "   errText: errEl ? errEl.innerText.trim() : '',"
                "   bannerVisible: !!banner && banner.offsetParent !== null,"
                "   promptValue: (document.querySelector('textarea') || {}).value || '',"
                "   dispatchKinds: window.__TRINITY_DISPATCH_CALLS__ || [],"
                " };"
                "}"
            )
            assert not errs, f"JS pageerrors: {errs[:3]}"
            return state
        finally:
            browser.close()


def test_dispatch_success_keeps_optimistic_running(tmp_path, monkeypatch):
    """{tier:'extension', ok:true} → the optimistic 'Council running' spinner stays,
    the Launch button reads 'Council in progress…' (disabled), the prompt stays cleared.
    A regression that rolls back on SUCCESS (inverted predicate) reds here."""
    pytest.importorskip("playwright.sync_api")
    serve_root = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(serve_root)
    try:
        st = _drive(port, "{tier:'extension', ok:true}")
    finally:
        httpd.shutdown()

    # The RIGHT dispatch fired.
    assert st["dispatchKinds"] == ["launch-council"], (
        f"a launch must dispatch exactly one 'launch-council' action; got {st['dispatchKinds']!r}"
    )
    # SUCCESS keeps the optimistic running state (the poller takes over) — it must NOT
    # roll back. A predicate inversion that treats ok:true as failure would hide the
    # spinner + re-enable the button here.
    assert st["launchStatusVisible"] and st["spinnerVisible"], (
        "a SUCCESSFUL launch dropped its optimistic 'Council running' spinner — the "
        f"dispatch-result handler rolled back on ok:true (inverted predicate). state={st!r}"
    )
    assert st["launchBtnLabel"] == "Council in progress…" and st["launchBtnDisabled"] is True, (
        "after a successful launch the button must stay disabled as 'Council in "
        f"progress…' — it re-enabled (rollback fired on success). state={st!r}"
    )
    assert st["promptValue"] == "", (
        f"a successful launch must keep the prompt cleared (no rollback); got {st['promptValue']!r}"
    )
    # No failure surfaces on success.
    assert not st["bannerVisible"] and not st["errText"], (
        f"a successful launch surfaced a failure banner/error — state inverted. state={st!r}"
    )


def test_dispatch_no_route_rolls_back_and_banners(tmp_path, monkeypatch):
    """{tier:'install-prompt', ok:false} → ROLL BACK: spinner gone, button re-enabled to
    'Launch Council', the typed prompt RESTORED, and the 'No dispatch path' banner shown.
    A dropped rollback (the founder stuck-launch symptom) reds here."""
    pytest.importorskip("playwright.sync_api")
    serve_root = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(serve_root)
    try:
        st = _drive(port, "{tier:'install-prompt', ok:false}")
    finally:
        httpd.shutdown()

    assert st["dispatchKinds"] == ["launch-council"], st["dispatchKinds"]
    # THE FOUNDER STUCK-LAUNCH SYMPTOM: a no-route failure must NOT leave the UI sitting
    # on "Council in progress…" polling a status file that will never be written.
    assert not st["spinnerVisible"], (
        "a no-route (install-prompt) dispatch failure left the optimistic spinner "
        "running — the rollback (clearOperation) didn't fire, so the launchpad sticks "
        f"'Council in progress…' forever (founder launch_mpm0bght_gx1y9v). state={st!r}"
    )
    assert st["launchBtnLabel"] == "Launch Council" and st["launchBtnDisabled"] is False, (
        "after a no-route failure the Launch button must re-enable to 'Launch Council' "
        f"— it stayed disabled (busy never cleared). state={st!r}"
    )
    # The user's typed prompt is restored so they can retry without retyping.
    assert st["promptValue"] == _TASK, (
        "a no-route failure must RESTORE the typed prompt (pendingPrompt → prompt) so the "
        f"user doesn't have to retype; got {st['promptValue']!r}"
    )
    # And the honest "no dispatch path" banner is shown.
    assert st["bannerVisible"], (
        f"a no-route failure must open the 'No dispatch path is wired up' banner. state={st!r}"
    )


def test_dispatch_extension_error_surfaces_detail_and_rolls_back(tmp_path, monkeypatch):
    """{tier:'extension', ok:false, response:{error}} → roll back + surface the error
    detail in the .status-error ribbon (not a generic banner)."""
    pytest.importorskip("playwright.sync_api")
    serve_root = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(serve_root)
    try:
        st = _drive(port, "{tier:'extension', ok:false, response:{error:'quota-exhausted-detail'}}")
    finally:
        httpd.shutdown()

    assert st["dispatchKinds"] == ["launch-council"], st["dispatchKinds"]
    # Rolls back like the no-route path.
    assert not st["spinnerVisible"], (
        f"an extension !ok failure left the optimistic spinner running (no rollback). state={st!r}"
    )
    assert st["launchBtnLabel"] == "Launch Council" and st["launchBtnDisabled"] is False, (
        f"an extension !ok failure must re-enable the Launch button. state={st!r}"
    )
    assert st["promptValue"] == _TASK, (
        f"an extension !ok failure must restore the typed prompt; got {st['promptValue']!r}"
    )
    # The specific error detail (not a generic banner) reaches the user.
    assert "quota-exhausted-detail" in st["errText"], (
        "an extension !ok failure must surface the response error DETAIL in the "
        f".status-error ribbon, not swallow it. errText={st['errText']!r}"
    )
