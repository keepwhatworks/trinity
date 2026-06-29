"""The HOME taste-card's memory-viewer deep links must broker through the sandbox
escape AND give FEEDBACK on a failed open — exactly like the /stats portal chips.

Driving the REAL extension side panel @ 393px on the HOME view (2026-06-20): the
"Your taste, distilled" card carries memory-viewer deep links — "View full lens →"
(../portal_pages/memory.html?file=lens.md) and the per-tension "Spans" basin chips
(../portal_pages/memory.html?file=topics.json&basin=<id>). Like the /stats memory
chips, these point at a page that does NOT exist in the sandbox, so a self-nav there
is "blocked by Chrome". The sandbox click interceptor (launchpad_runtime.py
PORTAL_RX) stops the blocked self-nav and dispatches `open-launchpad` instead; on a
FAILED open (the common real case — native host not registered) the runtime bridges
the result back via the `trinity:dispatch-result` event so the app raises its
"Extension installed, host not registered" banner.

WHY THIS GUARD EXISTS — a guard GAP, not a present bug. The /stats portal chips are
covered by test_sidepanel_stats_portal_chip_feedback_browser.py, but the HOME
taste-card chips were only string-tested (test_lens_basin_chips.py asserts the
.cross-memory-chip class + href are PRESENT in the template — never a click, never a
broker). The HOME view is a DISTINCT code path: the interceptor's `onLaunchpad`
branch handles home<->stats view toggles before the PORTAL_RX escape, and the home
view renders a different card. A refactor that (a) reshaped the taste-card chip hrefs
so they no longer match PORTAL_RX, (b) broke the home-view click delegation, or (c)
severed the failure-banner wiring would turn the home taste-card chips into silent
dead-ends (or "blocked by Chrome" self-navs) while the string test stayed GREEN —
Trinity's signature "green while the value is gone" shape on a real affordance.

Live-verified clean 2026-06-20 (open-launchpad fires, the host-not-registered banner
appears); this guard pins that behavior at the rendered-DOM level so it can't rot.

Mutation-provable: break the broker (PORTAL_RX self-nav, or the empty onResult that
swallows the failure) and this reds with the founder symptom; the string test stays
green. Slow + browser marked; skips without Playwright/chromium.
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
    """Seed a synthetic home (lights the taste card), stub the native host, load the
    real extension, open the side panel, and return (ctx, ext_id, page) after mount."""
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
# the panel (inExtensionPanel copy). offsetParent !== null = truly painted.
_FAILURE_BANNER_PROBE = r"""()=>{
  const banner = [...document.querySelectorAll('section.card')].find(e =>
    e.offsetParent !== null &&
    /host not registered|isn’t wired|isn't wired|No dispatch path|native host/i.test(e.textContent || ''));
  return {
    openCalls: window.__OPEN_CALLS__ || [],
    bannerVisible: !!banner,
    bannerText: banner ? (banner.innerText || '').replace(/\s+/g, ' ').slice(0, 120) : '',
    rawLeak: (document.body.innerHTML || '').includes('{{'),
    url: location.href,
  };
}"""


def _drive_home_portal_chip(lf, page, selector, label):
    """Click a HOME taste-card portal chip and return the post-click probe."""
    lf.evaluate(_FAILING_DISPATCH_STUB)
    chip = lf.locator(selector).first
    assert chip.count() > 0, f"{label}: chip not rendered on the HOME taste card"
    href = chip.get_attribute("href") or ""
    assert "portal_pages/memory.html" in href, (
        f"{label}: not a memory-viewer portal link (got {href!r}) — the broker only "
        "intercepts portal_pages/*.html, so a non-portal href would dead-end silently"
    )
    url_before = lf.url
    chip.click(timeout=5000)
    page.wait_for_timeout(1200)
    lf2 = page.frames[-1]
    after = lf2.evaluate(_FAILURE_BANNER_PROBE)
    after["url_before"] = url_before
    return after


def test_taste_card_view_full_lens_failed_open_shows_feedback_in_panel(tmp_path, monkeypatch):
    """The HOME 'View full lens →' link must intercept to open-launchpad (NOT a blocked
    self-nav) and, on a FAILED open, surface the host-not-registered banner — not a
    silent dead click. (The string test only checks the href is present.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # PRECONDITION/BITE: we must be on the HOME view (the taste card is a
            # home-card; on /stats it's display:none). Asserting this keeps the guard
            # non-vacuous — it proves we drove the HOME code path, not /stats.
            view = lf.evaluate("()=>(document.querySelector('#launchpad-app')||{}).className||''")
            assert "lp-view-home" in view, f"expected the HOME view; got {view!r}"

            after = _drive_home_portal_chip(
                lf, page, 'section.taste-card a:has-text("View full lens")', "View full lens"
            )

            assert after["url"] == after["url_before"], (
                "the 'View full lens →' link self-NAVIGATED instead of intercepting to "
                f"open-launchpad — a sandboxed self-nav lands on 'blocked by Chrome': {after['url']}"
            )
            assert "chrome-error" not in (after["url"] or ""), (
                f"'View full lens →' landed on a blocked chrome-error page: {after['url']}"
            )
            assert not after["rawLeak"], "raw {{ }} leaked (app un-mounted) after the lens-link click"

            kinds = [c.get("kind") if c else None for c in after["openCalls"]]
            assert "open-launchpad" in kinds, (
                "the HOME 'View full lens →' link did not dispatch open-launchpad — the "
                "sandbox escape from the blocked memory.html self-nav never fired; "
                f"calls={kinds}"
            )
            # THE BITE: a FAILED open-launchpad must surface the failure banner on the
            # HOME view too. If the runtime swallowed the result (empty onResult) the
            # taste-card lens link would be a SILENT dead-end — no nav, no banner.
            assert after["bannerVisible"], (
                "the HOME 'View full lens →' link whose open-launchpad dispatch FAILED "
                "gave NO feedback in the side panel — a silent dead click on the "
                "first-impression taste card (the host-not-registered banner never "
                f"appeared). probe={after}"
            )
            assert "register" in after["bannerText"].lower() or "host" in after["bannerText"].lower(), (
                f"the dispatch-failure banner copy is wrong for the panel: {after['bannerText']!r}"
            )
        finally:
            ctx.close()


def test_taste_card_basin_chip_failed_open_shows_feedback_in_panel(tmp_path, monkeypatch):
    """The HOME per-tension 'Spans' basin chip (topics.json deep link) must broker the
    same way — open-launchpad on click, failure banner on a failed open. The basin
    chip is a SECOND portal-link surface on the taste card and a distinct selector;
    it shares the PORTAL_RX broker, so it must share the feedback contract."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            view = lf.evaluate("()=>(document.querySelector('#launchpad-app')||{}).className||''")
            assert "lp-view-home" in view, f"expected the HOME view; got {view!r}"

            after = _drive_home_portal_chip(
                lf, page, "section.taste-card a.lens-basin-chip", "Spans basin chip"
            )

            assert after["url"] == after["url_before"], (
                "the taste-card 'Spans' basin chip self-NAVIGATED to memory.html instead "
                f"of intercepting to open-launchpad (blocked-by-Chrome dead-end): {after['url']}"
            )
            kinds = [c.get("kind") if c else None for c in after["openCalls"]]
            assert "open-launchpad" in kinds, (
                "the HOME taste-card basin chip did not dispatch open-launchpad — the "
                f"topics.json deep link dead-ended in the sandbox; calls={kinds}"
            )
            assert after["bannerVisible"], (
                "the HOME taste-card basin chip whose open-launchpad dispatch FAILED gave "
                "NO feedback in the side panel — a silent dead click (the "
                f"host-not-registered banner never appeared). probe={after}"
            )
        finally:
            ctx.close()
