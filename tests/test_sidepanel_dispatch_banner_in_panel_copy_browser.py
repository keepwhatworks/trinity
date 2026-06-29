"""The REACTIVE failed-dispatch banner must not tell a user INSIDE the extension's
own side panel to "Install the Trinity browser extension" / "load the
browser-extension/ folder" — they're plainly already in it.

Found 2026-06-18 driving a forced `tier:'install-prompt'` (reason `no-route`)
dispatch in the REAL extension side panel: clicking Launch surfaced the reactive
`dispatchBannerOpen` banner reading

    NO DISPATCH PATH IS WIRED UP
    Install the Trinity browser extension to dispatch from any platform
    1. Open chrome://extensions … load the browser-extension/ folder. …

— a founder-class contradiction (telling you to install the extension you are
demonstrably running inside). This is the SAME contradiction the PROACTIVE
"Finish setup" card was explicitly fixed for via the `inExtensionPanel` reframe
(launchpad_template.py ~L1268: "In the side panel the user is ALREADY inside the
installed extension, so 'Install the Chrome extension' reads as a contradiction").
The fix landed on the proactive card; the REACTIVE twin — the `dispatchBannerOpen`
banner that fires only after a failed dispatch — was MISSED. It had no
`inExtensionPanel` guard, so it shipped the install pitch into the panel.

The Iter-71 sibling-surface-drift class: a copy/UX fix landed on one surface, the
sibling surface carrying the identical control/affordance was missed.

Root-cause fix: gate the `no-route`/`else` branch of the banner on
`inExtensionPanel`. In the panel a 'no-route' failure means the extension is
present but the local capture host isn't registered yet — so the banner collapses
to the same register-the-host remediation as `native-host-unavailable`
("Extension installed, host not registered" + `trinity-local install-extension`),
never the chrome://extensions sideload steps. The file:// launchpad
(`!inExtensionPanel`) keeps the full install pitch (covered by
test_launchpad_failed_dispatch_browser).

Why this needs a REAL-panel guard: `inExtensionPanel` is `!!window.__TRINITY_HOST_FETCH__`,
which is ONLY set in the opaque-origin sandbox the Chrome side panel actually uses.
A file:// render can never exercise the in-panel branch — it always takes the
`!inExtensionPanel` install-pitch path. If this regressed, every user who hit a
no-route dispatch failure FROM THE PANEL would be told to install the extension
they're sitting inside, while every existing test stayed green.

Mutation-proven: dropping the `|| inExtensionPanel` / `v-else-if="inExtensionPanel"`
guards from the BUNDLED sandbox/launchpad.html lets the install pitch leak back
into the panel → the assertions red with the founder symptom.

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

# A dispatcher that resolves the WORST in-panel failure: tier:'install-prompt'
# (no extension route). On the file:// launchpad this opens the "install the
# extension" banner; in the panel the user is already inside the extension, so
# that copy is a contradiction. probe:'absent' mirrors the real no-route path.
_STUB_NO_ROUTE = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (o) {
      window.__lastDispatch = o && o.extensionAction;
      if (o && o.onResult) setTimeout(function () {
        o.onResult({ tier: 'install-prompt', ok: false, reason: 'no-route' });
      }, 20);
    },
    probe: function () { return Promise.resolve('absent'); },
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


def test_no_route_dispatch_banner_does_not_say_install_extension_in_panel(tmp_path, monkeypatch):
    """A forced no-route dispatch in the REAL side panel must surface a banner that
    does NOT tell the user to install/sideload the extension they're already inside,
    and DOES point at the real remaining step (register the host)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Precondition: this IS the in-panel opaque-origin path (the only context
            # where inExtensionPanel is true). A file:// render would always fail this.
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path; "
                "the inExtensionPanel branch can't be exercised, so the guard would be vacuous"
            )

            lf.evaluate(_STUB_NO_ROUTE)
            lf.fill("#council-prompt", "Compare three rate-limiting strategies")
            lf.locator(".actions button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(700)  # the stub resolves onResult after 20ms

            res = lf.evaluate(
                "()=>{const vw=document.documentElement.clientWidth;"
                "const banner=[...document.querySelectorAll('.card')]"
                ".find(e=>e.offsetParent!==null && /(No dispatch path is wired up|host not registered|Install the Trinity browser extension|capture host just isn't wired)/i.test(e.innerText||''));"
                "const over=[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "return {visible:!!banner, text:banner?banner.innerText.trim():null,"
                " docW:document.documentElement.scrollWidth, vw:vw, over:over,"
                " raw:(document.body.innerHTML||'').includes('{{')};}"
            )

            assert res["visible"], (
                "the reactive failed-dispatch banner never rendered in the panel — the "
                "guard would be vacuous (the no-route dispatch produced no feedback at all)"
            )
            text = res["text"] or ""
            # THE BUG: the install pitch must NOT appear inside the extension's own panel.
            assert "Install the Trinity browser extension" not in text, (
                "the in-panel failed-dispatch banner tells the user to 'Install the Trinity "
                "browser extension' — a contradiction: they're already in its side panel. The "
                "reactive banner was missed by the inExtensionPanel reframe the proactive card got. "
                f"Banner: {text!r}"
            )
            assert "load the" not in text and "browser-extension/ folder" not in text, (
                "the in-panel banner still shows the chrome://extensions sideload steps "
                f"('load the browser-extension/ folder') inside the installed extension: {text!r}"
            )
            assert "chrome://extensions" not in text, (
                f"the in-panel banner still points at chrome://extensions sideloading: {text!r}"
            )
            # And it DOES name the real remaining step: register the capture host.
            assert "install-extension" in text, (
                "the in-panel no-route banner dropped the actionable remediation — it should "
                f"point at `trinity-local install-extension` to register the host: {text!r}"
            )
            assert "host" in text.lower(), (
                f"the in-panel banner doesn't explain the host-registration gap: {text!r}"
            )
            # Paint: no leak / no overflow at the narrow panel width.
            assert not res["raw"], "raw {{ }} leaked — the launchpad app lost its mount"
            assert int(res["docW"]) <= int(res["vw"]) + 1 and not res["over"], (
                f"the failed-dispatch banner overflows the {res['vw']}px panel: "
                f"docW={res['docW']} overflowing={res['over']}"
            )
            assert errors == [], f"console errors during the in-panel no-route dispatch: {errors}"
        finally:
            ctx.close()


if __name__ == "__main__":  # pragma: no cover - manual harness
    sys.exit(pytest.main([__file__, "-v", "-s"]))
