"""Browser guard for KEYBOARD OPERABILITY of the in-provider SYNC PILL
(browser-extension/sync-pill.js) — WCAG 2.1.1 (Keyboard) + 4.1.2 (Name, Role,
Value) + 2.4.7 (Focus Visible).

The "⠕ N to sync" pill is injected into claude.ai / chatgpt.com / gemini.google.com
and is the ONLY way to start a current-tab sync. It was a bare
`<div role="status">` with an `onclick` and NO tabindex / role=button / keydown —
i.e. MOUSE-ONLY. Driven 2026-06-21: 40 Tabs never landed on it, `.focus()` didn't
take, Enter/Space dispatched nothing — a keyboard-only user could never trigger a
sync. Same WCAG 2.1.1 shape as the memory-viewer topology nodes fixed in iter 232.

The fix (renderIdle): promote the IDLE pill to a real button widget — tabindex=0,
role=button, aria-label, and a keydown firing the SAME startSync as the click;
role flips back to `status` in renderActive/renderJustFinished (those are live
progress announcements, not controls).

This drives the REAL source unmodified by mapping `claude.ai` → 127.0.0.1 via
`--host-resolver-rules` so `location.hostname === "claude.ai"` and the IIFE's
host-guard passes. chrome.runtime.sendMessage is stubbed (no live capture host /
no real sync / no real Chrome dir touched). Slow + browser marked; skips cleanly
without Playwright/chromium.

Mutation-proven (iter 233): stripping the tabindex/role=button/keydown block from
renderIdle reds this guard ("keyboard user could never start a sync — WCAG 2.1.1");
the existing string-presence sync-pill tests stay green (they never drive it).
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
PILL_SRC = REPO / "browser-extension" / "sync-pill.js"

CHROME_STUB = """
window.__SENT__ = [];
window.chrome = {
  runtime: {
    id: 'testid',
    lastError: null,
    sendMessage: function(msg, cb) {
      window.__SENT__.push(msg);
      if (msg.type === 'get_current_tab_sync_state') { cb && cb({active:false}); return; }
      if (msg.query_kind === 'sync_status') { cb && cb({ok:true, missing_count:3, missing_ids:['a','b','c']}); return; }
      if (msg.type === 'start_current_tab_sync') { cb && cb({ok:true}); return; }
      cb && cb({});
    }
  }
};
"""


def _serve(root: Path):
    (root / "index.html").write_text(
        "<!doctype html><html><head><meta charset=utf-8><title>provider</title></head>"
        "<body><h1>fake provider page</h1><button id='before'>before</button></body></html>"
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[assignment]
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _n_sync_dispatches(page) -> int:
    return page.evaluate(
        "window.__SENT__.filter(m => m.type === 'start_current_tab_sync').length"
    )


def test_idle_sync_pill_is_keyboard_operable(tmp_path):
    """Tab reaches the idle pill, focus actually lands on it, and Enter (then a
    fresh render, Space) dispatches start_current_tab_sync — the SAME effect as a
    mouse click. The pre-activation state has ZERO sync dispatched (so the
    keyboard press proves a real transition), and the pill is the ACTUAL
    activeElement (focus via keyboard, not a mouse/programmatic leak)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    pill_src = PILL_SRC.read_text()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=[
                    f"--host-resolver-rules=MAP claude.ai 127.0.0.1:{port}",
                    "--headless=new",
                ],
            )
            ctx = browser.new_context()
            ctx.add_init_script(CHROME_STUB)
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://claude.ai:{port}/index.html", wait_until="load")
            assert page.evaluate("location.hostname") == "claude.ai"

            # Inject the REAL source; it schedules its first tick after
            # FIRST_POLL_DELAY_MS — wait for the idle pill to paint.
            page.evaluate(pill_src)
            page.wait_for_selector("#__trinity_sync_pill__:not([hidden])", timeout=8000)

            # --- BITE PRECONDITION (A): role/tabindex are the button-widget state.
            info = page.evaluate(
                """() => {
                  const el = document.getElementById('__trinity_sync_pill__');
                  return {role: el.getAttribute('role'),
                          tabindex: el.getAttribute('tabindex'),
                          aria: el.getAttribute('aria-label'),
                          text: el.textContent};
                }"""
            )
            assert "to sync" in info["text"], f"idle pill did not paint: {info}"
            assert info["role"] == "button", (
                "the in-provider sync pill is the ONLY way to start a current-tab "
                "sync but it is NOT a button to AT/keyboard — role=%r "
                "(WCAG 4.1.2). A keyboard user on claude.ai can't operate it." % info["role"]
            )
            assert info["tabindex"] == "0", (
                "the sync pill is not in the keyboard focus order (tabindex=%r) — "
                "mouse-only sync trigger (WCAG 2.1.1 Keyboard)." % info["tabindex"]
            )
            assert info["aria"], "the sync pill button has no accessible name (WCAG 4.1.2)"

            # --- BITE PRECONDITION (B): pre-activation state is the OTHER state —
            # zero syncs dispatched. So a post-Enter dispatch proves a real
            # transition (not a pre-existing fire).
            assert _n_sync_dispatches(page) == 0, "a sync was dispatched before activation"

            # Reach the pill the way a KEYBOARD user does — Tab from #before.
            page.focus("#before")
            landed = False
            for _ in range(30):
                page.keyboard.press("Tab")
                if page.evaluate("document.activeElement && document.activeElement.id") == "__trinity_sync_pill__":
                    landed = True
                    break
            assert landed, (
                "Tab never reaches the sync pill — a keyboard user could never "
                "start a sync on claude.ai/chatgpt.com/gemini (WCAG 2.1.1)."
            )
            # Focus IS on the pill (keyboard, not mouse) before we activate.
            assert page.evaluate(
                "document.activeElement && document.activeElement.id"
            ) == "__trinity_sync_pill__"

            # ENTER fires the SAME sync as a click.
            page.keyboard.press("Enter")
            page.wait_for_function(
                "window.__SENT__.filter(m => m.type === 'start_current_tab_sync').length >= 1",
                timeout=3000,
            )
            assert _n_sync_dispatches(page) >= 1, (
                "Enter on the focused sync pill did NOT dispatch start_current_tab_sync "
                "— keyboard activation is a no-op (WCAG 2.1.1)."
            )

            assert errs == [], f"page errors: {errs}"
            browser.close()
    finally:
        httpd.shutdown()


