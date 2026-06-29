"""The PRIMARY council composer in the REAL side panel must launch on ⌘/Ctrl+Enter
— the same keyboard convention the council REFINE composer already ships and
advertises ("⌘/Ctrl+Enter to send"). A keyboard user must be able to submit the
headline action from the focal element without reaching for the mouse.

Why this needs a REAL-panel guard the existing suite doesn't give:
  * The home composer (#council-prompt) is the FOCAL element — the launchpad's CSS
    flex order pulls it directly under the hero so "a first-time eye lands on
    #council-prompt". Yet it shipped with NO keyboard-submit handler: a keyboard
    user typed a council prompt and had NO way to launch from the textarea — they
    had to mouse to (or Tab past every card to) "Launch Council". The SECONDARY
    refine composer one click deeper (council_review.py) was MORE keyboard-operable
    than the headline one (`@keydown.enter.meta.prevent="startRefine"` +
    `.ctrl.prevent`). Driven 2026-06-22 in the side panel: Cmd+Enter / Ctrl+Enter
    on the home composer fired ZERO dispatch (the Launch CLICK fired
    'launch-council' — so the dispatch path worked; the keyboard binding was the
    sole gap).
  * Every existing real-panel composer test (dispatch_lifecycle, empty_validation,
    dispatch_banner) drives the Launch *click* — none presses ⌘/Ctrl+Enter, so the
    keyboard path was wholly un-driven and a regression that drops the binding stays
    green across the whole suite.

This drives the REAL extension (the only thing that exercises the opaque-origin
sandbox the Chrome side panel actually uses, at 393px) with a STUBBED
`window.__TRINITY_DISPATCH__` that RECORDS each dispatch's extensionAction.kind:
  - precondition A (surface paints): the composer is present, mounted, no `{{` leak;
  - precondition B (discriminating positive control): a Launch CLICK with the same
    valid prompt fires a 'launch-council' dispatch — so the dispatch path is live
    and the keyboard assertion below isn't vacuous;
  - the FIX: focusing #council-prompt and pressing ⌘+Enter (then, fresh, Ctrl+Enter)
    fires a 'launch-council' dispatch.

Mutation-proven (BUNDLE-AWARE): removing the `@keydown.enter.meta.prevent` /
`.ctrl.prevent` bindings from the home composer in src AND REBUILDING the extension
bundles reds the keyboard assertion with the founder symptom (⌘/Ctrl+Enter fired no
launch from the focal composer). Mutating src alone tests the STALE bundle and
falsely passes — the panel renders the BUILT sandbox/launchpad.html.

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

# A dispatcher that RECORDS each dispatch's extensionAction.kind. A keyboard launch
# must push 'launch-council' just like the Launch click does.
_STUB_REC = """
() => {
  window.__kinds = [];
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (o) { window.__kinds.push(o && o.extensionAction ? o.extensionAction.kind : 'launch'); },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
    onStateChange: function () {},
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


_VALID = "Compare three caching strategies for a read-heavy API"


def _drive_keyboard_submit(p, tmp_path, monkeypatch, combo):
    """Boot a FRESH panel (each launch leaves the app `busy` until onResult, and the
    recording stub never resolves it, so a second launch in the same panel would
    early-return — drive each chord in its own panel), then:
      A. assert the focal composer paints + mounted, no raw leak;
      B. (discriminating positive control) a Launch CLICK fires exactly one
         'launch-council' dispatch — proving the dispatch path is live so the
         keyboard assertion isn't vacuous; we then RELOAD the panel so the click's
         busy state can't shadow the chord;
      then press `combo` (Meta+Enter / Control+Enter) on a focused #council-prompt
    and return (kinds_after_chord, errors)."""
    ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
    try:
        lf = page.frames[-1]
        assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
        # Precondition: the in-panel opaque-origin path (where the worst bugs hide).
        assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
            "the in-panel host-fetch signal isn't set — not the real sandbox path"
        )
        # ── Precondition A: the focal composer paints + mounted, no raw leak ──
        paint = lf.evaluate(
            "()=>{const t=document.querySelector('#council-prompt');"
            "const b=document.querySelector('.actions button.button.primary');"
            "return {hasComposer:!!t, composerVisible:!!(t&&t.offsetParent!==null),"
            " hasLaunch:!!b, mounted:!document.querySelector('[v-scope][v-cloak]'),"
            " raw:document.body.innerHTML.includes('{{')};}"
        )
        assert paint["hasComposer"] and paint["composerVisible"], (
            f"the primary #council-prompt composer never painted in the panel: {paint!r}"
        )
        assert paint["hasLaunch"], "the Launch Council button is missing — composer surface incomplete"
        assert paint["mounted"], "the launchpad app never mounted (v-cloak still set)"
        assert not paint["raw"], "raw {{ }} leaked — the launchpad app lost its mount"

        # ── Precondition B (discriminating positive control): a Launch CLICK with a
        #    valid prompt DOES fire 'launch-council' — proves the dispatch path is
        #    live, so a green on the keyboard case below isn't vacuous. ──
        lf.evaluate(_STUB_REC)
        lf.evaluate(
            "(v)=>{const t=document.querySelector('#council-prompt');t.value=v;"
            "t.dispatchEvent(new Event('input',{bubbles:true}));}",
            _VALID,
        )
        lf.locator(".actions button.button.primary").first.click(timeout=5000)
        page.wait_for_timeout(500)
        after_click = lf.evaluate("()=>window.__kinds.slice()")
        assert after_click == ["launch-council"], (
            f"the positive control failed — a Launch CLICK did NOT fire exactly one "
            f"'launch-council' dispatch ({after_click!r}); the keyboard assertion would be "
            f"vacuous, so this drive is aborting rather than passing meaninglessly"
        )

        # The click left the app `busy` (the stub never resolves onResult). RELOAD
        # the panel so the chord drives a clean, non-busy composer.
        page.reload(wait_until="load", timeout=20000)
        page.wait_for_timeout(4000)
        lf = page.frames[-1]
        assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing after reload: {lf.url}"
        errors.clear()  # drop any reload-load noise; we assert clean across the chord only

        # ── The FIX: the keyboard chord on the focal composer fires a launch. ──
        lf.evaluate(_STUB_REC)
        lf.evaluate(
            "(v)=>{const t=document.querySelector('#council-prompt');t.value=v;"
            "t.dispatchEvent(new Event('input',{bubbles:true}));t.focus();}",
            _VALID,
        )
        page.wait_for_timeout(150)
        page.keyboard.press(combo)
        page.wait_for_timeout(500)
        kinds = lf.evaluate("()=>window.__kinds.slice()")
        return kinds, errors
    finally:
        ctx.close()


