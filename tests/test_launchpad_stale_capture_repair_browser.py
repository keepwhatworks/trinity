"""A user staring at a STALE browser-capture card must be able to reach the
self-healing 'Repair extension' flow FROM THAT CARD.

USEFULNESS / DISCOVERABILITY defect (2026-06-17 UX sweep): the "Repair extension"
button — Trinity's self-healing flagship (#147) — used to live ONLY in the
memory-health card's eyebrow. That card renders on an UNRELATED trigger (a stale
*cognitive memory* file like core.md), and only carries a capture-drift hint when
`detect_failure_patterns` happens to find a code-patch pattern. Meanwhile the
card that actually tells the user capture is broken — the STALE browser-capture
card (`browserCapture.stale`, i.e. no captures in 24h) — sent them to
`chrome://extensions` to debug by hand, with NO link to the auto-repair button.

So the two staleness signals diverge: capture can be stale (the symptom card the
user sees) while memory-health shows no capture-drift issue (the button hidden on
a different card). The fix surfaces the SAME `repairExtension()` action as a chip
ON the stale browser-capture card, one click from the symptom.

This drives the REAL petite-vue render with a seeded STALE-capture page_data and
asserts a clickable "Repair extension" affordance lives inside
`section.browser-capture-card` — a DOM relationship only the JS render resolves.
Mutation-provable: delete the chip from the template and the in-card assertion
reds (verified by reverting the template edit → red → restore).
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_repair_chip_lives_in_browser_capture_template():
    """CI-runnable canary: the stale-capture branch must carry the repairExtension
    action (fails fast in plain pytest, no browser needed)."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    # The repair button must appear in the browser-capture stale branch, not only
    # in the memory-health eyebrow. Find the stale-capture <p> and assert a
    # repairExtension chip follows before the card closes.
    anchor = "the provider may have refactored their streaming API"
    assert anchor in src, "stale browser-capture copy changed — re-anchor this guard"
    after = src[src.index(anchor):]
    card_end = after.index("</section>")
    stale_block = after[:card_end]
    assert '@click="repairExtension"' in stale_block, (
        "the STALE browser-capture card lost its 'Repair extension' chip — a user "
        "who sees capture is broken is again sent to chrome://extensions by hand "
        "instead of one-click self-healing (#147 buried on the memory-health card)"
    )


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_stale_capture_card_offers_in_card_repair(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html

    # IMPORTANT: pass the INNER pageData (not the {pageData:...} wrapper) so
    # `pageData.browserCapture` resolves in the template — passing the wrapper is
    # the known false-alarm shape ([[ux-sweep]] iter 4 lesson).
    page_data = build_launchpad_payload()["pageData"]
    # Seed a STALE browser-capture card: has data, but nothing in 24h.
    page_data["browserCapture"] = {
        "has_data": True,
        "stale": True,
        "total_captured": 6,
        "captured_24h": 0,
        "providers": [
            {"provider": "claude", "count": 4, "missing_count": 0, "sidebar_count": 4},
            {"provider": "chatgpt", "count": 2, "missing_count": 0, "sidebar_count": 2},
        ],
        "last_capture_iso": "2026-06-14T00:00:00",
        "last_capture_ago_seconds": 3 * 86400,
        "last_capture_ago_human": "3d",
        "install_command": "trinity-local install-extension",
    }
    # Render the /stats view — that's where the stats-card browser-capture card
    # is actually visible (it's display:none on home).
    html = render_launchpad_html(page_data=page_data, view="stats")

    from trinity_local.vendor import publish_vendor_files

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
                page = browser.new_context(viewport={"width": 393, "height": 1000}).new_page()
                # Stub dispatch so a click never hits a real extension / council.
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
                # The stale browser-capture card must be visible AND contain a
                # working 'Repair extension' control — the DOM ancestry is the
                # assertion (the button must be INSIDE this card, not on a
                # different one).
                probe = page.evaluate(
                    "() => {"
                    " const card = document.querySelector('section.browser-capture-card');"
                    " if (!card) return { card: false };"
                    " const visible = card.offsetParent !== null;"
                    " const btns = Array.from(card.querySelectorAll('button'));"
                    " const repair = btns.find(b => /repair extension/i.test(b.textContent));"
                    " return { card: true, visible, hasRepair: !!repair,"
                    "   repairText: repair ? repair.textContent.trim() : null }; }"
                )
                assert probe["card"], "browser-capture card not rendered"
                assert probe["visible"], "stale browser-capture card not visible on /stats"
                assert probe["hasRepair"], (
                    "the stale browser-capture card has NO in-card 'Repair extension' "
                    "affordance — the self-healing flow (#147) is unreachable from the "
                    "symptom; the user is sent to chrome://extensions by hand"
                )
                # And clicking it fires the dispatch (not a dead button).
                page.evaluate(
                    "() => { const card = document.querySelector('section.browser-capture-card');"
                    " const btns = Array.from(card.querySelectorAll('button'));"
                    " const repair = btns.find(b => /repair extension/i.test(b.textContent));"
                    " repair.click(); }"
                )
                fired = page.wait_for_function(
                    "() => window.__lastDispatch && window.__lastDispatch.extensionAction"
                    " && window.__lastDispatch.extensionAction.kind === 'extension-repair-auto'",
                    timeout=4000,
                )
                assert fired, "in-card Repair extension click did not dispatch extension-repair-auto"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_repair_cta_shows_progress_and_result_feedback(tmp_path, monkeypatch):
    """The flagship self-heal CTA must give FEEDBACK, not silently no-op.

    USEFULNESS / NO-FEEDBACK guard (UX sweep iter 109): the existing
    `test_stale_capture_card_offers_in_card_repair` only asserts the click
    DISPATCHES `extension-repair-auto` — its stub resolves onResult synchronously,
    so the deferred `running` spinner state is never observed and the done/failed
    feedback is never asserted. A handler refactor that dropped the
    `repairExtensionStatus`/`repairExtensionError` wiring (the `running` set, the
    `done`/`failed` transition, or the co-located error) would leave that guard
    GREEN while the CTA became a NO-FEEDBACK dead-end: the user clicks Trinity's
    flagship "Trinity heals itself" button (#147), a multi-minute council is
    dispatched, and the button just sits there — reading as broken.

    This drives BOTH branches in the REAL render with a DEFERRED-resolving stub so
    each transition is observable, and asserts the RENDERED button text + spinner +
    disabled + co-located error <p>:
      idle  → "Repair extension"
      click → "Repairing…" + spinner + disabled   (IMMEDIATE ack)
      ok    → "✓ Dispatched"                        (success feedback)
      fail  → "⚠ Failed" + co-located error reason  (honest failure, not silent)
    Mutation-proven: drop `this.repairExtensionStatus = 'running'` (or the failed
    branch's error co-location) in repairExtension() → these assertions RED.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    page_data["browserCapture"] = {
        "has_data": True,
        "stale": True,
        "total_captured": 6,
        "captured_24h": 0,
        "providers": [
            {"provider": "claude", "count": 4, "missing_count": 0, "sidebar_count": 4},
            {"provider": "chatgpt", "count": 2, "missing_count": 0, "sidebar_count": 2},
        ],
        "last_capture_iso": "2026-06-14T00:00:00",
        "last_capture_ago_seconds": 3 * 86400,
        "last_capture_ago_human": "3d",
        "install_command": "trinity-local install-extension",
    }
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    # Read the repair button's RENDERED state inside the stale capture card.
    read_repair = (
        "() => {"
        " const card = document.querySelector('section.browser-capture-card');"
        " if (!card) return { card: false };"
        " const btns = Array.from(card.querySelectorAll('button'));"
        " const r = btns.find(b => /repair|repairing|dispatched|failed/i.test(b.textContent));"
        " const errP = Array.from(card.querySelectorAll('p'))"
        "   .find(p => /couldn.t dispatch/i.test(p.textContent));"
        " return { card: true,"
        "   text: r ? r.textContent.trim().replace(/\\s+/g, ' ') : null,"
        "   disabled: r ? r.disabled : null,"
        "   hasSpinner: r ? !!r.querySelector('.spinner') : false,"
        "   err: errP ? errP.textContent.trim().replace(/\\s+/g, ' ') : null }; }"
    )
    click_repair = (
        "() => { const c = document.querySelector('section.browser-capture-card');"
        " const b = Array.from(c.querySelectorAll('button'))"
        "   .find(x => /repair/i.test(x.textContent)); b.click(); }"
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # ---- SUCCESS branch: deferred onResult so 'running' is observable ----
                ctx = browser.new_context(viewport={"width": 393, "height": 1000})
                page = ctx.new_page()
                page.add_init_script(
                    "window.__pending = [];"
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    " window.__lastDispatch = o;"
                    " window.__pending.push(()=>{ if(o&&o.onResult)o.onResult({ok:true}); });"
                    " return new Promise(()=>{}); }, onStateChange: function(){},"
                    " isAvailable: function(){return true;} };"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }", timeout=10000,
                )
                idle = page.evaluate(read_repair)
                assert idle["card"] and idle["text"] == "Repair extension", (
                    f"repair CTA not in idle 'Repair extension' state: {idle}"
                )

                page.evaluate(click_repair)
                running = page.wait_for_function(
                    "() => { const c = document.querySelector('section.browser-capture-card');"
                    " const b = Array.from(c.querySelectorAll('button'))"
                    "  .find(x => /repairing/i.test(x.textContent));"
                    " return b && b.disabled && !!b.querySelector('.spinner'); }",
                    timeout=4000,
                )
                assert running, (
                    "clicking 'Repair extension' gave NO immediate feedback — the "
                    "button did not enter the disabled 'Repairing…' + spinner state. "
                    "A multi-minute council was dispatched and the flagship self-heal "
                    "CTA (#147) reads as a dead no-op (NO-FEEDBACK class)."
                )

                # Resolve the deferred dispatch → must flip to the ✓ done feedback.
                page.evaluate("() => { window.__pending.forEach(f => f()); }")
                done = page.wait_for_function(
                    "() => { const c = document.querySelector('section.browser-capture-card');"
                    " const b = Array.from(c.querySelectorAll('button'))"
                    "  .find(x => /dispatched/i.test(x.textContent));"
                    " return b && /\\u2713/.test(b.textContent); }",
                    timeout=4000,
                )
                assert done, (
                    "the Repair dispatch SUCCEEDED but the CTA never showed the "
                    "'✓ Dispatched' result — the user has no confirmation the "
                    "self-heal council was launched."
                )
                assert page.evaluate(
                    "() => window.__lastDispatch && window.__lastDispatch.extensionAction"
                    " && window.__lastDispatch.extensionAction.kind"
                ) == "extension-repair-auto"
                ctx.close()

                # ---- FAILURE branch: onResult ok:false → honest co-located error ----
                ctx2 = browser.new_context(viewport={"width": 393, "height": 1000})
                page2 = ctx2.new_page()
                page2.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    " window.__lastDispatch = o;"
                    " if(o&&o.onResult) setTimeout(()=>o.onResult("
                    "  {ok:false, response:{detail:'capture host crashed: ECONNREFUSED'}}), 80);"
                    " return Promise.resolve({ok:false}); }, onStateChange: function(){},"
                    " isAvailable: function(){return true;} };"
                )
                page2.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page2.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }", timeout=10000,
                )
                page2.evaluate(click_repair)
                failed = page2.wait_for_function(
                    "() => { const c = document.querySelector('section.browser-capture-card');"
                    " const b = Array.from(c.querySelectorAll('button'))"
                    "  .find(x => /failed/i.test(x.textContent));"
                    " if (!b || !/\\u26a0/.test(b.textContent)) return false;"
                    " const errP = Array.from(c.querySelectorAll('p'))"
                    "  .find(p => /couldn.t dispatch/i.test(p.textContent));"
                    " return errP && /ECONNREFUSED/.test(errP.textContent); }",
                    timeout=4000,
                )
                assert failed, (
                    "a FAILED Repair dispatch gave NO honest feedback — the button "
                    "did not show '⚠ Failed' AND the real reason "
                    "('capture host crashed: ECONNREFUSED') was not co-located on "
                    "this card. A silent-failure flagship CTA reads as broken, or "
                    "the error leaks onto the council ribbon (#242(a) class)."
                )
                final = page2.evaluate(read_repair)
                assert final["err"] and "trinity-local extension repair --auto" in final["err"], (
                    "the failed-repair error must give the terminal fallback "
                    f"(`trinity-local extension repair --auto`); got: {final['err']}"
                )
                ctx2.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
