"""The settings modal's close affordance must stay REACHABLE in the REAL side panel —
even when the modal card is taller than a short panel viewport.

Founder lineage ([[sidepanel_test_and_modal_close_fix]], 2026-06-15): in the narrow
side panel the settings modal "couldn't be closed" — a centered, non-scrolling overlay
pushed the × off-screen. The fix is the modal's `align-items: flex-start` + `overflow-y:
auto` (launchpad_template.py:998): the card pins to the TOP of the viewport so the ×
(absolute top:16px of the card) stays just below the top edge, and the body scrolls to
reach the lower controls. If anyone reverts to `align-items: center` (or drops the
flex-start), a card taller than a SHORT panel re-centers with a NEGATIVE top offset →
the × lands ABOVE the viewport (closeTop < 0, elementFromPoint == null) → the modal can
never be closed by the ×. That is precisely the founder bug.

A file:// / http render at a TALL window (852px) can't catch this — the 709px card fits,
so the × is always in view. Only the REAL chrome-extension://sidepanel.html sandbox at a
SHORT panel height (the actual Chrome side-panel shape — it shares vertical space and is
often < 600px tall) reproduces the overflow-the-viewport condition. This drives the genuine
opaque-origin panel and asserts: at a short panel the × is IN-VIEW, the elementFromPoint at
its center IS the close button (no scrim click-through / nothing covering it), and clicking
it actually closes the modal. The modal body must also be scrollable so the lower controls
remain reachable.

Mutation-proven: forcing `align-items: center` on the live modal at a 400px panel pushed
the × to top -137.56 (off-screen, elementFromPoint null) — the exact founder symptom.

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
    """Seed a synthetic home, stub the native host (delegating every non-launchpad_data
    query to the REAL capture-host handlers), load the real extension, open the side
    panel, and return (ctx, ext_id, page) after the launchpad mounts."""
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


def test_settings_close_button_reachable_in_short_panel(tmp_path, monkeypatch):
    """At a SHORT panel height where the modal card overflows the viewport, the × close
    button must stay in-view + clickable + actually close the modal — not vanish above
    the top edge (the founder 'can't close the settings modal in the narrow panel' bug).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            # Re-shape to a SHORT side panel (the real Chrome side panel is often
            # < 600px tall; the settings card is ~709px so it overflows).
            page.set_viewport_size({"width": 393, "height": 480})
            page.wait_for_timeout(300)

            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Open the settings modal.
            lf.locator("button[title='Settings']").first.click(timeout=5000)
            page.wait_for_timeout(500)
            mf = page.frames[-1]

            geo = mf.evaluate(
                "()=>{"
                "const vw=document.documentElement.clientWidth,"
                "vh=document.documentElement.clientHeight;"
                "const modal=document.querySelector('.settings-modal');"
                "if(!modal) return {modalPresent:false};"
                "const card=modal.querySelector('.card');"
                "const close=modal.querySelector(\"button[aria-label='Close settings']\");"
                "const cb=close?close.getBoundingClientRect():null;"
                "const cr=card?card.getBoundingClientRect():null;"
                "let inView=false,hitLabel=null;"
                "if(cb){"
                "  const cx=cb.left+cb.width/2,cy=cb.top+cb.height/2;"
                "  inView=cx>=0&&cx<=vw&&cy>=0&&cy<=vh;"
                "  const hit=document.elementFromPoint(cx,cy);"
                "  hitLabel=hit?hit.getAttribute('aria-label'):null;"
                "}"
                "return {modalPresent:true,vw,vh,"
                " closeTop:cb?cb.top:null,closeBottom:cb?cb.bottom:null,closeInView:inView,"
                " hitLabel,"
                " cardOverflows:cr?(cr.bottom>vh):null,"
                " modalScrollable:modal.scrollHeight>modal.clientHeight,"
                " overflowY:getComputedStyle(modal).overflowY};"
                "}"
            )
            assert isinstance(geo, dict)
            assert geo.get("modalPresent") is True, "settings modal did not open in the side panel"
            # Sanity: this test only proves anything if the card actually overflows the
            # short viewport (otherwise the × is trivially in view and a center-revert
            # wouldn't be caught). Pin the precondition so the guard can't go vacuous.
            assert geo.get("cardOverflows") is True, (
                "precondition failed: the settings card must overflow the short panel for "
                f"this guard to bite (vh={geo.get('vh')}, closeBottom={geo.get('closeBottom')})"
            )
            close_top = geo.get("closeTop")
            assert close_top is not None
            assert float(close_top) >= 0, (
                "the settings-modal × close button rendered ABOVE the side-panel viewport "
                f"(top={close_top}) — the founder 'can't close the settings modal in the "
                "narrow panel' bug: a centered, oversized card pushes the × off-screen. The "
                "modal must keep align-items:flex-start so the × stays below the top edge."
            )
            assert geo.get("closeInView") is True, (
                "the settings-modal × close button is OUT of the side-panel viewport — the "
                f"narrow-panel 'can't close the modal' bug (closeTop={close_top})."
            )
            assert geo.get("hitLabel") == "Close settings", (
                "elementFromPoint at the × center is NOT the close button "
                f"(got {geo.get('hitLabel')!r}) — something covers it / it's off-screen, so a "
                "click can't reach it (the founder 'modal won't close' bug)."
            )
            # The lower controls must stay reachable: the overflowing modal must scroll.
            assert geo.get("modalScrollable") is True and geo.get("overflowY") == "auto", (
                "the settings modal does not scroll a card taller than the short panel — the "
                f"lower controls are unreachable (scrollable={geo.get('modalScrollable')}, "
                f"overflowY={geo.get('overflowY')})."
            )

            # And clicking the × actually closes the modal.
            mf.locator("button[aria-label='Close settings']").first.click(timeout=5000)
            page.wait_for_timeout(500)
            af = page.frames[-1]
            still_open = af.evaluate("()=>!!document.querySelector('.settings-modal')")
            assert still_open is False, (
                "clicking the × did NOT close the settings modal in the short side panel."
            )
        finally:
            ctx.close()