def test_primary_composer_launches_on_cmd_enter_in_panel(tmp_path, monkeypatch):
    """⌘+Enter on the focal #council-prompt fires a 'launch-council' dispatch in the
    REAL side panel — keyboard parity with the refine composer's '⌘/Ctrl+Enter'."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        kinds, errors = _drive_keyboard_submit(p, tmp_path, monkeypatch, "Meta+Enter")
    assert kinds == ["launch-council"], (
        "⌘+Enter on the focal #council-prompt composer fired NO launch in the side "
        f"panel ({kinds!r}) — the keyboard-submit binding the refine composer ships "
        "('⌘/Ctrl+Enter to send') is absent from the HEADLINE composer, so a keyboard "
        "user can't launch from the element a first-time eye lands on"
    )
    assert errors == [], f"console errors during the ⌘+Enter drive: {errors}"


def test_primary_composer_launches_on_ctrl_enter_in_panel(tmp_path, monkeypatch):
    """Ctrl+Enter on the focal #council-prompt fires a 'launch-council' dispatch in the
    REAL side panel — the non-mac keyboard sibling of ⌘+Enter."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        kinds, errors = _drive_keyboard_submit(p, tmp_path, monkeypatch, "Control+Enter")
    assert kinds == ["launch-council"], (
        "Ctrl+Enter on the focal #council-prompt composer fired NO launch in the side "
        f"panel ({kinds!r}) — the non-mac keyboard-submit binding is absent from the "
        "HEADLINE composer (the refine composer ships it; the headline one didn't)"
    )
    assert errors == [], f"console errors during the Ctrl+Enter drive: {errors}"


if __name__ == "__main__":  # pragma: no cover - manual harness
    sys.exit(pytest.main([__file__, "-v", "-s"]))
