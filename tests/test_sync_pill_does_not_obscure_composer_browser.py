"""Browser guard: the in-provider SYNC PILL (browser-extension/sync-pill.js) must
NOT obscure the provider page's SEND button.

The pill is injected into claude.ai / chatgpt.com / gemini.google.com at
z-index 2147483647 (the 32-bit max, so nothing on the host page can paint over
it). It used to sit at `bottom:16px; right:16px` — directly on top of the
provider's SEND button. All three providers anchor Send to the bottom-right of a
bottom-fixed composer, and on narrow/mobile widths that composer is full-bleed so
Send reaches the viewport's bottom-right corner.

Driven 2026-06-23 against a synthetic provider page (a bottom-fixed composer with
a circular send button anchored bottom-right — the universal chat-UI layout) at
393/375/320: the pill overlapped the send button by ~1737px², `elementFromPoint`
at the send-button center returned the PILL, and a real tap on the send button
center fired a Trinity sync (`start_current_tab_sync`) instead of the host page's
send handler (send_clicks:0, sync_dispatched:1) — a fixed top-z overlay hijacking
the host page's PRIMARY control. The fix moves the pill to the bottom-LEFT corner,
which is clear of Send on every provider.

This drives the REAL source unmodified by mapping `claude.ai` → 127.0.0.1 via
`--host-resolver-rules` so the IIFE's host-guard passes; chrome.runtime is stubbed
(no live capture host / no real sync / no real Chrome dir touched). Slow + browser
marked; skips cleanly without Playwright/chromium.

Mutation-proven (2026-06-23): reverting the pill to `right: 16px` reds
test_sync_pill_does_not_cover_provider_send_button on exactly the
overlap/click-steal assertions (overlap > 0, send center top-element is the pill,
send_clicks == 0, sync_dispatched == 1) at every narrow breakpoint; the existing
string-presence + behavior sync-pill tests stay green (none drive the geometry).
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
    id: 'testid', lastError: null,
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

# A synthetic provider page that mimics the layout the pill lands next to: a
# bottom-fixed composer with a circular SEND button anchored bottom-right —
# the universal chat-UI layout of claude.ai / chatgpt.com / gemini.google.com.
# NEVER the real provider sites.
HOST_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>provider</title>
<style>
 html,body{margin:0;height:100%;font-family:sans-serif;background:#fff;color:#111;}
 .composer{position:fixed;left:0;right:0;bottom:0;display:flex;align-items:flex-end;gap:8px;
   padding:12px 16px;background:#f7f7f7;border-top:1px solid #ccc;}
 .composer textarea{flex:1;min-height:44px;}
 #send{width:48px;height:48px;border-radius:50%;border:0;background:#3f777c;color:#fff;
   font-size:20px;cursor:pointer;}
</style></head><body>
<div class="composer"><textarea placeholder="Message..."></textarea>
<button id="send" aria-label="Send message">&#10148;</button></div>
<script>window.__SEND_CLICKS__=0;
document.getElementById('send').addEventListener('click',()=>{window.__SEND_CLICKS__++;});</script>
</body></html>"""


