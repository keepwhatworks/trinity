"""A /stats memory-viewer chip click in the side panel must give FEEDBACK when its
open-launchpad dispatch fails — not a silent dead click.

Found 2026-06-19 driving the REAL extension side panel @ 393px on the /stats view:
the four "memory-chip" links (core.md / lens.md / topics.json / vocabulary.md) and
the routing/picks/topics deep links all point at ../portal_pages/memory.html — a
page that does NOT exist in the sandbox, so a self-nav there is "blocked by Chrome".
The sandbox click interceptor (launchpad_runtime.py PORTAL_RX) correctly stops the
blocked self-nav and instead dispatches `open-launchpad` to open the full dashboard
in a browser. BUT __trinityOpenFullLaunchpad swallowed the dispatch RESULT with an
empty `onResult: function () {}`. When that dispatch FAILS (the common real case —
the native capture host isn't registered), the user got ZERO feedback: no nav, no
banner, the chip click did literally nothing. The popup's own open-launchpad button
surfaces an error (popup.js setStatus); the panel did not — sibling-surface drift.

The fix bridges the runtime-level dispatch result back into the Vue app via a
`trinity:dispatch-result` CustomEvent the app's init() listens for and routes to
handleDispatchResult — so a failed open-launchpad raises the SAME
"Extension installed, host not registered" banner every in-app dispatch shows.

This drives the REAL extension panel (the only thing that exercises the sandbox's
opaque origin + the PORTAL_RX interceptor — a file:// render self-navigates fine and
never reaches the open-launchpad escape) and asserts: clicking the chip dispatches
open-launchpad, and on a FAILED dispatch the failure banner becomes VISIBLE.

Mutation-provable: revert __trinityOpenFullLaunchpad's onResult to the empty
`function () {}` and the banner never appears → this reds with the founder symptom.

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


# A dispatcher that ALWAYS fails open-launchpad with native-host-unavailable and
# records the calls — installed before any click so it never reaches the real host.
_FAILING_DISPATCH_STUB = """()=>{
  window.__OPEN_CALLS__ = [];
  window.__TRINITY_DISPATCH__ = {
    dispatch: function(opts){
      window.__OPEN_CALLS__.push(opts && opts.extensionAction);
      if (opts && typeof opts.onResult === 'function') {
        opts.onResult({ tier: 'extension', ok: false, reason: 'native-host-unavailable',
                        response: { error: 'native-host-unavailable' } });
      }
      return Promise.resolve({ tier: 'extension', ok: false });
    },
    onStateChange: function(){ return function(){}; },
    getState: function(){ return 'native-missing'; }
  };
}"""

# Locate the failure banner by its rendered text + actual visibility (NOT a source
# string): the dispatchBannerOpen card reads "host not registered" / "isn't wired" in
# the panel (inExtensionPanel copy). Geometry: offsetParent !== null = truly painted.
_FAILURE_BANNER_PROBE = r"""()=>{
  const banner = [...document.querySelectorAll('section.card')].find(e =>
    e.offsetParent !== null &&
    /host not registered|isn’t wired|isn't wired|No dispatch path|native host/i.test(e.textContent || ''));
  return {
    openCalls: window.__OPEN_CALLS__ || [],
    bannerVisible: !!banner,
    bannerTop: banner ? Math.round(banner.getBoundingClientRect().top) : null,
    bannerText: banner ? (banner.innerText || '').replace(/\s+/g, ' ').slice(0, 120) : '',
    rawLeak: (document.body.innerHTML || '').includes('{{'),
    url: location.href,
  };
}"""


def test_stats_portal_chip_failed_open_shows_feedback_in_panel(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Stub the dispatcher to a FAILING open-launchpad BEFORE any click, so it
            # never touches the real native host and we exercise the failure branch.
            lf.evaluate(_FAILING_DISPATCH_STUB)

            # Flip to /stats (in-place view toggle, where the memory chips live).
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(800)
            sf = page.frames[-1]
            url_before = sf.url

            chip = sf.locator('a.memory-chip[href*="portal_pages/memory.html"]').first
            assert chip.count() > 0, "no memory-viewer chip rendered on the /stats view"
            chip_href = chip.get_attribute("href") or ""
            assert "portal_pages/memory.html" in chip_href, f"chip not a portal link: {chip_href!r}"

            chip.click(timeout=5000)
            page.wait_for_timeout(1000)
            sf = page.frames[-1]
            after = sf.evaluate(_FAILURE_BANNER_PROBE)

            # The click is intercepted into an open-launchpad dispatch (NOT a blocked
            # self-nav): URL unchanged, no chrome-error, no raw-template leak.
            assert after["url"] == url_before, (
                f"the portal chip self-NAVIGATED (should intercept to open-launchpad): {after['url']}"
            )
            assert "chrome-error" not in (after["url"] or ""), (
                f"the portal chip landed on a blocked chrome-error page: {after['url']}"
            )
            assert not after["rawLeak"], "raw {{ }} leaked (app un-mounted) after the chip click"

            kinds = [c.get("kind") if c else None for c in after["openCalls"]]
            assert "open-launchpad" in kinds, (
                "clicking a /stats memory chip did not dispatch open-launchpad "
                f"(the sandbox escape from the blocked self-nav); calls={kinds}"
            )

            # THE BITE: a FAILED open-launchpad must surface the failure banner. Before
            # the fix __trinityOpenFullLaunchpad swallowed the result in an empty
            # onResult, so the click was a SILENT dead-end — no nav, no banner, nothing.
            assert after["bannerVisible"], (
                "a /stats memory-viewer chip whose open-launchpad dispatch FAILED gave "
                "NO feedback in the side panel — the click was a silent dead-end "
                "(empty onResult swallowed the native-host-unavailable result); the "
                f"'host not registered' banner never appeared. probe={after}"
            )
            assert "register" in after["bannerText"].lower() or "host" in after["bannerText"].lower(), (
                f"the dispatch-failure banner copy is wrong for the panel: {after['bannerText']!r}"
            )
        finally:
            ctx.close()


# Probe BOTH surfaces at once: the optimistic portal notice (set by showPortalNotice
# BEFORE the dispatch resolves) and the failure banner (raised by handleDispatchResult
# AFTER). On a FAILED open the notice must be GONE; the banner must stand alone.
_NOTICE_AND_BANNER_PROBE = r"""()=>{
  const notice = document.querySelector('.portal-open-notice');
  const banner = [...document.querySelectorAll('section.card')].find(e =>
    e.offsetParent !== null &&
    /host not registered|isn.t wired|No dispatch path|native host/i.test(e.textContent||''));
  return {
    openCalls: window.__OPEN_CALLS__ || [],
    noticeVisible: notice ? (notice.offsetParent !== null) : false,
    noticeText: notice ? (notice.innerText||'').replace(/\s+/g,' ').trim() : '',
    bannerVisible: !!banner,
  };
}"""


def test_stats_portal_chip_failed_open_clears_optimistic_notice(tmp_path, monkeypatch):
    """The 291 deferred-action NO-FEEDBACK class, portal-bounce twin.

    A /stats memory-viewer chip click in the side panel fires showPortalNotice
    ("Opening the full dashboard … lives there, not in this panel") IMMEDIATELY,
    BEFORE the open-launchpad dispatch resolves — the right immediate ack on the
    SUCCESS path. But that notice is success-SHAPED: it claims the dashboard is
    opening. When open-launchpad FAILS (native host not registered — the common
    real case), the optimistic notice was left STANDING for its full 6s lifetime
    RIGHT NEXT TO the "host not registered" failure banner handleDispatchResult
    raises — a self-contradiction (one card says it's opening, the other says it
    couldn't). The fix clears the optimistic notice inside handleDispatchResult's
    failed-result block so the honest banner stands alone.

    THE BITE: drive the real panel to a FAILED open-launchpad and assert the
    optimistic notice is GONE while the failure banner is present. Mutation-proven:
    drop the `this.dismissPortalNotice()` call in handleDispatchResult's failed
    block (rebuild) → the lying notice stays visible alongside the banner → this
    reds on noticeVisible with the contradiction in the message.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            lf.evaluate(_FAILING_DISPATCH_STUB)

            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(800)
            sf = page.frames[-1]

            chip = sf.locator('a.memory-chip[href*="portal_pages/memory.html"]').first
            assert chip.count() > 0, "no memory-viewer chip rendered on the /stats view"
            chip.click(timeout=5000)
            page.wait_for_timeout(1000)
            sf = page.frames[-1]
            after = sf.evaluate(_NOTICE_AND_BANNER_PROBE)

            # PRECONDITION A: the chip actually bounced to open-launchpad (proves we
            # exercised the portal path, not some other click) — guards a vacuous pass.
            kinds = [c.get("kind") if c else None for c in after["openCalls"]]
            assert "open-launchpad" in kinds, (
                f"the chip click did not dispatch open-launchpad; calls={kinds}"
            )
            # PRECONDITION B: the FAILURE actually surfaced (the failed dispatch did
            # raise its honest banner) — proves we're in the failure sub-state, so a
            # missing notice can't be "nothing happened at all".
            assert after["bannerVisible"], (
                "the failed open-launchpad did not raise its failure banner — the "
                f"failure sub-state was never reached. probe={after}"
            )
            # THE INVARIANT: the optimistic "Opening the full dashboard …" notice must
            # be CLEARED on the failed open — it never opened, so the success-shaped
            # claim is a lie that must not stand next to the failure banner.
            assert not after["noticeVisible"], (
                "the optimistic 'Opening the full dashboard …' portal notice is STILL "
                "VISIBLE after the open-launchpad dispatch FAILED — a self-contradiction "
                "standing right next to the 'host not registered' failure banner (the "
                "deferred-action NO-FEEDBACK class: an optimistic 'doing X…' that "
                f"ignores {{ok:false}}). notice still reads: {after['noticeText']!r}"
            )
        finally:
            ctx.close()
