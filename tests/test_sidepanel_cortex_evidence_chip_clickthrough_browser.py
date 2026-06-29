"""The /stats cortex cheat-sheet EVIDENCE CHIP — "click a council to see the work
behind the recommendation" — must broker to a HYDRATED in-panel thread page in the
REAL side panel, driven end-to-end through a real chip click.

Why this needs its OWN real-panel guard (the coverage gap this closes):
  The cortex evidence chip is the ONLY path from a routing pick back to the
  deliberation that produced it. Critically, it is a DIFFERENT nav path than the
  history rail (already guarded by test_sidepanel_rail_council_clickthrough_browser):

    • The RAIL anchors are STATIC HTML in recentSidebarHtml, and
      build_extension_launchpad.py REWRITES their hrefs
      `../review_pages/live_council.html?…` → `./live_council.html?…` at inject time.
      The rail guard asserts that rewrite fired (no `../review_pages/` survives).

    • The EVIDENCE chip is rendered by VUE at runtime from `evidenceUrl(cid)`
      (`${pageData.liveReviewUrl}?thread_id=…` = `../review_pages/live_council.html?…`).
      The build's static-HTML rewrite does NOT touch a runtime-computed Vue href, so
      the chip's `href` STILL carries the `../review_pages/` prefix in the panel.
      It therefore depends SOLELY on the runtime broker chain to reach the council:
        1. sandbox click-interceptor (launchpad_runtime) matches NAV_RX against the
           `../review_pages/live_council.html?…` href — NAV_RX is
           `/(?:^|\\/)(launchpad\\.html|live_council\\.html)(\\?…)?$/`, so the leading
           `/` of `…/live_council.html` is what makes it match a PREFIXED path;
        2. __trinityNavigate postMessages the full href UP to the shell;
        3. sidepanel-bridge.js extracts the BASENAME (`m[1]`) + query (`m[2]`) and sets
           `frame.src = "sandbox/" + m[1] + (m[2]||"")` — the basename-extraction is
           what STRIPS the `../review_pages/` prefix the rail rewrite would otherwise.

  So the evidence chip lands correctly ONLY because (a) NAV_RX matches a `../review_pages/`
  PREFIXED href and (b) the bridge rebuilds the src from the basename, not the raw href.
  If NAV_RX were tightened to require a `./` / bare basename, OR the bridge stopped
  stripping the path (e.g. swapped frame.src to the raw `../review_pages/…`), the chip
  would dead-end on a nonexistent `chrome-extension://…/review_pages/…` page
  ("This page has been blocked by Chrome") — Trinity's signature navigate-to-nowhere
  strand, on the README "show its work" affordance.

  Existing coverage drives NEITHER through a real PANEL chip click:
    • test_launchpad_evidence_chip_opens_council_browser — renders /stats over
      http://127.0.0.1 and does a REAL same-origin navigation to the chip href; over
      http a `../review_pages/` nav JUST WORKS, so it NEVER exercises the sandbox
      click-interceptor, NAV_RX, or the bridge basename-extraction. (The Iter-114
      lesson: the file:// / http substrate self-navigates fine and hides the broker.)
    • test_sidepanel_rail_council_clickthrough_browser — clicks the RAIL anchor, whose
      href the build REWROTE to `./live_council.html`; it asserts NO `../review_pages/`
      survives, so it cannot cover the Vue-rendered chip that KEEPS that prefix.
    • test_sidepanel_nav_broker — asserts the NAV_RX *source string*; never clicks.

This drives the REAL extension side panel over the delegating capture-host stub
(launchpad_data from the seeded synthetic home, every other query a real host read),
flips to /stats, finds the cortex evidence chip (whose href STILL carries
`../review_pages/`), clicks it, and asserts the panel brokered to
sandbox/live_council.html with the chip's ?thread_id=, the council HYDRATED (NOT
blocked / chrome-error / blank), and no raw {{ }} leaked.

Mutation-proven: breaking the bridge's basename-extraction (swap frame.src to the raw
`m[0]` href instead of `"sandbox/" + m[1]`) lands the chip on a blocked
`../review_pages/` page — the landing + hydration assertions red with the exact symptom.

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

# Stub dispatch: resolve onResult ok; never touches a real extension/council.
_STUB_OK = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) {
      if (opts && opts.onResult) opts.onResult({ ok: true, tier: 'extension' });
    },
    probe: function () { return Promise.resolve('present'); },
    onStateChange: function () {}, subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Boot the REAL side panel over a delegating capture-host stub, seeded with the
    synthetic home so the /stats cortex cheat-sheet renders routing picks WITH evidence
    chips. launchpad_data carries the seeded payload; every other query (thread_manifest,
    council_outcome) hits the REAL capture-host handler so the chip click hydrates a real
    thread off the seeded council outcomes."""
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
    page.add_init_script(_STUB_OK)
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page, errors


