"""In a real browser, surfaces say what actually happened (UI batch U3b, coherence review
2026-10-15):

- the live council page polled a "running" status file forever when the runner had died; it
  now applies the server's staleness window and says the council stopped updating;
- every copy button said "Copied" even when nothing reached the clipboard;
- both health-card buttons showed for any issue, so "Repair extension" (a full council on the
  user's subscription) appeared when only core.md needed attention.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_STUB_DISPATCH = ("window.__TRINITY_DISPATCH__ = {dispatch:(a,cb)=>{if(cb)cb({ok:true});},"
                  "probe:()=>Promise.resolve({state:'ready'}),subscribe:()=>{},onStateChange:()=>{}};")


def _serve(root: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _browser(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no launchable chromium: {exc}")


def _seed_running(token: str, monkeypatch, *, stale: bool) -> None:
    from trinity_local import council_status, state_paths, vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    # Pages first: write_portal_html reads council statuses, and the server-side rule would
    # rewrite a stale "running" file to failed before the browser saw it. This test is for the
    # page's own rule, which is what runs when nothing on the Python side reads the file.
    write_portal_html()
    write_live_council_page(force=True)
    vendor.publish_vendor_files(state_paths.portal_pages_dir())
    # A scoped patch: undoing the outer monkeypatch would also undo TRINITY_HOME.
    with monkeypatch.context() as m:
        if stale:   # the runner wrote this long ago and then died
            m.setattr(council_status, "now_iso", lambda: "2026-01-01T00:00:00Z")
        council_status.init_council_run_state(token, task_text="Pick a cache", bundle_id="b_" + token,
                                              members=["claude", "codex"])
        council_status.write_council_status(token, status="running")


@pytest.mark.parametrize("stale", [True, False])
def test_a_dead_council_stops_polling_and_says_so(tmp_path, monkeypatch, stale):
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    token = "stale_tok_%d" % int(stale)
    _seed_running(token, monkeypatch, stale=stale)
    httpd, port = _serve(tmp_path)
    try:
        with __import__("playwright.sync_api").sync_api.sync_playwright() as p:
            browser = _browser(p)
            try:
                page = browser.new_context().new_page()
                page.add_init_script(_STUB_DISPATCH)
                page.goto(f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}")
                page.wait_for_timeout(2500)
                text = page.evaluate("() => document.body.innerText")
                if stale:
                    assert "This council stopped updating" in text, text[:600]
                else:   # the control: a fresh "running" status is still shown as running
                    assert "This council stopped updating" not in text, text[:600]
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def _launchpad(tmp_path, page_data_patch: dict | None = None, view: str = "stats") -> str:
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files
    page_data = build_launchpad_payload()["pageData"]
    page_data.update(page_data_patch or {})
    html = render_launchpad_html(page_data=page_data, view=view)
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return "portal_pages/launchpad.html"


def test_a_failed_copy_does_not_say_copied(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    rel = _launchpad(tmp_path, view="home")
    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            browser = _browser(p)
            try:
                page = browser.new_context(viewport={"width": 1180, "height": 2000}).new_page()
                page.add_init_script(_STUB_DISPATCH + (
                    "Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => "
                    "Promise.reject(new Error('blocked'))}, configurable: true});"
                    "document.execCommand = () => false;"))
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                badge = page.locator(".code-copy-wrap .copy-badge:visible").first
                badge.wait_for(timeout=10000)
                badge.click()
                page.wait_for_timeout(600)
                state = page.evaluate("""() => ({
                  failure: (document.querySelector('.copy-failure') || {}).innerText || '',
                  badges: [...document.querySelectorAll('.code-copy-wrap .copy-badge')].map(b => b.innerText)
                })""")
                assert "Couldn't copy" in state["failure"], state
                assert not any("Copied" in b for b in state["badges"]), state
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("issues,refresh,repair", [
    ([{"name": "core.md", "status": "missing", "hint": "h", "command": "trinity-local lens", "href": None}],
     True, False),
    ([{"name": "extension", "status": "capture-drift", "hint": "h", "command": "x", "href": None}],
     False, True),
    ([{"name": "extension", "status": "auth-cookie-stale", "hint": "h", "command": None, "href": None}],
     False, False),
])
def test_each_health_button_shows_only_for_its_issues(tmp_path, monkeypatch, issues, refresh, repair):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    rel = _launchpad(tmp_path, {"memoryHealth": {"issues": issues, "ok_count": 5, "total_count": 6}})
    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            browser = _browser(p)
            try:
                page = browser.new_context(viewport={"width": 1180, "height": 2000}).new_page()
                page.add_init_script(_STUB_DISPATCH)
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_selector(".memory-health-card", timeout=10000)
                labels = page.evaluate(
                    "() => [...document.querySelectorAll('.memory-health-card button')].map(b => b.innerText)")
                low = [b.lower() for b in labels]   # the chips render upper-case
                assert any("refresh memory" in b for b in low) is refresh, labels
                assert any("repair extension" in b for b in low) is repair, labels
            finally:
                browser.close()
    finally:
        httpd.shutdown()
