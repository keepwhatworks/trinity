"""A PARTIAL council (one member FAILED) must read HONESTLY end-to-end IN THE REAL
SIDE PANEL — the "Failed" member badge AND the aggregate "⚠ N provider attempted
but failed … this synthesis is over the M that responded" disclosure note must both
render, with the correct M, through BOTH the live poll state AND after the outcome
hydrate.

Why this needs its OWN real-panel guard (the coverage gap this closes):
  Iter 76 found + fixed the failed-member disclosure VANISHING on the live POLL
  path: update_member_failure flips the member to status:'failed' in the status
  sidecar's members map but NEVER writes metadata.failed_members (the runner only
  writes that into the persisted OUTCOME). The fix made failedMembersFor take
  Math.max of metadata.failed_members.length AND the count of failed member ROWS,
  so the poll path recovers the count from the same data the row badge already
  trusts. BUT that fix was only ever driven on the HTTP live_council.html page
  (?status_token= over http). The REAL side panel reaches the same page through a
  DIFFERENT data spine — the opaque-origin sandbox + the host-fetch loaders
  (__TRINITY_HOST_FETCH__ → __trinityHostQuery('council_status'/'council_outcome')
  → the native capture-host reading council_status_<token>.js / <cid>.js). And the
  panel's COMPLETED state runs an extra leg the HTTP poll test never exercised:
  startPolling's status==='completed' branch fires _loadOutcomeIntoSegment, which
  REPLACES runState with outcomeToRunState(outcome) — and outcomeToRunState builds
  the members map ONLY from member_results (responders, all status:'done'), so the
  failed provider has NO row after the hydrate. From that point the disclosure note
  relies ENTIRELY on metadata.failed_members surviving in the outcome. If the
  outcome's metadata ever drops failed_members (or a refactor stops reading it after
  the hydrate clobbers the failed ROW), the panel would render the council as a
  CLEAN 2-of-2 — the casualty silently erased — the exact green-while-degraded
  state-contradiction shape Iter 76 fixed on the OTHER path. No existing test drives
  the failed-member disclosure IN THE PANEL, through the host-fetch spine, or across
  the outcome-hydrate transition.

This drives the REAL extension side panel with a STUBBED dispatch (so nothing hits
a real council) but the REAL delegating capture-host (council_status / council_outcome
are genuine host reads of seeded files). It launches, writes a RUNNING 3-member
status with codex FAILED and NO metadata.failed_members (the exact live poll shape),
opens the in-panel live page, and asserts:
  1. RUNNING/just-polled: the codex member renders a "Failed" badge AND the
     disclosure note fires reading "over the 2 that responded" + "1 provider
     attempted but failed" (NOT double-counted to 3, NOT suppressed).
Then it flips the status to COMPLETED (so _loadOutcomeIntoSegment hydrates from the
seeded outcome, whose member_results are the 2 RESPONDERS and whose
metadata.failed_members=['codex'] is the real runner shape) and asserts:
  2. COMPLETED (post-hydrate): the disclosure note SURVIVES — "1 provider attempted
     but failed … over the 2 that responded" still renders, the synthesis verdict
     shows, and the casualty was NOT silently erased by the outcome hydrate.

Mutation-proven: reverting failedMembersFor to the old metadata-only logic reds the
RUNNING-state assertion (the poll path has no metadata.failed_members, so the note
vanishes — Iter 76's exact bug, now on the panel). Restored byte-identical.

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

CID = "council_failedmember01"
SYNTH = "FAILED-MEMBER PANEL SYNTHESIS verdict text."

# Stub dispatch: resolve onResult ok so the optimistic launch operation stays
# 'running' (so we can read the launchpad-generated status_token). NEVER touches
# a real extension / council.
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


def _seed_partial_outcome() -> None:
    """The completed OUTCOME the live page hydrates on completion: member_results
    are the TWO RESPONDERS only (codex, the casualty, has no member result — exactly
    what create_council_outcome produces) and metadata.failed_members=['codex'] (the
    real runner shape, written into final_metadata at council_runner.py L870)."""
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                            output_text="Claude's partial-council answer. " * 12),
        CouncilMemberResult(provider="antigravity", model="gemini-3.1-pro",
                            output_text="Gemini's partial-council answer. " * 12),
    ]
    label = CouncilRoutingLabel(
        winner="claude", runner_up="antigravity", confidence="high", task_type="design",
        provider_scores={"claude": {"overall": 0.84}, "antigravity": {"overall": 0.66}},
        agreed_claims=["partial agreed point"],
        disagreed_claims=[{"claim": "partial tradeoff", "providers_for": ["claude"],
                           "providers_against": ["antigravity"], "why_matters": "clarity"}],
    )
    save_council_outcome(CouncilOutcome(
        council_run_id=CID, bundle_id=CID, task_cluster_id="cluster_partial",
        primary_provider="claude", primary_model="claude-opus-4-8",
        winner_provider="claude", winner_model="claude-opus-4-8", agreement_score=0.74,
        # The real runner shape: failed providers recorded in metadata.failed_members.
        metadata={"task_text": "why is the sky blue?", "round_number": 1,
                  "failed_members": ["codex"]},
        member_results=members, synthesis_prompt="Review.",
        synthesis_output=SYNTH, routing_label=label,
        created_at="2026-06-18T01:00:00+00:00",
    ))


def _boot_panel(p, tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_partial_outcome()  # the outcome the live page hydrates on completion
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    # launchpad_data → the prebuilt payload; every OTHER query (council_status,
    # council_outcome) → the REAL capture-host handler, so the disclosure render
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


def test_failed_member_disclosure_holds_in_panel(tmp_path, monkeypatch):
    """A 2-of-3 council (codex failed) must show the Failed badge + the honest
    disclosure note IN THE PANEL, both on the live poll AND after the outcome
    hydrate — the Iter-76 fix must hold on the host-fetch spine."""
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

            # ── Launch → read the launchpad-generated status_token ────────────
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

            # Write a RUNNING status with codex FAILED and NO metadata.failed_members
            # — the EXACT live poll shape update_member_failure produces (the member
            # flips to status:'failed' in the map; the sidecar metadata never carries
            # failed_members). claude + antigravity responded; codex is the casualty.
            write_council_status(
                token, status="running", task_text="why is the sky blue?",
                council_id=CID, metadata={"round_number": 1},
                members={
                    "claude": {"status": "done", "response_text": "Claude answered."},
                    "antigravity": {"status": "done", "response_text": "Gemini answered."},
                    "codex": {"status": "failed",
                              "reasoning_summary": "codex exec exited with code 1: quota exceeded."},
                },
                synthesis={"status": "running"}, active_provider="claude",
            )
            # Inject a LIVE runner_pid — the council is genuinely in-flight (synthesis
            # running). The host council_status query now coerces a DEAD-runner
            # 'running' council to 'failed'; a pidless seed would read as dead and
            # blank the in-flight failed-member disclosure. Model production.
            import os as _os

            from trinity_local.council_status import (
                _write_status,
                council_status_json_path,
            )

            _run = json.loads(council_status_json_path(token).read_text(encoding="utf-8"))
            _run["runner_pid"] = _os.getpid()
            _write_status(token, _run)

            # ── Open the in-panel live page (the nav broker swaps the frame) ──
            lf.locator(".launch-status-actions a.button.ghost").first.click(timeout=5000)
            page.wait_for_timeout(3500)  # nav broker swap + live page mount + first poll

            cf = page.frames[-1]
            running = cf.evaluate(
                "()=>{const body=document.body.innerText||'';"
                "const badges=[...document.querySelectorAll('.provider-status-badge')]"
                ".map(b=>b.textContent.trim());"
                "const note=[...document.querySelectorAll('.status-error')]"
                ".map(n=>n.textContent.replace(/\\s+/g,' ').trim())"
                ".filter(t=>t.includes('attempted but failed'));"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " failedBadge:badges.includes('Failed'),"
                " note:note[0]||'',"
                " blocked:body.toLowerCase().includes('blocked'),"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " bodyLen:body.length};}"
            )

            assert running["isLive"], (
                f"the in-panel live page never mounted (nav broker / host-fetch poll broke): {running['url']!r}"
            )
            assert not running["blocked"], "the live page shows 'blocked by Chrome' (opaque-origin self-nav block)"
            assert not running["rawLeak"], "raw {{ }} leaked — the live_council app never mounted in-panel"
            # The codex casualty renders HONESTLY as a Failed badge row (the same data
            # source the disclosure note must agree with).
            assert running["failedBadge"], (
                "the failed codex member did NOT render a 'Failed' badge in the panel — "
                "the host-fetch council_status poll dropped its status:'failed' row"
            )
            # THE ITER-76 INVARIANT ON THE PANEL: the aggregate disclosure note fires,
            # reading the count from the failed ROW (the poll sidecar carries NO
            # metadata.failed_members). A regression to metadata-only counting makes
            # this VANISH while the page still shows the Failed badge one row up — the
            # green-while-degraded state-contradiction Iter 76 fixed (now on the panel).
            assert running["note"], (
                "GREEN-WHILE-DEGRADED IN THE PANEL: the council shows a 'Failed' member "
                "badge but the honest-degradation disclosure note SILENTLY VANISHED on "
                "the live poll path — failedMembersFor read only metadata.failed_members "
                "(absent on the host-fetch council_status sidecar), so the Iter-76 fix "
                "regressed on the panel's host-fetch spine"
            )
            assert "1 provider" in running["note"] and "over the 2 that responded" in running["note"], (
                f"the disclosure note mis-counts the partial council in the panel "
                f"(should be '1 provider … over the 2 that responded'): {running['note']!r}"
            )
            assert "3 that responded" not in running["note"], (
                f"the disclosure note double-counted the failed member into the responder "
                f"count (self-contradicting '1 failed … over the 3 that responded'): {running['note']!r}"
            )

            # ── Flip to COMPLETED → _loadOutcomeIntoSegment hydrates from the
            #    seeded outcome (member_results = the 2 responders; the failed ROW
            #    DISAPPEARS, so the note must now ride metadata.failed_members) ───
            write_council_status(
                token, status="completed", task_text="why is the sky blue?",
                council_id=CID, metadata={"round_number": 1},
                members={
                    "claude": {"status": "done", "response_text": "Claude answered."},
                    "antigravity": {"status": "done", "response_text": "Gemini answered."},
                    "codex": {"status": "failed",
                              "reasoning_summary": "codex exec exited with code 1: quota exceeded."},
                },
                synthesis={"status": "done"},
            )
            page.wait_for_timeout(4500)  # 1.5s poll + outcome hydrate

            done = cf.evaluate(
                "()=>{const body=document.body.innerText||'';const vw=document.documentElement.clientWidth;"
                "const over=[...document.querySelectorAll('*')]"
                ".filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "const note=[...document.querySelectorAll('.status-error')]"
                ".map(n=>n.textContent.replace(/\\s+/g,' ').trim())"
                ".filter(t=>t.includes('attempted but failed'));"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " synthesis:body.includes('FAILED-MEMBER PANEL SYNTHESIS'),"
                " note:note[0]||'',"
                " stillSpinning:!!document.querySelector('.spinner-row'),"
                " blocked:body.toLowerCase().includes('blocked'),"
                " failed:body.includes('Council failed'),"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " overflow:over, docW:document.documentElement.scrollWidth, vw:vw};}"
            )

            assert done["isLive"], (
                f"the live page navigated AWAY on completion instead of rendering in-place: {done['url']!r}"
            )
            assert not done["stillSpinning"], "the running spinner never cleared after completion"
            assert not done["blocked"], "the live page shows 'blocked by Chrome' after completion"
            assert not done["failed"], "the partial council falsely reports 'Council failed' (it had 2 responders)"
            assert not done["rawLeak"], "raw {{ }} leaked after the in-place completion render"
            # The synthesis verdict (over the 2 responders) hydrated.
            assert done["synthesis"], (
                "the partial council's SYNTHESIS VERDICT never rendered after completion — "
                "the council_outcome hydrate never ran (green-while-the-value-is-gone)"
            )
            # THE COMPLETED-STATE INVARIANT: after _loadOutcomeIntoSegment clobbers
            # runState with outcomeToRunState (member_results = responders only, so the
            # failed ROW is gone), the disclosure note must SURVIVE by reading
            # metadata.failed_members from the outcome. If it vanishes here the panel
            # renders the partial council as a CLEAN 2-of-2 — the casualty erased.
            assert done["note"], (
                "GREEN-WHILE-DEGRADED IN THE PANEL (completed state): after the outcome "
                "hydrate the disclosure note VANISHED — the panel renders the 2-of-3 council "
                "as a clean 2-of-2, silently erasing the codex casualty (the outcome's "
                "metadata.failed_members was dropped or no longer read after the hydrate "
                "clobbers the failed ROW)"
            )
            assert "1 provider" in done["note"] and "over the 2 that responded" in done["note"], (
                f"the completed-state disclosure note mis-counts the partial council "
                f"(should be '1 provider … over the 2 that responded'): {done['note']!r}"
            )
            assert int(done["docW"]) <= int(done["vw"]) + 1 and done["overflow"] == 0, (
                f"the completed partial-council live page overflows the {done['vw']}px panel: "
                f"docW={done['docW']} overflowing={done['overflow']}"
            )
            assert errors == [], f"console errors during the failed-member panel lifecycle: {errors}"
        finally:
            ctx.close()
