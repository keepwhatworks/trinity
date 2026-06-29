"""Browser guard for HONEST TERMINAL FEEDBACK of the in-provider SYNC PILL's
just-finished state (browser-extension/sync-pill.js `renderJustFinished`).

FOUNDER SYMPTOM (UX sweep 2026-06-23): the current-tab sync orchestrator
(background.js `runCurrentTabSync`) only increments `landed` when a thread's
capture actually lands; a nav timeout (SYNC_NAV_TIMEOUT_MS = 12s) or a host that
never writes the file leaves `landed` un-incremented while the loop STILL finishes
with `finished_at` set. So a WHOLLY-FAILED sync — 0 of N threads captured — reaches
`renderJustFinished` with `landed === 0, total > 0`, and the pill painted
`⠕ ✓ Synced 0/N`: a green SUCCESS checkmark claiming success when NOTHING synced,
announced verbatim to a screen reader via the pill's role=status (implicit
aria-live=polite). The only terminal feedback the user / AT got on a total sync
failure was the word "Synced" and a ✓ — a deferred-result action that lied about
its outcome.

This is the NO-FEEDBACK / misleading-success sibling of the content-script sweep
(the in-flight Cancel-button contrast fix, 2026-06-22). The finished state is
injected into claude.ai / chatgpt.com / gemini.google.com by the same content
script, so no launchpad-render audit ever touched it.

ROOT CAUSE / CLASS: a deferred-completion UI that paints a single fixed "success"
string for every terminal outcome — including total failure. FIX
(renderJustFinished): branch on `landed`. 0 landed → "⚠ Sync failed — 0/N captured"
(honest, no ✓); partial → "Synced n/N" (count, no unqualified ✓ success framing);
full → "✓ Synced N" (the genuine success, unchanged).

This drives the REAL source unmodified by mapping `claude.ai` → 127.0.0.1 via
`--host-resolver-rules` so `location.hostname === "claude.ai"` and the IIFE's
host-guard passes. chrome.runtime.sendMessage is stubbed to report a FINISHED
current-tab sync with landed=0/total=5 (finished <10s ago, total>0) so
renderJustFinished fires the failed branch (no live capture host / no real sync /
no real Chrome dir touched). The pill stays role=status so the failure is
ANNOUNCED. Slow + browser marked; skips cleanly without Playwright/chromium.

Mutation-proven (2026-06-23): revert renderJustFinished to the unconditional
`⠕ ✓ Synced ${landed}/${total}` and this guard reds — the failed sync paints a ✓
and the word "Synced" with no "failed", the named founder symptom. The existing
sync-pill contrast/keyboard tests stay green (they never enter the finished-failed
state).
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


def _chrome_stub(landed: int, total: int) -> str:
    """Report a FINISHED (active:false) current-tab sync with the given landed/total,
    finished_at = now (within the 10s success-banner window), total>0 — the exact
    shape that drives renderJustFinished."""
    return f"""
