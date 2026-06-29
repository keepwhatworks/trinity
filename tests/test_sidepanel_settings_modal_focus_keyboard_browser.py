"""The settings modal's KEYBOARD-MODAL mechanics must hold in the REAL side panel.

Founder lineage: the keyboard focus mechanics (Iter-95 file:// guard,
test_launchpad_settings_modal_focus_trap_browser.py) were fixed in the SHARED
launchpad template — `openSettings` moves focus INTO the modal, `trapSettingsTab`
keeps Tab inside it, `closeSettings` returns focus to the gear, and Esc closes it.
That guard drives the FILE:// surface (a plain top-level document) and asserts the
file:// header "exercises the same code the side panel renders".

But the side panel is NOT a plain document — it's a `<iframe sandbox>` (opaque
origin, relaxed CSP for petite-vue's eval) hosted inside the CSP-safe shell page
(sidepanel.html → sidepanel-shell.js + sidepanel-bridge.js). The `keydown` listener
that drives the trap is registered on the IFRAME's `document`; keyboard events,
focus, and `document.activeElement` all live in that sandboxed sub-frame, behind a
postMessage bridge — exactly the environment a file:// render canNOT reproduce, and
exactly where the loop's worst bugs have hidden (the nav-broker "blocked by Chrome",
the blank provider row, the #6 spinner — all green on the file:// sweep, all real in
the panel). A future change that captures Escape in the shell, or that breaks the
iframe-document keydown wiring, would leave the file:// trap guard GREEN while a
keyboard / screen-reader user is stranded in the real panel: Tab walks the page
behind the backdrop, or Esc no longer closes the modal, or focus never returns.

This drives the genuine `chrome-extension://…/sidepanel.html` (the real sandboxed
panel) and asserts, IN THE PANEL:
  1. opening the gear via the KEYBOARD (Enter) moves focus INTO the modal,
  2. Tab never escapes the open modal into the page behind the backdrop (and
     Shift+Tab wraps too),
  3. ESC (not the ×) closes the modal AND returns focus to the gear trigger.

Mutation-proven: revert `trapSettingsTab` to a no-op (so Tab walks out) and assertion
(2) reds in the panel; revert the Esc branch / `_settingsTrigger` return and (3) reds.

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


def _boot_panel(p, tmp_path, monkeypatch, width=393, height=852):
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
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def test_settings_modal_keyboard_trap_and_esc_return_in_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel: Enter on the gear moves focus into the modal,
    Tab/Shift+Tab never escape it, and Esc closes it AND returns focus to the gear."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Focus the gear and open the modal with the KEYBOARD (Enter), the way a
            # keyboard user would — not a mouse click.
            gear = lf.locator("button[aria-label='Open settings']").first
            gear.focus()
            focused = lf.evaluate(
                "() => document.activeElement.getAttribute('aria-label')"
            )
            assert focused == "Open settings", (
                f"could not focus the settings gear in the panel (active={focused!r})"
            )
            gear.press("Enter")
            page.wait_for_timeout(500)

            # (1) focus must move INTO the modal on open — not stay on the gear behind
            # the backdrop (or the first Tab starts outside + walks the page).
            opened = lf.evaluate(
                "() => { const m = document.querySelector('.settings-modal'), "
                "a = document.activeElement; "
                "return { present: !!m, inModal: !!(m && m.contains(a)), "
                "role: m ? m.getAttribute('role') : null, "
                "ariaModal: m ? m.getAttribute('aria-modal') : null }; }"
            )
            assert opened["present"], "settings modal did not open on keyboard Enter in the panel"
            assert opened["role"] == "dialog" and opened["ariaModal"] == "true", (
                "settings modal missing role=dialog/aria-modal in the panel: "
                f"role={opened['role']!r} aria-modal={opened['ariaModal']!r}"
            )
            assert opened["inModal"], (
                "focus did NOT move into the settings modal on open in the REAL panel — "
                "it stayed behind the backdrop, so the first Tab walks the sandboxed page "
                "(the panel keyboard-modal gap a file:// render can't catch)."
            )

            # Count the modal's focusables so the trap precondition can't be vacuous
            # (a degenerate 0/1-control modal would 'trap' trivially).
            nfoc = lf.evaluate(
                "() => { const m = document.querySelector('.settings-modal'); "
                "return Array.from(m.querySelectorAll("
                "'a[href], button:not([disabled]), input:not([disabled]), "
                "textarea:not([disabled]), select:not([disabled]), "
                "[tabindex]:not([tabindex=\\\"-1\\\"])')).filter("
                "el => el.offsetParent !== null || el === document.activeElement).length; }"
            )
            assert nfoc >= 3, (
                f"settings modal has only {nfoc} focusable control(s) in the panel — too "
                "few for the Tab-trap assertion to be meaningful (precondition)."
            )

            # (2) the headline: Tab must NEVER escape the open modal into the page behind
            # the backdrop. Press more times than there are controls so a leak surfaces.
            escapes = []
            for i in range(nfoc + 6):
                page.keyboard.press("Tab")
                page.wait_for_timeout(40)
                d = lf.evaluate(
                    "() => { const m = document.querySelector('.settings-modal'), "
                    "a = document.activeElement; "
                    "return { open: !!m, inModal: !!(m && m.contains(a)), "
                    "tag: a.tagName, "
                    "label: (a.getAttribute('aria-label')||a.innerText||a.value||'').slice(0,40) }; }"
                )
                if d["open"] and not d["inModal"]:
                    escapes.append((i, d["tag"], d["label"]))
            assert not escapes, (
                "Tab ESCAPED the open settings modal into the page behind the backdrop "
                f"IN THE REAL PANEL ({len(escapes)} of {nfoc + 6} presses) — a keyboard / "
                "screen-reader user can operate the obscured sandboxed launchpad while the "
                f"modal claims to be modal. Sample escapes: {escapes[:5]}"
            )

            # Shift+Tab must also stay trapped (wrap at the first control).
            for _ in range(nfoc + 4):
                page.keyboard.press("Shift+Tab")
                page.wait_for_timeout(30)
            shift_inside = lf.evaluate(
                "() => { const m = document.querySelector('.settings-modal'), "
                "a = document.activeElement; return !!(m && m.contains(a)); }"
            )
            assert shift_inside, (
                "Shift+Tab escaped the open settings modal in the real panel."
            )

            # (3) ESC (the keyboard close path, distinct from the × click guard) must
            # close the modal AND return focus to the gear trigger.
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            closed = lf.evaluate(
                "() => ({ open: !!document.querySelector('.settings-modal'), "
                "isBody: document.activeElement === document.body, "
                "label: document.activeElement.getAttribute('aria-label') })"
            )
            assert not closed["open"], (
                "Escape did NOT close the settings modal in the REAL side panel — the "
                "keydown-on-iframe-document Esc path is broken in the sandboxed panel "
                "(a file:// render can't catch this)."
            )
            assert not closed["isBody"] and closed["label"] == "Open settings", (
                "closing the settings modal with Escape dumped focus to <body> instead of "
                f"returning it to the gear trigger in the real panel: {closed}"
            )
        finally:
            ctx.close()
