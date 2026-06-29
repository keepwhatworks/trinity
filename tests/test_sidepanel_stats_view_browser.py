"""The side-panel "View full stats" link must SHOW stats — not "blocked by Chrome".

Founder report 2026-06-17 (Image #10): clicking the launchpad's stats link in the
Chrome side panel landed on "This page has been blocked by Chrome". Root cause —
the SAME class as the council-link bug (Image #5): a sandboxed page (opaque origin)
cannot self-navigate to another extension page, and there is NO sandbox/stats.html
(the launchpad ALWAYS contains every card; /stats is a CSS root-class toggle, not a
separate page). The bare `<a href="./stats.html">` therefore self-navigated → blocked.

The fix flips the view IN PLACE in the sandbox (setLaunchpadView swaps
lp-view-home ↔ lp-view-stats, no nav), and the memory-viewer deep links
(../portal_pages/*.html, reachable only once /stats works) get a graceful escape
instead of their own blocked self-nav.

This drives the REAL extension panel (the only thing that exercises the sandbox's
opaque origin — a file:// render can't, which is why the bug shipped) and asserts:
clicking the stats link does NOT navigate / block, flips the root to lp-view-stats,
and reveals stats content. Mutation-provable: revert the interceptor and the click
self-navigates → the frame URL changes / blocks and the assertion reds.

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
    the side panel, and return (ctx, ext_id, page) after the launchpad mounts."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    # Seed so /stats has real cards (routing, evals, memory) to reveal.
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    # launchpad_data uses the prebuilt payload (fast); every OTHER query kind
    # delegates to the REAL capture-host handlers so thread_manifest /
    # council_outcome answer with the genuine seeded data — letting a clicked
    # rail council hydrate end-to-end (a hand-rolled stub would fabricate a
    # false "Could not load council outcome", masking the real behaviour).
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


def test_stats_link_flips_view_in_panel_not_blocked(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Pre-state: home view, the stats link is present.
            pre = lf.evaluate(
                "()=>{const r=document.getElementById('launchpad-app');"
                "return {cls:r?r.className:'', hasLink:!!document.querySelector('a[href$=\"stats.html\"]')};}"
            )
            assert pre["hasLink"], "no ./stats.html link rendered on the side-panel home"
            assert "lp-view-home" in pre["cls"], f"launchpad didn't start on home: {pre['cls']}"

            # Click "View full stats". The url must NOT change (in-place toggle) and
            # must NOT become a chrome-error/blocked page.
            url_before = lf.url
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(800)
            sf = page.frames[-1]
            assert sf.url == url_before, f"stats link NAVIGATED (should toggle in place): {sf.url}"
            assert "chrome-error" not in (sf.url or ""), f"stats link BLOCKED by Chrome: {sf.url}"

            post = sf.evaluate(
                "()=>{const r=document.getElementById('launchpad-app');"
                "const hdr=[...document.querySelectorAll('.stats-card h1')].map(h=>h.textContent).join(' ');"
                "const statsCard=[...document.querySelectorAll('.stats-card')].some(c=>c.offsetParent!==null);"
                "return {cls:r?r.className:'', blocked:(document.body.innerText||'').toLowerCase().includes('blocked'),"
                " raw:(document.body.innerHTML||'').includes('{{'), statsVisible:statsCard, hdr};}"
            )
            assert "lp-view-stats" in post["cls"], f"root did not flip to the stats view: {post['cls']}"
            assert "lp-view-home" not in post["cls"], f"home view still active after toggle: {post['cls']}"
            assert post["statsVisible"], "no .stats-card became visible after the toggle"
            assert "Trinity · stats" in post["hdr"], f"stats header didn't render: {post['hdr']!r}"
            assert not post["blocked"], "the panel shows a 'blocked' message after clicking stats"
            assert not post["raw"], "raw {{ }} leaked (app un-mounted) after the toggle"

            # And "← Back to the council" flips back to the home composer (no nav).
            sf.locator(".stats-card a[href$='launchpad.html']").first.click(timeout=5000)
            page.wait_for_timeout(600)
            bf = page.frames[-1]
            assert bf.url == url_before, f"back-link NAVIGATED (should toggle in place): {bf.url}"
            back = bf.evaluate(
                "()=>{const r=document.getElementById('launchpad-app');"
                "return {cls:r?r.className:'', composerVisible:(()=>{const t=document.getElementById('council-prompt');"
                "return !!(t && t.offsetParent!==null);})()};}"
            )
            assert "lp-view-home" in back["cls"], f"back-link did not return to home: {back['cls']}"
            assert back["composerVisible"], "the council composer isn't visible after returning home"
        finally:
            ctx.close()


def test_rail_council_click_opens_live_council_not_blocked(tmp_path, monkeypatch):
    """Clicking a recent council in the side-panel rail drawer must broker the nav
    through the shell to the SANDBOX live_council page (./live_council.html?thread_id=…)
    and hydrate the council — NOT land on 'blocked by Chrome' and NOT navigate to the
    ../review_pages/ chrome-extension path (which IS blocked).

    The rail href the Python builder emits is `../review_pages/live_council.html?
    thread_id=<root>`; the sidepanel build rewrites it to `./live_council.html?…` and
    the in-panel click interceptor hands it UP to the shell, which re-roots it under
    sandbox/. None of that round trip (rewrite + broker + thread hydration) had any
    real-browser coverage — a file:// render can't reproduce the sandbox opaque origin
    that makes the bare nav 'blocked by Chrome'. Mutation-provable: revert the nav
    broker / the build href rewrite and the click self-navigates the sandbox iframe →
    chrome-error (blocked), or lands ../review_pages/… (also blocked), and the
    thread_id-bearing live_council URL never appears → this reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Open the rail drawer (where the recent councils live).
            page.frame_locator("#app").locator(".rail-toggle").first.click(timeout=5000)
            page.wait_for_timeout(500)
            rf = page.frames[-1]
            rails = rf.evaluate(
                "()=>[...document.querySelectorAll('.council-rail .rail-council')].map("
                "a=>({href:a.getAttribute('href')}))"
            )
            assert rails, "no recent councils rendered in the side-panel rail (seed/empty-state regression?)"
            # The sidepanel build must have rewritten the host's ../review_pages/ href
            # to the sandbox sibling — otherwise the click self-navs to a blocked path.
            assert all((r.get("href") or "").startswith("./live_council.html?thread_id=") for r in rails), (
                f"rail hrefs not rewritten to the sandbox live_council sibling: {rails}"
            )

            # Click the first rail council. The broker swaps the iframe src to
            # sandbox/live_council.html?thread_id=… (a VISIBLE reload).
            rf.locator(".council-rail .rail-council").first.click(timeout=5000)
            page.wait_for_timeout(3500)  # nav-broker swap + thread manifest + outcome hydrate

            cf = page.frames[-1]
            state = cf.evaluate(
                "()=>({"
                "url: location.href,"
                "blocked: (document.body.innerText||'').toLowerCase().includes('blocked'),"
                "rawLeak: (document.body.innerHTML||'').includes('{{'),"
                "failed: (document.body.innerText||'').includes('Council failed')"
                " || (document.body.innerText||'').includes('Could not load council outcome'),"
                "hasSynthesis: !!document.querySelector('.routing-label, .synthesis, .live-synthesis')"
                " || (document.body.innerText||'').includes('ROUTING LABEL'),"
                "bodyHead: (document.body.innerText||'').slice(0,200)"
                "})"
            )
            assert "chrome-error" not in (cf.url or ""), (
                f"rail-council click was BLOCKED by Chrome (sandbox self-nav): {cf.url}"
            )
            assert not state["blocked"], f"the panel shows a 'blocked' message: {state['bodyHead']!r}"
            assert "sandbox/live_council.html" in (cf.url or ""), (
                f"rail-council click did NOT land the sandbox live_council page (nav broker failed): {cf.url}"
            )
            assert "thread_id=council_syn" in (cf.url or ""), f"thread_id lost in the nav: {cf.url}"
            assert "/review_pages/" not in (cf.url or ""), (
                f"navigated to the BLOCKED ../review_pages/ chrome-extension path: {cf.url}"
            )
            assert not state["rawLeak"], "raw {{ }} leaked — the live_council app never mounted"
            # The clicked thread must actually HYDRATE (manifest + outcome), not show
            # a false "Council failed" banner. This is the rail's whole point.
            assert not state["failed"], (
                f"rail-council opened a FALSE 'Council failed' page: {state['bodyHead']!r}"
            )
            assert state["hasSynthesis"], (
                f"the opened council rendered no synthesis/routing-label content: {state['bodyHead']!r}"
            )
        finally:
            ctx.close()
