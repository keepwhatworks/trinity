"""A multi-round CHAIN council opened from the side-panel rail must hydrate EVERY
round in the opaque-origin sandbox — not just the first, and not a blocked/empty page.

Founder lineage: the rail collapses a chain (refine / continue / auto-chain rounds)
to ONE card carrying a "N rounds" badge whose link is `?thread_id=<bundle_root>`
(launchpad_data.build_recent_sidebar_html). In the Chrome side panel that click is
brokered through the shell to the sandbox `live_council.html?thread_id=…`, and the
page calls `loadThread` → `loadThreadScript`. In the panel (opaque origin) that takes
the `__trinityHostFetch()` branch and asks the native host for `thread_manifest`, then
builds ONE segment PER manifest entry, each hydrating its outcome via the host's
`council_outcome` query. None of that multi-segment round trip had real-browser
coverage: the only rail-click panel test drives a SINGLE council (1-segment fallback,
`thread_id=council_syn…`), and the chain coverage on disk is purely DATA-level
(segment_count in the Python builder, rail HTML link shape) — nothing drove the panel.

A file:// render can't catch a regression here: file:// takes the OTHER branch of
`loadThreadScript` (a `<script src=_thread_….js>` injection), so the in-panel host-query
path — the one the real Chrome side panel actually runs — is exercised ONLY by the real
extension sandbox. If `loadThreadScript`'s host-query branch, the manifest segment
build, or the per-segment outcome hydration regressed, EVERY chain council would silently
open showing only round 1 (or a blocked/failed page) while every existing test stayed
green — the "green while the value is gone" shape, on Trinity's signature
iterate-to-convergence feature.

This drives the REAL panel: seeds a 2-round chain via the production council writers,
opens the rail drawer, clicks the "2 rounds" card, and asserts the brokered live_council
page hydrates BOTH rounds (Round 1 AND Round 2 eyebrows + both routing-label panels),
with no "blocked"/"Council failed"/raw-`{{` leak and no horizontal overflow.

Mutation-proven: breaking the in-panel `thread_manifest` host query (so `loadThread`
falls back to treating the bundle root as a single council_id) collapses the page to ONE
segment → the "Round 2" assertion reds with the exact symptom.

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

CHAIN_ROOT = "bundle_chainseed01"


def _seed_two_round_chain(home: Path) -> None:
    """Add a 2-round chain (one chain_root_id, two finalized rounds) via the
    production writers so the rail collapses it to one "2 rounds" card and the
    thread manifest (_thread_<root>.js) lists both segments in order."""
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    for rnd in (1, 2):
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
            council_run_id=f"council_chain{rnd:02d}", bundle_id=CHAIN_ROOT,
            task_cluster_id="cluster_design", primary_provider="claude",
            primary_model="claude-opus-4-8", winner_provider="claude",
            winner_model="claude-opus-4-8", agreement_score=0.7,
            metadata={"task_text": "Chain: how should I isolate tenants?",
                      "chain_root_id": CHAIN_ROOT, "round_number": rnd,
                      "user_refinement": ("round 2 refinement: tighten" if rnd > 1 else None)},
            member_results=members, synthesis_prompt="Review.",
            synthesis_output=f"Round {rnd} synthesis: claude wins.",
            routing_label=label,
            created_at=f"2026-06-1{rnd}T0{rnd}:00:00+00:00",
        ))


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home + a 2-round chain, stub the native host (delegating
    every non-launchpad_data query to the REAL capture-host handlers so
    thread_manifest / council_outcome answer with the genuine seeded data), load
    the real extension, open the side panel, return (ctx, ext_id, page)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_two_round_chain(home)
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


def test_chain_council_rail_card_hydrates_every_round_in_panel(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Open the rail drawer where the recent councils live.
            page.frame_locator("#app").locator(".rail-toggle").first.click(timeout=5000)
            page.wait_for_timeout(600)
            rf = page.frames[-1]
            rails = rf.evaluate(
                "()=>[...document.querySelectorAll('.council-rail .rail-council')].map("
                "a=>({href:a.getAttribute('href'),"
                "rounds:(a.querySelector('.rail-council-rounds')?.textContent||'').trim()}))"
            )
            assert rails, "no recent councils rendered in the side-panel rail (seed/empty-state regression?)"
            # Exactly the chain card carries the multi-round badge; it links to the
            # bundle chain root (not a per-round council id).
            chain = [r for r in rails if r.get("rounds")]
            assert chain, f"the 2-round chain rail card showed no '2 rounds' badge: {rails}"
            chain_href = chain[0]["href"] or ""
            assert "2 rounds" in chain[0]["rounds"], f"chain badge wrong count: {chain[0]['rounds']!r}"
            assert chain_href.startswith(f"./live_council.html?thread_id={CHAIN_ROOT}"), (
                f"chain rail href not rewritten to the sandbox sibling with the bundle root: {chain_href!r}"
            )
            chain_idx = next(i for i, r in enumerate(rails) if r.get("rounds"))

            # Click the chain card. The broker swaps the iframe src to the sandbox
            # live_council page (a VISIBLE reload), which loads the thread manifest.
            rf.locator(".council-rail .rail-council").nth(chain_idx).click(timeout=5000)
            page.wait_for_timeout(4000)  # broker swap + thread manifest + 2× outcome hydrate

            cf = page.frames[-1]
            state = cf.evaluate(
                "()=>{const vw=document.documentElement.clientWidth;"
                "const ebs=[...document.querySelectorAll('.eyebrow')].map(e=>e.textContent.replace(/\\s+/g,' ').trim());"
                "const overEls=[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>vw+1)"
                ".map(e=>e.tagName+'.'+(typeof e.className==='string'?e.className:'')).slice(0,6);"
                "const body=document.body.innerText||'';"
                "return {"
                "url: location.href,"
                "blocked: body.toLowerCase().includes('blocked'),"
                "rawLeak: (document.body.innerHTML||'').includes('{{'),"
                "failed: body.includes('Council failed') || body.includes('Could not load council outcome'),"
                "eyebrows: ebs,"
                "round1: body.includes('Round 1 synthesis'),"
                "round2: body.includes('Round 2 synthesis'),"
                "routingPanels: document.querySelectorAll('.routing-label-grid').length,"
                "refinementVisible: body.includes('round 2 refinement'),"
                "docW: document.documentElement.scrollWidth, vw: vw, overEls: overEls,"
                "bodyHead: body.slice(0,200)"
                "};}"
            )
            # Brokered, not blocked, mounted.
            assert "chrome-error" not in (cf.url or ""), (
                f"chain rail click was BLOCKED by Chrome (sandbox self-nav): {cf.url}"
            )
            assert "sandbox/live_council.html" in (cf.url or ""), (
                f"chain rail click did NOT land the sandbox live_council page (nav broker failed): {cf.url}"
            )
            assert f"thread_id={CHAIN_ROOT}" in (cf.url or ""), f"chain thread_id lost in the nav: {cf.url}"
            assert not state["blocked"], f"the panel shows a 'blocked' message: {state['bodyHead']!r}"
            assert not state["rawLeak"], "raw {{ }} leaked — the live_council app never mounted"
            assert not state["failed"], (
                f"chain rail opened a FALSE 'Council failed' page: {state['bodyHead']!r}"
            )

            # The DECISIVE assertion: a multi-round chain must hydrate EVERY round.
            # Round 1 alone is the single-segment fallback that fires when the
            # in-panel thread_manifest host query breaks — the green-while-the-
            # value-is-gone symptom on the iterate-to-convergence feature.
            round_eyebrows = [e for e in state["eyebrows"] if e.lower().startswith("▾ round")
                              or "round 1" in e.lower() or "round 2" in e.lower()]
            assert state["round1"], (
                "a 2-round chain opened in the side panel rendered NO Round 1 synthesis "
                f"(thread manifest never hydrated). eyebrows={state['eyebrows']!r}"
            )
            assert state["round2"], (
                "a 2-round chain opened in the side panel rendered ONLY Round 1 — Round 2 "
                "is MISSING, so the in-panel thread_manifest host query collapsed the chain "
                f"to a single segment (the green-while-the-value-is-gone symptom). eyebrows={state['eyebrows']!r}"
            )
            assert int(state["routingPanels"]) >= 2, (
                "a 2-round chain must render a routing-label panel PER round; only "
                f"{state['routingPanels']} rendered — Round 2 didn't hydrate. eyebrows={state['eyebrows']!r}"
            )
            assert any("round 2" in e.lower() for e in round_eyebrows), (
                f"no 'Round 2' eyebrow on the multi-segment page: {state['eyebrows']!r}"
            )
            assert state["refinementVisible"], (
                "round 2's user refinement directive didn't render — the chain segment "
                "hydrated without its metadata.user_refinement eyebrow."
            )
            # PAINT: no horizontal overflow at the narrow panel width.
            assert int(state["docW"]) <= int(state["vw"]) + 1 and not state["overEls"], (
                f"the chain live-council page overflows the {state['vw']}px panel: "
                f"docW={state['docW']} overflowing={state['overEls']}"
            )
        finally:
            ctx.close()
