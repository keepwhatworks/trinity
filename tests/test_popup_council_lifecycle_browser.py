"""Browser guard for the Chrome-extension popup's running-council state machine.

`browser-extension/popup.js` drives the toolbar council launcher entirely
client-side over `chrome.runtime.sendMessage`: launch-council → poll
get-council-status (members queued→running→done/failed) → synthesis →
terminal state. Despite being a real user-facing prod surface, it had ZERO
behavioural test coverage — only doc/snippet tests reference "popup". Found
2026-06-06 by dogfooding the popup in real Chrome with a stubbed
chrome.runtime; everything rendered correctly, so this pins it before a
refactor of popup.js can silently break the launcher:

  • the status panel replaces the compose form,
  • all four rows (3 members + chairman synthesis) pre-render, then transition,
  • a FAILED member (the rate-limit / quota case) renders honestly
    (dot.failed + a "Failed" pill) rather than vanishing or showing "Done",
  • the terminal state swaps Stop → Dismiss,
  • and no JS error blanks the panel.

Slow-marked (spawns chromium); skips when Playwright/chromium are absent.
Loads popup.html via file:// (the chrome.runtime API is fully stubbed, so no
extension context or Native-Messaging host is needed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
POPUP = REPO / "browser-extension" / "popup.html"

# Stateful chrome.runtime stub: each get-council-status poll advances the
# council one stage. Codex finishes FAILED (the quota case) while the other two
# succeed — so the council completes 2-of-3, the honest degraded outcome.
_CHROME_STUB = """
window.__pollCount = 0;
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        const n = ++window.__pollCount;
        let members, synth, top;
        if (n <= 1) { members={claude:{status:'running'},codex:{status:'queued'},antigravity:{status:'queued'}}; synth={status:'pending'}; top='running'; }
        else if (n === 2) { members={claude:{status:'done'},codex:{status:'running'},antigravity:{status:'done'}}; synth={status:'pending'}; top='running'; }
        else if (n === 3) { members={claude:{status:'done'},codex:{status:'done'},antigravity:{status:'done'}}; synth={status:'running'}; top='running'; }
        else { members={claude:{status:'done'},codex:{status:'failed'},antigravity:{status:'done'}}; synth={status:'done'}; top='completed'; }
        setTimeout(() => cb({ok:true, status:{members, synthesis:synth, status:top}}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
"""

_STATE_PROBE = """() => {
  const row = (p) => {
    const r = document.querySelector(`#member-rows .member-row[data-provider='${p}']`);
    return r ? { pill: r.querySelector('.pill').textContent.trim(), dot: r.querySelector('.dot').className, name: r.querySelector('.name').textContent.trim() } : null;
  };
  const vis = (id) => { const b = document.getElementById(id); return b ? getComputedStyle(b).display !== 'none' : null; };
  const tipEl = document.querySelector('.panel-tip');
  return {
    composeHidden: getComputedStyle(document.getElementById('compose')).display === 'none',
    rowCount: document.querySelectorAll('#member-rows .member-row').length,
    claude: row('claude'), codex: row('codex'), antigravity: row('antigravity'), synth: row('__synthesis__'),
    title: document.getElementById('panel-title').textContent,
    tip: tipEl ? tipEl.textContent : null,
    stopVisible: vis('panel-stop-btn'), dismissVisible: vis('panel-dismiss-btn'),
  };
}"""


def test_popup_council_lifecycle_renders():
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
            # Inject the stub BEFORE popup.js runs so its dispatch() sees chrome.
            page.add_init_script(_CHROME_STUB)
            page.goto(f"file://{POPUP}")

            page.fill("#task", "SQLite or DuckDB for an analytics workload?")
            page.click("#run-btn")
            page.wait_for_selector("#status-panel", state="visible", timeout=5000)

            # Drive the 1500ms poll loop to completion (4 stages, ~4.5s).
            page.wait_for_function(
                "document.getElementById('panel-title').textContent === 'Council ready'",
                timeout=15000,
            )
            s = page.evaluate(_STATE_PROBE)

            assert s["composeHidden"], "compose form should be hidden once a council launches"
            assert s["rowCount"] == 4, f"expected 3 members + synthesis row, got {s['rowCount']}"
            # The quota'd codex member renders honestly — NOT vanished, NOT "Done".
            assert s["codex"]["pill"] == "Failed", f"failed member mis-rendered: {s['codex']}"
            assert "failed" in s["codex"]["dot"], f"failed dot class missing: {s['codex']}"
            # The two that succeeded show Done.
            assert s["claude"]["pill"] == "Done", s["claude"]
            assert s["antigravity"]["pill"] == "Done", s["antigravity"]
            # #275 (2026-06-06): member rows read as the MODEL BRAND (Claude / GPT
            # / Gemini), matching the launchpad council panel + live council page —
            # NOT the harness slug (Codex / Antigravity).
            assert s["claude"]["name"] == "Claude", s["claude"]
            assert s["codex"]["name"] == "GPT", f"codex must label as GPT (#275): {s['codex']}"
            assert s["antigravity"]["name"] == "Gemini", f"antigravity must label as Gemini (#275): {s['antigravity']}"
            # Terminal state: Stop hidden, Dismiss shown.
            assert s["title"] == "Council ready"
            # HONESTY guard: the user-pick / click-to-rate UI was retired
            # 2026-05-22 — the chairman picks the winner and the council page
            # presents it already marked. The completed-state tip must NOT tell
            # the user to "pick a winner" (an action that no longer exists; when
            # they open the page there is nothing to pick), and must instead
            # point at the chairman's verdict. Founder symptom: copy names a
            # retired action the user can't find (the Iter-119 class).
            assert "pick a winner" not in (s["tip"] or "").lower(), (
                "popup 'Council ready' tip tells the user to 'pick a winner' — "
                "the click-to-pick UI was retired 2026-05-22; the chairman picks "
                f"and the page presents the verdict already marked. tip={s['tip']!r}"
            )
            assert "chairman" in (s["tip"] or "").lower() or "verdict" in (s["tip"] or "").lower(), (
                "popup 'Council ready' tip must point at the chairman's verdict "
                f"(what the page actually shows), got tip={s['tip']!r}"
            )
            assert s["stopVisible"] is False, "Stop button should hide in the terminal state"
            assert s["dismissVisible"] is True, "Dismiss button should appear in the terminal state"
            assert not errs, f"JS errors during the popup council lifecycle: {errs[:3]}"
        finally:
            browser.close()


# A chrome.runtime stub that drives the council straight to COMPLETED and makes
# the auto-open-on-completion `open-council-page` dispatch FAIL ({ok:false}) —
# exactly the host state on first install (no live_council.html yet → needs_regen)
# or when webbrowser.open() can't launch a browser. The lifecycle stub above
# always answers open-council-page with the {ok:true} catch-all, so the
# completed-branch FAILURE path is otherwise never exercised.
_COMPLETED_OPEN_FAILS_STUB = """
window.__opens = 0;
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 4); return; }
      if (kind === 'get-council-status') {
        const members={claude:{status:'done'},codex:{status:'done'},antigravity:{status:'done'}};
        setTimeout(() => cb({ok:true, status:{members, synthesis:{status:'done'}, status:'completed'}}), 4);
        return;
      }
      if (kind === 'open-council-page') { window.__opens++; setTimeout(() => cb({ok:false, error:'council not ready yet'}), 6); return; }
      setTimeout(() => cb({ok:true}), 4);
    }
  }
};
"""


def test_popup_completed_auto_open_failure_gives_feedback():
    """USEFULNESS/NO-FEEDBACK guard for the AUTO-open-on-completion path — the
    asymmetric sibling of the manual #panel-open-btn handler (which already
    surfaces a failed open). When a council COMPLETES, popup.js auto-fires
    open-council-page and, before the fix, DISCARDED the result. So a failed
    auto-open (first-install needs_regen, an invalid token, or webbrowser.open()
    returning False) left the panel standing on the optimistic tip "Opening the
    council page — the chairman's verdict is ready." — a LIE: the page never
    opened, no error surfaced, and the user got no nudge toward the still-visible
    "Open council page" retry button. A silent dead-end on the happy path's twin.

    Drives the REAL popup.js to the completed terminal state with open-council-
    page stubbed to {ok:false}, then asserts the tip CORRECTS itself (drops the
    "Opening…" claim, names the failure, and points at the manual retry) and the
    manual button stays visible.

    Mutation proof: revert the completed-branch result check (drop the
    `if (tip && !(openRes && openRes.ok)) { ... }` correction) → the tip stays
    "Opening the council page…" on a failed open → this reds.
    """
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
            page.add_init_script(_COMPLETED_OPEN_FAILS_STUB)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "SQLite or DuckDB for an analytics workload?")
            page.click("#run-btn")
            # Reach the completed terminal state.
            page.wait_for_function(
                "document.getElementById('panel-title').textContent === 'Council ready'",
                timeout=15000,
            )
            # The auto-open dispatch round-trip resolves just AFTER terminal state;
            # wait for the tip to settle past the optimistic "Opening…" copy.
            page.wait_for_function(
                "() => !/Opening the council page/.test(document.querySelector('.panel-tip').textContent)",
                timeout=5000,
            )

            tip = page.evaluate("() => document.querySelector('.panel-tip').textContent")
            opens = page.evaluate("() => window.__opens")
            open_btn_visible = page.evaluate(
                "() => getComputedStyle(document.getElementById('panel-open-btn')).display !== 'none'"
            )

            # The founder symptom this defends: council finishes, the tip says it's
            # opening the page, the page never opens, and nothing tells you why.
            assert opens >= 1, "the completed branch must have attempted the auto-open (guard hollow otherwise)"
            assert "Opening the council page" not in tip, (
                "the popup left the optimistic 'Opening the council page…' tip standing "
                f"after the auto-open FAILED ({{ok:false}}) — a silent lie. tip={tip!r}"
            )
            assert "Couldn't open the council page" in tip, (
                "a FAILED auto-open-on-completion must say so — the user was told it was "
                f"opening and it wasn't, with no error and no retry nudge. tip={tip!r}"
            )
            assert "council not ready yet" in tip, f"the host error must reach the user: {tip!r}"
            assert "Open council page" in tip, (
                "the corrected tip must point at the still-visible manual retry button "
                f"so the verdict is reachable, got tip={tip!r}"
            )
            assert open_btn_visible, "the manual 'Open council page' retry must stay visible after a failed auto-open"
            assert not errs, f"JS errors during the completed auto-open failure: {errs[:3]}"
        finally:
            browser.close()


# WCAG 2.5.5 / Apple HIG — the popup is tappable on a touch toolbar; its action
# buttons must clear the same 44px floor the launchpad's .button already enforces
# (design_system.py). The hand-maintained .btn shipped at 37-39px.
_MIN_TAP = 44

_POPUP_BTN_PROBE = """() => {
  const out = [];
  document.querySelectorAll('#compose .btn').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    out.push({ id: el.id || null, text: (el.textContent || '').trim().slice(0, 40),
               h: Math.round(r.height * 10) / 10 });
  });
  return out;
}"""


def test_popup_action_buttons_meet_44px_tap_target():
    """The popup's compose-state action buttons ("Send to council" / "Open Trinity
    launchpad →") shipped at 37-39px — under the 44px touch floor the launchpad's
    .button already enforces. Bites the founder symptom: a sub-44 mis-tap on the
    primary toolbar launcher."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 440, "height": 760}).new_page()
            page.add_init_script(_CHROME_STUB)
            page.goto(f"file://{POPUP}")
            page.wait_for_selector("#compose", state="visible", timeout=5000)
            btns = page.evaluate(_POPUP_BTN_PROBE)
            assert any(b["id"] == "run-btn" for b in btns), (
                f"run-btn ('Send to council') not measured — guard is hollow: {btns}"
            )
            assert any(b["id"] == "open-launchpad-btn" for b in btns), (
                f"open-launchpad-btn not measured — guard is hollow: {btns}"
            )
            for b in btns:
                assert b["h"] >= _MIN_TAP, (
                    f"popup action button {b['id']!r} ({b['text']!r}) is {b['h']}px tall — "
                    f"under the {_MIN_TAP}px touch floor (shipped at 37-39px; a sub-44 "
                    f"mis-tap on the primary toolbar council launcher)"
                )
        finally:
            browser.close()