window.chrome = {{
  runtime: {{
    id: 'testid', lastError: null,
    sendMessage: function(msg, cb) {{
      if (msg.type === 'get_current_tab_sync_state') {{
        cb && cb({{ok:true, active:false, provider:'claude',
                   landed:{landed}, total:{total}, finished_at: Date.now()}});
        return;
      }}
      if (msg.query_kind === 'sync_status') {{
        cb && cb({{ok:true, missing_count:0, missing_ids:[]}}); return;
      }}
      cb && cb({{}});
    }}
  }}
}};
"""


def _serve(root: Path):
    (root / "index.html").write_text(
        "<!doctype html><html><head><meta charset=utf-8><title>provider</title></head>"
        "<body><h1>fake provider page</h1></body></html>"
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[assignment]
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _render_finished(tmp_path, landed: int, total: int) -> dict:
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
            ctx.add_init_script(_chrome_stub(landed, total))
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://claude.ai:{port}/index.html", wait_until="load")
            assert page.evaluate("location.hostname") == "claude.ai"

            page.evaluate(pill_src)
            page.wait_for_selector("#__trinity_sync_pill__:not([hidden])", timeout=8000)
            info = page.evaluate(
                """() => {
                  const el = document.getElementById('__trinity_sync_pill__');
                  return {text: el.textContent, role: el.getAttribute('role')};
                }"""
            )
            info["errs"] = errs
            browser.close()
            return info
    finally:
        httpd.shutdown()


def test_wholly_failed_sync_does_not_claim_success(tmp_path):
    """A finished current-tab sync that captured ZERO of N threads (landed=0,
    total>0) must NOT paint a ✓ or the bare word "Synced" — that lied about a total
    failure. It must say it failed, and stay role=status so the failure is ANNOUNCED
    to a screen reader.

    FOUNDER SYMPTOM: the old renderJustFinished painted `⠕ ✓ Synced 0/5` for a
    wholly-failed sync — a success checkmark + "Synced", read verbatim by AT, when
    nothing synced."""
    info = _render_finished(tmp_path, landed=0, total=5)
    text = info["text"]

    # --- BITE PRECONDITION (non-vacuous): we are in the finished state for a
    # zero-landed sync (total appears, so renderJustFinished's total>0 gate fired).
    assert "5" in text, (
        f"the finished pill did not paint the total count: {text!r} — the failed-"
        "feedback assertion would be vacuous."
    )

    # The success checkmark must be GONE on a total failure.
    assert "✓" not in text, (
        f"a wholly-failed sync (0/5 captured) still paints a SUCCESS checkmark: "
        f"{text!r}. FOUNDER SYMPTOM: renderJustFinished printed `⠕ ✓ Synced 0/5` — a "
        f"green ✓ claiming success when NOTHING synced, announced verbatim to a "
        f"screen reader via role=status. Branch on landed===0 and say it failed."
    )
    # The bare unqualified "Synced" success word must be GONE too.
    assert "Synced" not in text, (
        f"a wholly-failed sync still says 'Synced': {text!r} — that reads as success. "
        f"Say it failed (e.g. 'Sync failed — 0/N captured')."
    )
    # And it must positively SAY it failed (honest, lead-with-the-answer copy).
    assert "fail" in text.lower(), (
        f"a wholly-failed sync gives no failure signal at all: {text!r}. The user/AT "
        f"got no terminal feedback that the sync failed (NO-FEEDBACK)."
    )

    # Still ANNOUNCED: role=status (implicit aria-live=polite) so an AT user hears the
    # failure, not silence.
    assert info["role"] == "status", (
        f"the finished-failed pill is role={info['role']!r}, not 'status' — the sync "
        f"failure is NOT announced to a screen reader (silent failure)."
    )
    assert info["errs"] == [], f"page errors: {info['errs']}"


def test_full_success_still_shows_check(tmp_path):
    """The genuine all-landed success path is unchanged: landed===total paints the
    ✓ success (proves the failed-branch fix didn't break the real success copy)."""
    info = _render_finished(tmp_path, landed=5, total=5)
    assert "✓" in info["text"] and "Synced 5" in info["text"], (
        f"the full-success finished state lost its ✓ Synced copy: {info['text']!r}"
    )
    assert info["errs"] == []


def test_partial_sync_drops_unqualified_success(tmp_path):
    """A partial sync (some landed, some didn't) shows the count without an
    unqualified ✓ success — landed 2 of 5 is not a clean success."""
    info = _render_finished(tmp_path, landed=2, total=5)
    assert "2/5" in info["text"], f"partial count missing: {info['text']!r}"
    assert "✓" not in info["text"], (
        f"a partial sync (2/5) still paints a clean-success ✓: {info['text']!r} — the "
        f"3 that didn't land are unacknowledged."
    )
    assert info["errs"] == []
