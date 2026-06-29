"""Browser guard: a user-CANCELED in-provider sync must read "canceled", NOT
"Sync failed" / a bare "Synced N/M" — the in-provider SYNC PILL terminal state
(browser-extension/sync-pill.js `renderJustFinished` + background.js
`get_current_tab_sync_state`).

FOUNDER SYMPTOM (UX sweep 2026-06-23): the current-tab sync orchestrator
(background.js `runCurrentTabSync`) leaves the SAME terminal state shape for a
user CANCELLATION as for a failure/partial finish: the cancel handler sets
`canceled=true`, the loop breaks, and the run finishes with `active:false`,
`finished_at` set, and `landed < total` (often `landed === 0` if the user cancels
before the first capture lands). `get_current_tab_sync_state` did NOT expose the
`canceled` flag, so the pill's `renderJustFinished` could not tell a deliberate
cancel apart from a sync FAILURE and painted:
  * `⠕ ⚠ Sync failed — 0/N captured`  (cancel @ landed=0 — a deliberate cancel
    mislabeled as a FAILURE, announced verbatim to a screen reader via role=status)
  * `⠕ Synced 2/5`                    (cancel @ landed=2 — a cancel framed as a
    partial sync, with no acknowledgment the user stopped it)

This is the CANCEL sibling of the wholly-failed-sync misleading-feedback fix
(test_sync_pill_failed_sync_feedback_browser.py, same day): that fix made a natural
failure honest but, by leading with "Sync failed" on landed===0, made a canceled
sync LIE in the opposite direction (claim a failure the user caused on purpose).

ROOT CAUSE / CLASS: a deferred-completion terminal UI that infers OUTCOME from a
count alone, when the state machine already distinguishes the outcome (canceled vs
failed) but drops the distinguishing flag at the boundary. FIX: (1) background.js
`get_current_tab_sync_state` exposes `canceled`; (2) `renderJustFinished` branches
on `state.canceled` FIRST → "Sync canceled" / "Sync canceled — n/N captured" (lead
with the answer: the user canceled), before the failed / full / partial branches.

This drives the REAL source unmodified by mapping `claude.ai` → 127.0.0.1 via
`--host-resolver-rules` so the IIFE's host-guard passes, then stubs
chrome.runtime.sendMessage to report a FINISHED, CANCELED current-tab sync (the
exact shape background.js now returns). No live capture host / real sync / real
Chrome dir touched. Slow + browser marked; skips cleanly without Playwright/chromium.

Mutation-proven (2026-06-23): revert `renderJustFinished`'s `state.canceled` branch
(let a canceled sync fall through to the failed/partial branches) and these guards
red — a canceled-at-0 sync paints "Sync failed" (the named founder symptom) and a
canceled-at-2 sync paints a bare "Synced" with no "cancel". The existing
sync-pill failed/contrast/keyboard tests stay green (they never set canceled=true).
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


def _chrome_stub(landed: int, total: int, canceled: bool) -> str:
    """Report a FINISHED (active:false), CANCELED current-tab sync — the exact shape
    background.js `get_current_tab_sync_state` returns after a user clicks Cancel
    (active:false, finished_at within the 10s window, total>0, canceled flag set)."""
    cflag = "true" if canceled else "false"
    return f"""
window.chrome = {{
  runtime: {{
    id: 'testid', lastError: null,
    sendMessage: function(msg, cb) {{
      if (msg.type === 'get_current_tab_sync_state') {{
        cb && cb({{ok:true, active:false, provider:'claude',
                   landed:{landed}, total:{total},
                   finished_at: Date.now(), canceled:{cflag}}});
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


def _render_finished(tmp_path, landed: int, total: int, canceled: bool) -> dict:
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
            ctx.add_init_script(_chrome_stub(landed, total, canceled))
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


def test_canceled_at_zero_is_not_labeled_a_failure(tmp_path):
    """A user who cancels BEFORE any thread lands (canceled, landed=0, total>0) must
    NOT be told the sync FAILED — they stopped it on purpose.

    FOUNDER SYMPTOM: the pill painted `⠕ ⚠ Sync failed — 0/5 captured` for a
    deliberate cancellation (announced verbatim to a screen reader via role=status),
    because get_current_tab_sync_state dropped the `canceled` flag and
    renderJustFinished fell through to the landed===0 failed branch."""
    info = _render_finished(tmp_path, landed=0, total=5, canceled=True)
    text = info["text"]

    # BITE PRECONDITION (non-vacuous): we are in the finished terminal state.
    assert "cancel" in text.lower(), (
        f"a CANCELED sync gives no cancel signal at all: {text!r}. The user stopped "
        "the sync but the pill never says so."
    )
    # The deliberate cancel must NOT be framed as a FAILURE.
    assert "fail" not in text.lower(), (
        f"a user-CANCELED sync is mislabeled as a FAILURE: {text!r}. FOUNDER SYMPTOM: "
        "get_current_tab_sync_state dropped the `canceled` flag, so renderJustFinished "
        "fell through to '⚠ Sync failed — 0/5 captured' for a deliberate cancel. "
        "Expose `canceled` and branch on it FIRST."
    )
    assert "✓" not in text, f"a canceled sync paints a success ✓: {text!r}"
    # Still ANNOUNCED to a screen reader.
    assert info["role"] == "status", (
        f"the canceled-terminal pill is role={info['role']!r}, not 'status' — the "
        "cancellation outcome is not announced to a screen reader."
    )
    assert info["errs"] == [], f"page errors: {info['errs']}"


def test_canceled_midrun_is_not_framed_as_a_partial_sync(tmp_path):
    """A user who cancels mid-run (canceled, landed=2, total=5) must read that they
    CANCELED — not a bare 'Synced 2/5' that frames the stop as a partial sync."""
    info = _render_finished(tmp_path, landed=2, total=5, canceled=True)
    text = info["text"]
    assert "cancel" in text.lower(), (
        f"a mid-run cancel gives no cancel signal: {text!r} — a bare 'Synced 2/5' "
        "frames a deliberate stop as a partial sync."
    )
    # The count is preserved (informative) but the framing is honest.
    assert "2/5" in text, f"the canceled count was dropped: {text!r}"
    assert "✓" not in text, f"a canceled sync paints a success ✓: {text!r}"
    assert info["role"] == "status"
    assert info["errs"] == []


def test_natural_failure_still_says_failed_when_not_canceled(tmp_path):
    """COMPLEMENT (proves the cancel branch only fires on a real cancel): a NATURAL
    wholly-failed sync (canceled=false, landed=0) still reads 'Sync failed', so the
    cancel branch didn't swallow genuine failures."""
    info = _render_finished(tmp_path, landed=0, total=5, canceled=False)
    text = info["text"]
    assert "fail" in text.lower(), (
        f"a genuine (non-canceled) failed sync lost its failure copy: {text!r} — the "
        "cancel branch over-reached."
    )
    assert "cancel" not in text.lower(), (
        f"a non-canceled failure was mislabeled as a cancel: {text!r}"
    )
    assert info["errs"] == []
