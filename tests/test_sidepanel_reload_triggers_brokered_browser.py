"""CLASS-CLOSURE guard for EVERY self-reload trigger in the side panel — not just
the settings toggle.

The self-navigation class (founder "blocked by Chrome": stats-nav #10, rail-council
#5, settings-toggle reload Iter 132) has THREE reload entry points in the shared
launchpad runtime, each of which calls into the SAME __trinityReload() broker:

  1. scheduleLaunchpadReload  — a successful settings/sharing toggle (Iter 132,
     guarded by test_sidepanel_settings_toggle_reload_browser).
  2. the `pageshow` bfcache handler — fires on event.persisted / a back_forward
     restore (the panel page restored from Chrome's back-forward cache).
  3. the poll-completion no-review_path fallback — a council finished but wrote no
     review page, so the launchpad refreshes in place.

In the side panel the launchpad runs inside a SANDBOXED (manifest sandbox.pages)
iframe with an OPAQUE origin, where a bare window.location.reload() is the SAME
self-navigation Chrome blocks for a link click — it lands the WHOLE panel on
chrome-error://chromewebdata/ ("This page has been blocked by Chromium"): composer
gone, app root gone, the panel bricked. __trinityReload() brokers the reload UP to
the shell (sidepanel-bridge.js re-swaps frame.src through the spinner-covered reveal
path); a file:// render CANNOT reproduce the brick (no opaque sandbox origin), which
is why the class kept re-shipping one trigger at a time.

THE COVERAGE GAP THIS CLOSES: the settings-toggle guard drives trigger #1 only. If a
refactor reverted JUST the `pageshow` handler (#2) — an INDEPENDENTLY mutable call
site — to a bare window.location.reload(), the settings-toggle guard stays green
while a back-forward restore bricks the panel, the founder's exact symptom, on a
DIFFERENT trigger. This guard drives the `pageshow` bfcache trigger AND a direct
__trinityReload() in the REAL panel and asserts the reload lands the panel back on
sandbox/launchpad.html (app root + composer present), never a chrome-error brick.

Mutation-proven: revert the `pageshow` handler's __trinityReload() to a bare
window.location.reload() (launchpad_runtime.py) + rebuild, and the post-pageshow
assertion reds with the founder symptom (frame on chrome-error, app root gone).

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
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def _frame_state(frame):
    return frame.evaluate(
        "()=>{const b=document.body.innerText||'';return {"
        " url:location.href,"
        " isLaunchpad:location.href.includes('sandbox/launchpad.html'),"
        " chromeError:location.href.startsWith('chrome-error'),"
        " blocked:b.toLowerCase().includes('blocked'),"
        " hasApp:!!document.getElementById('launchpad-app'),"
        " composer:!!document.querySelector('textarea,.composer'),"
        " rawLeak:(document.body.innerHTML||'').includes('{{')};}"
    )


def test_pageshow_and_direct_reload_triggers_broker_and_dont_brick_panel(tmp_path, monkeypatch):
    """The `pageshow` bfcache reload trigger AND a direct __trinityReload() must both
    re-mount the launchpad IN the panel — never brick it to a chrome-error page."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox opaque-origin path"
            )
            pre = _frame_state(lf)
            assert pre["isLaunchpad"] and pre["hasApp"], f"panel didn't start on the launchpad: {pre}"

            # ── Trigger #2: the bfcache `pageshow` handler ──────────────────────
            # Restoring the panel page from Chrome's back-forward cache fires
            # pageshow{persisted:true} → __trinityReload(). A bare reload here would
            # self-navigate the opaque-origin sandbox iframe → "blocked by Chromium".
            lf.evaluate(
                "()=>{window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));}"
            )
            page.wait_for_timeout(3800)  # brokered reload + spinner-covered re-mount
            after = page.frames[-1]
            ps = _frame_state(after)
            assert not ps["chromeError"], (
                "the bfcache pageshow reload BRICKED the panel to a chrome-error page "
                f"(opaque-origin self-nav block — 'blocked by Chromium'): {ps['url']!r}"
            )
            assert not ps["blocked"], (
                "the bfcache pageshow reload shows 'blocked by Chrome' — the reload "
                "wasn't brokered through the shell (a back-forward restore bricks the panel)"
            )
            assert ps["isLaunchpad"], (
                f"the bfcache pageshow reload did not re-mount the in-panel launchpad: {ps['url']!r}"
            )
            assert ps["hasApp"], (
                "the launchpad app root vanished after the bfcache pageshow reload — composer gone"
            )
            assert ps["composer"], (
                "the composer vanished after the bfcache pageshow reload — the panel bricked"
            )
            assert not ps["rawLeak"], "raw {{ }} leaked after the bfcache pageshow reload (app un-mounted)"

            # ── Direct __trinityReload() — the broker every trigger funnels into ──
            after.evaluate("()=>{ if (typeof __trinityReload === 'function') __trinityReload(); }")
            page.wait_for_timeout(3800)
            direct = _frame_state(page.frames[-1])
            assert not direct["chromeError"], (
                f"a direct __trinityReload() bricked the panel to a chrome-error page: {direct['url']!r}"
            )
            assert direct["isLaunchpad"] and direct["hasApp"] and direct["composer"], (
                f"a direct __trinityReload() did not re-mount the in-panel launchpad: {direct}"
            )
            assert not direct["rawLeak"], "raw {{ }} leaked after a direct __trinityReload()"
        finally:
            ctx.close()
