"""Clicking "Continue (one round)" on the live council page IN THE REAL SIDE PANEL
must run the production optimistic-append → in-place-completion chain lifecycle —
NOT navigate away to a blocked ../review_pages/ dead-end, and NOT leave the new
round stuck un-hydrated.

Founder lineage + why this needs a REAL-panel guard:
  The unified live council page (render_unified_council_page → bundled
  sandbox/live_council.html) handles Continue/Refine/Auto-chain via
  `_startChainAction`, which does an OPTIMISTIC APPEND: it pushes a brand-new
  round segment IN-PAGE and calls `startPolling()` to hydrate it in place — it
  does NOT navigate the page. (The OTHER, retired template's
  `_pollChainStatus → navigateToReviewPath` flow is dead code; the production
  page never navigates on a chain round.)

  Two ways this silently breaks ONLY in the opaque-origin panel, with every
  existing test green:
    1. If a refactor ever routes the chain-completion through a cross-page nav to
       `../review_pages/…` (the shape the retired template used), a sandboxed
       page CANNOT reach that chrome-extension path → "blocked by Chrome"
       (the [[sidepanel_sandbox_nav_block]] class). The composer launch path got
       a real-panel guard in Iter 49; the chain-CONTINUE path never did.
    2. If the optimistic `segments.push(newSeg)` or the `startPolling()` hydrate
       wire is dropped, Continue appends NOTHING (or a stuck spinner) and the
       next round never renders — green-while-the-value-is-gone on Trinity's
       signature iterate-to-convergence feature.

  The ONLY existing chain-action browser test (test_council_chain_action_browser)
  drives a file://-over-http render and exercises the FAILURE / rollback path
  with a FAILING dispatcher — it never (a) runs in the sandbox opaque origin,
  (b) drives the SUCCESS path, or (c) asserts the new round hydrates in-place
  without a cross-page nav. This closes that gap.

This drives the REAL extension side panel (real chrome.runtime + sandbox iframe +
delegating capture-host stub), opens a completed council from the rail, stubs the
dispatcher to SUCCEED + the in-panel status loader to resolve the new round as
COMPLETED, clicks Continue, and asserts the optimistic round-2 segment appears,
hydrates to completed IN-PLACE (Round 2 synthesis rendered), the page did NOT
navigate away / is not "blocked", no raw-{{ leak, no overflow at the panel width.

Mutation-proven: dropping the optimistic `segments.push(newSeg)` from
`_startChainAction` (so Continue appends no new round) reds the "round 2 hydrated"
assertion with the exact symptom.

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

R1 = "council_chaincont01"
R2 = "council_chaincont02"


def _seed_outcome(cid: str, rnd: int) -> None:
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                            output_text=f"Round {rnd} claude answer. " * 12),
        CouncilMemberResult(provider="codex", model="gpt-5.5",
                            output_text=f"Round {rnd} codex answer. " * 12),
    ]
    label = CouncilRoutingLabel(
        winner="claude", runner_up="codex", confidence="high", task_type="design",
        provider_scores={"claude": {"overall": 0.82}, "codex": {"overall": 0.61}},
        agreed_claims=[f"round {rnd} agreed point"],
        disagreed_claims=[{"claim": f"round {rnd} tradeoff", "providers_for": ["claude"],
                           "providers_against": ["codex"], "why_matters": "isolation"}],
    )
    save_council_outcome(CouncilOutcome(
        council_run_id=cid, bundle_id=cid, task_cluster_id="cluster_design",
        primary_provider="claude", primary_model="claude-opus-4-8",
        winner_provider="claude", winner_model="claude-opus-4-8", agreement_score=0.7,
        metadata={"task_text": "How should I isolate tenants?", "round_number": rnd},
        member_results=members, synthesis_prompt="Review.",
        synthesis_output=f"Round {rnd} synthesis: claude wins.", routing_label=label,
        created_at=f"2026-06-1{rnd}T0{rnd}:00:00+00:00",
    ))


def _boot_panel(p, tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_outcome(R1, 1)
    _seed_outcome(R2, 2)  # the round-2 outcome the completed poll hydrates from
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


def test_chain_continue_appends_and_hydrates_in_panel(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            # Open the completed Round-1 council from the rail.
            page.frame_locator("#app").locator(".rail-toggle").first.click(timeout=5000)
            page.wait_for_timeout(600)
            rf = page.frames[-1]
            idx = rf.evaluate(
                "(cid)=>[...document.querySelectorAll('.council-rail .rail-council')]"
                ".findIndex(a=>(a.getAttribute('href')||'').includes(cid))", R1)
            assert idx is not None and idx >= 0, "the seeded Round-1 council is missing from the rail"
            rf.locator(".council-rail .rail-council").nth(idx).click(timeout=5000)
            page.wait_for_timeout(3500)
            cf = page.frames[-1]
            assert "sandbox/live_council.html" in (cf.url or ""), f"rail click did not land the council page: {cf.url}"
            assert f"thread_id={R1}" in (cf.url or ""), f"opened the wrong council: {cf.url}"

            before = cf.evaluate(
                "()=>({segs: document.querySelectorAll('[data-seg-key]').length,"
                " hasContinue: !!document.querySelector('.chain-button-row .button.primary')})")
            assert before["segs"] == 1, f"expected the single completed council, got {before['segs']} segments"
            assert before["hasContinue"], (
                "the 'Continue (one round)' button is missing on a completed council "
                "(canChainNext false) — the chain-actions section never rendered"
            )

            # Stub the dispatcher to SUCCEED and the in-panel status loader to
            # resolve the next round as COMPLETED (hydrating from the R2 outcome).
            # Hook __trinityNavigate so a regression that routes chain completion
            # through a cross-page nav (the blocked ../review_pages/ shape) is caught.
            cf.evaluate(
                "(r2)=>{"
                "  window.__navAway = null;"
                "  const origTN = window.__trinityNavigate;"
                "  window.__trinityNavigate = function(u){ window.__navAway = String(u); return origTN ? origTN(u) : undefined; };"
                "  window.__TRINITY_DISPATCH__ = { dispatch: function(o){ window.__act = o.extensionAction;"
                "    setTimeout(function(){ o.onResult({ok:true, tier:'extension'}); }, 20); } };"
                "  window.loadStatusScript = function(token, cb){ cb({ status:'completed', council_id:r2,"
                "    task_text:'How should I isolate tenants?', metadata:{round_number:2},"
                "    members:{claude:{status:'done'}, codex:{status:'done'}}, synthesis:{status:'done'} }); };"
                "}", R2)

            cf.locator(".chain-button-row .button.primary").first.click(timeout=5000)
            page.wait_for_timeout(3500)

            after = cf.evaluate(
                "()=>{const vw=document.documentElement.clientWidth; const body=document.body.innerText||'';"
                "const over=[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>vw+1)"
                ".map(e=>e.tagName+'.'+(typeof e.className==='string'?e.className:'')).slice(0,5);"
                "return {url:location.href, navAway:window.__navAway, act:window.__act,"
                "segs:document.querySelectorAll('[data-seg-key]').length,"
                "round1:body.includes('Round 1 synthesis'), round2:body.includes('Round 2 synthesis'),"
                "blocked:body.toLowerCase().includes('blocked'),"
                "rawLeak:(document.body.innerHTML||'').includes('{{'),"
                "failed:body.includes('Council failed')||body.includes('never started'),"
                "chainBusy:!!document.querySelector('.chain-loading'),"
                "docW:document.documentElement.scrollWidth, vw:vw, over:over};}")

            # 1. The chain action dispatched the canonical council-iterate with a
            #    fresh status_token (underscore key) bound to THIS council.
            act = after["act"] or {}
            assert act.get("kind") == "council-iterate", f"Continue dispatched the wrong action: {act}"
            assert act.get("council") == R1, f"council-iterate bound to the wrong council: {act}"
            assert act.get("status_token"), f"council-iterate carried no status_token (the chain round can't be polled): {act}"

            # 2. It did NOT navigate the page away (the production optimistic-append
            #    design stays put) and is NOT blocked. A regression that routes chain
            #    completion through a cross-page nav would land ../review_pages/… =
            #    "blocked by Chrome" in the opaque-origin sandbox.
            assert "chrome-error" not in (after["url"] or ""), (
                f"Continue NAVIGATED to a chrome-error page (sandbox self-nav blocked): {after['url']}"
            )
            assert f"thread_id={R1}" in (after["url"] or ""), (
                f"Continue navigated AWAY from the live council page — a chain round must append "
                f"in-place, not cross-navigate (the blocked ../review_pages/ dead-end class): {after['url']}"
            )
            assert not after["navAway"], (
                f"Continue brokered a cross-page nav to {after['navAway']!r} — the production chain "
                "round appends optimistically in-place; a nav here is the blocked-by-Chrome shape"
            )
            assert not after["blocked"], "the panel shows a 'blocked by Chrome' message after Continue"

            # 3. Optimistic append + in-place hydration: a SECOND round segment
            #    appeared and rendered its Round-2 synthesis. Segs==1 (no append) or
            #    round2 missing (no hydrate) = green-while-the-value-is-gone on the
            #    iterate-to-convergence feature.
            assert after["segs"] == 2, (
                f"Continue did NOT optimistically append the next round — segments stayed at "
                f"{after['segs']} (expected 2). The chain-continue append wire is broken."
            )
            assert after["round1"], "the original Round 1 segment vanished after Continue"
            assert after["round2"], (
                "the appended round NEVER hydrated — 'Round 2 synthesis' is missing, so "
                "startPolling()'s in-place completion never ran (the next round stays a stuck "
                "spinner). Green-while-the-value-is-gone on iterate-to-convergence."
            )
            assert not after["rawLeak"], "raw {{ }} leaked — the live_council app lost its mount"
            assert not after["failed"], "Continue produced a FALSE 'Council failed / never started' page"
            assert not after["chainBusy"], "the chain spinner is stuck busy after the round completed"

            # 4. PAINT: no horizontal overflow at the narrow panel width.
            assert int(after["docW"]) <= int(after["vw"]) + 1 and not after["over"], (
                f"the chain-continue page overflows the {after['vw']}px panel: "
                f"docW={after['docW']} overflowing={after['over']}"
            )
        finally:
            ctx.close()
