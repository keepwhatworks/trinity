"""The council-rail search filter must ANNOUNCE its result to assistive tech — in
the REAL side panel, populated history, where the filter actually runs.

The defect this guards (WCAG 4.1.3 Status Messages, AA): the rail filter narrows a
populated council list purely by toggling row `display`, and surfaces a visual
"No councils match that search." line by flipping ITS `display` from none→''. But
toggling an already-present static string from hidden→shown is NOT reliably
announced (the text never CHANGED, only its visibility), and the visual line lives
OUTSIDE the petite-vue app's `#liveAnnouncement` region. So a keyboard /
screen-reader user typed a query that matched zero councils, the list silently
emptied, and they were told NOTHING — no feedback the filter found nothing, and no
feedback when matches returned. A sighted user reads the line; a blind user hears
silence.

The fix: a PERSISTENT `#rail-filter-status` (role=status, aria-live=polite) region
present from first render whose TEXT mutates as the filter runs — "No councils match
that search." on a zero-match query, "N of M councils match." on a partial match,
and empty for the unfiltered baseline. The reliable announce pattern (mirrors the
Vue-bound #liveAnnouncement and the memory viewer's #sr-status).

Why this needs a REAL-panel guard (not a source-string check):
  - The rail is HYDRATED CLIENT-SIDE in the side panel (recentSidebarHtml injected
    into #recent-sidebar-mount), and the filter IIFE binds its listener + reads the
    row set only AFTER hydration. A source string can't prove the listener fires and
    actually writes the live region on a real keystroke; only a real panel boot +
    a typed query + a region-text read can.
  - The region must be a TRUE live region (role/aria-live present) AND carry the
    no-match text after a zero-match query — both halves are asserted from the
    rendered DOM.

Mutation-proven: drop the `announceRail('No councils match that search.')` call in
the filter IIFE (or strip the role/aria-live off #rail-filter-status) and rebuild the
sidepanel bundle → the assertion goes RED ("the rail filter emptied the list with no
announced status — a screen-reader user heard silence").

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

_STUB_OK = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) { if (opts && opts.onResult) opts.onResult({ ok: true, tier: 'extension' }); },
    probe: function () { return Promise.resolve('present'); },
    onStateChange: function () {}, subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
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
    page.add_init_script(_STUB_OK)
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)
    return ctx, ext_id, page


def test_rail_filter_zero_match_is_announced_in_panel(tmp_path, monkeypatch):
    """REAL side panel, populated rail: typing a query that matches no councils must
    push a status message through a TRUE live region (the visual line alone is mute
    to AT). A partial match announces the count; clearing the box stays silent."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert lf is not None, "the launchpad iframe never loaded in the panel"

            ham = lf.locator("button.rail-toggle")
            assert ham.count() == 1, "the rail hamburger toggle is missing — history is unreachable"
            ham.first.click(timeout=5000)
            page.wait_for_timeout(700)
            assert lf.evaluate("()=>document.body.classList.contains('rail-open')"), (
                "the hamburger did not open the rail drawer — the history nav is unreachable"
            )

            # The rail is populated (the seeded councils hydrated) so the filter runs.
            rail = lf.evaluate("""()=>{
              const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
              const filt = document.getElementById('rail-filter');
              const cs = filt ? getComputedStyle(filt) : null;
              const fb = filt ? filt.getBoundingClientRect() : null;
              return {
                rowCount: rows.length,
                filterVisible: filt ? (cs.display !== 'none' && fb.width > 0 && fb.height > 0) : false,
              };
            }""")
            assert rail["rowCount"] >= 2 and rail["filterVisible"], (
                f"expected a populated, filterable rail to drive the announce path, got {rail!r}"
            )

            # The status region must be a TRUE live region from first render (a region
            # inserted already-populated is not reliably announced; this one is present
            # and only its TEXT mutates).
            region = lf.evaluate("""()=>{
              const sr = document.getElementById('rail-filter-status');
              if (!sr) return {present: false};
              return {
                present: true,
                role: sr.getAttribute('role'),
                ariaLive: sr.getAttribute('aria-live'),
              };
            }""")
            assert region["present"], (
                "the rail filter has NO #rail-filter-status region — a screen-reader user "
                "filtering the council history hears silence (WCAG 4.1.3)"
            )
            assert region["role"] == "status" and region["ariaLive"] == "polite", (
                "the rail filter status region is not a true live region "
                f"(role/aria-live missing) — AT will not announce it: {region!r}"
            )

            # THE BITE: type a zero-match query → the live region must carry the
            # no-match message (the rows emptied; without this announcement the
            # screen-reader user heard silence as the list vanished).
            lf.locator("#rail-filter").fill("zzqqnomatch_xyz")
            page.wait_for_timeout(350)
            zero = lf.evaluate("""()=>{
              const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
              const shown = rows.filter(r => getComputedStyle(r).display !== 'none').length;
              const sr = document.getElementById('rail-filter-status');
              return {shown, srText: sr ? (sr.textContent || '').trim() : null};
            }""")
            assert zero["shown"] == 0, (
                f"the zero-match query did not empty the list — fixture/filter changed: {zero!r}"
            )
            assert zero["srText"] and "No councils match" in zero["srText"], (
                "the rail filter emptied the list with no announced status — a screen-reader "
                f"user heard silence as the council history vanished (WCAG 4.1.3): {zero!r}"
            )

            # A PARTIAL match announces the count (so a partial filter is also spoken).
            lf.locator("#rail-filter").fill("")
            page.wait_for_timeout(120)
            lf.locator("#rail-filter").fill("design")
            page.wait_for_timeout(350)
            partial = lf.evaluate("""()=>{
              const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
              const shown = rows.filter(r => getComputedStyle(r).display !== 'none').length;
              const sr = document.getElementById('rail-filter-status');
              return {total: rows.length, shown, srText: sr ? (sr.textContent || '').trim() : null};
            }""")
            assert 0 < partial["shown"] < partial["total"], (
                f"the partial query did not narrow the list — fixture changed: {partial!r}"
            )
            assert partial["srText"] and "match" in partial["srText"] and str(partial["shown"]) in partial["srText"], (
                "a partial rail filter did not announce its match count to AT "
                f"(the count is the feedback a blind user needs): {partial!r}"
            )

            # Clearing the box returns to the unfiltered baseline — NO spurious status
            # (the full list is not a result worth speaking).
            lf.locator("#rail-filter").fill("")
            page.wait_for_timeout(350)
            cleared = lf.evaluate("""()=>{
              const sr = document.getElementById('rail-filter-status');
              return {srText: sr ? (sr.textContent || '').trim() : null};
            }""")
            assert not cleared["srText"], (
                "clearing the rail filter left a stale status announcement — the baseline "
                f"full list should be silent: {cleared!r}"
            )
        finally:
            ctx.close()
