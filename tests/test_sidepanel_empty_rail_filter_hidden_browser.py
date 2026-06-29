"""The council-rail search box must NOT render over an EMPTY history — in the
REAL side panel, cold-start, where the bug bit.

The defect this guards (founder symptom): the cold side panel opened its history
drawer to a live "Search councils…" input sitting above "No councils yet — ask one
above." Typing into it did NOTHING observable — and the "No councils match that
search" line is STRUCTURALLY suppressed when there are zero rows (the filter IIFE
gates that line on `rows.length && shown === 0`, so with `rows.length === 0` it
never appears). So the box read as a dead control on the panel's first-impression
surface: a focusable, interactive affordance over a collection it cannot filter.

The fix: the rail-filter IIFE hides the input when `rows.length === 0` (nothing to
filter), so the empty-state message stands alone; it re-shows automatically once a
council lands and the rail re-renders with rows.

Why this needs a REAL-panel guard (not a source-string check):
  - The rail is HYDRATED CLIENT-SIDE in the side panel (from recentSidebarHtml,
    injected into #recent-sidebar-mount), so `rows.length` is only correct AFTER
    hydration. A source string can't prove the IIFE runs post-hydrate and actually
    hides the rendered input; only a real panel boot + computed-style read can.
  - The empty case AND the populated case are pinned in ONE run: empty → box gone,
    populated (the same boot, re-seeded) → box visible + filters. So the guard bites
    a regression that drops the `rows.length === 0` branch AND a regression that
    over-hides the box when councils exist.

Mutation-proven: revert the `if (rows.length === 0) { input.style.display='none'; }`
branch (the un-fixed shape) and rebuild the sidepanel bundle → the empty-rail
assertion goes RED ("the cold-start council rail still shows a live 'Search
councils…' box over an empty history").

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


def _boot_panel(p, tmp_path, monkeypatch, *, seed_councils: bool):
    """Boot the REAL side panel over a delegating capture-host stub. When
    seed_councils is False the home is COLD (no councils) → the rail hydrates to the
    empty state; True seeds the synthetic 5-council home → the rail hydrates rows."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    sys.path.insert(0, str(REPO / "scripts"))
    if seed_councils:
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


def _open_rail_and_probe(page):
    """Open the history drawer in the panel and return the rail state."""
    lf = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
    assert lf is not None, "the launchpad iframe never loaded in the panel"
    ham = lf.locator("button.rail-toggle")
    assert ham.count() == 1, "the rail hamburger toggle is missing — history is unreachable"
    ham.first.click(timeout=5000)
    page.wait_for_timeout(700)
    assert lf.evaluate("()=>document.body.classList.contains('rail-open')"), (
        "the hamburger did not open the rail drawer — the history nav is unreachable"
    )
    return lf.evaluate("""()=>{
      const filt = document.getElementById('rail-filter');
      const empty = document.querySelector('.council-rail .rail-empty');
      const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
      const cs = filt ? getComputedStyle(filt) : null;
      const fb = filt ? filt.getBoundingClientRect() : null;
      return {
        rowCount: rows.length,
        filterPresent: !!filt,
        filterVisible: filt ? (cs.display !== 'none' && cs.visibility !== 'hidden'
                               && fb.width > 0 && fb.height > 0) : false,
        emptyVisible: empty ? getComputedStyle(empty).display !== 'none' : false,
        emptyText: empty ? (empty.textContent || '').trim() : null,
      };
    }""")


def test_empty_rail_hides_the_dead_search_box_in_panel(tmp_path, monkeypatch):
    """COLD side panel: the history drawer shows the empty-state message and NO
    live search box (the box has nothing to filter — a dead control before the fix).
    And the SAME boot, re-seeded with councils, still renders + filters the box."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # --- COLD: no councils. The box must be GONE; the empty message present. ---
        ctx, _, page = _boot_panel(p, tmp_path, monkeypatch, seed_councils=False)
        try:
            cold = _open_rail_and_probe(page)
        finally:
            ctx.close()

        assert cold["rowCount"] == 0, (
            f"expected a cold (empty) rail to drive the dead-box case, got {cold!r}"
        )
        # Non-vacuous: the empty state actually rendered (so the box-absence below is
        # the fix, not a failed-to-load rail).
        assert cold["emptyVisible"] and cold["emptyText"] and "No councils yet" in cold["emptyText"], (
            f"the cold rail did not render its 'No councils yet' empty state: {cold!r}"
        )
        # THE BITE: the rail filter must not be a live, focusable control over an
        # empty history. (filterPresent may stay True — it's display:none'd — what
        # matters is it isn't VISIBLE/interactive.)
        assert not cold["filterVisible"], (
            "the cold-start council rail still shows a live 'Search councils…' box over "
            "an empty history — a dead control (typing does nothing; the no-match line is "
            f"structurally suppressed at rows.length===0). {cold!r}"
        )

        # --- POPULATED: same boot, councils seeded. The box must RETURN + work. ---
        pop_path = tmp_path / "pop"
        pop_path.mkdir()
        ctx2, _, page2 = _boot_panel(p, pop_path, monkeypatch, seed_councils=True)
        try:
            pop = _open_rail_and_probe(page2)
            assert pop["rowCount"] >= 2, (
                f"expected the seeded rail to carry councils to exercise the filter, got {pop!r}"
            )
            # The fix is surgical: with councils present the search box is BACK and
            # visible (a regression that always-hid the box would red here).
            assert pop["filterVisible"], (
                "the council rail HID the search box even though councils exist — the "
                f"orphan-control fix over-reached and broke history search: {pop!r}"
            )
            # And it still FILTERS (drive a real query that matches a subset).
            lf2 = next((f for f in page2.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            lf2.locator("#rail-filter").fill("design")
            page2.wait_for_timeout(400)
            filtered = lf2.evaluate("""()=>{
              const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
              const shown = rows.filter(r => getComputedStyle(r).display !== 'none').length;
              return {total: rows.length, shown};
            }""")
            assert 0 < filtered["shown"] < filtered["total"], (
                f"the rail search no longer narrows the list — filter regressed: {filtered!r}"
            )
        finally:
            ctx2.close()
