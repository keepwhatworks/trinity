"""Browser guard: the COLD (empty-home) LAUNCHPAD — the primary first-run surface a
brand-new user sees before building anything — must render whole-page error-free,
non-blank, with no template/degenerate leaks and a real next-step CTA.

Coverage gap this fills: the cold-start *memory viewer* has a full-page browser
guard (test_memory_viewer_cold_start_browser), and the eval card's empty state has
one (test_launchpad_eval_empty_state_browser) — but that eval test feeds a
HAND-BUILT page_data centered on the eval summary; it never renders the launchpad
from a GENUINELY empty home via the real `render_launchpad_html()` → `build_page_data()`
path. So the OTHER cards' cold-start states (routing, memory-health, timeline,
drift, council, sync) aren't browser-verified end-to-end on a fresh install. A new
card or a data-shape change that dead-ends / blanks / throws on an empty home would
slip past — and this is the #115 first-run-wow surface, the make-or-break moment.

Renders the REAL cold-start launchpad (empty TRINITY_HOME, autoscan off) over http
(petite-vue mounts + vendor fonts/chart need real serving) and pins:
  • no uncaught JS / console errors,
  • the petite-vue shell actually mounts (`.launchpad-shell` present),
  • a non-blank page (not a broken/empty render),
  • no `{{ }}` template leak, no `undefined` / `NaN` / `[object Object]` in the
    visible text (degraded-data leaking to the UI),
  • a real first-run CTA (the council / Ask painkiller — the product's lead).

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_cold_start_launchpad_renders_useful_and_error_free(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # A genuinely empty home — the brand-new-install state. autoscan off so the
    # render can't kick a background lens build mid-test.
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    html = render_launchpad_html()  # builds cold-start page_data from the empty home
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1600}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.on(
                    "console",
                    lambda m: errs.append("console.error: " + m.text[:200])
                    if m.type == "error" and "favicon" not in m.text.lower()
                    else None,
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1200)

                assert not errs, f"cold-start launchpad threw JS errors: {errs[:4]}"

                # the petite-vue shell mounted — not a broken/blank pre-mount page
                assert page.query_selector(".launchpad-shell") is not None, (
                    "the .launchpad-shell didn't mount on a cold home — the first-run "
                    "render is broken"
                )

                body = page.evaluate("document.body.innerText")
                assert len(body) > 200, (
                    f"cold launchpad rendered a near-blank page ({len(body)} chars) — "
                    "a broken empty-home render, not a useful first-run state"
                )

                # degraded-data / template leaks must NOT reach the visible UI.
                leaks = [tok for tok in ("{{", "}}", "undefined", "NaN", "[object Object]")
                         if tok in body]
                assert not leaks, (
                    f"cold launchpad leaked {leaks} into the visible text — a template "
                    "directive or degraded-data value reached the UI"
                )

                # the product leads with the council / Ask painkiller — the first-run
                # CTA must be present so a new user has a next step.
                low = body.lower()
                assert "council" in low or "ask" in low, (
                    "cold launchpad shows no council/Ask first-run CTA — a new user "
                    "has no obvious next step"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
