"""WCAG 4.1.3 Status Messages — the Chrome-extension popup announces feedback.

The popup (`browser-extension/popup.html` + `popup.js` + `harness-snippets.js`)
gives the user FEEDBACK without moving focus or reloading:

  • the "Type a question first." VALIDATION error (clicked Send with an empty
    textarea — a sighted user sees the ribbon, a screen-reader user needs to
    hear WHY the submit didn't fire),
  • the "Failed: <error>" DISPATCH FAILURE ribbon,
  • the "✓ Copied — paste into …" COPY ACK on the setup-card / harness-snippet
    buttons (a button flipping its OWN textContent is MUTE to AT).

Before the fix the popup had ZERO aria-live regions — every one of these was a
plain `<div>` / a bare button-label flip, so a screen-reader user heard
nothing. This pins the live-region wiring: the validation error + dispatch
failure land inside a role=status/aria-live region (`#status`), and the copy
acks are pushed through the persistent `#popup-sr-status` polite mirror (the
visible button copy is unchanged — only the announcement is added).

Found 2026-06-21 by driving the real popup and reading the DOM live-region
ancestry. Slow + browser; loads popup.html via file:// with a fully stubbed
chrome.runtime (no extension context / Native-Messaging host needed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
POPUP = REPO / "browser-extension" / "popup.html"

# Walk up from `sel`, returning the first ancestor (or self) that is a live
# region (role=status/alert or aria-live), or None — exactly what an AT uses to
# decide whether a text change is announced.
_LIVE_ANCESTOR = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  let n = el;
  while (n && n.nodeType === 1) {
    const role = n.getAttribute && n.getAttribute('role');
    const live = n.getAttribute && n.getAttribute('aria-live');
    if (role === 'status' || role === 'alert' || live) {
      return { id: n.id || n.tagName, role: role, live: live,
               text: (n.textContent || '').slice(0, 80) };
    }
    n = n.parentElement;
  }
  return null;
}"""

# launch-council returns a hard failure so we can read the dispatch-failure
# ribbon; everything else resolves ok so popup.js initializes cleanly.
_STUB_FAIL = """
window.chrome = { runtime: { id: 'testext', lastError: null,
  sendMessage: (m, cb) => {
    if (m && m.kind === 'launch-council') {
      setTimeout(() => cb({ ok: false, error: 'CLI exited 1: boom' }), 5); return;
    }
    setTimeout(() => cb({ ok: true }), 5);
  } } };
"""

# Every action returns native-host-unavailable so the popup renders the setup
# card (which carries the copy-ack buttons).
_STUB_SETUP = """
window.chrome = { runtime: { id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', lastError: null,
  sendMessage: (m, cb) => { setTimeout(() => cb({ ok: false, error: 'native-host-unavailable' }), 5); } } };
"""


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no launchable chromium: {exc}")


