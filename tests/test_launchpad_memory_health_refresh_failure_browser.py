"""A failed "Refresh memory" / "Repair extension" click must keep its error
CO-LOCATED on the memory-health card — NOT leaked onto the council composer ribbon.

USEFULNESS / cross-surface-misattribution defect (2026-06-17 UX sweep, the
#242(a) class): refreshMemory() and repairExtension() routed their dispatch
result through handleDispatchResult(), which on a (non-host) extension error
sets `launchError` — a surface that renders ONLY in the COUNCIL composer's
launch-status ribbon. So a failed "Refresh memory" click on /stats showed a
bare "⚠ Failed" with no reason, while the real error ("capture host crashed …")
appeared in the HOME-view council ribbon as `.status-error`, reading as if a
COUNCIL had failed — even though the user never launched one. The lens-build
Stop/Restart buttons were already fixed off handleDispatchResult for exactly
this reason (lensBuildError, co-located); these two buttons were never migrated.

The fix gives refreshMemory/repairExtension their own co-located error
(refreshMemoryError / repairExtensionError) rendered on the memory-health (and
stale-capture) card, and stops them calling handleDispatchResult on failure.

This drives the REAL petite-vue render: seeds a stale memory state so the
memory-health card with the Refresh button renders, stubs a FAILED extension
dispatch, clicks Refresh, then asserts BOTH halves:
  (1) the WHY shows CO-LOCATED on the memory-health card, and
  (2) the council ribbon's `.status-error` does NOT carry the leaked error,
      even after flipping to the HOME view.

Mutation-provable: revert finish() back to calling handleDispatchResult (and
drop the co-located <p>) → the error leaks to .status-error on home + the
co-located message vanishes → this guard reds with the exact symptom.
"""
from __future__ import annotations

import functools
import http.server
import os
import threading
import time
from pathlib import Path

import pytest


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_refresh_memory_failure_stays_co_located_not_council_ribbon(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # Seed a stale state so _memory_health emits issues → the memory-health card
    # (which carries the Refresh-memory + Repair-extension buttons) renders.
    core = home / "core.md"
    core.write_text("old distillation", encoding="utf-8")
    old = time.time() - 10
    os.utime(core, (old, old))
    (home / "memories" / "lens.md").write_text("fresh lens tensions", encoding="utf-8")
    (home / "memories" / "topics.json").write_text(
        '{"basins": [{"id": "b00", "size": 1, "top_terms": [], "centroid": []}]}',
        encoding="utf-8",
    )

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    issues = page_data.get("memoryHealth", {}).get("issues", [])
    assert issues, "fixture should produce >=1 memory-health issue so the card renders"

    # Render the single-page app (BOTH views via the CSS toggle) so we can flip
    # home<->stats in place — exactly the side-panel/launchpad navigation.
    html = render_launchpad_html(page_data=page_data)

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
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda e: page_errors.append(str(e)))
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
                # Flip to /stats so the memory-health card is visible + interactive.
                page.evaluate("() => { if (window.setLaunchpadView) window.setLaunchpadView('stats'); }")
                page.wait_for_timeout(150)

                # Stub a FAILED extension dispatch (non-host error: the
                # "extension installed but the host errored" case).
                page.evaluate(
                    "() => { window.__TRINITY_DISPATCH__ = { dispatch: (o) => {"
                    " if (o && o.onResult) o.onResult({ tier: 'extension', ok: false,"
                    " response: { detail: 'capture host crashed (errno 13)' },"
                    " reason: 'subprocess-error' }); } }; }"
                )

                card_visible = page.evaluate(
                    "() => { const c = document.querySelector('section.memory-health-card');"
                    " return !!c && c.offsetParent !== null; }"
                )
                assert card_visible, "memory-health card not visible on /stats"

                # Click "Refresh memory".
                clicked = page.evaluate(
                    "() => { const card = document.querySelector('section.memory-health-card');"
                    " const btn = Array.from(card.querySelectorAll('button'))"
                    "   .find(b => /refresh memory|refreshing|updated/i.test(b.textContent));"
                    " if (btn) { btn.click(); return true; } return false; }"
                )
                assert clicked, "Refresh-memory button not found on the memory-health card"
                page.wait_for_timeout(300)

                on_stats = page.evaluate(
                    "() => { const card = document.querySelector('section.memory-health-card');"
                    " const colocated = Array.from(card.querySelectorAll('p'))"
                    "   .map(p => p.textContent.trim())"
                    "   .filter(t => /couldn't dispatch/i.test(t) && /capture host crashed/.test(t));"
                    " const se = document.querySelector('.status-error');"
                    " return { colocated, statusErr: se ? se.textContent.trim() : null,"
                    "   statusErrVisible: se ? se.offsetParent !== null : false }; }"
                )

                # (1) The WHY shows CO-LOCATED on the memory-health card.
                assert on_stats["colocated"], (
                    "a FAILED 'Refresh memory' click showed no co-located error on the "
                    "memory-health card — the user sees a bare '⚠ Failed' with no reason "
                    "while the real error ('capture host crashed …') was routed elsewhere"
                )
                # (2) The council ribbon's .status-error does NOT carry the leaked error.
                assert not on_stats["statusErrVisible"], (
                    "the failed Refresh-memory error leaked into the COUNCIL composer's "
                    "launch-status ribbon (.status-error) on /stats — the #242(a) "
                    f"cross-surface misattribution: {on_stats['statusErr']!r}"
                )

                # Flip to HOME — the leaked launchError used to surface in the council
                # ribbon here, reading as if a COUNCIL failed with 'capture host crashed'.
                page.evaluate("() => { if (window.setLaunchpadView) window.setLaunchpadView('home'); }")
                page.wait_for_timeout(200)
                on_home = page.evaluate(
                    "() => { const se = document.querySelector('.status-error');"
                    " const ls = document.querySelector('section.launch-status');"
                    " return { statusErr: se ? se.textContent.trim() : null,"
                    "   statusErrVisible: se ? se.offsetParent !== null : false,"
                    "   launchStatusVisible: ls ? ls.offsetParent !== null : false }; }"
                )
                assert not on_home["statusErrVisible"], (
                    "after a failed Refresh-memory click on /stats, the COUNCIL composer "
                    "ribbon on the HOME view shows the leaked error — a council the user "
                    f"never launched reads as failed: {on_home['statusErr']!r}"
                )
                assert not on_home["launchStatusVisible"], (
                    "the council launch-status ribbon is visible on home after a "
                    "memory-card dispatch failure — launchError was wrongly set on it"
                )

                assert not page_errors, f"console pageerrors during the flow: {page_errors}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