def test_idle_sync_pill_space_activates(tmp_path):
    """Space (the other button-activation key) on the focused idle pill also fires
    the sync — a fresh page so the one-shot onkeydown is armed."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    pill_src = PILL_SRC.read_text()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=[
                    f"--host-resolver-rules=MAP claude.ai 127.0.0.1:{port}",
                    "--headless=new",
                ],
            )
            ctx = browser.new_context()
            ctx.add_init_script(CHROME_STUB)
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://claude.ai:{port}/index.html", wait_until="load")
            page.evaluate(pill_src)
            page.wait_for_selector("#__trinity_sync_pill__:not([hidden])", timeout=8000)
            assert _n_sync_dispatches(page) == 0

            page.focus("#before")
            landed = False
            for _ in range(30):
                page.keyboard.press("Tab")
                if page.evaluate("document.activeElement && document.activeElement.id") == "__trinity_sync_pill__":
                    landed = True
                    break
            assert landed, "Tab never reaches the sync pill (WCAG 2.1.1)"
            page.keyboard.press(" ")
            page.wait_for_function(
                "window.__SENT__.filter(m => m.type === 'start_current_tab_sync').length >= 1",
                timeout=3000,
            )
            assert _n_sync_dispatches(page) >= 1, (
                "Space on the focused sync pill did NOT dispatch the sync (WCAG 2.1.1)."
            )
            assert errs == [], f"page errors: {errs}"
            browser.close()
    finally:
        httpd.shutdown()
