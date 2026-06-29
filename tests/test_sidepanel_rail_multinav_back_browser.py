"""The side-panel SHELL-BROKER MULTI-NAV journey — rail council A → ← Launchpad →
rail council B — driven end-to-end in the REAL side panel.

Why this needs its OWN real-panel guard (the coverage gap this closes):
  The single-nav broker is solid and guarded (test_sidepanel_rail_council_clickthrough
  drives ONE hop: click rail → live page; test_sidepanel_nav_broker asserts the NAV_RX
  source string). But the founder's actual workflow is MULTI-hop: open one council from
  history, read it, click BACK to the launchpad, then open a DIFFERENT council. That
  A→back→B sequence rides the broker THREE times on ONE shell page, and a whole class
  of regressions only surfaces on the SECOND hop or the back leg — none of which the
  single-nav guards can see:

    • A one-shot bridge listener (someone adds `{ once: true }` to the shell's
      `__trinityNav` handler, or a "navigating" flag that's set but never reset) →
      the 2nd hop's postMessage is ignored, the panel stays STUCK on council A.
    • A back affordance that strands: the ← Launchpad link's `./launchpad.html` must
      re-match NAV_RX and re-mount the launchpad iframe (rail rows back). If the back
      leg lands blank / blocked, the user can never reach a second council.
    • Council A's ?thread_id= bleeding into B: frame.src must be FULLY reassigned each
      hop. If a stale query / segment survives, B's page renders A's thread — the
      [[sidepanel_sandbox_nav_block]] broker leaking state across navigations.

  The single-nav test would stay GREEN through every one of those — it never does a
  2nd hop, never clicks back, never compares two distinct thread tokens.

This drives the REAL extension side panel with a STUBBED dispatch (resolves ok,
nothing hits a real council) over the REAL delegating capture-host (launchpad_data is
a genuine pre-built payload carrying recentSidebarHtml; thread_manifest/council_outcome
are real host reads of the seeded synthetic councils). It clicks rail council A, asserts
the panel brokered to its live page; clicks ← Launchpad, asserts the launchpad iframe
RE-MOUNTED with its rail; clicks a DIFFERENT rail council B, asserts the 2nd-hop broker
landed on B's live page with B's thread_id (NOT stale A, NOT blocked, NOT chrome-error).

Mutation-proven: disable the shell's nav-broker frame.src swap (early-return the
`__trinityNav` handler body in sidepanel-bridge.js, the hand-maintained shell — NO
rebuild) and the FIRST hop already strands; to bite the MULTI-nav specifically, make
the handler one-shot (`removeEventListener` after the first swap) and the 2nd hop reds
while the 1st stays green.

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

# Stub dispatch: mirror the REAL dispatcher interface (dispatch/probe/onStateChange/
# state/extensionId) so the launchpad app's onStateChange subscription never throws,
# and nothing ever reaches a real extension/council.
_STUB_OK = """
() => {
  const listeners = new Set();
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) {
      if (opts && opts.onResult) opts.onResult({ ok: true, tier: 'extension' });
    },
    probe: function () { return Promise.resolve('present'); },
    onStateChange: function (cb) { listeners.add(cb); return function () { listeners.delete(cb); }; },
    subscribe: function () { return function () {}; },
    get state() { return 'present'; },
    get extensionId() { return 'stub'; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Boot the REAL side panel over a delegating capture-host stub, seeded with the
    synthetic home so the council rail carries MULTIPLE rows (the seeder writes 5
    distinct councils). launchpad_data is a genuine pre-built payload; thread_manifest
    + council_outcome hit the REAL capture-host handler so each rail click hydrates a
    real thread (this is the proven _boot_panel from the single-nav rail guard)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import _assemble_page_data, build_launchpad_payload

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


def _lp_frame(page):
    return next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)


def _live_frame(page):
    return next((f for f in page.frames if "live_council.html" in (f.url or "")), None)


def _open_rail(lf, page):
    """Open the off-canvas history drawer (rail is a drawer at panel width)."""
    ham = lf.locator("button.rail-toggle")
    assert ham.count() == 1, "the rail hamburger toggle is missing — history is unreachable"
    ham.first.click(timeout=5000)
    page.wait_for_timeout(600)
    assert lf.evaluate("()=>document.body.classList.contains('rail-open')"), (
        "the hamburger did not open the rail drawer — history nav is unreachable in the panel"
    )


