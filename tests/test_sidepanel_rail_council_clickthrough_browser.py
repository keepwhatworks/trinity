"""The council RAIL — clicking a past council in the side panel's history drawer —
must broker to a HYDRATED in-panel thread page, driven end-to-end in the REAL side
panel.

Why this needs its OWN real-panel guard (the coverage gap this closes):
  Opening a past council from the rail is the side panel's MOST COMMON navigation,
  and it rides a TWO-STEP rewrite chain that no existing test drives through a real
  rail-anchor click:

    1. build_extension_launchpad.py rewrites the host's rail-council hrefs
       `../review_pages/live_council.html?thread_id=…` → `./live_council.html?…`
       at inject time (gated on __TRINITY_HOST_FETCH__) — otherwise the click
       resolves to a nonexistent chrome-extension://…/review_pages/… page →
       "This page has been blocked by Chrome" (founder-caught, the original rail bug).
    2. The sandbox click-interceptor (launchpad_runtime) catches the anchor, matches
       NAV_RX, and __trinityNavigate-postMessages the URL UP to the shell
       (sidepanel-bridge NAV_RX), which swaps frame.src to sandbox/live_council.html.

  Existing coverage drives NEITHER through a real rail click:
    • test_sidepanel_nav_broker — asserts the NAV_RX *source string*; never clicks.
    • test_sidepanel_live_council_subpage_nav — fires the __trinityNav postMessage
      DIRECTLY via evaluate() and drives the controls ON the council page (← Launchpad
      / View full thread); it BYPASSES the rail-anchor click + the build's href rewrite.
    • test_launchpad_recent_card_clickthrough — clicks the rail anchor, but over
      http://127.0.0.1 (the file:// substrate), where a real same-origin navigation
      works; it never exercises the sandbox broker or the `../review_pages/` rewrite.

  So if step 1's split/join rewrite OR step 2's NAV_RX allowlist regresses, every
  rail click strands the panel on a blocked/blank page — the user opens their
  history, clicks a council, and lands nowhere, with every existing test green.
  That's Trinity's signature navigate-to-nowhere strand, on the panel's primary nav.

This drives the REAL extension side panel with a STUBBED dispatch (resolves ok,
nothing hits a real council) but the REAL delegating capture-host (so launchpad_data
carries a genuine recentSidebarHtml and thread_manifest is a real host read of the
seeded synthetic councils). It opens the rail drawer via the hamburger, clicks the
first rail council, and asserts the panel brokered to sandbox/live_council.html with
the right ?thread_id=, the thread manifest hydrated the verdict (NOT blocked /
chrome-error / blank), and no raw {{ }} leaked.

Mutation-proven: revert the build's rail-href rewrite (`./live_council.html` →
`../review_pages/live_council.html`, the un-fixed shape) and the click lands on a
blocked/blank page — the landing + hydration assertions red.

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
    synthetic home so the council rail is populated. launchpad_data carries a genuine
    recentSidebarHtml (the rail rows); every other query (thread_manifest,
    council_outcome) hits the REAL capture-host handler so the rail click hydrates a
    real thread."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import _assemble_page_data, build_launchpad_payload

    # _assemble_page_data returns (pageData, recentSidebarHtml) — the rail HTML the
    # served launchpad bakes into the page and the panel hydrates client-side.
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
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(_STUB_OK)
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount + rail hydrate
    return ctx, ext_id, page, errors


def test_rail_council_click_brokers_to_hydrated_thread_in_panel(tmp_path, monkeypatch):
    """Clicking a council in the side panel's history rail lands on a real, hydrated
    in-panel thread page — never a blocked / blank / chrome-error self-nav."""
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

            # The rail rows hydrate client-side from recentSidebarHtml. Confirm the
            # build's href rewrite fired: in the panel the anchors must point at the
            # sandbox sibling ./live_council.html, NOT the dead ../review_pages/ path.
            rail = lf.evaluate(
                "()=>{const a=Array.from(document.querySelectorAll('a.rail-council'));"
                " return {count:a.length,"
                "  firstHref:a.length?a[0].getAttribute('href'):null,"
                "  anyReviewPages:a.some(x=>(x.getAttribute('href')||'').includes('../review_pages/'))};}"
            )
            assert rail["count"] >= 1, (
                "the council rail rendered no rows — the panel can't navigate history "
                f"({rail!r}); the recentSidebarHtml hydrate regressed"
            )
            assert not rail["anyReviewPages"], (
                "a rail-council href still points at ../review_pages/live_council.html in the "
                "SANDBOX — the build's panel href rewrite regressed; clicking it would land on "
                f"a nonexistent chrome-extension page ('blocked by Chrome'): {rail['firstHref']!r}"
            )
            assert (rail["firstHref"] or "").startswith("./live_council.html?thread_id="), (
                f"the rail-council href isn't the sandbox-relative thread link: {rail['firstHref']!r}"
            )
            expected_thread = (rail["firstHref"] or "").split("thread_id=")[-1]

            # Open the history drawer (the rail is an off-canvas drawer at panel width)
            # then click the first council — the REAL nav path, through the click
            # interceptor → NAV_RX → __trinityNavigate → shell broker.
            ham = lf.locator("button.rail-toggle")
            assert ham.count() == 1, "the rail hamburger toggle is missing — history is unreachable"
            ham.first.click(timeout=5000)
            page.wait_for_timeout(700)
            assert lf.evaluate("()=>document.body.classList.contains('rail-open')"), (
                "the hamburger did not open the rail drawer — the history nav is unreachable in the panel"
            )

            first = lf.locator("a.rail-council").first
            box = first.bounding_box()
            assert box is not None and box["height"] >= 44, (
                f"the rail-council tap target is under 44px (touch width): {box}"
            )
            first.click(timeout=6000)
            page.wait_for_timeout(4000)  # nav broker swap + live mount + thread manifest load

            cf = next((f for f in page.frames if "live_council.html" in (f.url or "")), None)
            assert cf is not None, (
                "clicking a rail council did NOT swap the panel to the in-panel live council page "
                "— the sandbox self-nav was blocked / the click interceptor or shell broker never "
                f"fired. Frames: {[ (f.url or '')[:90] for f in page.frames ]!r}"
            )
            landed = cf.evaluate(
                "()=>{const b=document.body.innerText||'';"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " hasThreadId:location.href.includes('thread_id='),"
                " blocked:b.toLowerCase().includes('blocked'),"
                " chromeError:location.href.startsWith('chrome-error'),"
                " hydrated:b.includes('the answer')||b.includes('ROUTING LABEL')||b.includes('Synthesis'),"
                " segCount:document.querySelectorAll('.chain-segment').length,"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " bodyLen:b.length};}"
            )
            assert landed["isLive"], (
                f"the rail-council click did NOT land on the in-panel live council page — the "
                f"sandbox self-nav was blocked / the broker never fired: {landed['url']!r}"
            )
            assert not landed["chromeError"], (
                f"the rail-council click landed on a chrome-error page (opaque-origin self-nav "
                f"block — the ../review_pages/ href rewrite regressed): {landed['url']!r}"
            )
            assert not landed["blocked"], (
                "the rail-council click shows 'blocked by Chrome' — the sandbox self-nav wasn't brokered"
            )
            assert landed["hasThreadId"] and expected_thread and expected_thread in landed["url"], (
                f"the live page lost the rail's ?thread_id={expected_thread} — it can't load the "
                f"thread manifest: {landed['url']!r}"
            )
            # The whole point of clicking a past council is to SEE it. A bare nav to
            # ./live_council.html with no hydration renders a blank verdict (the
            # navigate-to-nowhere strand) even when the URL looks right.
            assert landed["hydrated"] and landed["segCount"] >= 1, (
                "the rail council opened a BLANK live page — the ?thread_id= manifest never "
                f"hydrated the verdict (segCount={landed['segCount']}, bodyLen={landed['bodyLen']}). "
                "Trinity's navigate-to-nowhere strand, on the panel's primary history nav."
            )
            assert not landed["rawLeak"], (
                "raw {{ }} leaked after the rail-council nav — the live council app never mounted in-panel"
            )

            real_errs = [e for e in errors if "blocked" in e.lower() or "chrome-error" in e.lower()]
            assert not real_errs, f"page errors during rail nav: {real_errs!r}"
        finally:
            ctx.close()