def test_cortex_evidence_chip_brokers_to_hydrated_thread_in_panel(tmp_path, monkeypatch):
    """Clicking a /stats cortex evidence chip — whose Vue-rendered href STILL carries
    the un-rewritten `../review_pages/` prefix — lands on a real, hydrated in-panel
    thread page, NOT a blocked / blank / chrome-error self-nav. Guards the runtime broker
    (NAV_RX prefix match + bridge basename-extraction), the path the build rewrite does
    NOT cover."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert lf is not None, "the launchpad iframe never loaded in the panel"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            # Flip to /stats in place (the cortex cheat-sheet is a stats-card).
            lf.evaluate(
                "()=>{const a=[...document.querySelectorAll('a,button')]"
                ".find(e=>/View full stats/i.test(e.textContent||'')); if(a) a.click();}"
            )
            page.wait_for_timeout(900)
            assert "lp-view-stats" in (lf.evaluate("()=>document.getElementById('launchpad-app').className") or ""), (
                "the panel did not flip to the /stats view — the cortex cheat-sheet is unreachable"
            )

            # The evidence chip: a Vue-rendered anchor whose href STILL points at
            # ../review_pages/live_council.html (the build rewrite is static-HTML only).
            # This is the precondition that makes the guard non-vacuous AND proves it
            # exercises the broker path, not the rewritten rail path.
            chip = lf.evaluate(
                "()=>{const a=[...document.querySelectorAll('a')]"
                ".filter(x=>/review_pages\\/live_council\\.html\\?thread_id=/.test(x.getAttribute('href')||'')"
                " && x.offsetParent!==null);"
                " return {count:a.length, href:a.length?a[0].getAttribute('href'):null,"
                "  txt:a.length?a[0].textContent.trim().slice(0,16):null};}"
            )
            assert chip["count"] >= 1, (
                "no cortex evidence chip with a `../review_pages/live_council.html?thread_id=` href "
                f"rendered on /stats — the cortex cheat-sheet or evidenceUrl() regressed ({chip!r}); "
                "this guard can't bite without it."
            )
            assert "../review_pages/" in (chip["href"] or ""), (
                "the cortex evidence chip href no longer carries the `../review_pages/` prefix "
                f"({chip['href']!r}); if the build started rewriting it this guard would be "
                "covering the rail path instead of the runtime-broker path it's meant to."
            )
            expected_thread = (chip["href"] or "").split("thread_id=")[-1]

            # Click the REAL chip — through the click interceptor → NAV_RX → shell broker.
            target = lf.locator(
                "a[href*='review_pages/live_council.html?thread_id=']"
            ).first
            target.scroll_into_view_if_needed()
            target.click(timeout=6000)
            page.wait_for_timeout(4000)  # broker swap + live mount + thread/outcome load

            cf = next((f for f in page.frames if "live_council.html" in (f.url or "")), None)
            assert cf is not None, (
                "clicking the cortex evidence chip did NOT swap the panel to the in-panel live "
                "council page — the `../review_pages/` href self-nav was blocked / the click "
                "interceptor (NAV_RX) or the shell broker (basename-extraction) never fired. "
                f"Frames: {[ (f.url or '')[:90] for f in page.frames ]!r}"
            )
            landed = cf.evaluate(
                "()=>{const b=document.body.innerText||'';"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " hasThreadId:location.href.includes('thread_id='),"
                " stillReviewPages:location.href.includes('review_pages'),"
                " blocked:b.toLowerCase().includes('blocked'),"
                " chromeError:location.href.startsWith('chrome-error'),"
                " hydrated:b.includes('the answer')||b.includes('ROUTING LABEL')||b.includes('Synthesis')||b.includes('round 1 complete'),"
                " segCount:document.querySelectorAll('.chain-segment').length,"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " bodyLen:b.length};}"
            )
            assert landed["isLive"] and not landed["stillReviewPages"], (
                "the cortex evidence chip click did NOT land on the in-panel sandbox live council "
                "page — the broker either never fired or swapped frame.src to the raw "
                f"../review_pages/ href (which 'blocked by Chrome'): {landed['url']!r}"
            )
            assert not landed["chromeError"], (
                "the cortex evidence chip click landed on a chrome-error page (opaque-origin "
                f"self-nav block — the runtime broker didn't strip ../review_pages/): {landed['url']!r}"
            )
            assert not landed["blocked"], (
                "the cortex evidence chip shows 'blocked by Chrome' — the `../review_pages/` "
                "self-nav wasn't brokered to the sandbox sibling"
            )
            assert landed["hasThreadId"] and expected_thread and expected_thread in landed["url"], (
                f"the live page lost the chip's ?thread_id={expected_thread} — it can't load the "
                f"council outcome behind the routing pick: {landed['url']!r}"
            )
            # The whole point of the evidence chip is to SEE the deliberation behind the
            # pick. A bare nav with no hydration renders a blank verdict — the
            # navigate-to-nowhere strand even when the URL looks right.
            assert landed["hydrated"] and landed["segCount"] >= 1, (
                "the cortex evidence chip opened a BLANK live page — the ?thread_id= outcome never "
                f"hydrated the verdict (segCount={landed['segCount']}, bodyLen={landed['bodyLen']}). "
                "Trinity's navigate-to-nowhere strand, on the README 'show its work' affordance."
            )
            assert not landed["rawLeak"], (
                "raw {{ }} leaked after the evidence-chip nav — the live council app never mounted in-panel"
            )

            real_errs = [e for e in errors if "blocked" in e.lower() or "chrome-error" in e.lower()]
            assert not real_errs, f"page errors during evidence-chip nav: {real_errs!r}"
        finally:
            ctx.close()