def test_rail_multinav_A_back_B_brokers_each_hop_without_token_bleed(tmp_path, monkeypatch):
    """A→back→B in the real panel: each broker hop lands on the right in-panel page,
    the back leg re-mounts the launchpad, and council B never inherits A's thread."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = _lp_frame(page)
            assert lf is not None, "the launchpad iframe never loaded in the panel"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            # Two DISTINCT rail councils (the seeder writes 5). Read their hrefs so we
            # can drive a specific A then a specific B and compare their thread tokens.
            hrefs = lf.evaluate(
                "()=>Array.from(document.querySelectorAll('a.rail-council'))"
                ".map(a=>a.getAttribute('href'))"
            )
            assert len(hrefs) >= 2, (
                f"the rail rendered < 2 councils — can't drive a multi-nav ({hrefs!r})"
            )
            href_a, href_b = hrefs[0], hrefs[1]
            assert href_a.startswith("./live_council.html?thread_id=") and \
                   href_b.startswith("./live_council.html?thread_id="), \
                f"rail hrefs aren't sandbox thread links: {href_a!r} {href_b!r}"
            tid_a = href_a.split("thread_id=")[-1]
            tid_b = href_b.split("thread_id=")[-1]
            assert tid_a and tid_b and tid_a != tid_b, (
                f"need two DISTINCT thread ids to prove no bleed: A={tid_a!r} B={tid_b!r}"
            )

            # ---- HOP 1: click rail council A -> broker -> live page A ------------
            _open_rail(lf, page)
            lf.locator(f"a.rail-council[href*='thread_id={tid_a}']").first.click(timeout=6000)
            page.wait_for_timeout(3500)  # nav broker swap + live mount + manifest load
            cf = _live_frame(page)
            assert cf is not None, (
                "HOP 1: clicking rail council A did NOT swap the panel to the live council "
                f"page — the broker never fired. Frames: {[ (f.url or '')[:80] for f in page.frames ]!r}"
            )
            land_a = cf.evaluate(
                "()=>({url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " thread:new URL(location.href).searchParams.get('thread_id'),"
                " blocked:(document.body.innerText||'').toLowerCase().includes('blocked'),"
                " chromeError:location.href.startsWith('chrome-error')})"
            )
            assert land_a["isLive"] and not land_a["blocked"] and not land_a["chromeError"], (
                f"HOP 1 landed on a blocked/chrome-error page (broker failed): {land_a!r}"
            )
            assert land_a["thread"] == tid_a, (
                f"HOP 1 landed on the wrong thread — expected A={tid_a!r}, got {land_a['thread']!r}"
            )

            # ---- BACK: ← Launchpad -> broker -> launchpad iframe RE-MOUNTS -------
            back = cf.locator("a.topbar-back")
            assert back.count() >= 1, (
                "the live council page has NO back affordance — the user is STRANDED on the "
                "council page and can never reach a second council (multi-nav dead-end)"
            )
            back_href = back.first.get_attribute("href")
            back.first.click(timeout=6000)
            page.wait_for_timeout(3500)  # broker swap back + launchpad re-mount
            lf2 = _lp_frame(page)
            assert lf2 is not None, (
                "BACK: the ← Launchpad link did NOT re-mount the launchpad iframe — the back "
                f"leg stranded the panel (href={back_href!r}). "
                f"Frames: {[ (f.url or '')[:80] for f in page.frames ]!r}"
            )
            back_state = lf2.evaluate(
                "()=>({railRows:document.querySelectorAll('a.rail-council').length,"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " mounted:!!document.getElementById('launchpad-app') &&"
                "   !document.getElementById('launchpad-app').hasAttribute('v-cloak')})"
            )
            assert back_state["mounted"] and back_state["railRows"] >= 2, (
                f"BACK re-mounted a BLANK/partial launchpad — petite-vue never mounted or the "
                f"rail is gone ({back_state!r}); the back leg is a navigate-to-nowhere strand"
            )
            assert not back_state["rawLeak"], (
                "raw {{ }} leaked after the back nav — the launchpad app never re-mounted in-panel"
            )

            # ---- HOP 2: click a DIFFERENT rail council B ------------------------
            _open_rail(lf2, page)
            row_b = lf2.locator(f"a.rail-council[href*='thread_id={tid_b}']")
            assert row_b.count() == 1, f"rail row for council B ({tid_b}) missing after back"
            row_b.first.click(timeout=6000)
            page.wait_for_timeout(3500)  # 2nd broker swap + live mount
            cf2 = _live_frame(page)
            assert cf2 is not None, (
                "HOP 2: the SECOND rail-council click did NOT swap to the live council page — "
                "the broker is one-shot (the shell's __trinityNav handler fired only on the "
                "first hop and the panel is STUCK). "
                f"Frames: {[ (f.url or '')[:80] for f in page.frames ]!r}"
            )
            land_b = cf2.evaluate(
                "()=>({url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " thread:new URL(location.href).searchParams.get('thread_id'),"
                " blocked:(document.body.innerText||'').toLowerCase().includes('blocked'),"
                " chromeError:location.href.startsWith('chrome-error')})"
            )
            assert land_b["isLive"] and not land_b["blocked"] and not land_b["chromeError"], (
                f"HOP 2 landed on a blocked/chrome-error page — the allowlist failed on the "
                f"SECOND hop: {land_b!r}"
            )
            assert land_b["thread"] == tid_b, (
                f"HOP 2 landed on the WRONG thread — council A's ?thread_id bled into B: "
                f"expected B={tid_b!r}, got {land_b['thread']!r} (A was {tid_a!r}). "
                "frame.src wasn't fully reassigned across the broker swap "
                "([[sidepanel_sandbox_nav_block]] state leaking across navigations)."
            )
            assert land_b["thread"] != tid_a, (
                f"HOP 2 is showing council A's thread ({tid_a}) after navigating to B — stale "
                "token bleed across the multi-nav"
            )

            real_errs = [e for e in errors if "blocked" in e.lower() or "chrome-error" in e.lower()]
            assert not real_errs, f"page errors during the multi-nav journey: {real_errs!r}"
        finally:
            ctx.close()
