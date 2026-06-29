"""A successful settings toggle in the side panel must reload the launchpad IN
PLACE — never brick the whole panel to "blocked by Chromium".

Founder-class bug (driven 2026-06-19): the settings sharing/telemetry toggle is a
privacy control the modal promises you can use "anytime". On a SUCCESSFUL apply,
triggerSettingsAction → scheduleLaunchpadReload → window.location.reload(). In the
Chrome side panel the launchpad runs inside a SANDBOXED (manifest sandbox.pages)
iframe with an OPAQUE origin, and a bare window.location.reload() is the SAME
self-navigation Chrome blocks for a link click or window.location.assign — so the
reload landed the panel on chrome-error://chromewebdata/ ("This page has been
blocked by Chromium"): composer gone, app root gone, the entire panel bricked by a
privacy toggle. Same root-cause CLASS as the stats-nav (#10) and rail-council (#5)
"blocked by Chrome" bugs — a sandboxed page must broker every navigation/reload UP
to the shell, which owns the iframe and CAN swap its src.

The fix routes scheduleLaunchpadReload (and the bfcache pageshow reload + the
poll-completion no-review_path fallback) through __trinityReload(), which in the
sandbox postMessages a re-nav of the CURRENT page up to sidepanel-bridge.js (the
shell re-swaps frame.src through the spinner-covered reveal path) and falls back to
a normal window.location.reload() on file:///localhost.

This drives the REAL extension panel — the ONLY surface with the sandbox opaque
origin that makes the bare reload "blocked" (a file:// render can't reproduce it,
which is why the bug shipped). It stubs window.__TRINITY_DISPATCH__ to return a
SUCCESS result for the settings action (so scheduleLaunchpadReload actually fires)
WITHOUT touching any real extension/host/council. Mutation-provable: revert
scheduleLaunchpadReload to a bare window.location.reload() and the post-toggle frame
lands on chrome-error (url contains 'chrome-error', body says "blocked", the app
root + composer vanish) → every assertion reds.

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


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host, load the real extension, open
    the side panel, return (ctx, ext_id, page) after the launchpad iframe mounts."""
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
        f"if qk == 'launchpad_data':\n"
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
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def test_settings_toggle_success_reloads_panel_in_place_not_blocked(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Open settings via the gear button.
            lf.locator("button[aria-label='Open settings']").first.click(timeout=5000)
            page.wait_for_timeout(500)
            modal = lf.evaluate(
                "()=>{const m=document.querySelector('.settings-modal');"
                "const r=m?m.getBoundingClientRect():null;"
                "return {visible: !!(r && r.width>0 && r.height>0),"
                " hasToggle: !!document.querySelector('.sharing-toggle .toggle-slider')};}"
            )
            assert modal["visible"], "settings modal did not open in the panel"
            assert modal["hasToggle"], "no sharing toggle rendered in the settings modal"

            # Stub the dispatcher to SUCCEED for the settings action — this is what
            # makes triggerSettingsAction call scheduleLaunchpadReload (the reload
            # that bricked the panel). No real extension/host/council is touched.
            lf.evaluate(
                "()=>{window.__TRINITY_DISPATCH__={dispatch:function(o){"
                "var r={tier:'extension',ok:true,response:{ok:true}};"
                "if(o&&o.onResult)o.onResult(r);return Promise.resolve(r);}};}"
            )

            # Click the visible toggle slider (the <input> itself is opacity:0).
            lf.locator(".sharing-toggle .toggle-slider").first.click(timeout=5000)
            # scheduleLaunchpadReload fires after ~1400ms → the brokered reload +
            # the shell's spinner-covered re-mount take a couple seconds.
            page.wait_for_timeout(3500)

            af = page.frames[-1]
            state = af.evaluate(
                "()=>{const r=document.getElementById('launchpad-app');"
                "return {url: location.href,"
                " blocked: (document.body.innerText||'').toLowerCase().includes('blocked'),"
                " chromeError: (location.href||'').includes('chrome-error'),"
                " rawLeak: (document.body.innerHTML||'').includes('{{'),"
                " appRoot: !!r,"
                " composerVisible: (()=>{const t=document.getElementById('council-prompt');"
                "   return !!(t && t.offsetParent!==null);})()};}"
            )
            # The founder symptom: a privacy toggle blanked the whole panel.
            assert not state["chromeError"], (
                "settings toggle bricked the panel to a chrome-error page "
                f"(blocked-by-Chromium self-reload of a sandboxed iframe): {state['url']}"
            )
            assert not state["blocked"], (
                "the panel shows a 'blocked by Chromium' message after a settings toggle "
                "(the sandboxed launchpad self-reloaded instead of brokering through the shell)"
            )
            assert "sandbox/launchpad.html" in state["url"], (
                f"the panel navigated away from the launchpad after a settings toggle: {state['url']}"
            )
            assert state["appRoot"], (
                "the launchpad app root vanished after a settings toggle "
                "(the in-panel reload did not re-mount)"
            )
            assert state["composerVisible"], (
                "the council composer is gone after a settings toggle "
                "(the panel did not recover from the reload)"
            )
            assert not state["rawLeak"], (
                "raw {{ }} leaked after the settings-toggle reload (app un-mounted)"
            )
        finally:
            ctx.close()
