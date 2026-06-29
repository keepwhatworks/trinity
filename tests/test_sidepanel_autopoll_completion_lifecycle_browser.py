"""The launchpad's OWN auto-poll completion branch — the STAY-on-the-launchpad
path — must land the user on the completed council's VERDICT in the side panel,
not a blank page.

The coverage gap this closes (distinct from the "Open council page" CLICK path
that test_sidepanel_launch_to_completion_lifecycle already drives):

  When the user clicks Launch and STAYS on the launchpad (never clicks "Open
  council page"), `startOperationPolling` (launchpad_template.py) keeps polling
  the council status. On `completed` it calls
  `navigateToReviewPath(status.review_path, status.council_id)`. In the SANDBOX
  (side panel) a sandboxed page can't self-navigate to the absolute
  ~/.trinity/review_pages/… path, so navigateToReviewPath collapses to the
  in-panel ./live_council.html sibling.

  The BUG (driven 2026-06-18): before the fix it collapsed to a TOKEN-LESS bare
  ./live_council.html — no status_token AND no council_id. The live page's init()
  then had nothing to load and fell to the loadActiveCouncilScript fallback, which
  reads _active_council.js — a sidecar ONLY written by the popup's
  `open-council-page` dispatch. On THIS path the user never clicked that, so the
  sidecar was never written; the host's active_council query returned null and the
  live page rendered BLANK (body length ~19 chars, zero segments). The council
  completed but the user landed on an empty page and NEVER SAW THE VERDICT — the
  signature "green while the value is gone" strand, on the panel's own auto-poll
  completion branch.

  The fix carries status.council_id through navigateToReviewPath so the sandbox
  navigates to ./live_council.html?council_id=<cid>, and the live page hydrates the
  outcome via its existing ?council_id= init path (host-fetched council_outcome) —
  the same identity finalize_council_run_state writes into the completed status
  alongside review_path.

This drives the REAL side panel with a STUBBED dispatch (nothing hits a real
council) but the REAL delegating capture-host (council_status / council_outcome are
genuine host reads of seeded files). It seeds a RUNNING activeOperation so the
launchpad RESUMES polling on mount, writes a RUNNING status, then flips the status
to COMPLETED *with a review_path* (the real completed-council shape) — WITHOUT ever
clicking "Open council page" and WITHOUT writing _active_council.js — and asserts
the panel transitions to the live council page carrying the council_id and renders
the SEEDED synthesis verdict in-place. NOT a blank token-less page.

Mutation-proven: revert navigateToReviewPath's sandbox branch to the token-less
bare ./live_council.html (the un-fixed code) and rebuild — the verdict-renders
assertion reds with the exact strand symptom.

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

CID = "council_autopoll01"
SYNTH = "AUTOPOLL-STAY SYNTHESIS verdict text."
TOKEN = "autopoll_tok_001"


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
        agreed_claims=["launch agreed point"], disagreed_claims=[],
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
    """Seed a synthetic home + completed outcome + a RUNNING activeOperation (so the
    launchpad resumes polling on mount), stub the native host (REAL capture-host
    handlers for every query but launchpad_data), load the real extension, open the
    side panel, return (ctx, ext_id, page, errors) after mount."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_completed_outcome()
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    # A RUNNING activeOperation so init() resumes startOperationPolling on mount —
    # the STAY-on-the-launchpad path (no "Open council page" click).
    payload["pageData"]["activeOperation"] = {
        "kind": "council", "status": "running", "task_text": "why is the sky blue?",
        "status_token": TOKEN, "memberOrder": ["claude", "codex"],
    }
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
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page, errors, home