def _serve(root: Path):
    (root / "index.html").write_text(HOST_HTML)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[assignment]
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def test_sync_pill_does_not_cover_provider_send_button(tmp_path):
    """At narrow/mobile widths (where the provider composer is full-bleed and Send
    reaches the viewport's bottom-right corner), the idle "N to sync" pill must NOT
    overlap the host page's send button, and a tap on the send button center must
    reach SEND — not get intercepted by the pill (which would start a sync instead
    of sending the user's message)."""
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
            for width in (393, 375, 320):
                ctx = browser.new_context(viewport={"width": width, "height": 740})
                ctx.add_init_script(CHROME_STUB)
                page = ctx.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.goto(f"http://claude.ai:{port}/index.html", wait_until="load")
                assert page.evaluate("location.hostname") == "claude.ai"

                page.evaluate(pill_src)
                page.wait_for_selector(
                    "#__trinity_sync_pill__:not([hidden])", timeout=9000
                )

                geo = page.evaluate(
                    """() => {
                      const pill = document.getElementById('__trinity_sync_pill__');
                      const send = document.getElementById('send');
                      const pr = pill.getBoundingClientRect();
                      const sr = send.getBoundingClientRect();
                      const ox = Math.max(0, Math.min(pr.right, sr.right) - Math.max(pr.left, sr.left));
                      const oy = Math.max(0, Math.min(pr.bottom, sr.bottom) - Math.max(pr.top, sr.top));
                      const scx = sr.left + sr.width/2, scy = sr.top + sr.height/2;
                      const topAtSend = document.elementFromPoint(scx, scy);
                      return {
                        zIndex: getComputedStyle(pill).zIndex,
                        overlapArea: Math.round(Math.max(0, ox) * Math.max(0, oy)),
                        sendCenterTopEl: topAtSend ? (topAtSend.id || topAtSend.tagName) : null,
                        pillText: pill.textContent,
                        pillVisible: !pill.hidden && pr.width > 0 && pr.height > 0,
                        pillRightPastVp: pr.right > window.innerWidth + 0.5,
                        pillBottomPastVp: pr.bottom > window.innerHeight + 0.5,
                      };
                    }"""
                )

                # --- BITE PRECONDITION (A): the pill actually painted at the top
                # of the z-stack (so it COULD obscure the send button — the test
                # isn't passing because the pill is missing/behind the page).
                assert geo["pillVisible"], (
                    f"@{width}: idle sync pill did not paint — geometry test vacuous"
                )
                assert "to sync" in geo["pillText"], (
                    f"@{width}: idle pill text unexpected: {geo['pillText']!r}"
                )
                assert geo["zIndex"] == "2147483647", (
                    f"@{width}: pill z-index changed ({geo['zIndex']!r}); the overlay "
                    "no longer sits on top so this geometry guard would no longer bite"
                )

                # --- THE BITING ASSERTIONS: Send must be uncovered + clickable.
                assert geo["overlapArea"] == 0, (
                    f"@{width}: the sync pill OVERLAPS the provider's send button by "
                    f"{geo['overlapArea']}px² — a fixed z-index-max overlay covering "
                    "the host page's primary control (the bottom-right Send button)."
                )
                assert geo["sendCenterTopEl"] == "send", (
                    f"@{width}: elementFromPoint at the send-button center is "
                    f"{geo['sendCenterTopEl']!r}, not 'send' — the sync pill is "
                    "painted on top of the provider's Send button."
                )
                assert not geo["pillRightPastVp"] and not geo["pillBottomPastVp"], (
                    f"@{width}: pill clipped past the viewport: {geo}"
                )

                # --- CLICK-STEAL PROOF: a real tap on the send button center must
                # reach SEND, not start a Trinity sync.
                sr = page.evaluate(
                    "() => { const s = document.getElementById('send').getBoundingClientRect();"
                    "return {x: s.left + s.width/2, y: s.top + s.height/2}; }"
                )
                page.mouse.click(sr["x"], sr["y"])
                page.wait_for_timeout(150)
                send_clicks = page.evaluate("window.__SEND_CLICKS__")
                sync_dispatched = page.evaluate(
                    "window.__SENT__.filter(m => m.type === 'start_current_tab_sync').length"
                )
                assert send_clicks == 1, (
                    f"@{width}: a tap on the provider's send button did NOT reach it "
                    f"(send_clicks={send_clicks}) — the sync pill intercepted the click."
                )
                assert sync_dispatched == 0, (
                    f"@{width}: a tap meant for the provider's Send button fired a "
                    f"Trinity sync ({sync_dispatched}×) instead — the pill hijacked the "
                    "host page's primary control."
                )

                assert errs == [], f"@{width}: page errors: {errs}"
                ctx.close()
            browser.close()
    finally:
        httpd.shutdown()
