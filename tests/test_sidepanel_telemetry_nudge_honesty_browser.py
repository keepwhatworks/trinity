"""The settings telemetry nudge must not promise a CROSS-USER benefit a stock
install can't deliver.

UX-sweep finding: the settings modal's active-benefit nudge read, unconditionally,
"Catch more issues like the bugs you've hit by keeping sharing on. Anonymous usage
events let Trinity surface broken flows ... ACROSS USERS — issues a single install
can't see on its own." But a STOCK PUBLIC INSTALL ships NO GA4 credentials
(docs/telemetry-spec.md §1: "the public build ships no GA4 credentials ... both
the CLI and the launchpad silently no-op"). So `launchpad_telemetry_state()` pops
the `endpoint`, the panel's `displayedEndpoint` reads "Not configured", and
`maybeSendTelemetry()` early-returns — NOTHING ever leaves the machine. The nudge
promised a collective benefit ("surface broken flows across users") that is
structurally impossible on the very install reading it, while the same modal's
Endpoint row honestly said "Not configured" (a contradiction most users won't
connect). MISLEADING-copy / orphan-affordance class.

The fix adds a `shareIsLive` getter (sharing consented AND a usable endpoint) and
gates the active-benefit nudge on it. When the endpoint is NOT configured (the
stock-install reality) the modal shows the honest line instead: "Nothing is leaving
this machine. Sharing is on, but this build has no collector endpoint configured ...
no anonymous events are sent."

This drives the REAL extension side panel at the DEFAULT seeded home (no GA4 creds
→ endpoint "Not configured"), opens settings, and asserts:
  (a) the Endpoint row reads "Not configured" AND the sharing toggle is ON
      (precondition — the exact stock-install state the bug lived in; non-vacuous),
  (b) the overclaiming "across users" nudge is ABSENT,
  (c) the honest "Nothing is leaving this machine" line IS present.

Mutation-provable: drop the `v-if="shareIsLive"` gate (restore the unconditional
nudge) and (b) reds — the "across users" overclaim ships on a stock install. Slow
+ browser marked; skips without Playwright/chromium.
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


def _boot_panel(p, tmp_path, monkeypatch, disable_share=False):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    if disable_share:
        # Persist the consent toggle OFF so launchpad_telemetry_state() reports
        # telemetry.enabled=false — the state the honest-line v-else-if must gate on.
        from trinity_local import telemetry as _tel

        _tel.disable_telemetry()
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
    page.set_viewport_size({"width": 393, "height": 852})
    page.add_init_script("window.__TRINITY_DISPATCH__ = async () => ({ ok: true });")
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)
    return ctx, ext_id, page


def test_telemetry_nudge_does_not_overclaim_on_stock_install(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            lf.locator('button[aria-label="Open settings"]').first.click(timeout=5000)
            page.wait_for_timeout(800)

            probe = lf.evaluate(
                "()=>{"
                "const modal=document.querySelector('.settings-modal');"
                "if(!modal) return {open:false};"
                "const ps=[...modal.querySelectorAll('p.meta')].map(p=>p.textContent.replace(/\\s+/g,' ').trim());"
                "const epRow=[...modal.querySelectorAll('.setting-row')].find(r=>/Endpoint/.test(r.textContent));"
                "const epText=epRow?epRow.textContent.replace(/\\s+/g,' ').trim():'';"
                "const toggle=(modal.querySelector('.sharing-toggle input')||{}).checked;"
                "return {open:true,"
                " endpoint:epText,"
                " toggleOn:!!toggle,"
                " hasOverclaim:ps.some(s=>s.includes('across users')),"
                " hasHonest:ps.some(s=>s.includes('Nothing is leaving this machine')),"
                " allMeta:ps};"
                "}"
            )
            assert probe["open"], "the settings modal never opened"

            # Preconditions — the EXACT stock-install state the bug lived in. The
            # seed ships no GA4 creds, so launchpad_telemetry_state() pops the
            # endpoint → "Not configured", and the consent toggle defaults ON.
            # Non-vacuous: if either flips, the assertion below isn't testing the
            # right state.
            assert "Not configured" in probe["endpoint"], (
                "precondition failed: the seeded stock install should read "
                f"'Endpoint: Not configured' (got {probe['endpoint']!r}) — without "
                "this the nudge-honesty test isn't exercising the no-endpoint state"
            )
            assert probe["toggleOn"], (
                "precondition failed: telemetry sharing should default ON (the "
                "consent state) so the nudge is the thing under test, not the toggle"
            )

            # The DEFECT this guards: the active-benefit nudge promised "surface
            # broken flows ACROSS USERS" on an install that sends nothing.
            assert not probe["hasOverclaim"], (
                "the settings telemetry nudge promises a CROSS-USER benefit "
                "('surface broken flows across users') while the Endpoint is "
                "'Not configured' and the install sends NOTHING — the "
                "misleading-copy class the shareIsLive gate was added to kill: "
                f"{probe['allMeta']!r}"
            )
            assert probe["hasHonest"], (
                "with no endpoint configured the modal must tell the user the "
                "honest truth ('Nothing is leaving this machine') instead of the "
                f"overclaiming nudge: {probe['allMeta']!r}"
            )
        finally:
            ctx.close()


def test_telemetry_honest_line_hidden_when_sharing_off(tmp_path, monkeypatch):
    """The honest no-endpoint line literally says "Sharing is on" — so it must NOT
    render once the user turns the consent toggle OFF, or it claims sharing is on
    while it's off (the SAME self-contradiction the overclaim fix targets, in the
    other state). The line is a v-else-if="telemetry.enabled", not a bare v-else,
    so when sharing is off NEITHER nudge shows."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch, disable_share=True)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            lf.locator('button[aria-label="Open settings"]').first.click(timeout=5000)
            page.wait_for_timeout(800)
            probe = lf.evaluate(
                "()=>{"
                "const modal=document.querySelector('.settings-modal');"
                "if(!modal) return {open:false};"
                "const ps=[...modal.querySelectorAll('p.meta')].map(p=>p.textContent.replace(/\\s+/g,' ').trim());"
                "const toggle=(modal.querySelector('.sharing-toggle input')||{}).checked;"
                "return {open:true, toggleOn:!!toggle,"
                " hasOverclaim:ps.some(s=>s.includes('across users')),"
                " hasSharingOnLine:ps.some(s=>s.includes('Sharing is on') || s.includes('Nothing is leaving this machine')),"
                " allMeta:ps};"
                "}"
            )
            assert probe["open"], "the settings modal never opened"
            # Precondition (non-vacuous): the toggle is genuinely OFF.
            assert not probe["toggleOn"], (
                "precondition failed: this test needs the consent toggle OFF "
                "(disable_telemetry should have persisted sharing_enabled=false)"
            )
            # The DEFECT a bare v-else would reintroduce: claiming "Sharing is on"
            # while it's off.
            assert not probe["hasSharingOnLine"], (
                "with sharing toggled OFF the modal still showed the "
                "'Nothing is leaving this machine — Sharing is on …' line, which "
                "claims sharing is ON while it's OFF — a bare v-else mis-fires here; "
                f"the honest line must be v-else-if=\"telemetry.enabled\": {probe['allMeta']!r}"
            )
            assert not probe["hasOverclaim"], (
                f"the cross-user overclaim showed with sharing off: {probe['allMeta']!r}"
            )
        finally:
            ctx.close()
