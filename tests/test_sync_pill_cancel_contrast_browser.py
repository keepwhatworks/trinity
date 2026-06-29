"""Browser WCAG-AA guard for the in-provider SYNC PILL's in-flight "Cancel" button
(browser-extension/sync-pill.js `renderActive` → `.__trinity_cancel`).

FOUNDER SYMPTOM (UX sweep 2026-06-22): while a current-tab sync is in flight the pill
paints "⠕ Syncing N/M… [Cancel]" on claude.ai / chatgpt.com / gemini.google.com. The
Cancel button — the ONLY way to abort an in-flight sync — drew its near-white label
(`color:inherit` #fbfdfc, 13px body) on a `rgba(251,253,252,0.16)` white tint, which
composited over the deep-teal pill fill (#34666b) to #547e82 = **4.38:1**, below the
WCAG AA 4.5:1 body floor. It was the LEAST-readable text in the in-flight sync UI.

This is the un-fixed CONTENT-SCRIPT sibling of the 2026-06-21 white-on-teal sweep
(test_white_on_teal_fill_contrast_browser.py fixed the council `.rank-badge` + the
memory-viewer `.view-toggle button.active`). Those sweeps render the launchpad / viewer
/ review surfaces; the sync pill is injected into the PROVIDER pages by a content
script, so no launchpad-render contrast guard ever touched it.

ROOT CAUSE / CLASS: a white-tint chip on a teal fill caps the white-text contrast —
the lighter the chip, the lower the label contrast. FIX (renderActive chip): hold the
tint LOW — base 0.10 (#547e7a → 5.01:1), hover 0.14 (4.58:1) — so the label clears AA
in both states while staying a recognisably lighter recessed chip.

This drives the REAL source unmodified by mapping `claude.ai` → 127.0.0.1 via
`--host-resolver-rules` so `location.hostname === "claude.ai"` and the IIFE's host-guard
passes. chrome.runtime.sendMessage is stubbed to report an ACTIVE current-tab sync, so
`renderActive` fires and paints the Cancel button (no live capture host / no real sync /
no real Chrome dir touched). Slow + browser marked; skips cleanly without
Playwright/chromium.

Mutation-proven (2026-06-22): revert the chip tint to `rgba(251,253,252,0.16)` and this
guard reds at ~4.38:1 with the named founder symptom; the existing sync-pill
keyboard/string tests stay green (they never enter the active state or read contrast).
The fix clears it (~5.01 base / ~4.58 hover).
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

# Report an ACTIVE current-tab sync so renderActive() fires and paints the Cancel
# button (the element under test). landed<total so the "Syncing N/M…" branch shows.
CHROME_STUB = """
window.__SENT__ = [];
window.chrome = {
  runtime: {
    id: 'testid',
    lastError: null,
    sendMessage: function(msg, cb) {
      window.__SENT__.push(msg);
      if (msg.type === 'get_current_tab_sync_state') {
        cb && cb({active:true, provider:'claude', landed:2, total:5}); return;
      }
      if (msg.query_kind === 'sync_status') { cb && cb({ok:true, missing_count:3, missing_ids:['a','b','c']}); return; }
      cb && cb({});
    }
  }
};
"""

# Composited-contrast helper run IN the page: folds the element's COMPUTED color over
# the entire ancestor background stack (GPU-style), returns the real WCAG ratio.
_CONTRAST_JS = r"""
(sel) => {
  function parse(c){ const m=(c||'').match(/[\d.]+/g); if(!m) return null;
    const n=m.map(Number); return {r:n[0],g:n[1],b:n[2],a:n.length>3?n[3]:1}; }
  function over(fg,bg){ const a=fg.a; return {r:fg.r*a+bg.r*(1-a), g:fg.g*a+bg.g*(1-a),
    b:fg.b*a+bg.b*(1-a), a:1}; }
  function lum(c){ const f=x=>{x/=255; return x<=0.03928?x/12.92:Math.pow((x+0.055)/1.055,2.4);};
    return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b); }
  function ratio(a,b){ const la=lum(a),lb=lum(b),hi=Math.max(la,lb),lo=Math.min(la,lb);
    return (hi+0.05)/(lo+0.05); }
  const el = document.querySelector(sel);
  if(!el) return {found:false};
  const cs = getComputedStyle(el);
  const fg = parse(cs.color);
  let stack = []; let node = el;
  while(node){ const b = parse(getComputedStyle(node).backgroundColor); if(b && b.a>0) stack.unshift(b); node = node.parentElement; }
  let acc = {r:255,g:255,b:255,a:1};
  for(const b of stack){ acc = over(b, acc); }
  const fgOver = over(fg, acc);
  return {found:true, visible: el.offsetParent !== null, text: (el.innerText||'').trim(),
          fontSize: parseFloat(cs.fontSize), fontWeight: cs.fontWeight, colorRaw: cs.color,
          bg: [Math.round(acc.r), Math.round(acc.g), Math.round(acc.b)],
          ratio: +ratio(fgOver, acc).toFixed(3)};
}
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