def test_launchpad_autopoll_completion_renders_verdict_in_panel(tmp_path, monkeypatch):
    """STAY on the launchpad → its auto-poll sees the council complete → the panel
    transitions to the live council page carrying council_id and renders the
    VERDICT. NOT a blank token-less ./live_council.html."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.council_status import write_council_status

    with sync_playwright() as p:
        ctx, ext_id, page, errors, home = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )
            # The launchpad RESUMED polling the running activeOperation on mount.
            assert lf.evaluate("()=>!!document.querySelector('.spinner-row')"), (
                "the launchpad did not resume the running spinner on mount — the "
                "stay-on-launchpad auto-poll path isn't being exercised"
            )

            # The native host has the REAL handlers; write a RUNNING status so the
            # resumed poll keeps spinning (not a give-up). Inject a LIVE runner_pid —
            # the council_runner ALWAYS writes one (os.getpid/os.getpgid) on a real
            # running status, so a genuinely-running council has a live pid. The host
            # council_status query now routes through load_council_status, which
            # coerces a running council whose runner is DEAD to 'failed' (the
            # dead-runner staleness gate). A pidless test seed would read as dead and
            # clear the spinner — so model production: a live pid keeps it running.
            import os as _os

            write_council_status(
                TOKEN, status="running", task_text="why is the sky blue?",
                council_id=CID, metadata={"round_number": 1},
                members={"claude": {"status": "running"}, "codex": {"status": "pending"}},
                synthesis={"status": "pending"}, active_provider="claude",
            )
            from trinity_local.council_status import (
                _write_status,
                council_status_json_path,
            )

            _running = json.loads(council_status_json_path(TOKEN).read_text(encoding="utf-8"))
            _running["runner_pid"] = _os.getpid()  # the live test process = a live runner
            _write_status(TOKEN, _running)
            page.wait_for_timeout(2200)
            assert lf.evaluate("()=>!!document.querySelector('.spinner-row')"), (
                "the spinner cleared on a RUNNING status — the poll mis-read running"
            )

            # ── Flip to COMPLETED *with a review_path* — the REAL completed shape.
            # We do NOT click "Open council page" and do NOT write _active_council.js:
            # this is purely the launchpad's OWN auto-poll completion branch.
            write_council_status(
                TOKEN, status="completed", task_text="why is the sky blue?",
                council_id=CID, metadata={"round_number": 1},
                members={"claude": {"status": "done"}, "codex": {"status": "done"}},
                synthesis={"status": "done"},
                review_path=str(home / "review_pages" / "some_review.html"),
            )
            page.wait_for_timeout(5500)  # 1.5s poll + nav broker swap + live mount + outcome hydrate

            cf = page.frames[-1]
            done = cf.evaluate(
                "()=>{const body=document.body?(document.body.innerText||''):'';"
                "const vw=document.documentElement.clientWidth;"
                "const over=[...document.querySelectorAll('*')]"
                ".filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "return {url:location.href,"
                " isLive:location.href.includes('sandbox/live_council.html'),"
                " hasCouncilId:location.href.includes('council_id='),"
                " synthesis:body.includes('AUTOPOLL-STAY SYNTHESIS'),"
                " complete:(body.includes('complete')||body.includes('Complete')),"
                " bodyLen:body.length,"
                " blocked:body.toLowerCase().includes('blocked'),"
                " neverStarted:body.includes('never started'),"
                " failed:body.includes('Council failed'),"
                " rawLeak:(document.body?document.body.innerHTML:'').includes('{{'),"
                " overflow:over, docW:document.documentElement.scrollWidth, vw:vw};}"
            )

            # The panel swapped to the live council page (the nav broker fired).
            assert done["isLive"], (
                f"the launchpad's auto-poll completion did NOT transition to the in-panel "
                f"live council page: {done['url']!r}"
            )
            assert "chrome-error" not in (done["url"] or ""), (
                f"the auto-poll completion landed on a chrome-error page: {done['url']!r}"
            )
            assert not done["blocked"], "the live page shows 'blocked by Chrome' after auto-poll completion"
            # THE BUG: a token-less ./live_council.html in the sandbox renders BLANK.
            # The fixed code carries council_id so the live page hydrates the outcome.
            assert done["hasCouncilId"], (
                "the panel navigated to a TOKEN-LESS ./live_council.html on the auto-poll "
                "completion branch — no council_id in the URL, so the live page has nothing "
                "to load and falls to the _active_council.js fallback (never written on this "
                "path). NAVIGATE-TO-NOWHERE: the user lands on a blank page after the council "
                f"completed. URL: {done['url']!r}"
            )
            assert done["synthesis"], (
                "the completed council's SYNTHESIS VERDICT never rendered in the panel on the "
                "STAY-on-launchpad auto-poll completion branch — the user Launched, the council "
                "ran and finished, and they landed on a BLANK live_council.html instead of the "
                "answer (green-while-the-value-is-gone / navigate-to-nowhere; "
                f"body length {done['bodyLen']})"
            )
            assert done["complete"], "the live page never showed the council as complete after auto-poll completion"
            assert not done["neverStarted"], "the completed live page falsely reports 'never started'"
            assert not done["failed"], "the completed live page falsely reports 'Council failed'"
            assert not done["rawLeak"], "raw {{ }} leaked — the live_council app never mounted in-panel"
            assert int(done["docW"]) <= int(done["vw"]) + 1 and done["overflow"] == 0, (
                f"the completed live council page overflows the {done['vw']}px panel: "
                f"docW={done['docW']} overflowing={done['overflow']}"
            )
            assert errors == [], f"console errors during the auto-poll completion lifecycle: {errors}"
        finally:
            ctx.close()
