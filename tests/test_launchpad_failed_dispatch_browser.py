"""A FAILED council dispatch must roll back cleanly — no JS errors, real feedback.

Found 2026-06-17 driving the failed-dispatch state (the founder's "dispatch banner
gave no feedback" lineage): when a dispatch fails, `handleDispatchResult` calls
`clearOperation()` (sets `operation = null`) to re-enable the Launch button. But
petite-vue re-evaluates the launch-status template bindings during that same
reactive flush — BEFORE the parent `v-if="operation"` tears the subtree down — so
any binding that dereferences `operation.X` without optional-chaining throws:

    TypeError: Cannot read properties of null (reading 'kind')
    TypeError: Cannot read properties of null (reading 'label')

The offenders were `{{ operation.label }}` and two `v-if="operation.kind === …"`.
The CLASS is: a template binding that dereferences a nullable reactive object
(`operation`) without `?.`, throwing the instant it's cleared (failed-dispatch
rollback, Dismiss, Stop). The root-cause fix is optional-chaining every such deref;
the getters were already guarded, so only the template leaked.

This guard DRIVES a real failed dispatch (operation set optimistically, then
cleared) and asserts ZERO console/page errors while STILL surfacing feedback (the
install banner) and rolling back (button re-enabled, prompt restored). A synthetic
render can't catch it — the throw only happens during petite-vue's live reactive
flush. Mutation-provable: drop a `?.` back to `.` and the TypeError reds this.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_failed_dispatch_rolls_back_without_console_errors(tmp_path, monkeypatch):
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
                for width in (1280, 393):
                    page = browser.new_context(viewport={"width": width, "height": 1100}).new_page()
                    errs: list[str] = []
                    page.on("pageerror", lambda e: errs.append("[pageerror] " + str(e)[:200]))
                    page.on(
                        "console",
                        lambda m: errs.append(f"[console.{m.type}] {m.text[:200]}") if m.type == "error" else None,
                    )
                    # Realistic no-extension dispatch: fails with the install-prompt
                    # tier, which triggers clearOperation()'s rollback (operation→null).
                    page.add_init_script(
                        "window.__TRINITY_DISPATCH__={dispatch:(o)=>{"
                        " if(o&&o.onResult) setTimeout(()=>o.onResult("
                        "{tier:'install-prompt', ok:false, reason:'no-route'}),10); }};"
                    )
                    page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                              wait_until="networkidle", timeout=20000)
                    page.wait_for_function(
                        "() => { const r = document.getElementById('launchpad-app');"
                        " return r && !r.hasAttribute('v-cloak'); }",
                        timeout=10000,
                    )
                    page.fill("#council-prompt", "Compare three rate-limiting strategies")
                    page.click(".actions button.button.primary", timeout=5000)
                    page.wait_for_timeout(700)

                    s = page.evaluate(
                        "() => { const b = document.querySelector('.actions button.button.primary');"
                        " const banner = [...document.querySelectorAll('.card,[class*=banner]')]"
                        ".find(e => e.offsetParent !== null && /extension|install/i.test(e.innerText||''));"
                        " return { btnText: b ? b.textContent.trim() : null, btnDisabled: b ? b.disabled : null,"
                        " prompt: (document.getElementById('council-prompt')||{}).value || '',"
                        " bannerVisible: !!banner }; }"
                    )
                    # ZERO JS errors — the heart of the class guard.
                    assert not errs, f"@{width}px a failed dispatch threw JS errors (operation.X on null): {errs[:4]}"
                    # Rollback: button re-enabled (not stuck on the busy label), prompt restored to retry.
                    assert s["btnDisabled"] is False, f"@{width}px Launch button stuck disabled after failed dispatch"
                    assert s["btnText"] == "Launch Council", f"@{width}px button stuck on busy label: {s['btnText']!r}"
                    assert "rate-limiting" in s["prompt"], f"@{width}px prompt not restored for retry: {s['prompt']!r}"
                    # Feedback: the install banner is visible (NOT a silent failure).
                    assert s["bannerVisible"], f"@{width}px failed dispatch surfaced no banner (silent failure)"
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


# A separator-free runner error — the exact shape launchError / operation.error
# (= status.error = str(exc) / why[:200] in council_runner.py, or a dispatch
# result.response.detail) can carry: a quota URL, an exception path, a base64/hash.
_LONG_ERR = "ConnectionError: dispatch failed for " + ("x" * 180)


def test_failed_dispatch_long_error_does_not_overflow(tmp_path, monkeypatch):
    """The dispatch-failure ribbon (``<p class="status-error">{{ launchError }}</p>``)
    renders the raw runner error. A separator-free token in it must WRAP inside the
    launch-status card, never force the card/document wider than the phone viewport.

    The ``extension``-tier non-host-unavailable branch sets ``launchError = String(detail)``
    (handleDispatchResult), surfacing the error directly in ``.status-error`` — which had
    only ``color`` (no ``overflow-wrap``). Found Iter 205 (asymmetric twin of the live
    council ``seg.errorText`` + the popup ``.panel-tip``): a 180-char unbreakable error
    streamed off-screen right and horizontal-scrolled the 320px home to ~1580px.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home2"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve2" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(), encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve2")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                for width in (320, 393):
                    page = browser.new_context(viewport={"width": width, "height": 1100}).new_page()
                    errs: list[str] = []
                    page.on("pageerror", lambda e: errs.append("[pageerror] " + str(e)[:200]))
                    # extension tier, NOT host-unavailable → sets launchError = String(detail).
                    page.add_init_script(
                        "window.__TRINITY_DISPATCH__={dispatch:(o)=>{"
                        " if(o&&o.onResult) setTimeout(()=>o.onResult("
                        "{tier:'extension', ok:false, response:{detail:"
                        "'ConnectionError: dispatch failed for ' + 'x'.repeat(180)}}),10); }};"
                    )
                    page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                              wait_until="networkidle", timeout=20000)
                    page.wait_for_function(
                        "() => { const r = document.getElementById('launchpad-app');"
                        " return r && !r.hasAttribute('v-cloak'); }",
                        timeout=10000,
                    )
                    page.fill("#council-prompt", "Compare three rate-limiting strategies")
                    page.click(".actions button.button.primary", timeout=5000)
                    page.wait_for_timeout(700)

                    geom = page.evaluate(
                        """() => {
                            const de = document.documentElement;
                            const errP = document.querySelector('p.status-error');
                            const vw = window.innerWidth;
                            let worst = {scrollW: 0, sel: ''};
                            for (const el of document.querySelectorAll('body *')) {
                                const cs = getComputedStyle(el);
                                if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
                                if (el.scrollWidth > vw + 1 && el.clientWidth <= vw + 1
                                    && el.scrollWidth > worst.scrollW) {
                                    worst = {scrollW: el.scrollWidth,
                                             sel: el.tagName.toLowerCase() + '.' +
                                                  String(el.className || '').slice(0, 30)};
                                }
                            }
                            return {
                                scrollW: de.scrollWidth, clientW: de.clientWidth,
                                errText: errP ? errP.textContent : null,
                                errScrollW: errP ? errP.scrollWidth : null,
                                errClientW: errP ? errP.clientWidth : null,
                                worst,
                            };
                        }"""
                    )
                    assert not errs, f"@{width}px failed dispatch threw JS errors: {errs[:4]}"
                    # Bite-not-vacuous: the long error must have rendered into .status-error.
                    assert geom["errText"] is not None and _LONG_ERR in geom["errText"], (
                        f"@{width}px the long runner error never reached .status-error "
                        f"(errText={geom['errText']!r}) — the failure path didn't render; "
                        "the overflow assertion would be a false pass"
                    )
                    # THE class guard: the raw runner error must wrap, never stretch the page.
                    assert geom["scrollW"] <= geom["clientW"] + 1, (
                        f"@{width}px the dispatch-failure ribbon OVERFLOWS the home horizontally: "
                        f"documentElement.scrollWidth={geom['scrollW']} > clientWidth={geom['clientW']} "
                        f"(worst content offender: {geom['worst']['sel']} @ {geom['worst']['scrollW']}px) "
                        "— launchError (raw status.error) carries a long unbreakable token "
                        "(URL/path/hash); .status-error needs overflow-wrap:anywhere"
                    )
                    assert geom["errScrollW"] <= geom["errClientW"] + 1, (
                        f"@{width}px .status-error grew to fit the unbreakable token "
                        f"(scrollWidth={geom['errScrollW']} > clientWidth={geom['errClientW']}) — it must wrap"
                    )
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