# A chrome.runtime stub that lets the test SCRIPT the response for any kind
# (notably open-council-page / stop-council FAILING) and stays RUNNING forever
# so the panel-action buttons can be driven mid-council. window.close is a
# no-op in a normal page, so we record it instead.
_SCRIPTABLE_STUB = """
window.__closed = false;
window.__responses = {};
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (window.__responses[kind] !== undefined) { setTimeout(() => cb(window.__responses[kind]), 4); return; }
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 4); return; }
      if (kind === 'get-council-status') {
        const members={claude:{status:'running'},codex:{status:'queued'},antigravity:{status:'queued'}};
        setTimeout(() => cb({ok:true, status:{members, synthesis:{status:'pending'}, status:'running'}}), 4);
        return;
      }
      setTimeout(() => cb({ok:true}), 4);
    }
  }
};
window.close = () => { window.__closed = true; };
"""


def _drive_into_running_panel(page):
    page.add_init_script(_SCRIPTABLE_STUB)
    page.goto(f"file://{POPUP}")
    page.fill("#task", "SQLite or DuckDB for analytics?")
    page.click("#run-btn")
    page.wait_for_selector("#status-panel", state="visible", timeout=5000)
    page.wait_for_timeout(350)  # let one running poll land


def test_popup_panel_open_button_failure_gives_feedback():
    """USEFULNESS/NO-FEEDBACK guard: clicking "Open council page" while the
    council page isn't ready yet (host returns ok:false) must NOT be a silent
    no-op. Before the fix, `if (r.ok) window.close()` had no else branch — the
    popup neither closed nor said anything, so the click read as dead. The panel
    must surface the failure AND stay open (the council is still running).

    Mutation proof: delete the `else`/feedback branch in panel-open-btn's
    handler → tip stays "Reticulating…" with no error text → this reds.
    """
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
            _drive_into_running_panel(page)

            page.evaluate(
                "window.__responses['open-council-page'] = {ok:false, error:'council not ready yet'};"
            )
            page.click("#panel-open-btn")
            page.wait_for_timeout(300)

            tip = page.evaluate("() => document.querySelector('.panel-tip').textContent")
            closed = page.evaluate("() => window.__closed")
            panel_visible = page.evaluate(
                "() => getComputedStyle(document.getElementById('status-panel')).display !== 'none'"
            )
            # The founder symptom this defends: "clicked Open council page, nothing
            # happened" — a failed open must speak.
            assert "Couldn't open the council page" in tip, (
                f"failed Open council page must surface feedback in the panel, got tip={tip!r}"
            )
            assert "council not ready yet" in tip, f"the host error must reach the user: {tip!r}"
            assert closed is False, "a FAILED open must not close the popup (council still running)"
            assert panel_visible, "the running panel must stay visible after a failed open"
            assert not errs, f"JS errors: {errs[:3]}"
        finally:
            browser.close()


# A chrome.runtime stub that RECORDS every sent kind and DELAYS the
# open-council-page reply, so a genuine double-click can land both clicks inside
# the host round-trip window — the race a non-disabled async button exposes.
_RECORDING_DELAYED_STUB = """
window.__sent = [];
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      window.__sent.push(kind);
      // Slow open so a 2nd click of a double-click lands before this resolves.
      if (kind === 'open-council-page') { setTimeout(() => cb({ok:true}), 150); return; }
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 4); return; }
      if (kind === 'get-council-status') {
        const members={claude:{status:'running'},codex:{status:'queued'},antigravity:{status:'queued'}};
        setTimeout(() => cb({ok:true, status:{members, synthesis:{status:'pending'}, status:'running'}}), 4);
        return;
      }
      setTimeout(() => cb({ok:true}), 4);
    }
  }
};
window.close = () => {};  // no-op so the panel survives the 2nd click for inspection
"""


def test_popup_panel_open_button_double_click_opens_one_tab():
    """USABILITY/double-fire guard for the panel "Open council page" button.

    open-council-page does a `webbrowser.open` on the HOST side, so each
    dispatch opens a real browser tab. The button stays visible+enabled through
    the ~120ms native-host round-trip, so a genuine double-click fired the
    dispatch TWICE → TWO tabs for ONE council. The launchpad's launchCouncil
    (`if (this.busy) return`) and the popup's own Stop button (disable-before-
    dispatch) already guard this; this twin button was missed.

    Founder symptom this defends: "double-clicked Open council page and it
    opened the council in two windows."

    Mutation proof (cp backup, restored byte-identical): drop the
    `if (btn.disabled) return; btn.disabled = true;` lines in panel-open-btn's
    handler → a real dblclick fires open-council-page twice → this reds.
    Also asserts a FAILED open RE-ENABLES the button (the failure twin of the
    Stop button's recovery) so it isn't stuck-disabled after one bad open.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 760})

            # ── A real OS double-click must open the council page exactly ONCE ──
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_RECORDING_DELAYED_STUB)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "SQLite or DuckDB for analytics?")
            page.click("#run-btn")
            page.wait_for_selector("#status-panel", state="visible", timeout=5000)
            page.wait_for_timeout(350)  # let a running poll land
            page.evaluate("() => { window.__sent = []; }")
            page.dblclick("#panel-open-btn")  # genuine OS double-click
            page.wait_for_timeout(400)
            opens = page.evaluate(
                "() => window.__sent.filter(k => k === 'open-council-page').length"
            )
            label = page.evaluate("() => document.getElementById('panel-open-btn').textContent")
            assert opens == 1, (
                "double-clicking 'Open council page' fired open-council-page "
                f"{opens}x — a non-disabled async button opens TWO browser tabs "
                "for ONE council (founder symptom: 'it opened in two windows'). "
                "The button must disable before dispatch like Stop / launchCouncil."
            )
            assert "Opening" in label or label == "Open council page", (
                f"open button must ack ('Opening…') or have restored, got {label!r}"
            )
            assert not errs, f"JS errors during the double-click open: {errs[:3]}"
            page.close()

            # ── A FAILED open must RE-ENABLE the button (not stuck-disabled) ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(
                _RECORDING_DELAYED_STUB.replace(
                    "if (kind === 'open-council-page') { setTimeout(() => cb({ok:true}), 150); return; }",
                    "if (kind === 'open-council-page') { setTimeout(() => cb({ok:false, error:'council not ready yet'}), 80); return; }",
                )
            )
            page.goto(f"file://{POPUP}")
            page.fill("#task", "SQLite or DuckDB for analytics?")
            page.click("#run-btn")
            page.wait_for_selector("#status-panel", state="visible", timeout=5000)
            page.wait_for_timeout(350)
            page.click("#panel-open-btn")
            page.wait_for_timeout(300)
            disabled = page.evaluate("() => document.getElementById('panel-open-btn').disabled")
            label2 = page.evaluate("() => document.getElementById('panel-open-btn').textContent")
            tip = page.evaluate("() => document.querySelector('.panel-tip').textContent")
            assert disabled is False, (
                "a FAILED open must RE-ENABLE the button so the user can retry — "
                "not leave it stuck-disabled after one un-ready open"
            )
            assert label2 == "Open council page", (
                f"label must restore so the affordance reads as retryable, got {label2!r}"
            )
            assert "Couldn't open the council page" in tip, (
                f"a failed open must still surface its reason, got tip={tip!r}"
            )
            page.close()

            assert not errs, f"JS errors during the failed-open recovery: {errs[:3]}"
        finally:
            browser.close()


def test_popup_panel_stop_button_failure_recovers():
    """USEFULNESS/NO-FEEDBACK guard: the panel "Stop council" button gives an
    immediate ack ("Stopping…", disabled) AND recovers on failure. Before the
    fix it set `disabled=true` then fired stop-council with no label change and
    no failure handling, so a FAILED stop left the button disabled FOREVER with
    no feedback while the council kept running (the launchpad's stopRequested
    pattern was never mirrored into the popup).

    Mutation proof: remove the failure re-enable/feedback branch → the button
    stays disabled with no error tip → this reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 760})

            # ── Immediate ACK on click (before any host response) ──
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            _drive_into_running_panel(page)
            # Stop succeeds — verify the immediate "Stopping…" ack, not a silent disable.
            page.evaluate("window.__responses['stop-council'] = {ok:true};")
            page.click("#panel-stop-btn")
            page.wait_for_timeout(120)
            ok_label = page.evaluate("() => document.getElementById('panel-stop-btn').textContent")
            ok_disabled = page.evaluate("() => document.getElementById('panel-stop-btn').disabled")
            ok_tip = page.evaluate("() => document.querySelector('.panel-tip').textContent")
            assert "Stopping" in ok_label, f"Stop must ack immediately as 'Stopping…', got {ok_label!r}"
            assert ok_disabled is True, "Stop must disable while stopping (no double-fire)"
            assert "Stopping the council" in ok_tip, f"tip must confirm the stop is underway: {ok_tip!r}"
            page.close()

            # ── FAILED stop must RE-ENABLE + give feedback (not stuck-disabled) ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            _drive_into_running_panel(page)
            page.evaluate("window.__responses['stop-council'] = {ok:false, error:'no such council'};")
            page.click("#panel-stop-btn")
            page.wait_for_timeout(300)
            fail_disabled = page.evaluate("() => document.getElementById('panel-stop-btn').disabled")
            fail_label = page.evaluate("() => document.getElementById('panel-stop-btn').textContent")
            fail_tip = page.evaluate("() => document.querySelector('.panel-tip').textContent")
            assert fail_disabled is False, (
                "a FAILED stop must RE-ENABLE the button — not leave it disabled forever"
            )
            assert fail_label == "Stop council", f"label must restore so the user can retry: {fail_label!r}"
            assert "Couldn't stop the council" in fail_tip, (
                f"a failed stop must say why, got tip={fail_tip!r}"
            )
            assert "no such council" in fail_tip, f"the host error must reach the user: {fail_tip!r}"
            page.close()

            assert not errs, f"JS errors: {errs[:3]}"
        finally:
            browser.close()


