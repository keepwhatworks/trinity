"""The OPEN mobile councils DRAWER must trap keyboard focus inside the rail — driven
end-to-end in the REAL Chrome side panel.

The defect this guards (the asymmetric-sibling leak):
  On narrow widths (phones + the side panel) the council-history rail is an
  off-canvas DRAWER over a SCRIM. The scrim pointer-blocks the page: a TAP on the
  obscured composer / settings gear is caught by the scrim, so the page is
  intentionally non-interactive while the drawer is open (a sighted mouse user
  cannot reach it). But the drawer carried NO Tab trap — KEYBOARD focus walked
  straight OUT of the rail to the hamburger, the settings gear, and the page links
  BEHIND the scrim. A keyboard / screen-reader user could operate obscured,
  pointer-blocked content while a mouse user could not.

  The settings modal ALREADY traps this exact leak (launchpad_template.py
  trapSettingsTab + the keydown handler comment names it: "a keyboard/SR user could
  operate the obscured page while the modal claimed to be modal"). The mobile drawer
  — which ALSO presents a scrim that pointer-blocks the page — was the sibling that
  never got the trap. The fix adds trapRailTab() (gated on railOpen, which is true
  ONLY on the narrow drawer, so desktop's persistent sidebar is untouched), moves
  focus into the rail on open, and returns focus to the hamburger on close.

This drives the REAL extension side panel with a STUBBED dispatch (resolves ok,
nothing hits a real council) over the REAL delegating capture-host (so the rail is
populated with the seeded synthetic councils). It opens the drawer via the
hamburger, focuses the search filter, presses Tab repeatedly, and asserts focus
NEVER lands on a control OUTSIDE the rail+toggle — i.e. never on the obscured page
behind the scrim.

Mutation-proven: remove the `if (e.key === 'Tab' && this.railOpen) ...` line from the
keydown handler (the un-fixed shape) and rebuild — the Tab walk leaks to
PAGE:'Open settings' / a page link and the assertion goes red.

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

_STUB_OK = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) { if (opts && opts.onResult) opts.onResult({ ok: true, tier: 'extension' }); },
    probe: function () { return Promise.resolve('present'); },
    onStateChange: function () {}, subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Boot the REAL side panel over a delegating capture-host stub, seeded so the
    council rail is populated (the drawer has rows + the focusable anchors the leak
    walked through)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import _assemble_page_data, build_launchpad_payload

    _, recent_sidebar = _assemble_page_data(force_live_page=False)
    payload = build_launchpad_payload()
    payload["recentSidebarHtml"] = recent_sidebar
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
    page.add_init_script(_STUB_OK)
    page.set_viewport_size({"width": 393, "height": 760})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)
    return ctx, ext_id, page


def test_open_drawer_traps_keyboard_focus_in_panel(tmp_path, monkeypatch):
    """With the mobile drawer open over the scrim, Tab must stay inside the rail —
    never leak to the obscured settings gear / composer / page links behind the
    scrim (the keyboard sibling of the pointer-block the scrim already provides)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert lf is not None, "the launchpad iframe never loaded in the panel"

            # PRECONDITION (non-vacuous): the rail must be populated, so the page
            # behind the scrim has reachable focusables AND the scrim is present.
            rail_rows = lf.evaluate("()=>document.querySelectorAll('.council-rail a.rail-council').length")
            assert rail_rows >= 1, (
                f"the council rail rendered no rows (got {rail_rows}) — the seed/hydrate regressed, "
                "the focus-leak precondition isn't set up"
            )

            ham = lf.locator("button.rail-toggle")
            assert ham.count() == 1, "the rail hamburger toggle is missing"
            ham.first.click(timeout=5000)
            page.wait_for_timeout(700)
            assert lf.evaluate("()=>document.body.classList.contains('rail-open')"), (
                "the hamburger did not open the rail drawer"
            )
            assert lf.evaluate("()=>!!document.querySelector('.rail-scrim')"), (
                "the scrim is absent — the obscured-page precondition isn't set"
            )

            # PRECONDITION: the scrim pointer-blocks the page (a tap on the gear is
            # caught by the scrim). This is what makes the keyboard leak a real
            # asymmetry — mouse can't reach the page, keyboard must not either.
            gear_hit = lf.evaluate(
                "()=>{const g=document.querySelector('[aria-label=\"Open settings\"]');"
                " if(!g) return null; const b=g.getBoundingClientRect();"
                " const h=document.elementFromPoint(b.x+b.width/2, b.y+b.height/2);"
                " return h ? (h.classList.contains('rail-scrim')?'scrim':h.className||h.tagName) : null;}"
            )
            assert gear_hit == "scrim", (
                f"the scrim does NOT pointer-block the settings gear (hit={gear_hit!r}) — "
                "the overlay isn't behaving as a scrim, so the keyboard-leak premise is off"
            )

            # Focus the rail's search filter, then Tab a full lap. Focus must NEVER
            # land on a control OUTSIDE the rail + the hamburger (the close control).
            lf.evaluate("()=>{const i=document.querySelector('.council-rail .rail-filter'); if(i) i.focus();}")
            leaks = []
            for _ in range(12):
                page.keyboard.press("Tab")
                page.wait_for_timeout(50)
                where = lf.evaluate(
                    "()=>{const a=document.activeElement; if(!a) return 'NONE';"
                    " const rail=document.querySelector('.council-rail');"
                    " const ham=document.querySelector('.rail-toggle');"
                    " const inRail = (rail && rail.contains(a)) || a===ham;"
                    " const label=(a.getAttribute('aria-label')||a.id||a.tagName||'').toString().slice(0,40);"
                    " return (inRail?'IN:':'LEAK:')+label;}"
                )
                if where.startswith("LEAK:"):
                    leaks.append(where)

            assert not leaks, (
                "keyboard focus LEAKED out of the open drawer to the page behind the scrim "
                f"({leaks!r}) — a keyboard/SR user can operate the obscured, pointer-blocked "
                "composer / settings gear / page links while a mouse user cannot. The mobile "
                "councils drawer needs the same Tab trap the settings modal has (trapRailTab "
                "gated on railOpen)."
            )

            # Sanity: the trap actually KEEPS focus inside (it didn't just lose focus
            # to <body>). At least one Tab landed on a real rail control.
            final = lf.evaluate(
                "()=>{const a=document.activeElement; const rail=document.querySelector('.council-rail');"
                " const ham=document.querySelector('.rail-toggle');"
                " return ((rail&&rail.contains(a))||a===ham) && a.tagName!=='BODY';}"
            )
            assert final, "after a Tab lap focus is not on a real rail control — the trap dropped focus to <body>"
        finally:
            ctx.close()