def test_popup_validation_and_dispatch_errors_are_announced():
    """The compose ribbon's validation error + dispatch failure are in a live region."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_context(viewport={"width": 460, "height": 760}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_STUB_FAIL)
            page.goto(f"file://{POPUP}")
            page.wait_for_selector("#compose", state="visible", timeout=5000)

            # PRECONDITION B (discriminating): before any action the ribbon is
            # empty — so a later non-empty announcement is a real transition.
            assert page.evaluate("document.getElementById('status').textContent") == "", \
                "precondition: #status ribbon should start empty"

            # PRECONDITION A: the action fires + the feedback PAINTS (visual).
            page.fill("#task", "")
            page.click("#run-btn")
            page.wait_for_function(
                "document.getElementById('status').textContent.includes('Type a question first')",
                timeout=3000,
            )

            live = page.evaluate(_LIVE_ANCESTOR, "#status")
            assert live is not None, (
                "4.1.3 FAIL: the popup validation error 'Type a question first.' is "
                "written into a plain <div> with NO aria-live region — a screen-reader "
                "user clicks Send on an empty textarea and hears NOTHING about why the "
                "submit didn't fire. #status needs role=status + aria-live."
            )
            assert live["role"] == "status" or live["live"], live
            assert "Type a question first" in live["text"], live

            # Dispatch FAILURE lands in the same live region.
            page.fill("#task", "SQLite or DuckDB?")
            page.click("#run-btn")
            page.wait_for_function(
                "document.getElementById('status').textContent.includes('Failed')",
                timeout=4000,
            )
            live2 = page.evaluate(_LIVE_ANCESTOR, "#status")
            assert live2 is not None and (live2["role"] == "status" or live2["live"]), (
                "4.1.3 FAIL: the popup 'Failed: <error>' dispatch failure is not in a "
                "live region — a screen-reader user hears nothing when the council fails."
            )
            assert "Failed" in live2["text"], live2

            assert not errs, f"popup raised JS errors: {errs}"
        finally:
            browser.close()


def test_popup_copy_acks_are_announced():
    """The setup-card + harness-snippet '✓ Copied' acks reach a polite live region."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            ctx = browser.new_context(
                viewport={"width": 460, "height": 760},
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_STUB_SETUP)
            page.goto(f"file://{POPUP}")

            # Trigger the setup card (dispatch returns host-unavailable).
            page.fill("#task", "x")
            page.click("#run-btn")
            page.wait_for_selector("#copy-setup-brief", state="visible", timeout=5000)

            # PRECONDITION B (discriminating): no sr mirror exists / it is empty
            # before the copy — so a later announcement is a real transition.
            pre = page.evaluate(
                "() => { const r = document.getElementById('popup-sr-status');"
                " return r ? r.textContent : null; }"
            )
            assert not pre, f"precondition: sr mirror should be absent/empty pre-copy, got {pre!r}"

            # PRECONDITION A: the copy ack fires + the button label flips (visual).
            page.click("#copy-setup-brief")
            page.wait_for_function(
                "document.getElementById('copy-setup-brief').textContent.includes('Copied')",
                timeout=3000,
            )

            region = page.evaluate(
                "() => { const r = document.getElementById('popup-sr-status');"
                " return r ? { role: r.getAttribute('role'), live: r.getAttribute('aria-live'),"
                " text: r.textContent } : null; }"
            )
            assert region is not None, (
                "4.1.3 FAIL: the popup copy-ack flips the button's OWN textContent to "
                "'✓ Copied' — which is MUTE to a screen reader — and there is NO "
                "aria-live region announcing the copy succeeded. Needs a persistent "
                "role=status mirror (#popup-sr-status)."
            )
            assert region["role"] == "status" or region["live"], region
            assert "Copied" in region["text"], region
            # The VISIBLE button copy must be unchanged by the a11y fix.
            assert "✓ Copied" in page.evaluate(
                "document.getElementById('copy-setup-brief').textContent"
            ), "the a11y fix must not change the visible copy-ack button text"

            # Sibling copy ack (terminal commands) routes through the same region.
            page.click("#copy-setup-cmds")
            page.wait_for_function(
                "document.getElementById('copy-setup-cmds').textContent.includes('Copied')",
                timeout=3000,
            )
            r2 = page.evaluate(
                "() => document.getElementById('popup-sr-status').textContent"
            )
            assert "Copied" in r2 and "terminal" in r2, r2

            # The harness-snippet picker copy ack (harness-snippets.js) too.
            if page.evaluate("() => !!document.querySelector('.harness-pill')"):
                page.click(".harness-pill")
                btn = page.query_selector("button:has-text('Copy config block')")
                if btn:
                    btn.click()
                    page.wait_for_function(
                        "document.getElementById('popup-sr-status').textContent.includes('Copied')",
                        timeout=3000,
                    )
                    r3 = page.evaluate(
                        "() => document.getElementById('popup-sr-status').textContent"
                    )
                    assert "Copied" in r3, (
                        "4.1.3 FAIL: the harness-snippet config-block copy ack is not "
                        f"announced (sr mirror={r3!r})."
                    )

            assert not errs, f"popup raised JS errors: {errs}"
        finally:
            browser.close()