def test_popup_failed_stop_keeps_the_running_display():
    """OPERATION-TYPE-CONFUSION guard — the EXTENSION POPUP sibling of the
    launchpad's Iter-354 handleStopResult fix. On the launchpad a FAILED Stop
    used to route through handleDispatchResult, whose rollback (clearOperation)
    is LAUNCH semantics — it dropped the optimistic operation that never started.
    But a Stop failure means the council is REAL and STILL RUNNING, so clearing
    it VANISHED the spinner + member grid + Stop/Open buttons and silently read as
    "stopped" while the backend council kept running (founder: "clicked Stop, the
    whole council display disappeared, but it never actually stopped"). The fix
    routes Stop through a dedicated handler that KEEPS the running display + shows
    an honest "still running, retry" message.

    The popup is a SEPARATE hand-maintained surface (NOT bundled, inherits
    NOTHING from the launchpad — the 231/239/244/247/248/312 drift class). Its
    Stop handler (popup.js #panel-stop-btn) is already dedicated and correct: on
    a failed stop it re-enables the button + sets an honest tip and does NOT call
    stopPolling() / hide #status-panel / show #compose / window.close(). The
    SIBLING test_popup_panel_stop_button_failure_recovers above pins the BUTTON
    recovery + tip, but a regression that ADDED the launchpad's pre-354 vanish
    (stopPolling(); panel.display='none'; compose.display='block') to the failure
    branch would still pass it — the button is re-enabled BEFORE any hide and the
    tip's textContent resolves even inside a hidden panel. So this guard pins the
    DISPLAY-PERSISTENCE invariant the vanish bug attacks, which that test misses:

      • #status-panel stays VISIBLE (the spinner/grid don't disappear),
      • the member rows (trio + chairman) all survive,
      • #compose stays HIDDEN (the popup doesn't bounce back to the launch form),
      • the popup does NOT close,
      • the poller stays ALIVE — the panel still reads "Council running" a full
        poll interval (1500ms) later, proving the still-running council is still
        being reflected (a vanish would also stopPolling()).

    Mutation proof (manual, restored byte-identical): in popup.js's
    #panel-stop-btn failure branch, ADD the launchpad's pre-354 vanish
    (`stopPolling(); $("status-panel").style.display = "none";
    $("compose").style.display = "block";`) → the running display disappears on a
    failed stop → panelVisible/composeHidden/rowCount/title-after-poll red. The
    sibling button-recovery test stays GREEN through that mutation (it only checks
    the button + tip), which is exactly why this DISPLAY guard is needed.
    """
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
            _drive_into_running_panel(page)

            # Precondition (bite not vacuous): the running display is UP first —
            # panel visible, compose hidden, the pre-rendered trio + chairman row.
            before = page.evaluate(
                "() => ({"
                " panelVisible: getComputedStyle(document.getElementById('status-panel')).display !== 'none',"
                " composeHidden: getComputedStyle(document.getElementById('compose')).display === 'none',"
                " rowCount: document.querySelectorAll('#member-rows .member-row').length,"
                " })"
            )
            assert before["panelVisible"], "running panel must be up before the stop (precondition)"
            assert before["composeHidden"], "compose must be hidden while a council runs (precondition)"
            assert before["rowCount"] == 4, (
                f"expected the pre-rendered trio + chairman (4 rows) before the stop, "
                f"got {before['rowCount']} — re-derive this guard's precondition"
            )

            # Fire a FAILED stop — the council is STILL RUNNING on the backend.
            page.evaluate("window.__responses['stop-council'] = {ok:false, error:'no such council'};")
            page.click("#panel-stop-btn")
            page.wait_for_timeout(350)

            after = page.evaluate(
                "() => ({"
                " panelVisible: getComputedStyle(document.getElementById('status-panel')).display !== 'none',"
                " composeHidden: getComputedStyle(document.getElementById('compose')).display === 'none',"
                " rowCount: document.querySelectorAll('#member-rows .member-row').length,"
                " title: document.getElementById('panel-title').textContent,"
                " tip: document.querySelector('.panel-tip').textContent,"
                " closed: window.__closed,"
                " })"
            )

            # The 354-vanish symptom this defends: a failed Stop must NOT clear the
            # still-running council. The display PERSISTS, honestly.
            assert after["panelVisible"], (
                "FAILED Stop VANISHED the running display — the popup hid "
                "#status-panel on a failed stop (the launchpad's pre-354 "
                "clearOperation rollback, here on the toolbar popup). The council "
                "is STILL RUNNING on the backend; the spinner/grid must stay."
            )
            assert after["composeHidden"], (
                "FAILED Stop bounced the popup back to the #compose launch form — "
                "the running council display must NOT be replaced by the composer "
                "when the stop FAILS (the council is still running)."
            )
            assert after["rowCount"] == 4, (
                f"FAILED Stop dropped member rows (got {after['rowCount']}, want 4) "
                "— the running roster must survive a failed stop, not vanish."
            )
            assert after["closed"] is False, (
                "FAILED Stop closed the popup — a failed stop must keep the popup "
                "open so the still-running council stays visible + retryable."
            )
            # The display stays HONEST: still 'Council running' (it IS), with the
            # 'still running, retry' message — NOT a silent 'stopped'.
            assert after["title"] == "Council running", (
                f"FAILED Stop must NOT read as stopped/terminal — the council is "
                f"still running; title={after['title']!r}"
            )
            assert "Couldn't stop the council" in after["tip"], (
                f"a failed stop must say the council did NOT stop, got tip={after['tip']!r}"
            )

            # The poller must still be ALIVE — a vanish would also stopPolling(), so
            # one poll interval (1500ms) later the panel still reflects the running
            # council. (If stopPolling had fired, the panel would be frozen but the
            # title would never re-confirm 'Council running' off a fresh poll.)
            page.wait_for_timeout(1700)
            late = page.evaluate(
                "() => ({"
                " panelVisible: getComputedStyle(document.getElementById('status-panel')).display !== 'none',"
                " title: document.getElementById('panel-title').textContent,"
                " })"
            )
            assert late["panelVisible"] and late["title"] == "Council running", (
                "after a failed stop the poller must keep reflecting the "
                "still-running council (panel up, 'Council running'); a vanish "
                f"would have stopPolling()'d and torn the panel down. got {late}"
            )
            assert not errs, f"JS errors during the failed-stop display-persistence check: {errs[:3]}"
        finally:
            browser.close()


def test_popup_setup_card_shows_python_substitution_hint():
    """The popup's install-first SETUP CARD (shown when the Native Messaging host
    is missing) renders a per-harness config block with `command = "PYTHON"`.
    Pasting that verbatim spawns a non-existent binary and the MCP server
    silently never starts. This pins the user-visible substitution hint the
    picker must render for any PYTHON-carrying block. Found 2026-06-06 dogfooding
    the setup card in real Chrome — the snippet had zero substitution guidance.
    """
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
            # showSetupCard reads chrome.runtime.id — give it one.
            page.add_init_script(
                "window.chrome = { runtime: { id: 'kjmoiabcdefghijklmnopqrstuvwx12',"
                " lastError: null, sendMessage: (m, cb) => setTimeout(() => cb(undefined), 5) } };"
            )
            page.goto(f"file://{POPUP}")
            # Render the install-first setup card directly (the native-host path).
            page.evaluate("showSetupCard('Native Messaging host not found.')")
            page.wait_for_selector(".harness-pill", state="visible", timeout=5000)
            # Select the first harness — its block carries the PYTHON placeholder.
            page.click(".harness-pill")
            page.wait_for_selector(".harness-substitute-hint", state="visible", timeout=3000)
            hint = page.evaluate(
                "() => { const h = document.querySelector('.harness-substitute-hint');"
                " return { shown: getComputedStyle(h).display !== 'none', text: h.textContent }; }"
            )
            assert hint["shown"], "substitution hint not visible after selecting a harness"
            assert "which python3" in hint["text"], f"hint missing the how-to: {hint['text']!r}"
            assert not errs, f"JS errors rendering the setup card: {errs[:3]}"
        finally:
            browser.close()


# A chrome.runtime stub whose launch-council reply is `cb(undefined)` — exactly
# what background.js relays when the Native Messaging host is missing (no CLI
# wired). popup.js `dispatch()` turns a falsy response into
# {ok:false, error:'native-host-unavailable'}, so the run-btn handler must tear
# down the panel it opened synchronously and surface the install-first setup card.
_HOST_MISSING_STUB = """
window.__sent = [];
window.chrome = {
  runtime: {
    id: 'kjmoiabcdefghijklmnopqrstuvwx12', lastError: null,
    sendMessage: (msg, cb) => { window.__sent.push(msg && msg.kind); setTimeout(() => cb(undefined), 5); }
  }
};
"""


