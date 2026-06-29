"""The HOME "Your taste, distilled" lens card's "Save as PNG card" button must
give HONEST feedback on a FAILED render.

WRONG-FEEDBACK defect (2026-06-17 UX sweep, USEFULNESS lens): renderMeCard set
``copiedKey = 'me-card'`` OPTIMISTICALLY — the moment the user clicked — so the
button flashed "✓ Rendered — opening" for 2.4s even when the PNG dispatch FAILED
(PIL not installed / host down). The real error, meanwhile, was routed through the
generic ``handleDispatchResult`` and surfaced in the COUNCIL-LAUNCH ribbon at the
top of the page — a surface that has nothing to do with the lens card the user was
sharing. So a failed PNG render told the user TWO contradictory things: the button
claimed success ("✓ Rendered — opening") while an error about it appeared in an
unrelated composer ribbon far above.

The fix gives renderMeCard a dedicated lifecycle (``meCardStatus`` idle→rendering
→done/error) + a dedicated result handler (``handleMeCardResult``) that surfaces a
failure IN-CARD, next to the button. On failure the button must NOT claim "Rendered".

These tests drive the REAL petite-vue render with a seeded taste lens and a stubbed
dispatcher that FAILS, asserting:
  - the button does NOT show "✓ Rendered" on failure,
  - the error surfaces inside the taste card (.taste-mecard-error),
  - the error does NOT leak into the council-launch ribbon.
Mutation-provable: restore the optimistic ``copiedKey='me-card'`` flash (route
onResult back through handleDispatchResult) and the in-card assertions red.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_render_me_card_uses_dedicated_lifecycle_not_optimistic_flash():
    """CI-runnable canary (no browser): renderMeCard must drive a pending→
    done/error lifecycle via its OWN handler, NOT flash an optimistic
    'me-card' success copiedKey routed through the generic dispatch handler."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    fn_start = src.index("renderMeCard()")
    fn_body = src[fn_start: fn_start + 1600]
    # The button-success cue must no longer be set optimistically before dispatch.
    assert "this.copiedKey = 'me-card'" not in fn_body, (
        "renderMeCard set copiedKey='me-card' OPTIMISTICALLY again — the button "
        "will flash '✓ Rendered — opening' even when the PNG render FAILS"
    )
    # It must hand the result to the dedicated handler, not the generic council one.
    assert "handleMeCardResult" in fn_body, (
        "renderMeCard no longer routes through handleMeCardResult — a failed PNG "
        "render will surface in the unrelated council-launch ribbon"
    )
    assert "this.meCardStatus = 'rendering'" in fn_body, (
        "renderMeCard lost its pending 'rendering' cue"
    )
    # The dedicated handler must surface a failure (error state), not swallow it.
    h_start = src.index("handleMeCardResult(r)")
    h_body = src[h_start: h_start + 1400]
    assert "this.meCardStatus = 'error'" in h_body, (
        "handleMeCardResult does not set an error state on a failed render — the "
        "failure gives no feedback"
    )


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _render_and_serve(tmp_path, view="home"):
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # Pass the INNER pageData (not the {pageData:...} wrapper) — passing the
    # wrapper is the known false-alarm shape ([[ux-sweep]] iter 4 lesson).
    page_data = build_launchpad_payload()["pageData"]
    # Seed a populated taste lens so the share card (and its PNG button) renders.
    page_data["tasteLenses"] = {
        "paired_lenses": [
            {
                "pole_a": "concrete",
                "pole_b": "abstract",
                "failure_a": "myopic detail",
                "failure_b": "vague hand-waving",
                "statement": "You privilege concreteness but abstract when the pattern repeats.",
                "tension_decisions": [],
            }
        ],
        "orderings": [],
        "rejections": [],
        "abstract_lenses": [],
        "vocabulary": [],
        "decisionsById": {},
        "combined_share_text": "My lenses:\n→ concrete ↔ abstract\n(via trinity-local)",
    }
    html = render_launchpad_html(page_data=page_data, view=view)
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(tmp_path / "serve")


