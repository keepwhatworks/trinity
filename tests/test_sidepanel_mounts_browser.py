"""The side-panel launchpad must MOUNT as a real chrome-extension:// page.

This is the guard that the earlier in-extension launchpad lacked. petite-vue
evaluates templates with new Function(); MV3 extension pages forbid 'unsafe-eval',
so a plain chrome-extension://launchpad.html renders raw {{ }} (founder report
2026-06-12). The fix runs the UI inside a manifest `sandbox.pages` iframe (eval
allowed) bridged to chrome.runtime by the CSP-safe shell (sidepanel.html ↔
sidepanel-bridge.js ↔ sandbox/_bridge.js).

The OLD test (`test_extension_launchpad_mounts_from_native_messaging`) served the
page over http://127.0.0.1 — where eval is allowed — so it was a FALSE GREEN that
never caught the CSP problem. This loads the page as a real `chrome-extension://`
page in a Playwright-loaded extension with a stub native host, and asserts the
sandboxed iframe actually mounts (no raw mustache) + fetched its data through the
bridge. If the side panel ever regresses to a non-sandboxed page, this reds.

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


def test_sidepanel_launchpad_mounts_in_sandbox(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Hermetic launchpad_data payload from an isolated home (no real ~/.trinity / PII).
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

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(ud), headless=False,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
            )
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
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
            # Now that we know the id, point the stub host's allowed_origins at it.
            (nm / f"{HOST}.json").write_text(json.dumps({
                "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
                "allowed_origins": [f"chrome-extension://{ext_id}/"],
            }), encoding="utf-8")

            errs: list[str] = []
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append("[pageerror] " + str(e)[:200]))
            page.on("console", lambda m: errs.append(f"[{m.type}] {m.text[:200]}") if m.type == "error" else None)
            page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
            page.wait_for_timeout(4000)  # iframe load + bridge fetch + petite-vue mount

            frame = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert frame, f"the sandboxed launchpad iframe never loaded; frames={[f.url for f in page.frames]}"
            s = frame.evaluate(
                "() => ({"
                " raw: (document.body.innerHTML||'').includes('{{'),"
                " fallback: (document.body.innerText||'').includes('reach the local Trinity engine'),"
                " composer: !!document.getElementById('council-prompt'),"
                " bodyLen: (document.body.innerText||'').length,"
                "})"
            )
            assert not s["raw"], (
                "the side-panel launchpad shows raw {{ }} — petite-vue did NOT mount. The "
                "sandbox/bridge regressed (this is exactly the MV3 'unsafe-eval' failure)."
            )
            assert not s["fallback"], "the launchpad showed the Native-Messaging fallback — the bridge fetch failed"
            # The mount invariant is the council COMPOSER, not a char count — the
            # chat-UI redesign made the cold-start home a minimal focal column
            # (composer + a couple cards), so a bodyLen floor is a stale proxy.
            assert s["composer"], "the council composer (#council-prompt) never rendered — launchpad did not mount"
            assert s["bodyLen"] > 800, f"launchpad rendered almost nothing (bodyLen={s['bodyLen']})"
            assert not errs, f"JS/console errors in the side panel: {errs[:5]}"
        finally:
            ctx.close()


def test_sidepanel_council_link_opens_in_panel_not_blocked(tmp_path, monkeypatch):
    """Clicking a rail-council link in the side panel must open the sandbox council
    page — NOT a blocked chrome-extension://…/review_pages/… page.

    Founder report (Image #5, 2026-06-16): clicking a council link in the panel hit
    "This page has been blocked by Chrome". Root cause: a sandboxed page (opaque
    origin) cannot self-navigate to another extension page (link OR window.location)
    — Chrome blocks it. The rail's hrefs also pointed at the file:// review_pages
    path, which doesn't exist as a chrome-extension resource. The fix (1) rewrites
    rail hrefs to the ./live_council.html sandbox sibling and (2) brokers the click
    UP to the shell (sidepanel-bridge.js), which swaps the iframe src. This drives
    the REAL panel and asserts the click lands on the in-panel council page.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    # Seed councils so the rail has links (an empty home → empty rail → vacuous test).
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    (tmp_path / "home").mkdir(parents=True)
    seed_synthetic_home.seed(tmp_path / "home")
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    assert "rail-council" in (payload.get("recentSidebarHtml") or ""), (
        "seed produced no rail-council links — the nav guard would be vacuous"
    )
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

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(ud), headless=False,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
            )
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
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

            fl = page.frame_locator("#app")
            fl.locator(".rail-toggle").first.click(timeout=5000)
            page.wait_for_timeout(400)
            lframe = page.frames[-1]
            hrefs = lframe.evaluate(
                "()=>Array.from(document.querySelectorAll('a.rail-council')).map(a=>a.getAttribute('href'))")
            assert hrefs, "no rail-council links rendered in the drawer"
            # The hrefs must point at the sandbox sibling, never the file:// path.
            assert not any("../review_pages/" in (h or "") for h in hrefs), (
                f"a rail-council href still targets ../review_pages/ (blocked in-panel): {hrefs[:3]}"
            )
            assert all((h or "").startswith("./live_council.html") for h in hrefs), hrefs[:3]

            lframe.locator("a.rail-council").first.click(timeout=5000)
            page.wait_for_timeout(2500)
            cframe = page.frames[-1]
            assert "sandbox/live_council.html" in cframe.url, (
                f"council link did not open the in-panel council page; url={cframe.url}"
            )
            assert "chrome-error" not in cframe.url, f"council link was BLOCKED by Chrome: {cframe.url}"
            body = cframe.evaluate("()=>document.body ? document.body.innerText.slice(0,200) : ''") or ""
            assert "blocked" not in body.lower(), f"council page shows a blocked message: {body[:120]!r}"

            # Back-navigation must NOT flash the raw, un-mounted template (founder-
            # caught: navigating back to the launchpad showed literal {{ }} + every
            # v-if section for seconds while the reloaded app waited on the host
            # fetch). The shell must cover the swap with its loading spinner.
            cframe.locator("a[href$='launchpad.html'], a:has-text('Launchpad')").first.click(timeout=5000)
            # Poll for the spinner window — it shows synchronously on the nav message
            # then hides on re-mount, which is FAST with a stub host, so a single
            # fixed-delay check would race it. Assert the cover appeared at all.
            covered = False
            for _ in range(40):
                s = page.evaluate(
                    "()=>{const l=document.getElementById('loading'),a=document.getElementById('app');"
                    "return !!(l && !l.hidden) && !!(a && a.hidden);}")
                if s:
                    covered = True
                    break
                page.wait_for_timeout(25)
            assert covered, "the shell never covered the back-nav with its spinner (raw-template flash)"
            # …and it must recover: the launchpad re-mounts + the shell reveals it.
            page.wait_for_timeout(9000)
            bframe = page.frames[-1]
            assert "sandbox/launchpad.html" in bframe.url, f"did not return to the launchpad: {bframe.url}"
            recovered = page.evaluate(
                "()=>{const l=document.getElementById('loading'),a=document.getElementById('app');"
                "return {loadingHidden: !!(l && l.hidden), appShown: !!(a && !a.hidden)};}")
            assert recovered["loadingHidden"] and recovered["appShown"], (
                f"the shell never revealed the re-mounted launchpad (spinner stuck): {recovered}"
            )
            # petite-vue strips v-scope/v-cloak on mount, so target the root by its
            # stable id. A clean re-mount = composer present, NOT cloaked, no raw {{.
            remount = bframe.evaluate(
                "()=>{const r=document.getElementById('launchpad-app');"
                "return {composer: !!document.getElementById('council-prompt'),"
                " cloaked: r ? r.hasAttribute('v-cloak') : null,"
                " raw: (document.body.innerHTML||'').includes('{{')};}")
            assert remount["composer"] and remount["cloaked"] is False and remount["raw"] is False, (
                f"launchpad did not re-mount cleanly after back-nav: {remount}"
            )
        finally:
            ctx.close()