def test_popup_first_run_send_to_council_falls_back_to_setup_card():
    """The genuine FIRST-RUN path: an unconfigured user (extension sideloaded, CLI
    not yet wired) types a question and clicks "Send to council". The host is
    unreachable, so background.js relays no response and popup.js's `dispatch()`
    resolves `{ok:false, error:'native-host-unavailable'}`. The run-btn handler
    opens the running panel SYNCHRONOUSLY (showStatusPanel) before awaiting the
    dispatch — so on a missing host it must TEAR THAT PANEL DOWN and swap in the
    install-first setup card, NOT leave the user stranded on a "Council running"
    panel that will never advance (the founder symptom: a first click that spins
    forever with no path to fix it).

    This is the one run-btn transition the suite never drove through the real
    click — `test_popup_setup_card_shows_python_substitution_hint` invokes
    `showSetupCard()` directly, bypassing the launch-council → dispatch → fallback
    wiring this test exercises end to end.

    Bite preconditions:
      (A) the panel ACTUALLY opened first (synchronous showStatusPanel) — proven
          by snapshotting #status-panel display the instant after the click,
          before the dispatch resolves;
      (B) launch-council was actually dispatched (the real handler ran).
    Mutation proof: in popup.js's run-btn handler, delete the
    `showSetupCard("Native Messaging host not found...")` line (or its `if`
    branch) so a missing host falls through to the generic else — the setup-card
    assertions below red with the founder symptom.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 460, "height": 900}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_HOST_MISSING_STUB)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "Which database for a multi-tenant SaaS?")

            # (A) Precondition: clicking run-btn opens the running panel
            # SYNCHRONOUSLY (showStatusPanel runs before the awaited dispatch
            # resolves). Snapshot the panel state with no wait so we prove the
            # pre-fallback state is the OTHER state (panel up), not the setup card.
            opened = page.evaluate(
                "() => { document.getElementById('run-btn').click();"
                " return { panelVisible: getComputedStyle(document.getElementById('status-panel')).display !== 'none',"
                "          composeHidden: getComputedStyle(document.getElementById('compose')).display === 'none' }; }"
            )
            assert opened["panelVisible"], (
                "run-btn must open the running panel synchronously before awaiting "
                "the dispatch — precondition for proving the host-missing teardown"
            )
            assert opened["composeHidden"], "run-btn must hide #compose when the panel opens"

            # Let the launch-council dispatch resolve (cb(undefined) → host missing).
            page.wait_for_selector("#copy-setup-brief", state="visible", timeout=5000)

            after = page.evaluate(
                "() => ({"
                " sent: window.__sent,"
                " panelGone: document.getElementById('status-panel') === null"
                "   || getComputedStyle(document.getElementById('status-panel')).display === 'none',"
                " setupReason: (document.querySelector('.setup-reason') || {}).textContent || null,"
                " h1: (document.querySelector('h1') || {}).textContent || null,"
                " hasBriefBtn: !!document.getElementById('copy-setup-brief'),"
                " })"
            )
            # (B) The real handler ran — launch-council was actually dispatched.
            assert "launch-council" in after["sent"], (
                f"run-btn must dispatch launch-council, sent={after['sent']}"
            )
            # The defect this guards: a missing host must NOT strand the user on a
            # "Council running" panel — the install-first setup card must replace it.
            assert after["panelGone"], (
                "FIRST-RUN founder symptom: clicking Send to council with no host "
                "wired left the popup stuck on the 'Council running' panel that "
                "never advances — the running panel must be torn down"
            )
            assert after["hasBriefBtn"], (
                "a missing host must surface the install-first setup card (with the "
                "'Copy install brief' button) — got h1="
                f"{after['h1']!r}, setup-reason={after['setupReason']!r}"
            )
            assert after["setupReason"] and "host" in after["setupReason"].lower(), (
                f"the setup card must explain WHY (host not found): {after['setupReason']!r}"
            )
            assert not errs, f"JS errors driving the first-run host-missing path: {errs[:3]}"
        finally:
            browser.close()


# A chrome.runtime stub that scripts the `open-launchpad` reply (success or
# failure) and records window.close, so the COMPOSE-state launchpad button can
# be driven for real. (The lifecycle/panel stubs above never touch #compose's
# "Open Trinity launchpad →" button.)
_LAUNCHPAD_STUB = """
window.__closed = false;
window.__sent = [];
window.__responses = {};
window.chrome = {
  runtime: {
    id: 'kjmoiabcdefghijklmnopqrstuvwx12', lastError: null,
    sendMessage: (msg, cb) => {
      window.__sent.push(msg && msg.kind);
      const kind = msg && msg.kind;
      if (window.__responses[kind] !== undefined) { setTimeout(() => cb(window.__responses[kind]), 4); return; }
      setTimeout(() => cb({ok:true}), 4);
    }
  }
};
window.close = () => { window.__closed = true; };
"""


def test_popup_compose_controls_give_feedback():
    """Real-browser guard for the COMPOSE-state controls that had ZERO behavioural
    coverage (only string-presence source checks): the "Open Trinity launchpad →"
    button and the empty-task "Send to council" validation. These carry NO-OP /
    NO-FEEDBACK risk exactly like the panel Open/Stop buttons the suite already
    guards — a refactor of popup.js could silently break the feedback path and the
    string-in-source tests would stay green. Drives each control and asserts the
    observable behaviour:

      • Empty "Send to council" → an error status ("Type a question first.") and
        NO launch-council dispatched (the panel never opens). A regression that
        dropped the guard would fire a council on an empty prompt.
      • "Open Trinity launchpad →" SUCCESS → dispatches `open-launchpad` and
        closes the popup.
      • "Open Trinity launchpad →" FAILURE (host down) → surfaces the error in the
        status row AND does NOT close the popup. The founder symptom this defends:
        a launchpad button that silently does nothing when the host is unreachable.

    Mutation proof: drop the empty-task `return`, or the open-launchpad `else`
    error branch, or the success `window.close()` — each assertion below reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 800})
            errs: list[str] = []

            # ── Empty "Send to council" → error + NO launch ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_LAUNCHPAD_STUB)
            page.goto(f"file://{POPUP}")
            page.click("#run-btn")  # task is empty
            page.wait_for_timeout(150)
            empty = page.evaluate(
                "() => ({ text: document.getElementById('status').textContent,"
                " cls: document.getElementById('status').className,"
                " panelHidden: getComputedStyle(document.getElementById('status-panel')).display === 'none',"
                " sent: window.__sent })"
            )
            assert "Type a question first" in empty["text"], (
                f"empty Send to council must warn, got status={empty['text']!r}"
            )
            assert "error" in empty["cls"], f"empty-task warning must use the error style: {empty['cls']!r}"
            assert empty["panelHidden"], "empty Send to council must NOT open the running panel"
            assert "launch-council" not in empty["sent"], (
                f"empty Send to council must NOT dispatch a council, sent={empty['sent']}"
            )
            page.close()

            # ── "Open Trinity launchpad →" SUCCESS → dispatch + close ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_LAUNCHPAD_STUB)
            page.goto(f"file://{POPUP}")
            page.evaluate("window.__responses['open-launchpad'] = {ok:true};")
            page.click("#open-launchpad-btn")
            page.wait_for_timeout(120)
            ok_sent = page.evaluate("() => window.__sent")
            page.wait_for_timeout(220)  # window.close fires ~200ms after success
            ok_closed = page.evaluate("() => window.__closed")
            assert "open-launchpad" in ok_sent, f"launchpad button must dispatch open-launchpad, sent={ok_sent}"
            assert ok_closed is True, "a successful Open launchpad must close the popup"
            page.close()

            # ── "Open Trinity launchpad →" FAILURE → error feedback, stays open ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_LAUNCHPAD_STUB)
            page.goto(f"file://{POPUP}")
            page.evaluate("window.__responses['open-launchpad'] = {ok:false, error:'host unavailable'};")
            page.click("#open-launchpad-btn")
            page.wait_for_timeout(250)
            fail = page.evaluate(
                "() => ({ text: document.getElementById('status').textContent,"
                " cls: document.getElementById('status').className,"
                " closed: window.__closed })"
            )
            assert "Couldn't open the launchpad" in fail["text"], (
                f"a failed Open launchpad must speak — founder symptom 'clicked, nothing happened': {fail['text']!r}"
            )
            assert "host unavailable" in fail["text"], f"the host error must reach the user: {fail['text']!r}"
            assert "error" in fail["cls"], f"failure must use the error style: {fail['cls']!r}"
            assert fail["closed"] is False, "a FAILED Open launchpad must NOT close the popup"
            page.close()

            assert not errs, f"JS errors driving the compose controls: {errs[:3]}"
        finally:
            browser.close()


def test_popup_composer_matches_its_sibling_composers():
    """Primary-vs-sibling parity guard for the popup's #task composer (the
    277-class: a composer must not be LESS polished than the launchpad / refine
    composers it twins). Two real defects this defends, both verified by driving
    the actual popup:

      • STALE SELF-CONTRADICTING VALIDATION. Click "Run" empty → "Type a question
        first." Then TYPE a question. The launchpad composer's onPromptInput clears
        EMPTY_TASK_ERROR the instant the field fills ("otherwise the red ribbon
        stayed pinned under a textarea the user had since filled, contradicting
        itself"). The popup was the hand-maintained sibling with the IDENTICAL
        validation message but NO input listener → the error stayed pinned while
        the user typed a question. Founder symptom: 'it says type a question while
        I'm typing a question.'

      • UNDISCOVERABLE KEYBOARD SUBMIT. The popup wires ⌘/Ctrl+Enter → run-btn
        (popup.js), and BOTH sibling composers (launchpad primary + council refine)
        advertise that shortcut in their placeholder. The popup's placeholder did
        not — a keyboard affordance the user can't discover effectively doesn't
        exist. Assert the placeholder advertises it AND that the shortcut fires.

    Mutation proof: remove the #task 'input' listener → the stale-error assertion
    reds (status stays 'Type a question first.'); drop the '(⌘/Ctrl+Enter ...)'
    from the placeholder → the advertise assertion reds; remove the keydown
    Meta/Ctrl+Enter handler → the fires assertion reds. Each bites the INTENDED
    assertion (the positive controls — empty-Run shows the error, the field
    accepts text — pass first).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 800})
            errs: list[str] = []
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_LAUNCHPAD_STUB)
            page.goto(f"file://{POPUP}")

            # PRECONDITION A (surface paints, no leak): the composer + run button
            # mounted, and the status row starts empty.
            assert page.query_selector("#task") is not None, "popup #task composer must mount"
            start = page.evaluate("() => document.getElementById('status').textContent")
            assert start.strip() == "", f"status must start empty, got {start!r}"

            # ── STALE VALIDATION: empty Run → error → type → error CLEARS ──
            page.fill("#task", "")
            page.click("#run-btn")
            page.wait_for_timeout(120)
            after_empty = page.evaluate(
                "() => ({ text: document.getElementById('status').textContent,"
                " cls: document.getElementById('status').className })"
            )
            # Positive control: the empty-Run validation fires (bite, not vacuous).
            assert "Type a question first" in after_empty["text"], (
                f"empty Run must warn first (positive control), got {after_empty['text']!r}"
            )
            assert "error" in after_empty["cls"], f"warning must use error style: {after_empty['cls']!r}"

            # Now TYPE a real question — the contradicting error must clear.
            page.focus("#task")
            page.type("#task", "what is the best caching strategy")
            page.wait_for_timeout(120)
            after_typing = page.evaluate(
                "() => document.getElementById('status').textContent"
            )
            assert "Type a question first" not in after_typing, (
                "popup composer left 'Type a question first.' pinned while the user "
                f"typed a question (self-contradicting; the launchpad's onPromptInput "
                f"clears it) — status was {after_typing!r}"
            )

            # ── DISCOVERABLE + WORKING KEYBOARD SUBMIT (sibling parity) ──
            placeholder = page.eval_on_selector(
                "#task", "el => el.getAttribute('placeholder') || ''"
            )
            assert "Enter" in placeholder, (
                "popup composer must advertise its ⌘/Ctrl+Enter submit in the "
                "placeholder (both the launchpad + refine composers do); got "
                f"{placeholder!r}"
            )

            # The advertised shortcut must actually fire the run button.
            page.eval_on_selector(
                "#run-btn",
                "el => { el.__clicks = 0; el.addEventListener('click', () => { el.__clicks++; }); }",
            )
            page.fill("#task", "another real council question")
            page.focus("#task")
            page.keyboard.down("Meta")
            page.keyboard.press("Enter")
            page.keyboard.up("Meta")
            page.wait_for_timeout(120)
            fired = page.eval_on_selector("#run-btn", "el => el.__clicks")
            assert fired >= 1, (
                "the advertised ⌘/Ctrl+Enter must fire the run button — placeholder "
                "promises a shortcut that does nothing"
            )

            assert not errs, f"JS errors driving the popup composer: {errs[:3]}"
        finally:
            browser.close()


# A chrome.runtime stub whose FIRST get-council-status poll already returns a
# WHOLE-COUNCIL terminal status (failed / canceled) — drives the two terminal
# branches the happy-path lifecycle test (status==='completed') never reaches.
# `__TERMINAL__` / `__ERR__` are substituted per-case.
_TERMINAL_STUB = """
window.__pollCount = 0;
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        const n = ++window.__pollCount;
        let members, top;
        if (n <= 1) { members={claude:{status:'running'},codex:{status:'queued'},antigravity:{status:'queued'}}; top='running'; }
        else { members={claude:{status:'done'},codex:{status:'failed'},antigravity:{status:'done'}}; top='__TERMINAL__'; }
        const st = {members, synthesis:{status:'pending'}, status:top};
        __ERR__
        setTimeout(() => cb({ok:true, status:st}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
"""


def test_popup_council_terminal_states_render_honestly():
    """USEFULNESS/NO-FEEDBACK guard for the popup panel's WHOLE-COUNCIL terminal
    branches — status.status === 'failed' and 'canceled'. The happy-path
    lifecycle test only drives 'completed' (a 2-of-3 where the COUNCIL still
    finishes); the two branches where the whole run dies had ZERO behavioural
    coverage. Driven 2026-06-18 in real Chrome — both render honestly (correct
    title, the runner's error string with a sensible fallback, Stop hidden,
    Dismiss shown, panel never blanks). This pins that before a refactor of
    startPolling()'s terminal handling can silently strand the user on a dead
    "Council running" panel or swallow the failure reason.

    Mutation proof (cp backup, restored byte-identical):
      • make the 'failed' branch reuse the 'completed' title → reds on title;
      • drop `status.error || …` fallback so a no-error failed council shows a
        blank tip → reds on the fallback assertion;
      • drop enterTerminalState() in the failed/canceled branches → Stop stays
        visible / Dismiss stays hidden → reds on the Stop/Dismiss swap.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    probe = """() => ({
      title: document.getElementById('panel-title').textContent,
      tip: document.querySelector('.panel-tip').textContent,
      stopVisible: getComputedStyle(document.getElementById('panel-stop-btn')).display !== 'none',
      dismissVisible: getComputedStyle(document.getElementById('panel-dismiss-btn')).display !== 'none',
      panelVisible: getComputedStyle(document.getElementById('status-panel')).display !== 'none',
    })"""

    # (terminal-status, error-injected-into-status, expected-title)
    cases = [
        ("failed", 'st.error = "Claude hit a rate limit; the chairman never ran.";',
         "Council failed", "Claude hit a rate limit"),
        ("failed", "",  # no error string → the hard-coded fallback copy
         "Council failed", "The council runner exited with an error."),
        ("canceled", 'st.error = "Run was canceled by the user.";',
         "Council stopped", "Run was canceled by the user."),
        ("canceled", "",  # no error string → the hard-coded fallback copy
         "Council stopped", "Run was canceled."),
    ]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 760})
            for top, err_js, want_title, want_tip in cases:
                page = ctx.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                stub = _TERMINAL_STUB.replace("__TERMINAL__", top).replace("__ERR__", err_js)
                page.add_init_script(stub)
                page.goto(f"file://{POPUP}")
                page.fill("#task", "Rust or Go for a CLI?")
                page.click("#run-btn")
                page.wait_for_selector("#status-panel", state="visible", timeout=5000)
                page.wait_for_function(
                    f"document.getElementById('panel-title').textContent === {repr(want_title)}",
                    timeout=10000,
                )
                s = page.evaluate(probe)

                # The whole-council terminal state must read HONESTLY, not stay
                # frozen on "Council running" (the founder symptom: "it just
                # spun forever / the panel went dead").
                assert s["title"] == want_title, (
                    f"{top} council must title '{want_title}', got {s['title']!r}"
                )
                assert want_tip in s["tip"], (
                    f"{top} council must surface its reason (or the fallback), got tip={s['tip']!r}"
                )
                # Stop is meaningless once the run is over; Dismiss is the only
                # forward affordance.
                assert s["stopVisible"] is False, (
                    f"Stop must hide in the {top} terminal state, else it's a dead disabled control"
                )
                assert s["dismissVisible"] is True, (
                    f"Dismiss must appear in the {top} terminal state so the user can return to compose"
                )
                assert s["panelVisible"], f"the panel must not blank in the {top} terminal state"
                assert not errs, f"JS errors driving the {top} terminal branch: {errs[:3]}"
                page.close()
        finally:
            browser.close()