@pytest.mark.slow
@pytest.mark.browser
def test_me_card_render_failure_gives_honest_in_card_feedback(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    httpd, port = _render_and_serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                # Stub a FAILING render-me-card dispatch (PIL missing / host error).
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    " window.__lastDispatch = o;"
                    " if(o&&o.onResult)o.onResult({ok:false, tier:'extension', reason:'render-failed',"
                    "   response:{error:'me-card render failed: PIL not installed'}});"
                    " return Promise.resolve({ok:false}); }, onStateChange: function(){},"
                    " isAvailable: function(){return true;} };"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # Click the PNG button.
                clicked = page.evaluate(
                    "() => { const btns = Array.from(document.querySelectorAll('section.taste-card button'));"
                    " const png = btns.find(b => /Save as PNG|Rendered|Rendering/i.test(b.textContent));"
                    " if(!png) return false; png.click(); return true; }"
                )
                assert clicked, "Save as PNG card button not found in the taste card"
                page.wait_for_timeout(250)
                state = page.evaluate(
                    "() => {"
                    " const btns = Array.from(document.querySelectorAll('section.taste-card button'));"
                    " const png = btns.find(b => /Save as PNG|Rendered|Rendering/i.test(b.textContent));"
                    " const dispatched = window.__lastDispatch ? window.__lastDispatch.extensionAction : null;"
                    " const inCard = document.querySelector('section.taste-card .taste-mecard-error');"
                    " const ribbon = document.querySelector('.launch-status .status-error');"
                    " return {"
                    "   btnLabel: png ? png.textContent.trim() : null,"
                    "   dispatchedKind: dispatched ? dispatched.kind : null,"
                    "   inCardErr: inCard && inCard.offsetParent !== null ? inCard.textContent.trim() : null,"
                    "   councilRibbon: ribbon && ribbon.offsetParent !== null ? ribbon.textContent.trim() : null }; }"
                )
                # It dispatched the render (not a dead button).
                assert state["dispatchedKind"] == "render-me-card", (
                    f"PNG button did not dispatch render-me-card: {state}"
                )
                # FAILURE must NOT flash the success cue on the button.
                assert "Rendered" not in (state["btnLabel"] or ""), (
                    "the 'Save as PNG card' button flashed '✓ Rendered — opening' on a "
                    f"FAILED render — a false success claim. Button reads: {state['btnLabel']!r}"
                )
                # FAILURE must surface IN the taste card, next to the button.
                assert state["inCardErr"], (
                    "a failed PNG render gave NO in-card feedback — the user is left "
                    "with a silent button (or a stale optimistic '✓ Rendered')"
                )
                assert "render the PNG card" in state["inCardErr"].lower() or "render the png card" in state["inCardErr"].lower()
                # FAILURE must NOT leak into the unrelated council-launch ribbon.
                assert not state["councilRibbon"], (
                    "the PNG-render error leaked into the council-launch ribbon "
                    f"({state['councilRibbon']!r}) — wrong surface for a lens-card action"
                )
                assert not errors, f"console errors during failed me-card render: {errors}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_me_card_render_success_shows_rendered_cue(tmp_path, monkeypatch):
    """The fix must not break the happy path: a successful render flashes
    '✓ Rendered — opening' and shows no error."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    httpd, port = _render_and_serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    " window.__lastDispatch = o; if(o&&o.onResult)o.onResult({ok:true});"
                    " return Promise.resolve({ok:true}); }, onStateChange: function(){},"
                    " isAvailable: function(){return true;} };"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                page.evaluate(
                    "() => { const btns = Array.from(document.querySelectorAll('section.taste-card button'));"
                    " const png = btns.find(b => /Save as PNG/i.test(b.textContent)); png.click(); }"
                )
                page.wait_for_timeout(200)
                state = page.evaluate(
                    "() => { const btns = Array.from(document.querySelectorAll('section.taste-card button'));"
                    " const png = btns.find(b => /Save as PNG|Rendered/i.test(b.textContent));"
                    " const inCard = document.querySelector('section.taste-card .taste-mecard-error');"
                    " return { btnLabel: png ? png.textContent.trim() : null,"
                    "   errVisible: inCard ? inCard.offsetParent !== null : false }; }"
                )
                assert "Rendered" in (state["btnLabel"] or ""), (
                    f"successful render did not flash '✓ Rendered — opening': {state}"
                )
                assert not state["errVisible"], "error surfaced on a SUCCESSFUL render"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
