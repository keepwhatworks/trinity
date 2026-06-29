"""A DEAD+STALE "running" council must reach the TERMINAL "Council failed" state in
the REAL side panel — not spin "Council running" forever.

Found 2026-06-21 driving the RUNNING live council page in the real extension side
panel. The live council page, when it runs in the opaque-origin sandbox the side
panel uses, reads its status through the native-messaging HOST RPC
(`__trinityHostQuery('council_status', …)` → capture_host `_query_council_status`),
NOT the script-tag path the served/file:// launchpad uses. That host query read the
status `.js` file RAW (`_read_js_object`) and returned it verbatim — bypassing
`council_status._coerce_stale_running_status`, the one place that turns a "running"
council whose RUNNER IS DEAD (crashed / killed / its pid reused) and whose status is
STALE (>30 min since the last write) into a terminal "failed". So a council whose
runner exited mid-flight painted **"Council running"** with the spinner cycling its
witty messages FOREVER in the side panel — an infinite spinner with no terminal
state, no "Try again", no honest "the runner exited" card. The user can't tell the
council died; they just watch it spin.

The CLASS + the parallel-surface drift: the ACTION path (`_read_council_status`, the
popup's `get-council-status` poll) and the launchpad_data scan BOTH route through
`load_council_status` (which applies the coercion). The QUERY path
(`_query_council_status`, the side panel's live-council read) was the un-coerced
sibling — the same data, two host handlers, one honest and one not. The fix routes
`_query_council_status` through `load_council_status` too, so every host read-path
gets the dead-runner coercion.

Why this needs a REAL-panel guard: the host-RPC read-path ONLY runs in the opaque-
origin sandbox the Chrome side panel actually uses (`__trinityHostFetch()` true).
The served/file:// live council reads the status via a script tag — a completely
different code path that never hits `_query_council_status`. A unit test on the
served page would stay green while the panel spun forever. This drives the REAL
extension side panel, delegates the council_status query to the REAL capture-host
`QUERY_HANDLERS` (where the fix lives), seeds a genuinely dead+stale running council
on disk, brokers the panel iframe to it, and asserts the panel reaches the terminal
failed card.

Mutation-proven: reverting `_query_council_status` to the raw `_read_js_object` read
makes the panel paint "Council running" with the spinner + an enabled "Stop council"
button (no terminal state) → the terminal-state assertions red with the founder
symptom (an infinite spinner on a dead council).

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
TOKEN = "dead_runner_stale_panel"


def _seed_dead_stale_running(home: Path) -> dict:
    """Write a 'running' status whose runner pid is DEAD and whose updated_at is
    ancient (>30 min). Returns the on-disk payload so the test can assert the
    discriminating seed RENDER-INDEPENDENTLY (the seed is the discriminator, not the
    painted value)."""
    sys.path.insert(0, str(REPO / "src"))
    from trinity_local.council_status import (  # noqa: E402
        council_status_json_path,
        write_council_status,
    )

    write_council_status(
        TOKEN,
        status="running",
        task_text="A council whose runner crashed mid-flight.",
        bundle_id="bundle_dead",
        council_id="council_dead",
        members={
            "claude": {"status": "done", "response_text": "a landed answer " * 6},
            "codex": {"status": "running", "reasoning_summary": "was mid-thought when it died"},
        },
        synthesis={"status": "pending"},
        active_provider="codex",
        active_providers=["codex"],
        metadata={"members": ["claude", "codex"], "round_number": 1},
    )
    # Inject the discriminator DIRECTLY on disk (no load_council_status — that would
    # coerce it here, defeating the seed). A pid of 999999 is not a live process;
    # an ancient updated_at is past the 30-min staleness floor.
    jp = council_status_json_path(TOKEN)
    obj = json.loads(jp.read_text(encoding="utf-8"))
    obj["runner_pid"] = 999999
    obj["updated_at"] = "2026-01-01T00:00:00+00:00"
    jp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    js = jp.with_suffix(".js")
    js.write_text(
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(TOKEN)}] = "
        f"{json.dumps(obj, separators=(',', ':'))};\n",
        encoding="utf-8",
    )
    return obj


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home + a dead+stale council, stub the native host (delegating
    every non-launchpad_data query to the REAL capture host QUERY_HANDLERS — where the
    council_status coercion fix lives), load the real extension, open the side panel."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import build_launchpad_payload  # noqa: E402

    # Build the launchpad payload FIRST, then seed the dead council. build_launchpad_
    # payload() runs _active_launchpad_operation, which calls load_council_status on
    # every status file and COERCES a dead+stale running council to failed on disk —
    # a SECOND (launchpad-scan) defense. Seeding after the payload build keeps the
    # on-disk status genuinely 'running' so the panel's live-council HOST RPC
    # (_query_council_status) is the ONLY read path under test. Otherwise the scan
    # masks the host-RPC bug and the guard wouldn't bite on the un-fixed code.
    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    seed = _seed_dead_stale_running(home)

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
    return ctx, ext_id, page, errors, seed


def test_dead_stale_council_reaches_terminal_failed_in_panel(tmp_path, monkeypatch):
    """A dead+stale 'running' council read through the side panel's host RPC must be
    coerced to terminal 'failed' — never spin 'Council running' forever."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors, seed = _boot_panel(p, tmp_path, monkeypatch)
        try:
            # BITE PRECONDITION (B): the seed is the discriminator, checked on the
            # FIXTURE CONSTANTS render-independently — the on-disk status is genuinely
            # "running" with a dead pid + ancient timestamp. If this isn't a dead+stale
            # running council, the test proves nothing.
            assert seed["status"] == "running", f"seed must be 'running': {seed['status']}"
            assert seed["runner_pid"] == 999999, "seed must carry a dead runner pid"
            assert seed["updated_at"] == "2026-01-01T00:00:00+00:00", "seed must be stale"
            assert seed["members"]["codex"]["status"] == "running", (
                "seed must have a member still 'running' (the thing that would spin forever)"
            )

            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # BITE PRECONDITION (A.1): the real opaque-origin host-fetch path — not the
            # script-tag path. The bug only exists on the host RPC.
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox host-RPC path"
            )

            # Broker the panel iframe to the dead council the way the sidepanel bridge
            # does on a __trinityNav message.
            page.evaluate(
                "(t)=>{document.getElementById('app').src="
                "'sandbox/live_council.html?status_token='+t;}", TOKEN
            )
            page.wait_for_timeout(5000)  # broker swap + mount + host poll round-trip(s)
            cf = page.frames[-1]
            assert "sandbox/live_council.html" in (cf.url or ""), (
                f"the broker did NOT swap to the live council page: {cf.url}"
            )

            state = cf.evaluate(
                "()=>{const vw=document.documentElement.clientWidth;"
                "const spinnerRow=document.querySelector('.spinner-row strong');"
                "const statusErr=[...document.querySelectorAll('.status-error')].map(e=>e.textContent.trim());"
                "const badges=[...document.querySelectorAll('.provider-status-badge')].map(b=>b.textContent.trim());"
                "const stop=[...document.querySelectorAll('.live-actions button')]"
                ".map(b=>b.textContent.trim());"
                "const over=[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>vw+1).length;"
                "return {spinnerRunning: spinnerRow?spinnerRow.textContent.trim():null,"
                " statusErr, badges, stopButtons:stop,"
                " hasSpinner:!!document.querySelector('.spinner'),"
                " rawLeak:(document.body.innerHTML||'').includes('{{'),"
                " mounted:!!document.querySelector('#live-council-app'),"
                " docW:document.documentElement.scrollWidth, vw:vw, over:over};}"
            )

            # BITE PRECONDITION (A.2): the live council app actually painted (no raw
            # template leak, app mounted) — so a terminal-state assert below isn't
            # vacuously passing on an un-mounted page.
            assert state["mounted"], "the live council app never mounted in the panel"
            assert not state["rawLeak"], "raw {{ }} leaked — the live_council app lost its mount"

            # THE FIX, sole-keyed on the host-RPC coercion binding:
            # 1. The dead council must NOT show the running spinner card.
            assert state["spinnerRunning"] != "Council running", (
                "a DEAD+STALE council painted 'Council running' in the side panel — the host "
                "RPC (_query_council_status) returned the raw 'running' status verbatim, bypassing "
                "the dead-runner coercion. The panel spins forever on a council whose runner exited."
            )
            assert not state["hasSpinner"], (
                "the dead council still shows a spinner in the panel — it reads as a live, "
                "in-flight council, not a runner that exited"
            )
            # 2. It must reach the honest TERMINAL failed card.
            assert any("Council failed" in e for e in state["statusErr"]), (
                f"the dead council never reached the terminal 'Council failed' card "
                f"(status errors on the page: {state['statusErr']}). It spins instead of "
                "telling the user the runner exited."
            )
            # 3. No live "Stop council" affordance on a council that already died (that
            #    would dispatch a stop against a non-existent runner).
            assert not any("Stop council" in b for b in state["stopButtons"]), (
                f"a dead council still offers 'Stop council' in the panel: {state['stopButtons']} — "
                "it reads as still-running"
            )

            # PAINT — no overflow at the narrow panel width.
            assert int(state["docW"]) <= int(state["vw"]) + 1 and not state["over"], (
                f"the dead-council failed card overflows the {state['vw']}px panel: "
                f"docW={state['docW']} overflowing={state['over']}"
            )
            assert errors == [], f"console errors during the in-panel dead-council read: {errors}"
        finally:
            ctx.close()


def test_query_council_status_coerces_dead_runner(tmp_path, monkeypatch):
    """Unit-level companion: the host QUERY handler itself must return the coerced
    'failed' (not raw 'running') for a dead+stale council — pins the fix at the
    binding even when Chromium isn't available."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    seed = _seed_dead_stale_running(home)
    assert seed["status"] == "running" and seed["runner_pid"] == 999999  # discriminator

    from trinity_local.capture_host import _query_council_status  # noqa: E402

    resp = _query_council_status({"query_kind": "council_status", "status_token": TOKEN})
    assert resp["ok"] is True
    result = resp["result"] or {}
    assert result.get("status") == "failed", (
        "the host council_status QUERY returned a non-failed status for a dead+stale "
        f"running council — the coercion was bypassed: {result.get('status')!r}. The side "
        "panel reads this verbatim and spins 'Council running' forever."
    )
    assert (result.get("members") or {}).get("codex", {}).get("status") == "failed", (
        "the still-'running' member wasn't coerced to failed on the host read-path"
    )