# A chrome.runtime stub that RECORDS every sent kind and DELAYS the open-launchpad
# reply, so a second click can land inside the host round-trip window — the race a
# non-disabled async dispatch button exposes.
_LAUNCHPAD_RECORDING_DELAYED_STUB = """
window.__closed = false;
window.__sent = [];
window.chrome = {
  runtime: {
    id: 'kjmoiabcdefghijklmnopqrstuvwx12', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      window.__sent.push(kind);
      // Slow open-launchpad so a forced 2nd click lands before the 1st resolves.
      if (kind === 'open-launchpad') { setTimeout(() => cb({ok:true}), 250); return; }
      setTimeout(() => cb({ok:true}), 4);
    }
  }
};
window.close = () => { window.__closed = true; };  // no-op so we can inspect after
"""


def test_popup_open_launchpad_button_double_click_opens_one_tab():
    """USABILITY/double-fire guard for the COMPOSE-state "Open Trinity launchpad →"
    button — the missed sibling of the panel "Open council page" fix (Iter 134).

    The host's `_open_launchpad` does an `open <launchpad.html>` (open_path →
    webbrowser.open analog) on EVERY dispatch, so each `open-launchpad` opens a real
    browser tab. The button stayed visible+enabled through the ~120ms native-host
    round-trip, so a genuine double-click — two `click`s landing inside the in-flight
    window — fired `open-launchpad` TWICE → TWO launchpad tabs for ONE click. Its
    twins (panel Open/Stop, launchpad `if (this.busy) return`) already guard this;
    this compose-state button was the last unguarded side-effectful dispatcher.

    Founder symptom this defends: "double-clicked Open Trinity launchpad and it
    opened two launchpad windows."

    The second click is FORCED (bypasses Playwright's enabled-wait) so the handler's
    OWN `if (btn.disabled) return;` guard is what must stop the double-fire — not
    Playwright auto-waiting on the disabled attribute. Mutation proof (cp backup,
    restored byte-identical): drop the `if (btn.disabled) return; btn.disabled = true;`
    lines in the open-launchpad-btn handler → a forced 2nd click fires open-launchpad
    twice → this reds. Also asserts a FAILED open RE-ENABLES the button so it isn't
    stuck-disabled (the failure twin of the panel buttons' recovery).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 800})
            errs: list[str] = []

            # ── A real double-click must dispatch open-launchpad exactly ONCE ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_LAUNCHPAD_RECORDING_DELAYED_STUB)
            page.goto(f"file://{POPUP}")
            # First click (normal), then a FORCED 2nd while the 1st is still in-flight.
            page.click("#open-launchpad-btn")
            page.click("#open-launchpad-btn", force=True)
            page.wait_for_timeout(450)
            opens = page.evaluate(
                "() => window.__sent.filter(k => k === 'open-launchpad').length"
            )
            assert opens == 1, (
                "double-clicking 'Open Trinity launchpad →' fired open-launchpad "
                f"{opens}x — a non-disabled async button opens TWO launchpad tabs for "
                "ONE click (founder symptom: 'it opened two launchpad windows'). The "
                "button must disable before dispatch like panel Open/Stop / launchCouncil."
            )
            assert not errs, f"JS errors during the double-click open-launchpad: {errs[:3]}"
            page.close()

            # ── A FAILED open must RE-ENABLE the button (not stuck-disabled) ──
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(
                _LAUNCHPAD_RECORDING_DELAYED_STUB.replace(
                    "if (kind === 'open-launchpad') { setTimeout(() => cb({ok:true}), 250); return; }",
                    "if (kind === 'open-launchpad') { setTimeout(() => cb({ok:false, error:'host unavailable'}), 80); return; }",
                )
            )
            page.goto(f"file://{POPUP}")
            page.click("#open-launchpad-btn")
            page.wait_for_timeout(300)
            disabled = page.evaluate("() => document.getElementById('open-launchpad-btn').disabled")
            closed = page.evaluate("() => window.__closed")
            text = page.evaluate("() => document.getElementById('status').textContent")
            assert disabled is False, (
                "a FAILED Open launchpad must RE-ENABLE the button so the user can "
                "retry — not leave it stuck-disabled after one un-reachable host call"
            )
            assert closed is False, "a FAILED Open launchpad must NOT close the popup"
            assert "Couldn't open the launchpad" in text and "host unavailable" in text, (
                f"a failed open must still surface its reason, got status={text!r}"
            )
            page.close()

            assert not errs, f"JS errors during the failed open-launchpad recovery: {errs[:3]}"
        finally:
            browser.close()


# ── WCAG AA contrast on the popup's SEMANTIC TEXT colors ────────────────────
#
# The popup is a hand-maintained shell that does NOT share design_system.py's
# SHARED_CSS, so when the launchpad split the FILL amber --warning into a
# readable --warning-text (UX sweep 2026-06-20), the popup kept painting status
# + pill TEXT with the fill tokens. Measured on the live popup these read:
#   .status.error  (failure copy)      #bd9658 → 2.31:1
#   .pill.failed   (a member failed)   #bd9658 → 2.64:1
#   .status.ok     (success copy)      #4f9095 → 3.08:1
#   .pill.done     (a member finished) #4f9095 → 3.52:1
#   .setup-footer a (install link)     #7fa0ad → 2.36:1
# all below the 4.5:1 AA body floor the whole palette was pushed to clear.
#
# This guard paints each state for real and computes the ratio from the
# COMPUTED color composited over the painted background — a string-presence
# check on the token would miss it (the *binding* was wrong, not the value).

_AA_BODY = 4.5


def _relative_luminance(rgb):
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# Paints every semantic-text state the popup actually renders and reports the
# COMPUTED color + the first opaque ancestor background it composites over.
_SEMANTIC_TEXT_PROBE = """() => {
  const rgb = (s) => (s.match(/[\\d.]+/g) || []).slice(0,3).map(Number);
  const bgOf = (el) => {
    while (el) {
      const c = getComputedStyle(el).backgroundColor;
      if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return rgb(c);
      el = el.parentElement;
    }
    return [255, 255, 255];
  };
  const out = {};
  // status.error + status.ok — the popup's primary feedback channel
  const st = document.getElementById('status');
  st.textContent = 'Failed: native messaging host not found.';
  st.className = 'status error';
  out['status.error'] = { fg: rgb(getComputedStyle(st).color), bg: bgOf(st) };
  st.textContent = 'Opening launchpad…';
  st.className = 'status ok';
  out['status.ok'] = { fg: rgb(getComputedStyle(st).color), bg: bgOf(st) };
  // .pill.failed + .pill.done — the per-member verdict (popup.js renders these)
  const mr = document.getElementById('member-rows');
  mr.innerHTML =
    '<div class="member-row"><span class="dot failed"></span>' +
    '<span class="name">Claude Opus 4.8</span>' +
    '<span class="pill failed">Failed</span></div>' +
    '<div class="member-row"><span class="dot done"></span>' +
    '<span class="name">GPT-5.5</span>' +
    '<span class="pill done">Done</span></div>';
  document.getElementById('status-panel').style.display = 'block';
  const pf = mr.querySelector('.pill.failed');
  out['pill.failed'] = { fg: rgb(getComputedStyle(pf).color), bg: bgOf(pf) };
  const pd = mr.querySelector('.pill.done');
  out['pill.done'] = { fg: rgb(getComputedStyle(pd).color), bg: bgOf(pd) };
  // .setup-footer a — the "Setup details →" install link (popup.js renders it)
  const f = document.createElement('div');
  f.className = 'setup-footer';
  f.innerHTML = 'Need a hand? <a href="#">Setup details →</a>';
  document.body.appendChild(f);
  const a = f.querySelector('a');
  out['setup-footer a'] = { fg: rgb(getComputedStyle(a).color), bg: bgOf(a) };
  return out;
}"""


def test_popup_semantic_text_meets_wcag_aa():
    """The popup's failure/success status copy + per-member Done/Failed pills +
    the setup install link must be READABLE (>= 4.5:1 on light). They shipped on
    the FILL tokens (--warning #bd9658, --success #4f9095, --info #7fa0ad) — 2.3
    to 3.5:1, unreadable-grade — because the popup never received the launchpad's
    --warning-text split. Founder symptom: a "Failed: host not found" error the
    user can barely read, and a Failed/Done member pill that vanishes into the
    card. Mutation proof: revert any of the four .color bindings to its fill token
    and the matching ratio drops below 4.5 → red.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 440, "height": 760}).new_page()
            page.goto(f"file://{POPUP}")
            page.wait_for_selector("#compose", state="visible", timeout=5000)
            measured = page.evaluate(_SEMANTIC_TEXT_PROBE)
            # the probe must have actually exercised every semantic-text site,
            # or the guard is hollow
            for site in ("status.error", "status.ok", "pill.failed", "pill.done", "setup-footer a"):
                assert site in measured, f"{site} not measured — guard is hollow: {measured}"
            for site, pair in measured.items():
                ratio = _contrast(pair["fg"], pair["bg"])
                assert ratio >= _AA_BODY, (
                    f"popup '{site}' text is {ratio:.2f}:1 (fg={pair['fg']} over "
                    f"bg={pair['bg']}) — below the {_AA_BODY}:1 WCAG AA body floor. "
                    "The popup painted semantic copy with a FILL token (the "
                    "hand-maintained sibling the launchpad's --warning-text split "
                    "missed); a 'Failed: host not found' error the user can barely "
                    "read is the founder symptom."
                )
        finally:
            browser.close()


