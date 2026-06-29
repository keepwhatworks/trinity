"""The code-block "Copy" badge must clear the 44px touch-target floor in the
side panel.

Founder symptom + audit (UX sweep): on the touch-width Chrome SIDE PANEL the
COLD taste card renders a `trinity-local lens` command with a `.copy-badge`
overlay — the brand-new user's PRIMARY "build your lens" affordance with no CLI
open. The visible pill is ~19px tall (HALF the WCAG 2.5.5 / Apple HIG 44px
floor the founder explicitly flagged: "every action button clear 44px on touch
widths"). The badge is a corner overlay on a code block, so it can't simply grow
to 44px without overlapping the code; the fix extends the HIT AREA to 44x44 with
a transparent `::before` pseudo-element centered on the pill (the same hit-area
pattern the sidepanel tip x + live-council Quote button use) — the visible chip
stays compact, the tap target clears 44px.

This is the un-fixed sibling of the Iter-152 `.btn` tap-target class: that fix
floored the hand-maintained shells (sidepanel.html / popup.html); the
`.copy-badge` lives in the GENERATED Vue launchpad (launchpad_template.py),
which renders INSIDE the sandboxed panel iframe — never inherited the floor.

Real-browser, rendered-geometry guard (not a string check): boots the REAL
unpacked extension, opens the side panel COLD, scrolls the copy badge into view,
and measures (a) the visible pill stays compact AND (b) the clickable hit area —
probed via document.elementFromPoint at offsets that exceed the visible pill —
spans at least 40px vertically (the 44px ::before box, minus a probe-resolution
margin). MUTATION-PROVEN to red when the ::before hit-area extender is removed.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import stat
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"


def _boot_panel(ctx, nm, hp):
    """Wait for the extension service worker, point the stub host at it, return
    the extension id."""
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
    (nm / f"{HOST}.json").write_text(
        json.dumps({
            "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
            "allowed_origins": [f"chrome-extension://{ext_id}/"],
        }),
        encoding="utf-8",
    )
    return ext_id


def test_copy_badge_clears_44px_tap_target(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # COLD isolated home (no lens) → the taste card renders its empty-state with
    # the `trinity-local lens` copy badge. Never touches the real ~/.trinity.
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    stub = (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "qk = (msg or {}).get('query_kind')\n"
        f"out = open({str(pl)!r}).read().encode() if qk=='launchpad_data' else json.dumps({{'ok':True}}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )
    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(stub, encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # Narrow touch widths — the draggable side panel band.
    for width in (393, 320):
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    str(ud), headless=False,
                    args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
                )
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                ext_id = _boot_panel(ctx, nm, hp)
                page = ctx.new_page()
                page.set_viewport_size({"width": width, "height": 850})
                page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
                page.wait_for_timeout(4000)

                frame = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
                assert frame, f"launchpad iframe never loaded; frames={[f.url for f in page.frames]}"

                # Precondition (non-vacuous): the cold taste-card copy badge IS
                # rendered + visible.
                found = frame.evaluate(
                    "() => !!([...document.querySelectorAll('.copy-badge')].find(b => b.offsetParent !== null))"
                )
                assert found, (
                    f"@ {width}px: no visible .copy-badge in the cold side panel — the cold taste "
                    "card's `trinity-local lens` copy affordance did not render; the precondition "
                    "for the tap-target guard is missing (fixture/state regressed)."
                )

                # Scroll the badge into the viewport (elementFromPoint only hits
                # visible points), then measure the visible pill + the extended
                # clickable hit band.
                frame.evaluate(
                    "() => { const b=[...document.querySelectorAll('.copy-badge')].find(x=>x.offsetParent!==null);"
                    " b && b.scrollIntoView({block:'center'}); }"
                )
                page.wait_for_timeout(400)

                m = frame.evaluate(
                    """() => {
                        const b = [...document.querySelectorAll('.copy-badge')].find(x => x.offsetParent !== null);
                        const rect = b.getBoundingClientRect();
                        const cx = rect.left + rect.width / 2;
                        const cy = rect.top + rect.height / 2;
                        // Probe the vertical hit band: how far above/below the
                        // visible pill center does a click still land on the badge?
                        // The transparent ::before extends this to the 44px box.
                        let up = 0, down = 0;
                        for (let dy = 0; dy <= 24; dy++) {
                            const el = document.elementFromPoint(cx, cy - dy);
                            if (el && el.closest && el.closest('.copy-badge')) up = dy; else break;
                        }
                        for (let dy = 0; dy <= 24; dy++) {
                            const el = document.elementFromPoint(cx, cy + dy);
                            if (el && el.closest && el.closest('.copy-badge')) down = dy; else break;
                        }
                        return { visibleH: Math.round(rect.height), hitBand: up + down };
                    }"""
                )

                # The visible pill is compact by design (corner overlay) — assert
                # it really is small so the extended hit area is what's load-bearing
                # (a guard that passed because the pill itself grew to 44px would be
                # the wrong fix; this proves the hit-area extender is the mechanism).
                assert m["visibleH"] < 30, (
                    f"@ {width}px: the .copy-badge visible pill is {m['visibleH']}px — expected the "
                    "compact corner-overlay pill (<30px). If it grew, this guard's premise changed."
                )
                # The BITE: the clickable hit band must span >=40px (the 44px
                # ::before box, minus a 1px-resolution + sub-pixel margin). Pre-fix
                # the band == the ~19px pill → RED. Founder symptom in the message.
                assert m["hitBand"] >= 40, (
                    f"@ {width}px: SUB-44px tap target — the code-block 'Copy' badge "
                    f"(`trinity-local lens`, the cold side-panel's primary lens-build affordance) "
                    f"has a clickable hit band of only {m['hitBand']}px (visible pill {m['visibleH']}px) "
                    "— under the 44px touch floor (WCAG 2.5.5 / Apple HIG). The ::before hit-area "
                    "extender on .copy-badge is missing or ineffective."
                )
            finally:
                ctx.close()
