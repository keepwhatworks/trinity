"""WCAG 2.4.3 Focus Order — launching a council from the popup must move
keyboard focus INTO the status panel it reveals.

The popup's "Send to council" button (and the ⌘/Ctrl+Enter submit) live inside
`#compose`. `showStatusPanel()` sets `#compose` to display:none and reveals
`#status-panel`. The control the user just activated is now inside a hidden
ancestor, so the browser drops focus to <body> — a keyboard user is stranded:
Tab restarts from the document top and never reaches the panel's Stop / Open /
Close controls (the panel-stop "Stop council" is the only way to cancel a
running council from here). The launchpad's dispatch flow already moves focus
into its new operation-status region (_focusOperationActions, iter 279); the
popup was the un-fixed sibling.

This guard drives the REAL popup (file://, fully stubbed chrome.runtime) on BOTH
activation paths (keyboard ⌘/Enter and a mouse click) and asserts
document.activeElement is a VISIBLE control inside #status-panel after launch —
not <body>, not the now-hidden #run-btn.

Slow + browser. Mutation-proven: deleting the requestAnimationFrame focus move
in showStatusPanel() reds this with activeElement back on <body>.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

POPUP = Path(__file__).resolve().parents[1] / "browser-extension" / "popup.html"

# A minimal stub: launch detaches, the first status poll reports "running" so
# the panel stays in the busy state (Open / Stop visible) we want to assert on.
_CHROME_STUB = """
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        setTimeout(() => cb({ok:true, status:{
          members:{claude:{status:'running'},codex:{status:'queued'},antigravity:{status:'queued'}},
          synthesis:{status:'pending'}, status:'running'}}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
"""

_FOCUS_PROBE = """() => {
  const ae = document.activeElement;
  const panel = document.getElementById('status-panel');
  const inPanel = !!(ae && ae.closest && ae.closest('#status-panel'));
  return {
    activeTag: ae ? ae.tagName : null,
    activeId: ae ? ae.id : null,
    activeIsBody: ae === document.body,
    activeVisible: ae ? getComputedStyle(ae).display !== 'none' : null,
    panelVisible: panel ? getComputedStyle(panel).display !== 'none' : null,
    composeHidden: getComputedStyle(document.getElementById('compose')).display === 'none',
    focusInsidePanel: inPanel,
  };
}"""


def _launch_and_probe(page, *, via: str) -> dict:
    page.reload()
    page.fill("#task", "SQLite or DuckDB for an analytics workload?")
    if via == "keyboard":
        # The common-path keyboard submit: focus the launch button, press Enter.
        page.focus("#run-btn")
        page.keyboard.press("Enter")
    else:
        page.click("#run-btn")
    page.wait_for_selector("#status-panel", state="visible", timeout=5000)
    # Defer past the requestAnimationFrame the focus move rides.
    page.wait_for_timeout(150)
    return page.evaluate(_FOCUS_PROBE)


def test_popup_launch_moves_focus_into_status_panel():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 460, "height": 760}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_CHROME_STUB)
            page.goto(f"file://{POPUP}")

            for via in ("keyboard", "click"):
                s = _launch_and_probe(page, via=via)

                # PRECONDITION A — the surface actually transitioned (panel
                # shown, compose hidden). A vacuous pass (panel never appeared)
                # can't credit the focus assertion.
                assert s["panelVisible"], (
                    f"[{via}] status panel did not become visible — "
                    f"surface never transitioned, focus assertion vacuous: {s}"
                )
                assert s["composeHidden"], (
                    f"[{via}] compose form should be hidden once a council "
                    f"launches: {s}"
                )

                # PRECONDITION B — the bug's signature is focus on <body>.
                # The fix must NOT leave focus on the document body.
                assert not s["activeIsBody"], (
                    f"[{via}] WCAG 2.4.3 — launching a council dumped keyboard "
                    f"focus to <body> (the #run-btn it was on is now inside a "
                    f"display:none #compose). The status panel's Stop/Open/Close "
                    f"controls are unreachable without re-tabbing from the top. "
                    f"showStatusPanel() must move focus INTO the panel like the "
                    f"launchpad's _focusOperationActions(). probe={s}"
                )

                # THE INVARIANT — focus is on a VISIBLE control inside the panel.
                assert s["focusInsidePanel"], (
                    f"[{via}] focus must land INSIDE #status-panel after launch, "
                    f"got activeId={s['activeId']!r} (tag={s['activeTag']}). {s}"
                )
                assert s["activeVisible"], (
                    f"[{via}] focus landed on a display:none element "
                    f"(activeId={s['activeId']!r}) — must be a visible control. {s}"
                )

            assert not errs, f"JS errors during popup launch focus drive: {errs[:3]}"
        finally:
            browser.close()
