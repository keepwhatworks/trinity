"""The FULL happy-path council lifecycle — composer Launch → running spinner →
the live council page mounted IN THE PANEL → the completed verdict — must advance
through every transition without STRANDING, driven end-to-end in the REAL side panel.

Why this needs its own real-panel guard (the coverage gap this closes):
  test_sidepanel_dispatch_lifecycle_browser drives Launch and asserts the running
  banner + that the "Open council page" link's HREF resolves to the in-panel
  ./live_council.html sibling — but it NEVER CLICKS that link, so the actual
  click-through (sandbox click-interceptor → __trinityNavigate postMessage → the
  shell's sidepanel-bridge NAV_RX → frame.src swap → the live page mounting in the
  opaque-origin sandbox → its status poll → the in-place running→completed render)
  was wholly UN-DRIVEN. test_sidepanel_chain_continue_lifecycle drives Continue on
  an ALREADY-OPEN council with a STUBBED status loader — never the fresh
  composer→live-page transition, and never the REAL host-fetch council_status /
  council_outcome path. So every wire on the most-used launch path — the nav
  broker, the live page's first mount, the poll-to-completion, the outcome hydrate —
  could regress while every existing test stayed green: a Launch whose "Open council
  page" lands on a blocked/blank page, a live page that never leaves the running
  spinner, a completion that never renders the verdict. That's Trinity's signature
  "green while the value is gone" shape, on the launch path the founder demos first.

This drives the REAL extension side panel with a STUBBED dispatch (resolves ok so
the optimistic operation stays running, nothing hits a real council) but the REAL
delegating capture-host (so council_status / council_outcome are genuine host reads
of seeded files, NOT a fabricated stub answer). It seeds a real completed outcome,
clicks Launch, reads the launchpad-generated status_token, writes a RUNNING status
file, CLICKS "Open council page", and asserts:
  1. The frame swapped IN-PANEL to ./live_council.html?status_token=<the launch
     token> — NOT a chrome-error / "blocked by Chrome" page (the opaque-origin
     self-nav block class), NOT a 404 "never started", NOT a raw-{{ flash.
  2. The live page mounts on the RUNNING status (spinner / running heading), NOT
     stuck blank and NOT a false "council never started".
Then it flips the status file to COMPLETED and asserts the live page transitions
IN-PLACE: the spinner clears, "complete" renders, and the SEEDED synthesis verdict
hydrates from the real council_outcome host read — no strand, no overflow at the
panel width, no console errors.

Mutation-proven: break the shell's NAV_RX (so the "Open council page" click is
ignored and the frame never swaps) and the in-panel-live-page assertion reds; drop
the completed-branch outcome hydrate and the verdict-renders assertion reds.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import re
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"

CID = "council_launchhappy01"
SYNTH = "LAUNCH-HAPPY-PATH SYNTHESIS verdict text."

# Stub dispatch: resolve onResult ok so the optimistic launch operation stays
# 'running' (the launchpad keeps polling), but capture the action so we can read
# the launchpad-generated status_token. NEVER touches a real extension / council.
_STUB_OK = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) {
      window.__lastDispatch = opts && opts.extensionAction;
      if (opts && opts.onResult) setTimeout(function () {
        opts.onResult({ ok: true, tier: 'extension' });
      }, 20);
    },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""


def _seed_completed_outcome() -> None:
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                            output_text="Claude's launch answer. " * 14),
        CouncilMemberResult(provider="codex", model="gpt-5.5",
                            output_text="Codex's launch answer. " * 14),
    ]
    label = CouncilRoutingLabel(
        winner="claude", runner_up="codex", confidence="high", task_type="design",
        provider_scores={"claude": {"overall": 0.84}, "codex": {"overall": 0.60}},
        agreed_claims=["launch agreed point"],
        disagreed_claims=[{"claim": "launch tradeoff", "providers_for": ["claude"],
                           "providers_against": ["codex"], "why_matters": "clarity"}],
    )
    save_council_outcome(CouncilOutcome(
        council_run_id=CID, bundle_id=CID, task_cluster_id="cluster_launch",
        primary_provider="claude", primary_model="claude-opus-4-8",
        winner_provider="claude", winner_model="claude-opus-4-8", agreement_score=0.78,
        metadata={"task_text": "why is the sky blue?", "round_number": 1},
        member_results=members, synthesis_prompt="Review.",
        synthesis_output=SYNTH, routing_label=label,
        created_at="2026-06-18T01:00:00+00:00",
    ))


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home + the completed outcome, stub the native host (the REAL
    capture-host handlers for every query but launchpad_data), load the real
    extension, open the side panel, return (ctx, ext_id, page, errors) after mount."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_completed_outcome()  # the outcome the live page hydrates on completion
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    # launchpad_data → the prebuilt payload; every OTHER query (council_status,
    # council_outcome) → the REAL capture-host handler, so the completion render
    # is driven by genuine host reads of the seeded files, not a fabricated stub.
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


def test_launch_open_council_page_running_then_completes_in_panel(tmp_path, monkeypatch):
    """Launch → click "Open council page" → the live page mounts IN-PANEL on the
    running status → flip to completed → the verdict hydrates IN-PLACE. No strand."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.council_status import write_council_status

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            # ── Launch ───────────────────────────────────────────────────────
            lf.evaluate(_STUB_OK)
            lf.fill("#council-prompt", "why is the sky blue?")
            lf.locator(".actions button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(800)

            href = lf.evaluate(
                "()=>{const a=document.querySelector('.launch-status-actions a.button.ghost');"
                "return a?a.getAttribute('href'):null;}"
            )
            assert href and href.startswith("./live_council.html?"), (
                f"'Open council page' did not resolve to the in-panel live page: {href!r}"
            )
            m = re.search(r"status_token=([A-Za-z0-9_]+)", href or "")
            assert m, f"the launchpad generated no status_token on the Open-council link: {href!r}"
            token = m.group(1)
            assert token.startswith("launch_"), f"unexpected launch token shape: {token!r}"

            # The native host has the REAL handlers; write a RUNNING status so the
            # live page mounts on a spinner (not a 404 "council never started").
            # Inject a LIVE runner_pid (the council_runner always writes one) — the
            # host council_status query now coerces a DEAD-runner 'running' council to
            # 'failed', so a pidless seed would read as dead. Model production.
            import os as _os

            write_council_status(
                token, status="running", task_text="why is the sky blue?",
                council_id=CID, metadata={"round_number": 1},
                members={"claude": {"status": "running"}, "codex": {"status": "pending"}},
                synthesis={"status": "pending"}, active_provider="claude",
            )
            from trinity_local.council_status import (
                _write_status,
                council_status_json_path,
            )

            _run = json.loads(council_status_json_path(token).read_text(encoding="utf-8"))
            _run["runner_pid"] = _os.getpid()
            _write_status(token, _run)

            # ── Click "Open council page" → broker swaps the frame in-panel ───
            lf.locator(".launch-status-actions a.button.ghost").first.click(timeout=5000)
            page.wait_for_timeout(3500)  # nav broker swap + live page mount + first poll

            cf = page.frames[-1]
            running = cf.evaluate(
                "()=>{const body=document.body.innerText||'';"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " hasToken:location.href.includes('status_token='),"
                " running:(body.includes('Council running')||body.includes('responding')"
                "          ||!!document.querySelector('.spinner-row')),"
                " done:body.includes('LAUNCH-HAPPY-PATH SYNTHESIS'),"
                " blocked:body.toLowerCase().includes('blocked'),"
                " neverStarted:body.includes('never started'),"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " bodyLen:body.length};}"
            )

            # 1. The frame ACTUALLY swapped in-panel to the live council page (the
            #    nav broker fired). A regression that drops the NAV_RX/frame.src swap
            #    leaves us on the launchpad (or a blocked chrome-error page).
            assert running["isLive"], (
                f"clicking 'Open council page' did NOT swap the panel to the in-panel live "
                f"council page — the nav broker never fired (sandbox self-nav blocked / dead "
                f"'Open council page'): {running['url']!r}"
            )
            assert "chrome-error" not in (running["url"] or ""), (
                f"'Open council page' landed on a chrome-error page (opaque-origin self-nav "
                f"block): {running['url']!r}"
            )
            assert running["hasToken"], (
                f"the in-panel live page lost the launch status_token — it can't poll: {running['url']!r}"
            )
            assert not running["blocked"], "the live page shows 'blocked by Chrome' after Open council page"
            # 2. It mounted on the RUNNING status (spinner), not a false 'never started'.
            assert not running["neverStarted"], (
                "the live page falsely reported the council 'never started' even though a "
                "running status file exists (the host-fetch council_status poll is broken)"
            )
            assert running["running"], (
                "the live page did NOT render the running/spinner state on a running status — "
                "it mounted blank or stuck (the running→ render strand)"
            )
            assert not running["done"], "the verdict rendered before the council completed (stale render)"
            assert not running["rawLeak"], "raw {{ }} leaked — the live_council app never mounted in-panel"

            # ── Flip to COMPLETED → the live page transitions IN-PLACE ────────
            write_council_status(
                token, status="completed", task_text="why is the sky blue?",
                council_id=CID, metadata={"round_number": 1},
                members={"claude": {"status": "done"}, "codex": {"status": "done"}},
                synthesis={"status": "done"},
            )
            page.wait_for_timeout(4500)  # 1.5s poll + outcome hydrate

            done = cf.evaluate(
                "()=>{const body=document.body.innerText||'';const vw=document.documentElement.clientWidth;"
                "const over=[...document.querySelectorAll('*')]"
                ".filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " synthesis:body.includes('LAUNCH-HAPPY-PATH SYNTHESIS'),"
                " complete:(body.includes('complete')||body.includes('Complete')),"
                " stillSpinning:!!document.querySelector('.spinner-row'),"
                " blocked:body.toLowerCase().includes('blocked'),"
                " neverStarted:body.includes('never started'),"
                " failed:body.includes('Council failed'),"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " overflow:over, docW:document.documentElement.scrollWidth, vw:vw};}"
            )

            # 3. The completion transition fired in-place: spinner cleared, the page
            #    stayed on the live council page, and the SEEDED synthesis verdict
            #    hydrated from the REAL council_outcome host read. A missing verdict =
            #    green-while-the-value-is-gone on the launch path (the council ran but
            #    the user never sees the answer).
            assert done["isLive"], (
                f"the live page navigated AWAY on completion instead of rendering in-place: {done['url']!r}"
            )
            assert not done["stillSpinning"], (
                "the running spinner NEVER cleared after the status completed — the "
                "running→completed transition stranded on a stuck spinner"
            )
            assert done["synthesis"], (
                "the completed council's SYNTHESIS VERDICT never rendered in the panel — the "
                "live page polled 'completed' but the council_outcome hydrate never ran, so the "
                "user sees no answer (green-while-the-value-is-gone on the launch happy path)"
            )
            assert done["complete"], "the live page never showed the council as complete after completion"
            assert not done["blocked"], "the live page shows 'blocked by Chrome' after completion"
            assert not done["neverStarted"], "the completed live page falsely reports 'never started'"
            assert not done["failed"], "the completed live page falsely reports 'Council failed'"
            assert not done["rawLeak"], "raw {{ }} leaked after the in-place completion render"
            assert int(done["docW"]) <= int(done["vw"]) + 1 and done["overflow"] == 0, (
                f"the completed live council page overflows the {done['vw']}px panel: "
                f"docW={done['docW']} overflowing={done['overflow']}"
            )
            assert errors == [], f"console errors during the launch→completion lifecycle: {errors}"
        finally:
            ctx.close()
