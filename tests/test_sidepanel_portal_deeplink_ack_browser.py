"""A side-panel memory-viewer deep link must ACKNOWLEDGE the click — not no-op.

Founder-class bug shape (driven 2026-06-21 in the REAL side panel): the /stats
cheat-sheet rows carry memory-viewer deep links — the "→ topology" chip
(`../portal_pages/memory.html?file=topics.json&basin=…`), the first-cell
`picks.json` link, the "core / lens / topics / vocabulary" lens chips, and the
`routing.json` links. In the panel sandbox (opaque origin) these can't self-navigate
to memory.html (it isn't in the sandbox), so the runtime click interceptor bounces
them to `open-launchpad` — which opens the FULL dashboard in a SEPARATE BROWSER
WINDOW. But `open-launchpad` succeeds SILENTLY: handleDispatchResult only acts on
FAILURE, so on success the panel showed NOTHING. Clicking "→ topology" changed
nothing visible in the panel and read as a dead link / no-op (the dashboard opened
in a window the user may not even see, and it's the launchpad HOME, not the basin
they clicked).

The fix gives the click an IMMEDIATE, visible, AT-announced acknowledgment — a
transient `.portal-open-notice` (role=status) that NAMES the destination ("Opening
the topology view in the full dashboard …"), fired BEFORE the dispatch resolves.

This drives the REAL extension panel (the only surface that exercises the sandbox
opaque origin that triggers the open-launchpad bounce — a file:// render navigates
normally and never hits this path) and asserts, on a SUCCESSFUL open-launchpad:
the notice is VISIBLE, carries role=status, and names the clicked destination.

Mutation-provable: revert the `__trinityEmitPortalOpen` / `showPortalNotice` wire-up
and the success path is silent again → no `.portal-open-notice` ever shows → this
reds with the "dead no-op" founder symptom. Slow + browser marked.
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


def _boot_panel(p, tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
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
    page.wait_for_timeout(4000)
    return ctx, ext_id, page


# The dispatcher returns a SUCCESSFUL open-launchpad — the case that was silent.
_STUB_DISPATCH_OK = (
    "()=>{ window.__TRINITY_DISPATCH__ = { dispatch(o){ "
    "if(o && typeof o.onResult==='function') "
    "o.onResult({ok:true, tier:'extension', action:(o.extensionAction||{}).kind}); } }; }"
)


def _flip_to_stats(page):
    lf = page.frames[-1]
    assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
    lf.evaluate(_STUB_DISPATCH_OK)
    lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
    page.wait_for_timeout(900)
    return page.frames[-1]


def test_topology_chip_acknowledges_click_in_panel(tmp_path, monkeypatch):
    """Clicking the /stats "→ topology" chip in the side panel must show a visible,
    role=status acknowledgment that names the topology destination — not silently
    open the dashboard in another window leaving the panel unchanged (a dead no-op).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            sf = _flip_to_stats(page)
            chips = sf.locator("a.cortex-topology-chip")
            assert chips.count() >= 1, "no '→ topology' chip on the side-panel /stats cheat-sheet (seed regression?)"
            # PRECONDITION: no notice before the click — proves the notice is a
            # RESPONSE to the click, not always-present.
            pre = sf.evaluate(
                "()=>{const n=document.querySelector('.portal-open-notice');"
                "return n? (n.offsetParent!==null):false;}"
            )
            assert not pre, "portal-open notice was already visible before any click (always-on, not a response)"

            chips.first.click(timeout=5000)
            page.wait_for_timeout(450)
            post = sf.evaluate(
                "()=>{const n=document.querySelector('.portal-open-notice');"
                "return {present:!!n, visible:n?(n.offsetParent!==null):false,"
                " role:n?n.getAttribute('role'):null, live:n?n.getAttribute('aria-live'):null,"
                " text:n?(n.innerText||'').replace(/\\s+/g,' ').trim():''};}"
            )
            assert post["visible"], (
                "clicking '→ topology' in the side panel showed NO acknowledgment — "
                "the open-launchpad bounce succeeded SILENTLY and the click read as a dead no-op"
            )
            assert post["role"] == "status", f"portal-open notice is not a role=status region: {post['role']!r}"
            assert post["live"] == "polite", f"portal-open notice is not aria-live=polite: {post['live']!r}"
            # The ack must NAME the topology destination (lead-with-the-answer) so the
            # user knows WHERE the click went, not just that something fired.
            assert "topology" in post["text"].lower(), (
                f"the ack didn't name the topology destination the user clicked: {post['text']!r}"
            )
            assert "browser" in post["text"].lower() or "dashboard" in post["text"].lower(), (
                f"the ack didn't tell the user the view opened in the browser dashboard: {post['text']!r}"
            )
        finally:
            ctx.close()


def test_picks_link_acknowledges_with_specific_label(tmp_path, monkeypatch):
    """The cheat-sheet's first-cell picks.json deep link must also acknowledge — and
    its label must be SPECIFIC to picks.json (proves the label derives from the
    clicked href, not a hard-coded string)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            sf = _flip_to_stats(page)
            link = sf.locator('a[href*="file=picks.json"]')
            assert link.count() >= 1, "no picks.json deep link on the side-panel /stats cheat-sheet"
            link.first.click(timeout=5000)
            page.wait_for_timeout(450)
            text = sf.evaluate(
                "()=>{const n=document.querySelector('.portal-open-notice');"
                "return n?(n.innerText||'').replace(/\\s+/g,' ').trim():'';}"
            )
            assert "picks.json" in text, (
                "the picks.json deep-link ack didn't name picks.json — the label isn't "
                f"derived from the clicked destination: {text!r}"
            )
        finally:
            ctx.close()
