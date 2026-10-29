"""With no dispatcher, a launchpad action must report a failure, not reload the page.

Every launchpad action routes through `window.__TRINITY_DISPATCH__`, which the runtime
injects on every page. Each call site used to carry an else-branch for a missing
dispatcher that clicked a link built by `buildShortcutUrl()`, a retired macOS Shortcut
tier that has returned '' since 2026-05-18. An `<a href="">` click navigates to the
current page: the launch reloaded, the typed prompt was lost and no feedback showed.
Refresh memory and Repair extension went further and reported success (`finish(null)`
read null as ok).

The fix sends every action through one `dispatchAction()` that, with no dispatcher,
hands the caller's own result handler the runtime's no-dispatcher failure. This drives
the real page: remove the dispatcher after load, click Launch, and assert the page did
not navigate, the install banner shows, and the prompt is back for a retry. Restoring
the old else-branch reds it (the navigation check).

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import pytest
from tests.browser_harness import serve_directory

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def test_launch_without_dispatcher_reports_failure_and_keeps_page(tmp_path, monkeypatch):
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
    httpd, port = serve_directory(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 1100})
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # A marker that only survives if the page does NOT navigate.
                page.evaluate("() => { window.__noNavMarker = 1; delete window.__TRINITY_DISPATCH__; }")
                page.fill("#council-prompt", "Compare three rate-limiting strategies")
                page.click(".actions button.button.primary", timeout=5000)
                page.wait_for_timeout(700)

                s = page.evaluate(
                    "() => { const b = document.querySelector('.actions button.button.primary');"
                    " const banner = [...document.querySelectorAll('.card,[class*=banner]')]"
                    ".find(e => e.offsetParent !== null && /extension|install/i.test(e.innerText||''));"
                    " return { stayed: window.__noNavMarker === 1,"
                    " btnDisabled: b ? b.disabled : null,"
                    " prompt: (document.getElementById('council-prompt')||{}).value || '',"
                    " bannerVisible: !!banner }; }"
                )
                assert s["stayed"], "launching with no dispatcher navigated away (the empty-link reload)"
                assert not errs, f"launching with no dispatcher threw: {errs[:3]}"
                assert s["bannerVisible"], "no dispatcher, yet no install banner: a silent failure"
                assert s["btnDisabled"] is False, "Launch button stuck disabled after the failure"
                assert "rate-limiting" in s["prompt"], f"prompt not restored for retry: {s['prompt']!r}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