def test_in_flight_cancel_button_label_clears_aa(tmp_path):
    """While a current-tab sync is in flight, the pill's "Cancel" button — the only
    abort affordance — paints near-white text on a teal-tint chip. Its label must clear
    WCAG AA 4.5:1 (13px body) in BOTH the default and hover states, measured from the
    COMPUTED color composited over the real chip-over-pill background.

    FOUNDER SYMPTOM: the chip was rgba(251,253,252,0.16) over #34666b → #547e82 =
    4.38:1 (sub-AA) — the least-readable text in the in-flight sync UI, the un-fixed
    content-script sibling of the white-on-teal sweep. The chip tint must stay LOW
    (0.10 base / 0.14 hover) so white text clears the floor."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    pill_src = PILL_SRC.read_text()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    args=[
                        f"--host-resolver-rules=MAP claude.ai 127.0.0.1:{port}",
                        "--headless=new",
                    ],
                )
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            ctx = browser.new_context()
            ctx.add_init_script(CHROME_STUB)
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://claude.ai:{port}/index.html", wait_until="load")
            assert page.evaluate("location.hostname") == "claude.ai"

            # Inject the REAL source; it schedules its first tick after
            # FIRST_POLL_DELAY_MS. With the stub reporting active=true, renderActive
            # paints "Syncing N/M…" + the Cancel button.
            page.evaluate(pill_src)
            page.wait_for_selector(
                "#__trinity_sync_pill__ .__trinity_cancel", timeout=8000
            )

            # --- BITE PRECONDITION (non-vacuous): we are genuinely in the in-flight
            # ACTIVE state (Syncing text present) and the Cancel chip actually paints.
            pill_text = page.evaluate(
                "document.getElementById('__trinity_sync_pill__').textContent"
            )
            assert "Syncing" in pill_text and "Cancel" in pill_text, (
                f"the pill is not in the in-flight active state: {pill_text!r} — "
                "the Cancel contrast sample would be vacuous."
            )

            d = page.evaluate(_CONTRAST_JS, "#__trinity_sync_pill__ .__trinity_cancel")
            assert d.get("found") and d.get("visible"), (
                "the in-flight Cancel button never painted — the contrast assertion "
                "would be vacuous."
            )
            assert d["text"] == "Cancel", f"unexpected Cancel label: {d['text']!r}"
            # It is small body text — the AA-normal 4.5 floor applies (not large 3:1).
            assert d["fontSize"] <= 16 and float(d["fontWeight"]) < 700, (
                f"the Cancel button is no longer small/normal text "
                f"({d['fontSize']}px/{d['fontWeight']}) — re-check the threshold."
            )
            assert d["ratio"] >= 4.5, (
                f"in-flight sync-pill Cancel button is {d['ratio']}:1 (label {d['text']!r}, "
                f"color {d['colorRaw']}, {d['fontSize']}px/{d['fontWeight']}, effective bg "
                f"{d['bg']}) — below WCAG AA 4.5 for body text. FOUNDER SYMPTOM: the chip "
                f"rgba(251,253,252,0.16) over #34666b composited to #547e82 = 4.38:1, the "
                f"least-readable text in the in-flight sync UI. Hold the chip tint low "
                f"(0.10 base) so the near-white Cancel label clears the floor."
            )

            # HOVER state must also clear AA (the label stays visible while hovered).
            page.hover("#__trinity_sync_pill__ .__trinity_cancel")
            page.wait_for_timeout(150)
            h = page.evaluate(_CONTRAST_JS, "#__trinity_sync_pill__ .__trinity_cancel")
            assert h["ratio"] >= 4.5, (
                f"the HOVERED Cancel button is {h['ratio']}:1 (effective bg {h['bg']}) — "
                f"below WCAG AA 4.5. FOUNDER SYMPTOM: the hover chip rgba(251,253,252,0.30) "
                f"over #34666b was 3.26:1. Hold the hover tint low (0.14) too."
            )

            assert errs == [], f"page errors: {errs}"
            browser.close()
    finally:
        httpd.shutdown()
