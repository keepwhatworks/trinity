"""Clicking "Stop council" ON THE LIVE COUNCIL PAGE in the REAL side panel must
ACK immediately — not look like a no-op.

Found 2026-06-18 driving the RUNNING live council page in the real extension
sandbox: clicking "Stop council" dispatches `stop-council` (confirmed firing) but
the UI showed NO change — the button still read "Stop council" (not disabled, so a
double-fire was possible) and the spinner kept cycling its witty messages
("Tokenizing real life…" → "Convincing AI not to turn evil…"). The actual cancel
only lands LATER, when the host writes a 'canceled' status the poller is waiting on.
So for the whole gap the user can't tell their click did anything — they'd click
again, or assume Stop is broken.

The CLASS + the gap: this is the SAME NO-FEEDBACK shape the founder hit on the
LAUNCHPAD's Stop council on 2026-06-17 (test_launchpad_stop_council_feedback_browser
::test_stop_council_acks_immediately) — but that fix added the `stopRequested`
immediate-ACK to `launchpad_template.py` ONLY. The LIVE COUNCIL PAGE
(`council_review.py`) has its OWN, separate "Stop council" button (the one that
renders on a running segment via the live_council page), and it was MISSED — its
`stopCouncil()` only surfaced feedback on the FAILURE path (chainError), never on
the success path. The two surfaces share the control; the fix only covered one.

The root-cause fix mirrors the launchpad's: a `stopRequested` flag set the instant
Stop is clicked → the button flips to "Stopping…" (disabled, no double-fire) and
`currentStatusMessageFor` pins "Stopping the council…" for the busy segment until
the poller finalizes to canceled (a new chain round / a dispatch failure reset it).

Why this needs a REAL-panel guard: the live council page only runs its
status-token polling + brokered nav in the opaque-origin sandbox the Chrome side
panel actually uses. The launchpad's Stop guard drives a `file://`-over-http
launchpad render — a completely different surface. If this immediate-ACK regressed,
every council Stopped FROM THE PANEL'S LIVE PAGE would silently look broken (the
user re-clicks, or assumes Stop does nothing) while every existing test stayed
green — Trinity's signature "green while the feedback is gone" shape on a cancel
control.

This drives the REAL extension side panel, swaps the iframe to the running
live_council page (`?status_token=…`), keeps it RUNNING (stubbed loadStatusScript
returns status:'running'), stubs the dispatcher to DEFER the stop result (the exact
"stop dispatch ok but cancel lands later" case), clicks the live page's Stop button,
and asserts the IMMEDIATE ack (<1s, well before any poller round-trip): the
`stop-council` entry dispatched, the button reads "Stopping…" + is disabled, and the
status pins "Stopping the council…".

Mutation-proven: dropping `this.stopRequested = true` from `stopCouncil()` in the
BUNDLED sandbox/live_council.html lets the button stay "Stop council" + the spinner
keep cycling → the assertions red with the founder symptom.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"
TOKEN = "launch_runningSTOPack"

# Keep the council RUNNING (so the spinner + Stop button render) and DEFER the stop
# result — dispatch() records the entry but never calls onResult, mirroring the real
# "stop dispatch succeeds but the cancel lands later via the poller" case that the
# immediate ACK fills.
_STUB_RUNNING_STOP = """
() => {
  window.__stopActs = [];
  window.loadStatusScript = function (token, cb) {
    cb({ status: 'running', task_text: 'Compare three rate-limiting strategies',
         members: { claude: { status: 'running' }, codex: { status: 'pending' } },
         synthesis: { status: 'pending' } });
  };
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (o) {
      window.__stopActs.push(o.extensionAction && o.extensionAction.kind);
      // No onResult — the cancel lands later (the poller writes 'canceled').
    },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host (delegating non-launchpad_data
    queries to the REAL capture host), load the real extension, open the side panel,
    and return (ctx, ext_id, page, errors) after the launchpad mounts."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    stub = (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json, os\n"
        f"os.environ['TRINITY_HOME'] = {str(home)!r}\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from trinity_local.capture_host import QUERY_HANDLERS\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "msg = msg or {}\n"
        "qk = msg.get('query_kind')\n"
        "if qk == 'launchpad_data':\n"
        f"    out = open({str(pl)!r}).read().encode()\n"
        "elif qk in QUERY_HANDLERS:\n"
        "    out = json.dumps(QUERY_HANDLERS[qk](msg), default=str).encode()\n"
        "else:\n"
        "    out = json.dumps({'ok': True}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )
    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(stub, encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    try:
        ctx = p.chromium.launch_persistent_context(
            str(ud), headless=False,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no launchable chromium: {exc}")

    sw = None
    for _ in range(50):
        if ctx.service_workers:
            sw = ctx.service_workers[0]
            break
        try:
            sw = ctx.wait_for_event("serviceworker", timeout=2000)
            break
        except Exception:
            time.sleep(0.1)
    assert sw, "extension service worker never registered (manifest invalid?)"
    ext_id = sw.url.split("/")[2]
    (nm / f"{HOST}.json").write_text(json.dumps({
        "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }), encoding="utf-8")

    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page, errors


def test_live_council_stop_acks_immediately_in_panel(tmp_path, monkeypatch):
    """Clicking Stop on the RUNNING live council page in the REAL side panel must give
    an immediate ACK (button "Stopping…" + disabled, status "Stopping the council…")
    — not look like a no-op while the cancel defers to the poller."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            # Broker a nav to the RUNNING live council page the way the sidepanel
            # bridge does on a __trinityNav message: swap the iframe to the
            # allowlisted sandbox sibling with a status_token (→ a busy segment).
            page.evaluate(
                "(t)=>{document.getElementById('app').src="
                "'sandbox/live_council.html?status_token='+t;}", TOKEN
            )
            page.wait_for_timeout(4000)  # broker swap + mount + first poll
            cf = page.frames[-1]
            assert "sandbox/live_council.html" in (cf.url or ""), (
                f"the broker did NOT swap to the live council page: {cf.url}"
            )

            # Keep it running + defer the stop result; let polling render the busy
            # segment with its Stop button.
            cf.evaluate(_STUB_RUNNING_STOP)
            page.wait_for_timeout(3500)

            pre = cf.evaluate(
                "()=>{const stop=[...document.querySelectorAll('.live-actions button')]"
                ".find(b=>/stop/i.test(b.textContent));"
                "return {exists:!!stop, label:stop?stop.textContent.trim():null,"
                " disabled:stop?stop.disabled:null};}"
            )
            # Precondition: the running council shows an ENABLED "Stop council" button
            # (so the post-click flip is observable — not a vacuous pass).
            assert pre["exists"], (
                "the live council page never rendered a Stop button on the running segment "
                "(the busy/spinner state didn't materialize from the status_token poll)"
            )
            assert pre["label"] == "Stop council" and pre["disabled"] is False, (
                f"precondition: the Stop button should read an enabled 'Stop council' before "
                f"the click (got label={pre['label']!r} disabled={pre['disabled']})"
            )

            # Click the live page's Stop button. The dispatcher defers (no onResult),
            # so the ONLY thing that can change the UI in the next 600ms is the
            # client-side immediate ACK — well before any poller round-trip.
            cf.locator(".live-actions button").first.click(timeout=5000)
            page.wait_for_timeout(600)

            after = cf.evaluate(
                "()=>{const vw=document.documentElement.clientWidth;"
                "const stop=[...document.querySelectorAll('.live-actions button')]"
                ".find(b=>/stop/i.test(b.textContent));"
                "const sm=[...document.querySelectorAll('.status-message')].map(e=>e.textContent.trim());"
                "const over=[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "return {acts:window.__stopActs||[], label:stop?stop.textContent.trim():null,"
                " disabled:stop?stop.disabled:null, statusMsgs:sm,"
                " stopping:sm.some(t=>/stopping the council/i.test(t)),"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " docW:document.documentElement.scrollWidth, vw:vw, over:over};}"
            )

            # 1. USABILITY — the click fired the canonical stop entry (not a no-op).
            assert "stop-council" in (after["acts"] or []), (
                f"clicking Stop on the live council page did NOT dispatch 'stop-council' "
                f"— it no-oped or fired the wrong entry: {after['acts']}"
            )
            # 2. FEEDBACK — the button itself ACKs the click + locks against a
            #    double-fire (the founder's NO-FEEDBACK symptom, on the live page).
            assert after["label"] == "Stopping…", (
                f"Stop gave NO immediate feedback on the live council page — the button still "
                f"reads {after['label']!r} (the cancel defers to the poller, so for the whole gap "
                "the click looks like a no-op). The live page's Stop council was missed by the "
                "2026-06-17 launchpad-only fix."
            )
            assert after["disabled"] is True, (
                "the live page's Stop button stayed CLICKABLE after a stop request — a second "
                "click would double-fire the cancel"
            )
            # 3. FEEDBACK — the status pins an honest "Stopping…" line instead of
            #    cycling witty messages that read like nothing happened.
            assert after["stopping"], (
                f"the live council spinner kept cycling its witty messages after Stop "
                f"(no 'Stopping the council…' override): {after['statusMsgs']!r} — looks like a no-op"
            )
            # 4. PAINT — no leak / no overflow at the narrow panel width.
            assert not after["rawLeak"], "raw {{ }} leaked — the live_council app lost its mount"
            assert int(after["docW"]) <= int(after["vw"]) + 1 and not after["over"], (
                f"the live council page overflows the {after['vw']}px panel after Stop: "
                f"docW={after['docW']} overflowing={after['over']}"
            )
            assert errors == [], f"console errors during the in-panel live Stop interaction: {errors}"
        finally:
            ctx.close()
