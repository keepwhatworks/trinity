"""Clicking the sharing (telemetry) toggle IN THE REAL SIDE PANEL must give
observable feedback and keep the displayed state HONEST — not silently no-op.

Founder lineage + why this needs a REAL-panel guard:
  The settings modal's sharing toggle is the privacy opt-out the modal copy
  promises you can "toggle it off anytime." When the user clicks it, the
  extension dispatch (`telemetry-enable` / `telemetry-disable`) can come back
  `ok:false` (the extension reached the host but couldn't apply it, or the
  native host is unavailable). `triggerSettingsAction` then routes to
  `fallbackToSettingsCli`: it keeps the modal OPEN and copies the CLI command,
  rendering the "✓ Copied — run it in your terminal to apply (the toggle stays
  as-is until you do)" confirmation. Crucially `toggleSharing` reverts
  `event.target.checked = this.telemetry.enabled` so the displayed switch does
  NOT flip to a state that isn't actually applied — showing it 'off' while
  telemetry is still on would be dishonest.

  The ONLY browser coverage of this interaction (test_file_substrate_render.py
  ::test_telemetry_toggle_without_extension_shows_cli_fallback) drives a
  `file://` render where there is NO reachable extension — so the dispatcher
  short-circuits to `tier:'install-prompt', ok:false` because `extensionId` is
  unset, and it never runs in the opaque-origin sandbox iframe the Chrome side
  panel actually uses. That render never:
    (a) runs in the sandbox opaque origin (where navigator.clipboard is governed
        by the sandboxed iframe permissions, not a file:// page — the
        confirmation must still appear there),
    (b) exercises a REAL dispatch that resolves `ok:false` (extension present
        but the action failed) — the `entry.extensionKind` true-branch, not the
        no-extension short-circuit,
    (c) asserts the CORRECT settings entry (`telemetry-disable`) was dispatched.

  If `toggleSharing` picked the wrong entry, the `event.target.checked` revert
  was dropped (the switch falsely flips to an un-applied state), or the
  `fallbackToSettingsCli` confirmation stopped rendering in the sandbox, the
  privacy opt-out would silently appear to do nothing — or LIE about being
  applied — in the real panel, while every existing test stayed green. Trinity's
  signature "green while the value is gone" shape, on a privacy control.

This drives the REAL extension side panel (real chrome.runtime + sandbox iframe
+ delegating capture-host stub), opens the settings modal, stubs the dispatcher
to RESOLVE `ok:false` (extension present, action not applied), clicks the
sharing-toggle LABEL (the input is opacity:0/0×0 — the user clicks the 44×24
slider), and asserts: the canonical `telemetry-disable` entry dispatched, the
modal stays open with the "✓ Copied — run it in your terminal" confirmation
VISIBLE, the displayed checkbox did NOT flip (stays at its persisted state), no
raw-{{ leak, no console errors, no overflow at the panel width.

Mutation-proven: dropping the `event.target.checked = this.telemetry.enabled`
revert in `toggleSharing` lets the switch flip to the un-applied state → the
"displayed state stays honest" assertion reds with the exact symptom.

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

# A dispatcher that resolves onResult with an extension FAILURE (ok:false) =
# the common "extension reached the host but telemetry-disable couldn't apply"
# case. This is the branch a file:// render never takes (there it short-circuits
# to tier:'install-prompt' because extensionId is unset). probe/subscribe stub
# the warm-probe so the real chrome.runtime path is never touched.
_STUB_FAIL = """
() => {
  window.__dispatched = [];
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) {
      window.__dispatched.push(opts && opts.extensionAction);
      if (opts && opts.onResult) setTimeout(function () {
        opts.onResult({ tier: 'extension', ok: false, response: { error: 'cannot apply' } });
      }, 20);
    },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
    onStateChange: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host (delegating every non-launchpad_data
    query to the REAL capture-host handlers), load the real extension, open the side
    panel, and return (ctx, ext_id, page, errors) after the launchpad mounts."""
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


def test_sharing_toggle_in_panel_gives_honest_feedback(tmp_path, monkeypatch):
    """Clicking the sharing toggle in the REAL side panel (dispatch resolves ok:false)
    must dispatch the right entry, show the ✓ CLI confirmation, and NOT flip the
    displayed switch to an un-applied state."""
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

            lf.evaluate(_STUB_FAIL)

            # Open the settings modal.
            lf.locator("button[title='Settings']").first.click(timeout=5000)
            page.wait_for_timeout(500)
            mf = page.frames[-1]

            before = mf.evaluate(
                "()=>{const cb=document.querySelector('.sharing-toggle input[type=checkbox]');"
                "return cb?cb.checked:null;}"
            )
            # Telemetry is ON by default — sanity-pin the precondition so a flip is
            # observable (telemetry-disable is the entry that should dispatch).
            assert before is True, (
                f"precondition: telemetry must read ENABLED by default for this guard to "
                f"observe a (non-)flip (before={before})"
            )

            # Click the visible 44×24 slider/label — the <input> is opacity:0/0×0,
            # so this is the control a real user clicks (and fires a trusted change).
            mf.locator(".sharing-toggle label.toggle-switch").first.click(timeout=5000)
            page.wait_for_timeout(700)

            after = mf.evaluate(
                "()=>{const vw=document.documentElement.clientWidth;"
                "const cb=document.querySelector('.sharing-toggle input[type=checkbox]');"
                "const conf=[...document.querySelectorAll('.settings-modal .meta')]"
                ".find(e=>/Copied/.test(e.textContent)&&e.offsetParent!==null);"
                "const over=[...document.querySelectorAll('.settings-modal *')]"
                ".filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "return {checked:cb?cb.checked:null, modalOpen:!!document.querySelector('.sharing-toggle'),"
                " confVisible:!!(conf&&conf.offsetParent!==null), confText:conf?conf.textContent.trim():null,"
                " dispatched:window.__dispatched||[],"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " over:over, docW:document.documentElement.scrollWidth, vw:vw};}"
            )

            # 1. USABILITY — the click fired the CANONICAL disable entry (not a no-op,
            #    not the wrong entry). telemetry was ON → clicking turns it OFF.
            kinds = [d.get("kind") if isinstance(d, dict) else None for d in after["dispatched"]]
            assert "telemetry-disable" in kinds, (
                f"clicking the sharing toggle (telemetry ON) did NOT dispatch 'telemetry-disable' "
                f"— it no-oped or fired the wrong entry: dispatched={after['dispatched']}"
            )

            # 2. USABILITY/FEEDBACK — on the ok:false fallback the modal stays open and
            #    the ✓ CLI confirmation is VISIBLE (not swallowed, not dead-ended on the
            #    install-extension banner) — even in the opaque-origin sandbox.
            assert after["modalOpen"], (
                "the settings modal closed after the failed toggle — the ✓ confirmation would "
                "be invisible (the privacy opt-out dead-ends with no feedback in the real panel)"
            )
            assert after["confVisible"], (
                "clicking the sharing toggle in the real panel showed NO ✓ confirmation — the "
                "privacy opt-out silently no-ops in the opaque-origin sandbox (the file:// test "
                "can't catch this: clipboard is governed by the sandboxed iframe there)"
            )
            assert "run it in your terminal" in (after["confText"] or ""), (
                f"the toggle confirmation copy is wrong/missing: {after['confText']!r}"
            )

            # 3. USEFULNESS/HONESTY — the displayed switch must NOT flip to an un-applied
            #    state. The command is COPIED, not applied; showing 'off' while telemetry
            #    is still on would LIE about the privacy state.
            assert after["checked"] == before, (
                f"the sharing switch FLIPPED its displayed state (before={before}, "
                f"after={after['checked']}) without the setting being applied — dishonest; it "
                "must stay as-is until the user runs the copied command (toggleSharing must "
                "revert event.target.checked = this.telemetry.enabled)"
            )

            # 4. PAINT — no leak / no overflow at the narrow panel width.
            assert not after["rawLeak"], "raw {{ }} leaked — the launchpad app lost its mount"
            assert int(after["docW"]) <= int(after["vw"]) + 1 and not after["over"], (
                f"the settings modal overflows the {after['vw']}px panel after the toggle: "
                f"docW={after['docW']} overflowing={after['over']}"
            )
            assert errors == [], f"console errors during the in-panel sharing-toggle interaction: {errors}"
        finally:
            ctx.close()


def test_reset_anonymous_id_feedback_is_inline_and_reset_worded(tmp_path, monkeypatch):
    """The 'Reset anonymous ID' button's no-extension CLI-copy confirmation must
    render INLINE next to the button it confirms — with reset-appropriate copy —
    not 155px down the modal at the sharing toggle, worded "the toggle stays as-is".

    Founder symptom (the bug this guard bites):
      Reset and the sharing toggle BOTH routed through fallbackToSettingsCli ->
      copyLens(cliCommand, 'settings-cli'), and the ONLY 'settings-cli' line lives
      at the very bottom of the modal beside the sharing toggle. So clicking Reset
      in the real (narrow, 393px) side panel:
        (a) showed its confirmation 155px BELOW the button — visually disconnected,
            often below the fold in the real panel, reading as NO feedback, and
        (b) the line it lit said "✓ Copied … (the toggle stays as-is until you do)"
            — a TOGGLE message for a button that has no toggle: the WRONG action.
      Routing Reset to its own feedbackKey ('settings-reset-cli') + an inline line
      beside the button fixes both. A string-presence test can't see (a); this
      asserts the rendered GEOMETRY (confirmation top within 60px of the button
      bottom) + that the toggle-worded line never lights for a Reset click.

    Mutation-proven: reverting the data-builder feedbackKey on the reset entry to
    'settings-cli' (or deleting the inline <p>) drops the inline line; the proximity
    assertion reds and/or the toggle-worded line is the only one visible.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Same ok:false fallback dispatcher; Reset takes the SAME extensionKind branch.
    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            lf.evaluate(_STUB_FAIL)

            lf.locator("button[title='Settings']").first.click(timeout=5000)
            page.wait_for_timeout(500)
            mf = page.frames[-1]

            reset_btn = mf.locator("button[aria-label='Reset anonymous ID']").first
            assert reset_btn.count() > 0, "the Reset anonymous ID button is missing from the settings modal"
            reset_box = reset_btn.bounding_box()
            assert reset_box, "Reset button has no geometry (not rendered)"

            reset_btn.click(timeout=5000)
            page.wait_for_timeout(700)

            after = mf.evaluate(
                "()=>{const metas=[...document.querySelectorAll('.settings-modal .meta')];"
                "const conf=metas.find(e=>/Copied/.test(e.textContent)&&e.offsetParent!==null);"
                "const toggleLine=metas.find(e=>/the toggle stays as-is/.test(e.textContent)&&e.offsetParent!==null);"
                "const r=conf?conf.getBoundingClientRect():null;"
                "return {dispatched:window.__dispatched||[],"
                " confVisible:!!(conf&&conf.offsetParent!==null),"
                " confText:conf?conf.textContent.trim():null,"
                " confTop:r?r.top:null,"
                " toggleLineVisible:!!(toggleLine&&toggleLine.offsetParent!==null),"
                " rawLeak:(document.body.innerHTML||'').includes('{{')};}"
            )

            # 1. USABILITY — Reset dispatched the canonical reset entry (not a no-op).
            kinds = [d.get("kind") if isinstance(d, dict) else None for d in after["dispatched"]]
            assert "telemetry-reset-id" in kinds, (
                f"clicking Reset anonymous ID did NOT dispatch 'telemetry-reset-id' — it "
                f"no-oped or fired the wrong entry: dispatched={after['dispatched']}"
            )

            # 2. FEEDBACK — a confirmation is visible at all.
            assert after["confVisible"], (
                "clicking Reset anonymous ID in the real panel showed NO ✓ confirmation — "
                "the action reads as a silent no-op"
            )

            # 3. PLACEMENT (the geometry that bites) — the confirmation renders INLINE,
            #    within 60px below the Reset button bottom. On the un-fixed code the only
            #    'settings-cli' line sits ~155px down at the sharing toggle.
            btn_bottom = reset_box["y"] + reset_box["height"]
            gap = after["confTop"] - btn_bottom
            assert 0 <= gap <= 60, (
                f"the Reset confirmation is not inline with its button: it renders {gap:.0f}px "
                f"below the button bottom (button_bottom={btn_bottom:.0f}, conf_top={after['confTop']:.0f}) "
                "— the founder symptom was a 155px gap down at the sharing toggle, reading as NO feedback"
            )

            # 4. COPY — the line must be RESET-worded (about the anonymous ID), and the
            #    toggle-worded line must NOT be the one lit for a Reset click.
            assert "anonymous ID" in (after["confText"] or ""), (
                f"the Reset confirmation copy is not reset-worded: {after['confText']!r}"
            )
            assert "the toggle stays as-is" not in (after["confText"] or ""), (
                f"the Reset click lit the TOGGLE-worded confirmation (wrong action): {after['confText']!r}"
            )
            assert not after["toggleLineVisible"], (
                "the sharing-toggle confirmation line is visible after a RESET click — Reset is "
                "lighting the wrong action's feedback"
            )

            # 5. PAINT — no raw-template leak.
            assert not after["rawLeak"], "raw {{ }} leaked — the launchpad app lost its mount"
            assert errors == [], f"console errors during the in-panel Reset interaction: {errors}"
        finally:
            ctx.close()