# On a FAILED council the popup's `.panel-tip` renders `status.error` verbatim
# (popup.js:382 `tip.textContent = status.error`). That string is an arbitrary
# runner exception / stderr excerpt — `str(exc)` in council_runner.py — which can
# carry a long UNBREAKABLE token: a Native-Messaging host path, a quota URL, a
# hash. The popup body is a FIXED 440px; without overflow-wrap on .panel-tip the
# token streams off the right edge and stretches the whole popup card past 440px
# (driven: the unbroken token blew the body to ~1352px). The launchpad's
# running-card .provider-status-detail ALREADY breaks this exact case
# (launchpad_template.py:717 `overflow-wrap: anywhere`, comment "a long
# unbreakable token (a path or URL) … must wrap, never force the row wider than
# the card") — the popup's .panel-tip was the asymmetric unprotected sibling.
_LONG_UNBREAKABLE_ERROR = "ConnectionError" + ("x" * 180)

# Stateful stub: first poll RUNNING, then a FAILED council whose error is the
# long unbreakable token. Two polls is enough to reach the terminal failed state.
_CHROME_STUB_FAILED_LONG_ERROR = (
    """
window.__pollCount = 0;
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        const n = ++window.__pollCount;
        let members, synth, top, error;
        if (n <= 1) { members={claude:{status:'running'},codex:{status:'queued'},antigravity:{status:'queued'}}; synth={status:'pending'}; top='running'; }
        else { members={claude:{status:'failed'},codex:{status:'failed'},antigravity:{status:'failed'}}; synth={status:'pending'}; top='failed'; error=__ERR__; }
        setTimeout(() => cb({ok:true, status:{members, synthesis:synth, status:top, error}}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
""".replace("__ERR__", repr(_LONG_UNBREAKABLE_ERROR))
)

_OVERFLOW_PROBE = """() => {
  const de = document.documentElement;
  const vw = window.innerWidth;
  let worst = null;
  for (const el of document.querySelectorAll('#status-panel *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (r.right > vw + 0.5) {
      const over = r.right - vw;
      if (!worst || over > worst.over) {
        worst = { tag: el.tagName, cls: String(el.className), id: el.id, over: Math.round(over * 10) / 10 };
      }
    }
  }
  const tip = document.querySelector('.panel-tip');
  return {
    title: document.getElementById('panel-title').textContent,
    tip: tip ? tip.textContent : null,
    docScrollW: de.scrollWidth, docClientW: de.clientWidth, vw,
    docOverflow: de.scrollWidth > de.clientWidth,
    tipScrollW: tip ? tip.scrollWidth : null,
    tipClientW: tip ? tip.clientWidth : null,
    worst,
  };
}"""


