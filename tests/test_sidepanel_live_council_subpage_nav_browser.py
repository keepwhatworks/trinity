"""The live-council SUB-PAGE in-panel navigation controls — the '← Launchpad'
back-link and the 'View full thread' link — must LAND on a real rendered in-panel
page, driven end-to-end in the REAL side panel.

Why this needs its own real-panel guard (the coverage gap this closes):
  Both controls are sandbox SELF-NAVS: the live council page runs in an opaque-origin
  sandbox iframe and CANNOT navigate ITSELF to another extension page — Chrome blocks
  a link click ("This page has been blocked by Chrome"). They only work because the
  sandbox click-interceptor hands the nav UP to the shell (sidepanel-bridge.js NAV_RX)
  which swaps the iframe src. test_sidepanel_nav_broker asserts the NAV_RX SOURCE
  STRING contains 'launchpad.html' / 'live_council.html' — but NEVER CLICKS either
  control in a real panel, so the actual click-through (interceptor → __trinityNavigate
  postMessage → shell NAV_RX match → frame.src swap → the target page mounting in the
  opaque-origin sandbox + hydrating) was wholly UN-DRIVEN. The build also rewrites
  pageData.launchpadUrl from ../portal_pages/launchpad.html → ./launchpad.html
  (build_extension_sidepanel.py) specifically so the back-link stays inside the
  sandbox; if that rewrite or the NAV_RX allowlist regresses, the '← Launchpad'
  back-link or 'View full thread' link strands the panel on a blocked/blank page —
  the user opens a council, then can't get back to the launchpad, with every existing
  test green. That's Trinity's signature navigate-to-nowhere strand (the same shape
  Iter 105 fixed on the auto-poll completion branch), on the council page itself.

This drives the REAL extension side panel with a STUBBED dispatch (resolves ok,
nothing hits a real council) but the REAL delegating capture-host (so council_outcome
/ thread_manifest are genuine host reads of seeded files). It seeds a THREAD-KEYED
chain of two completed rounds, brokers the panel to the round-2 live council page,
then:
  1. Clicks 'View full thread' → asserts the frame swapped IN-PANEL to
     ?thread_id=<root> (NOT blocked / chrome-error / blank), the thread manifest
     loaded, and BOTH rounds' synthesis verdicts rendered as two .chain-segment
     stacks — not a stranded single segment.
  2. Clicks '← Launchpad' → asserts the frame swapped back to the REAL launchpad
     (sandbox/launchpad.html with #launchpad-app mounted) — NOT a blocked self-nav.

Mutation-proven: break the shell's NAV_RX (so the brokered swap never fires) and
BOTH the thread-view and back-link landing assertions red.

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

# A THREAD-KEYED chain root (NOT a 'council_…' id) so _maybeOfferThreadLink fires
# and the live page surfaces the 'View full thread' button.
CHAIN_ROOT = "bundle_subpagenav01"
CID1 = "council_subpagenav_r1"
CID2 = "council_subpagenav_r2"
SYNTH1 = "SUBPAGENAV ROUND-ONE SYNTHESIS verdict text."
SYNTH2 = "SUBPAGENAV ROUND-TWO SYNTHESIS verdict text."

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


def _seed_thread() -> None:
    from trinity_local.council_runtime import save_council_outcome, update_thread_manifest
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    def mk(cid: str, synth: str, rnd: int) -> None:
        members = [
            CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                                output_text="Claude round answer. " * 12),
            CouncilMemberResult(provider="codex", model="gpt-5.5",
                                output_text="Codex round answer. " * 12),
        ]
        label = CouncilRoutingLabel(
            winner="claude", runner_up="codex", confidence="high", task_type="design",
            provider_scores={"claude": {"overall": 0.84}, "codex": {"overall": 0.60}},
            agreed_claims=["agreed point"],
            disagreed_claims=[{"claim": "tradeoff", "providers_for": ["claude"],
                               "providers_against": ["codex"], "why_matters": "clarity"}],
        )
        oc = CouncilOutcome(
            council_run_id=cid, bundle_id=CHAIN_ROOT, task_cluster_id="cluster_spn",
            primary_provider="claude", primary_model="claude-opus-4-8",
            winner_provider="claude", winner_model="claude-opus-4-8", agreement_score=0.78,
            metadata={"task_text": "why is the sky blue?", "round_number": rnd,
                      "chain_root_id": CHAIN_ROOT},
            member_results=members, synthesis_prompt="Review.",
            synthesis_output=synth, routing_label=label,
            created_at=f"2026-06-18T0{rnd}:00:00+00:00",
        )
        save_council_outcome(oc)
        update_thread_manifest(oc)

    mk(CID1, SYNTH1, 1)
    mk(CID2, SYNTH2, 2)


def _boot_panel(p, tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_thread()
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    # launchpad_data → the prebuilt payload; every OTHER query (council_outcome,
    # thread_manifest) → the REAL capture-host handler, so the thread view is
    # driven by genuine host reads of the seeded files.
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


def test_live_council_back_and_thread_links_land_in_panel(tmp_path, monkeypatch):
    """The live council '← Launchpad' + 'View full thread' controls broker
    correctly in the real side panel — each lands on a real rendered page, never a
    blocked/blank self-nav."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            # ── Broker the panel to the round-2 live council page ────────────
            # This is the same brokered nav every in-panel council control uses
            # (the rail-council link, the autopoll completion nav): postMessage UP
            # to the shell with a ?council_id= target the NAV_RX allowlists.
            lf.evaluate(
                "()=>{ window.parent.postMessage({__trinityNav:true,"
                f"url:'./live_council.html?council_id={CID2}'}}, '*'); }}"
            )
            page.wait_for_timeout(3500)
            cf = page.frames[-1]
            live = cf.evaluate(
                "()=>{const b=document.body.innerText||'';"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " synth2:b.includes('SUBPAGENAV ROUND-TWO SYNTHESIS'),"
                " hasBack:!!document.querySelector('a.topbar-back'),"
                " threadVisible:(()=>{const a=document.querySelector('a.topbar-action');"
                "                     return a?!!(a.offsetParent):false;})(),"
                " rawLeak:(document.body.innerHTML||'').includes('{{')};}"
            )
            assert live["isLive"], f"the live council page did not mount in-panel: {live['url']!r}"
            assert live["synth2"], "the round-2 outcome verdict never hydrated on the live council page"
            assert not live["rawLeak"], "raw {{ }} leaked — the live council app never mounted in-panel"
            stub_install = _STUB_OK
            cf.evaluate(stub_install)  # so the page's dispatcher probe doesn't error

            # ── 1. Click 'View full thread' → must land on the in-panel thread view ──
            assert live["threadVisible"], (
                "the 'View full thread' link never surfaced on a >1-segment thread-keyed "
                "council — the thread-manifest host read or _maybeOfferThreadLink regressed"
            )
            cf.locator("a.topbar-action").first.click(timeout=5000)
            page.wait_for_timeout(3500)  # nav broker swap + thread mount + manifest load
            tf = page.frames[-1]
            thread = tf.evaluate(
                "()=>{const b=document.body.innerText||'';"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " hasThreadId:location.href.includes('thread_id='),"
                " blocked:b.toLowerCase().includes('blocked'),"
                " chromeError:location.href.startsWith('chrome-error'),"
                " r1:b.includes('SUBPAGENAV ROUND-ONE SYNTHESIS'),"
                " r2:b.includes('SUBPAGENAV ROUND-TWO SYNTHESIS'),"
                " segCount:document.querySelectorAll('.chain-segment').length,"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " bodyLen:b.length};}"
            )
            assert thread["isLive"], (
                f"'View full thread' did NOT swap the panel to the in-panel thread view — the "
                f"sandbox self-nav was blocked / the nav broker never fired: {thread['url']!r}"
            )
            assert not thread["chromeError"], (
                f"'View full thread' landed on a chrome-error page (opaque-origin self-nav block): "
                f"{thread['url']!r}"
            )
            assert not thread["blocked"], (
                "'View full thread' shows 'blocked by Chrome' — the sandbox self-nav wasn't brokered"
            )
            assert thread["hasThreadId"], (
                f"the thread view lost the ?thread_id= param — it can't load the manifest: {thread['url']!r}"
            )
            # The whole point of 'View full thread' is to STACK every round; a strand
            # that loads only one segment (or none) silently erases the conversation.
            assert thread["r1"] and thread["r2"], (
                "the thread view did NOT render BOTH rounds' verdicts — 'View full thread' "
                f"stranded on a partial/empty thread (r1={thread['r1']} r2={thread['r2']})"
            )
            assert thread["segCount"] >= 2, (
                f"the thread view rendered fewer than 2 .chain-segment stacks — the full thread "
                f"never loaded (segCount={thread['segCount']})"
            )
            assert not thread["rawLeak"], "raw {{ }} leaked after the in-panel thread-view nav"

            # ── 2. Click '← Launchpad' → must land back on the REAL launchpad ──
            tf.locator("a.topbar-back").first.click(timeout=5000)
            page.wait_for_timeout(3500)  # nav broker swap + launchpad mount
            bf = page.frames[-1]
            back = bf.evaluate(
                "()=>{const b=document.body.innerText||'';"
                "return {url:location.href,"
                " isLaunchpad:location.href.includes('sandbox/launchpad.html'),"
                " hasApp:!!document.getElementById('launchpad-app'),"
                " blocked:b.toLowerCase().includes('blocked'),"
                " chromeError:location.href.startsWith('chrome-error'),"
                " rawLeak:(document.body.innerHTML||'').includes('{{')};}"
            )
            assert back["isLaunchpad"], (
                f"the '← Launchpad' back-link did NOT return to the in-panel launchpad — the "
                f"sandbox self-nav was blocked / the build's launchpadUrl rewrite regressed: "
                f"{back['url']!r}"
            )
            assert not back["chromeError"], (
                f"'← Launchpad' landed on a chrome-error page (opaque-origin self-nav block): "
                f"{back['url']!r}"
            )
            assert not back["blocked"], (
                "'← Launchpad' shows 'blocked by Chrome' — the back-link self-nav wasn't brokered "
                "(the user opened a council and can't get back to the launchpad)"
            )
            assert back["hasApp"], (
                "the launchpad app (#launchpad-app) never mounted after '← Launchpad' — the "
                "back-link landed on a blank/wrong page"
            )
            assert not back["rawLeak"], "raw {{ }} leaked after the '← Launchpad' nav"

            assert errors == [], f"console errors during the sub-page nav lifecycle: {errors}"
        finally:
            ctx.close()