def test_popup_failed_council_long_error_does_not_overflow():
    """A FAILED council whose `status.error` is a long unbreakable token must
    wrap inside the fixed-440px popup — never stretch the card off the right
    edge. Founder symptom: a failed-council error path/URL streams the popup
    body to ~1352px wide (the 320px running-council horizontal-scroll class,
    here on the popup). The launchpad's .provider-status-detail already guards
    this; .panel-tip was the asymmetric unprotected sibling."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            # 440 == the popup's own declared body width (body { width: 440px }) —
            # the real width Chrome renders the toolbar popup at.
            page = browser.new_context(viewport={"width": 440, "height": 760}).new_page()
            page.add_init_script(_CHROME_STUB_FAILED_LONG_ERROR)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "Migrate the dispatch layer to Native Messaging?")
            page.click("#run-btn")
            page.wait_for_function(
                "() => document.getElementById('panel-title').textContent === 'Council failed'",
                timeout=10000,
            )
            page.wait_for_timeout(80)
            s = page.evaluate(_OVERFLOW_PROBE)

            # Precondition (bite is not vacuous): the long error actually landed in
            # the tip, and we are in the failed terminal state.
            assert s["title"] == "Council failed", s
            assert s["tip"] == _LONG_UNBREAKABLE_ERROR, (
                f"the long unbreakable error did not reach .panel-tip — guard is "
                f"hollow: tip={s['tip']!r}"
            )
            # The actual defect: the popup body must NOT overflow horizontally, and
            # the tip's own content must wrap within its box (scrollWidth ≈ clientWidth).
            assert not s["docOverflow"], (
                f"popup FAILED-council card OVERFLOWS its 440px body — a long "
                f"unbreakable error token (a path/URL/hash from status.error) "
                f"streams off the right edge: documentElement scrollWidth="
                f"{s['docScrollW']} > clientWidth={s['docClientW']} (worst="
                f"{s['worst']}). .panel-tip needs overflow-wrap (the launchpad's "
                f"running-card .provider-status-detail already breaks this case)."
            )
            assert s["worst"] is None, (
                f"an element in the FAILED popup panel extends past the {s['vw']}px "
                f"viewport: {s['worst']} — the long error token is not wrapping."
            )
            assert s["tipScrollW"] <= s["tipClientW"] + 1, (
                f".panel-tip content overflows its own box (scrollWidth="
                f"{s['tipScrollW']} > clientWidth={s['tipClientW']}) — the "
                f"unbreakable error token is not being broken."
            )
        finally:
            browser.close()


# HONESTY / single-provider council — the popup's status panel PRE-RENDERS the
# canonical trio (claude/codex/antigravity) so the user sees structure before the
# first status JSON arrives, and its poll loop iterated that SAME hardcoded array.
# But a user with only ONE provider enabled in config (the "Ask all three" promise
# degraded to a council of one) gets a status file whose `members` map carries ONLY
# the dispatched slug — `{claude: …}`. Without reconciling the provisional rows down
# to the real member set, the two un-dispatched rows (GPT, Gemini) sat permanently
# "Queued": the popup painted a 3-VOICE CONTEST when one voice answered — the popup
# analog of the share-card/review/rail solo overclaim fixed in 00f37adc (those
# LIST-reading surfaces already derive from the real member map; this one hardcoded
# the trio in popup.js's run-btn handler + startPolling loop).
#
# The stub below seeds the DISCRIMINATING single-provider state: every
# get-council-status poll returns a members map with EXACTLY ONE key (claude). The
# seed is render-independent — the JS literal has one member slug — so the guard
# bites on the count derivation, not on a rendered string.
_CHROME_STUB_SOLO_PROVIDER = """
window.__pollCount = 0;
window.__memberMapKeys = ['claude'];  // render-independent proof: ONE enabled provider
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        const n = ++window.__pollCount;
        const done = n >= 2;
        // ONLY claude in the members map — the runner dispatched a council of one.
        const members = { claude: { status: done ? 'done' : 'running' } };
        const synth = { status: done ? 'done' : 'pending' };
        const top = done ? 'completed' : 'running';
        setTimeout(() => cb({ok:true, status:{members, synthesis:synth, status:top}}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
"""

_SOLO_ROSTER_PROBE = """() => {
  const rows = Array.from(document.querySelectorAll('#member-rows .member-row'));
  const members = rows
    .filter((r) => r.dataset.provider !== '__synthesis__')
    .map((r) => ({
      provider: r.dataset.provider,
      name: r.querySelector('.name').textContent.trim(),
      pill: r.querySelector('.pill').textContent.trim(),
    }));
  return {
    title: document.getElementById('panel-title').textContent,
    members,
    // render-independent: the count the runner actually dispatched
    realMemberCount: (window.__memberMapKeys || []).length,
    hasSynthRow: rows.some((r) => r.dataset.provider === '__synthesis__'),
  };
}"""


def test_popup_single_provider_council_shows_one_member_not_the_trio():
    """HONESTY guard: a single-provider council (only `claude` enabled — the
    "Ask all three" promise degraded to a council of one) must render exactly ONE
    member row in the popup status panel, NOT the hardcoded Claude·GPT·Gemini trio
    with two rows stuck "Queued" forever.

    Founder symptom this defends: the popup promised a three-voice contest (GPT +
    Gemini rows that never resolve) when one provider answered — the same dishonest
    overclaim 00f37adc fixed on the share card / review page / recent-councils rail,
    here on the toolbar popup's status panel (popup.js hardcoded
    ['claude','codex','antigravity'] for both the pre-render and the poll loop).

    Bite preconditions:
      (A) the panel PAINTS (the status panel is plain HTML/JS — it never leaks a
          raw template; the lifecycle test proves it renders) and the council
          reaches its terminal "Council ready" state;
      (B) the seed is the DISCRIMINATING 1-provider state, asserted
          render-independently on the stub's members-map literal
          (realMemberCount === 1) BEFORE the roster assertion.

    Mutation proof (cp backup, restored byte-identical): in popup.js, revert the
    poll loop to iterate the hardcoded launch-time `members` array (or delete the
    `reconcileMemberRows(memberMap)` call) → GPT + Gemini rows resurrect and stay
    "Queued" → the realMemberCount/roster assertions red with the founder symptom.
    """
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
            page.add_init_script(_CHROME_STUB_SOLO_PROVIDER)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "Should we shard the write path or scale up the primary?")
            page.click("#run-btn")
            page.wait_for_selector("#status-panel", state="visible", timeout=5000)
            page.wait_for_function(
                "document.getElementById('panel-title').textContent === 'Council ready'",
                timeout=15000,
            )
            s = page.evaluate(_SOLO_ROSTER_PROBE)

            # Precondition (B): the seed really is the 1-provider state — proven on
            # the stub's members-map literal, independent of what rendered. If this
            # were the trio, the guard wouldn't be discriminating.
            assert s["realMemberCount"] == 1, (
                f"seed is not the discriminating single-provider state "
                f"(realMemberCount={s['realMemberCount']}) — guard is hollow"
            )
            # Precondition (A): the panel painted and reached the terminal state.
            assert s["title"] == "Council ready", s
            assert s["hasSynthRow"], "the chairman synthesis row must still render"

            names = [m["name"] for m in s["members"]]
            # The defect: a council of one must NOT show GPT / Gemini as members —
            # those un-dispatched rows sat stuck "Queued" forever, falsely promising
            # a three-voice contest.
            assert names == ["Claude"], (
                f"single-provider council overclaims the roster: rendered "
                f"members {names} (one provider was enabled — only Claude was "
                f"dispatched). The popup hardcoded the Claude·GPT·Gemini trio and "
                f"left GPT + Gemini stuck 'Queued' — the dishonest 3-voice contest "
                f"00f37adc fixed elsewhere, here on the toolbar popup. members={s['members']}"
            )
            # Belt-and-suspenders: no member row may be a phantom stuck "Queued"
            # (the precise founder symptom — a row that never resolves).
            stuck = [m for m in s["members"] if m["pill"] == "Queued"]
            assert not stuck, (
                f"phantom member row(s) stuck 'Queued' after the council finished: "
                f"{stuck} — an un-dispatched provider painted as a pending council member"
            )
            assert not errs, f"JS errors during the single-provider council: {errs[:3]}"
        finally:
            browser.close()


# A11y / WCAG 4.1.2 Name, Role, Value — the popup's running-panel CLOSE control
# is an ICON-ONLY <button> whose only visible content is the "×" glyph. With no
# aria-label the AccName algorithm falls back to the visible text content — the
# bare "×" (U+00D7 MULTIPLICATION SIGN) — and a `title` does NOT win over text
# content, so a screen-reader user heard "times, button" / "multiplication sign,
# button" with no idea it dismisses the popup. Found 2026-06-21 by reading the
# real Chromium accessibility tree (CDP Accessibility.getFullAXTree); the
# panel-close × computed name was literally "×".
def _ax_name_for_selector(page, selector):
    """The browser's COMPUTED accessible name for the element at `selector`,
    read from Chromium's real AX tree (not an attribute guess)."""
    handle = page.query_selector(selector)
    assert handle is not None, f"{selector!r} not present — guard is hollow"
    cdp = page.context.new_cdp_session(page)
    cdp.send("Accessibility.enable")
    # Walk the full AX tree, match by the DOM node's backendNodeId.
    dom = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    # Resolve the element's backendNodeId via querySelector on the document.
    res = cdp.send("DOM.querySelector", {
        "nodeId": dom["root"]["nodeId"], "selector": selector,
    })
    node_id = res.get("nodeId")
    assert node_id, f"could not resolve {selector!r} in the DOM tree"
    desc = cdp.send("DOM.describeNode", {"nodeId": node_id})
    backend_id = desc["node"]["backendNodeId"]
    tree = cdp.send("Accessibility.getFullAXTree")
    for n in tree.get("nodes", []):
        if n.get("backendDOMNodeId") == backend_id:
            return (n.get("name") or {}).get("value")
    return None


def test_popup_close_button_has_accessible_name():
    """The popup running-panel close "×" must announce a MEANINGFUL accessible
    name to AT — not the bare glyph. Reds with the founder symptom: an icon-only
    close button that a screen reader announces as "×, button"."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 440, "height": 760}).new_page()
            _drive_into_running_panel(page)

            close = page.query_selector("#panel-close-btn")
            assert close is not None, "#panel-close-btn missing — panel not open"

            # Precondition (A): the control PAINTS + is a real interactive button.
            geo = page.evaluate(
                "() => { const b = document.getElementById('panel-close-btn');"
                " const r = b.getBoundingClientRect();"
                " return { tag: b.tagName.toLowerCase(), visible: r.width>0 && r.height>0,"
                " disabled: b.disabled }; }"
            )
            assert geo["tag"] == "button" and geo["visible"] and not geo["disabled"], (
                f"#panel-close-btn is not a painted interactive button: {geo}"
            )
            # Precondition (B): it is ICON-ONLY — its visible text is just the glyph
            # (render-independent: no real text label), so its name MUST come from
            # an explicit accessible-name source.
            text = page.evaluate(
                "() => document.getElementById('panel-close-btn').textContent.trim()"
            )
            assert text in ("×", "✕", "x", "X"), (
                f"#panel-close-btn is no longer icon-only (visible text {text!r}) — "
                f"re-derive this guard's preconditions"
            )

            name = _ax_name_for_selector(page, "#panel-close-btn")

            assert name and name.strip(), (
                "popup running-panel close button has NO accessible name — a "
                "screen-reader user hears only 'button' (WCAG 4.1.2 Name, Role, "
                "Value)."
            )
            assert name.strip() not in ("×", "✕", "x", "X"), (
                f"popup running-panel close button announces only the bare glyph "
                f"{name!r} — a screen reader reads 'multiplication sign, button', "
                f"not what the control DOES. Needs an aria-label (WCAG 4.1.2)."
            )
            assert "close" in name.lower(), (
                f"popup close button accessible name {name!r} does not say it "
                f"CLOSES anything — lead with the action (WCAG 4.1.2)."
            )
        finally:
            browser.close()


# A chrome.runtime stub whose FIRST get-council-status poll already returns a
# top-level FAILED with an EMPTY members map + pending synthesis — the runner /
# native host died before writing any member status. The popup's pre-rendered
# trio (Claude/GPT/Gemini) + chairman row are still all-"Queued" at that instant,
# so this is the exact "dispatch may not have started" case where a member grid is
# a contradiction under a "Council failed" header.
_CHROME_STUB_FAILED_NO_PROGRESS = """
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        setTimeout(() => cb({ok:true, status:{
          members:{}, synthesis:{status:'pending'}, status:'failed',
          error:'the dispatch may not have started'
        }}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
"""


def test_popup_failed_council_with_no_member_progress_hides_the_queued_grid():
    """PAINT/USEFULNESS guard — the popup analog of the launchpad's terminal
    showProviderRows gate (launchpad-init.js:1606, fixed 2026-06-02 on the
    cold-start launch). When the council fails BEFORE any member wrote status
    (runner / native host died — members map empty, every pre-rendered row still
    'pending'), the popup must HIDE the member grid. Before this fix the popup
    painted "Council failed" ABOVE a roster reading Claude=Queued · GPT=Queued ·
    Gemini=Queued · Chairman synthesis=Queued — a self-contradiction (the header
    says it didn't run; the grid says everyone is still patiently waiting). The
    popup inherited the launchpad's pre-render + reconcile but NOT this terminal
    hide — the un-inherited straggler in the popup-drift meta-class (231/239/244/
    247). The complementary with-progress case (Claude=Done / codex=failed) is
    covered by test_popup_council_terminal_states_render_honestly, which keeps
    the grid — so this guard pins the HIDE without weakening the KEEP.

    Mutation proof (cp backup, restored byte-identical):
      • drop the hideMemberRowsIfNoProgress() call in the 'failed' branch (or
        revert the helper to a no-op) → the grid stays display:'' with 4 visible
        all-"Queued" rows → reds on `gridHidden` + `visibleRowCount == 0`.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    probe = """() => {
      const grid = document.getElementById('member-rows');
      const rows = Array.from(grid.querySelectorAll('.member-row'));
      const visible = rows.filter(r => r.offsetParent !== null);
      return {
        title: document.getElementById('panel-title').textContent,
        // template-leak canary: the panel paints plain JS-built DOM, never a
        // raw petite-vue mustache. (Precondition A — the panel PAINTED.)
        bodyHasMustache: document.body.innerHTML.includes('{{'),
        gridDisplay: grid.style.display,
        gridHidden: grid.offsetParent === null,
        rowCount: rows.length,
        visibleRowCount: visible.length,
        visibleRows: visible.map(r =>
          r.querySelector('.name').textContent + '=' + r.querySelector('.pill').textContent
        ),
      };
    }"""

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 440, "height": 760}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(_CHROME_STUB_FAILED_NO_PROGRESS)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "Which database for my app?")
            page.click("#run-btn")
            # Precondition (A): the panel PAINTS + reaches the failed terminal.
            page.wait_for_function(
                "document.getElementById('panel-title').textContent === 'Council failed'",
                timeout=8000,
            )
            page.wait_for_timeout(120)  # let the hide apply after the terminal tick
            s = page.evaluate(probe)

            assert not errs, f"JS errors driving the no-progress failed branch: {errs[:3]}"
            assert s["title"] == "Council failed", s["title"]
            assert not s["bodyHasMustache"], "raw petite-vue mustache leaked into the popup body"

            # Precondition (B), checked render-INDEPENDENTLY: the discriminating
            # input is that the pre-render produced FOUR rows (trio + chairman) and
            # NONE of them ever progressed past the pre-render — so a member grid
            # here can only read "Queued ×4". (rowCount proves the grid was built;
            # the hide is the thing under test, not whether rows exist.)
            assert s["rowCount"] == 4, (
                f"expected the pre-rendered trio + chairman (4 rows) before the hide, "
                f"got {s['rowCount']} — re-derive this guard's discriminating input"
            )

            # The behaviour under test: on a no-progress terminal failure the grid
            # is HIDDEN, so "Council failed" never sits above an all-"Queued" roster.
            assert s["gridHidden"] and s["gridDisplay"] == "none", (
                "popup painted 'Council failed' but LEFT the member grid visible — "
                f"the all-Queued contradiction (visible rows: {s['visibleRows']}). "
                "The launchpad hides this grid on a no-progress terminal "
                "(showProviderRows); the popup must too."
            )
            assert s["visibleRowCount"] == 0, (
                f"'Council failed' must not show ANY 'Queued' member row when nothing "
                f"ran — still visible: {s['visibleRows']}"
            )
        finally:
            browser.close()


# A chrome.runtime stub that drives the council to a PARTIALLY-RUN-then-DIED
# terminal state: the first poll has Claude running / the others pending; the
# second poll flips the TOP-LEVEL status to failed/canceled while ONE member is
# Done and the OTHERS are STILL running/pending — exactly what
# finalize_council_run_state writes (it rewrites only the top-level status, never
# the per-member statuses). `__TERMINAL__` is substituted per case.
_CHROME_STUB_PARTIAL_THEN_DIED = """
window.__pollCount = 0;
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:true, detached:true}), 5); return; }
      if (kind === 'get-council-status') {
        const n = ++window.__pollCount;
        let members, top;
        if (n <= 1) {
          members = {claude:{status:'running'}, codex:{status:'pending'}, antigravity:{status:'pending'}};
          top = 'running';
        } else {
          // Claude landed; codex still in-flight, antigravity never started —
          // the per-member map is FROZEN mid-flight while the run died.
          members = {claude:{status:'done'}, codex:{status:'running'}, antigravity:{status:'pending'}};
          top = '__TERMINAL__';
        }
        setTimeout(() => cb({ok:true, status:{
          members, synthesis:{status:'pending'}, status:top,
          error:'Council runner exited before completion.'
        }}), 5);
        return;
      }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
"""


def test_popup_terminal_council_relabels_frozen_members_no_stale_running():
    """PARALLEL-SURFACE-DRIFT guard — the EXTENSION POPUP twin of the live council
    page (council_review.py memberRowsFor) + the launchpad running card
    (launchpad_template.py providerStatusRows) terminal-member normalization
    (closed on those two surfaces in the prior iters). When a council dies
    PARTWAY — one member Done, the OTHERS still 'running'/'pending' — the runner's
    finalize step flips ONLY the top-level status (finalize_council_run_state
    never rewrites per-member statuses). So the popup grid, kept visible because
    the Done member is real progress, painted "Council failed" / "Council stopped"
    ABOVE a member still reading "Running" and another "Queued" — a finished/dead
    council with a member shown as still running. The two sibling surfaces already
    normalize terminal && (pending|running) → 'didnt-run' ("Didn't run", failed) /
    'stopped' ("Stopped", canceled) with the MUTED 'pending' style; the popup is
    the hand-maintained THIRD sibling and had drifted (no normalization).

    Founder symptom this defends: the toolbar popup says "Council failed" while GPT
    still reads "Running" and Gemini still reads "Queued" underneath.

    TWO bite preconditions (so the guard isn't vacuous):
      (A) the RUNNING state really shows a "Running" badge before the council dies
          — the mid-flight transition is real, not a no-op;
      (B) the Done member's "Done" row SURVIVES on the terminal council (the grid
          is shown via hideMemberRowsIfNoProgress' keep-on-progress branch) — so
          the rows the stale-check asserts on actually exist.

    Mutation proof (cp backup, restored byte-identical): revert the
    normalizeTerminalMemberRows(...) calls in the failed/canceled branches (or
    no-op the helper body) → the never-finished members stay "Running"/"Queued"
    under the terminal header → reds on the `stale` assertion below. The
    preconditions (A running badge, B Done survives) pass first.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    row_probe = """(p) => {
      const r = document.querySelector(`#member-rows .member-row[data-provider='${p}']`);
      if (!r) return null;
      // Read textContent (NOT innerText): the pill is plain text here, but be
      // explicit so an uppercase-transform can never make a "Running" read green.
      return { pill: r.querySelector('.pill').textContent.trim(),
               dot: r.querySelector('.dot').className };
    }"""

    # (terminal-status, expected-title, expected-relabel for never-finished members)
    cases = [
        ("failed", "Council failed", "Didn't run"),
        ("canceled", "Council stopped", "Stopped"),
    ]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 460, "height": 760})
            for terminal, want_title, want_label in cases:
                page = ctx.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(
                    _CHROME_STUB_PARTIAL_THEN_DIED.replace("__TERMINAL__", terminal)
                )
                page.goto(f"file://{POPUP}")
                page.fill("#task", "Rust or Go for a CLI?")
                page.click("#run-btn")
                page.wait_for_selector("#status-panel", state="visible", timeout=5000)

                # ── Precondition (A): the RUNNING state shows a live "Running" badge
                # (the transition under test is real, not a relabel of nothing). The
                # first poll lands within ~one interval; wait for it.
                page.wait_for_function(
                    "() => { const r = document.querySelector(\"#member-rows .member-row[data-provider='claude'] .pill\");"
                    " return r && r.textContent.trim() === 'Running'; }",
                    timeout=8000,
                )

                # Drive the council to its terminal title.
                page.wait_for_function(
                    f"document.getElementById('panel-title').textContent === {want_title!r}",
                    timeout=10000,
                )
                page.wait_for_timeout(200)  # let normalizeTerminalMemberRows settle

                claude = page.evaluate(row_probe, "claude")
                codex = page.evaluate(row_probe, "antigravity")  # never started (pending)
                running_member = page.evaluate(row_probe, "codex")  # was 'running' when it died
                grid_visible = page.evaluate(
                    "() => getComputedStyle(document.getElementById('member-rows')).display !== 'none'"
                )

                # ── Precondition (B): the Done member survives on the terminal grid.
                assert grid_visible, (
                    f"{terminal}: the grid must stay visible (a Done member is real "
                    "progress) — else the stale-member assertion has nothing to bite on"
                )
                assert claude and claude["pill"] == "Done", (
                    f"{terminal}: the member that landed must keep 'Done', got {claude}"
                )

                # ── The defect under test: NO never-finished member reads
                # "Running" or "Queued" under a terminal header.
                stale = [
                    (slug, r["pill"])
                    for slug, r in (("antigravity", codex), ("codex", running_member))
                    if r and r["pill"] in ("Running", "Queued")
                ]
                assert not stale, (
                    f"the popup painted '{want_title}' but a member still reads "
                    f"{stale} — a finished/dead council with a member shown as still "
                    "running/queued. The live council page + launchpad running card "
                    "already normalize a terminal council's frozen members to "
                    "'Didn't run'/'Stopped'; the popup is the drifted third sibling."
                )

                # ── And the never-finished members read the HONEST relabel with the
                # muted (non-error) style — exactly mirroring the two siblings.
                for slug, r in (("antigravity", codex), ("codex", running_member)):
                    assert r and r["pill"] == want_label, (
                        f"{terminal}: never-finished member {slug!r} must read "
                        f"{want_label!r} (the sibling normalization), got {r}"
                    )
                    assert r and "pending" in r["dot"], (
                        f"{terminal}: a never-ran member is NOT an error — its dot "
                        f"must use the muted 'pending' style, got {r}"
                    )

                assert not errs, f"JS errors driving the {terminal} partial-then-died popup: {errs[:3]}"
                page.close()
        finally:
            browser.close()


# The popup `.status` ribbon (#status, painted by setStatus) is a DIFFERENT element
# and a DIFFERENT code path from the `.panel-tip` guarded above. It renders the
# DISPATCH-FAILURE error — `setStatus("Failed: " + extractError(response))` on the
# synchronous-host backward-compat path (popup.js run-btn handler) and "Couldn't
# open the launchpad: <error>" on the open-launchpad path. That error is arbitrary
# runner text: extractError → safeErrorMessage replaces ABSOLUTE PATHS with "a local
# file" but a long quota/billing URL, a hash, or a non-path identifier survives
# INTACT as a separator-free token. `.status` had `white-space: pre-wrap` but NO
# overflow-wrap, so pre-wrap (which only breaks at spaces) let the token stream off
# the right edge and stretch the fixed-440px popup card ~2.4× wide. This is the
# asymmetric unprotected SIBLING of the Iter-204 `.panel-tip` fix and the Iter-205
# `.status-error` fixes — same class, different element, never driven.
#
# A long quota URL (NOT a /Users path → survives safeErrorMessage verbatim) on the
# dispatch-failure path is the worst real case (a council member that hit a rate
# limit and the CLI printed an upgrade URL to stderr).
_LONG_DISPATCH_ERROR = (
    "rate limit hit, see https://console.anthropic.com/settings/billing/upgrade?ref="
    + "quota_exceeded_" + ("x" * 120)
)

# launch-council returns ok:false (NOT detached, NOT native-host-unavailable, NOT a
# 'CLI not on PATH' error) → popup.js run-btn handler falls to the
# setStatus("Failed: " + extractError(response)) branch that paints the .status
# ribbon. This is the SYNCHRONOUS-host backward-compat dispatch-failure path.
_CHROME_STUB_DISPATCH_FAILURE_LONG_ERROR = (
    """
window.chrome = {
  runtime: {
    id: 'testext', lastError: null,
    sendMessage: (msg, cb) => {
      const kind = msg && msg.kind;
      if (kind === 'launch-council') { setTimeout(() => cb({ok:false, error: __ERR__}), 5); return; }
      setTimeout(() => cb({ok:true}), 5);
    }
  }
};
""".replace("__ERR__", repr(_LONG_DISPATCH_ERROR))
)

_STATUS_RIBBON_OVERFLOW_PROBE = """() => {
  const de = document.documentElement;
  const vw = window.innerWidth;
  const s = document.getElementById('status');
  let worst = null;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (r.right > vw + 0.5) {
      const over = r.right - vw;
      if (!worst || over > worst.over) {
        worst = { tag: el.tagName, cls: String(el.className), id: el.id, over: Math.round(over * 10) / 10 };
      }
    }
  }
  return {
    statusText: s ? s.textContent : null,
    statusClass: s ? s.className : null,
    statusScrollW: s ? s.scrollWidth : null,
    statusClientW: s ? s.clientWidth : null,
    docScrollW: de.scrollWidth, docClientW: de.clientWidth, vw,
    docOverflow: de.scrollWidth > de.clientWidth,
    worst,
  };
}"""


def test_popup_dispatch_failure_long_error_ribbon_does_not_overflow():
    """A DISPATCH FAILURE whose error carries a long unbreakable token (a quota
    URL that survives safeErrorMessage) must wrap inside the fixed-440px popup
    .status ribbon — never stretch the card off the right edge. Founder symptom:
    a dispatch-failure error URL streams the popup body to ~929px wide (the
    horizontal-scroll class, here on the .status ribbon that setStatus paints).
    The sibling .panel-tip already guards this; the .status ribbon was the
    asymmetric unprotected sibling on a separate (dispatch-failure) code path."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            # 440 == the popup's own declared body width (body { width: 440px }).
            page = browser.new_context(viewport={"width": 440, "height": 760}).new_page()
            page.add_init_script(_CHROME_STUB_DISPATCH_FAILURE_LONG_ERROR)
            page.goto(f"file://{POPUP}")
            page.fill("#task", "Should I migrate the dispatch layer to Native Messaging?")
            page.click("#run-btn")
            page.wait_for_function(
                "() => { const s = document.getElementById('status');"
                " return s && s.textContent.startsWith('Failed:'); }",
                timeout=10000,
            )
            page.wait_for_timeout(80)
            s = page.evaluate(_STATUS_RIBBON_OVERFLOW_PROBE)

            # Precondition (bite is not vacuous): the long error actually landed in
            # the .status ribbon and is the error variant.
            assert s["statusText"] and _LONG_DISPATCH_ERROR in s["statusText"], (
                f"the long unbreakable dispatch error did not reach #status — guard "
                f"is hollow: statusText={s['statusText']!r}"
            )
            assert "error" in (s["statusClass"] or ""), (
                f"#status is not in the error variant: class={s['statusClass']!r}"
            )
            # The actual defect: the popup body must NOT overflow horizontally, and
            # the ribbon's own content must wrap within its box.
            assert not s["docOverflow"], (
                f"popup DISPATCH-FAILURE .status ribbon OVERFLOWS its 440px body — a "
                f"long unbreakable error token (a quota/billing URL from "
                f"extractError) streams off the right edge: documentElement "
                f"scrollWidth={s['docScrollW']} > clientWidth={s['docClientW']} "
                f"(worst={s['worst']}). #status needs overflow-wrap (the popup's "
                f".panel-tip sibling already breaks this exact case)."
            )
            assert s["worst"] is None, (
                f"an element extends past the {s['vw']}px popup width: {s['worst']} — "
                f"the long error token is not wrapping in the .status ribbon."
            )
            assert s["statusScrollW"] <= s["statusClientW"] + 1, (
                f".status ribbon content overflows its own box (scrollWidth="
                f"{s['statusScrollW']} > clientWidth={s['statusClientW']}) — the "
                f"unbreakable dispatch-error token is not being broken."
            )
        finally:
            browser.close()
