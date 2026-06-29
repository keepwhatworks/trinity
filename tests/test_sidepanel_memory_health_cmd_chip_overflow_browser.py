"""The memory-health card's copy-command chip must not overflow a NARROW side
panel (320px) — in the REAL sandboxed panel, where the bug bit.

The defect this guards (founder symptom): on the /stats view, each memory-health
issue row renders a copy-command chip that shows the FULL CLI command (e.g.
`trinity-local dream --only-distill`). That chip carried `white-space: nowrap` and
NO max-width, so at 320px (the narrowest side-panel width) it became a single
unbreakable flex item WIDER than the card — and even inside the row's `flex-wrap`
it pushed ~14px past the card's right edge, scrolling the whole sandbox document
sideways (~13px of horizontal overflow). A copy-command chip the user can't read
without sideways-scrolling reads as a clipped/broken control on the memory-health
surface.

The fix: the command chip wraps instead of clipping — `white-space: normal;
overflow-wrap: anywhere; max-width: 100%` — so an arbitrary-length command stays
fully readable on two lines and can never exceed the card width. The hint span's
`min-width` was also relaxed (200px -> 140px) so it can't force the row wider than a
320px card before wrapping kicks in.

Why this needs a REAL-panel guard (not a source-string check):
  - The overflow is a COMPUTED-GEOMETRY fact: it depends on the chip's intrinsic
    text width vs the card's content box at a specific viewport. Only a real panel
    boot + getBoundingClientRect read at 320px can prove the chip stays inside the
    card. A source assertion that "white-space: normal" is present can't prove the
    long command actually fits.
  - The assertion is SCOPED to the memory-health card subtree: it asserts no child
    of any issue `<li>` extends past the card's right edge. (The eval-leaderboard
    table + the stats canvas have their own, separate, founder-gated mobile-clip
    behavior at 320px — this guard must NOT couple to those.)

Mutation-proven: revert the chip style to `white-space: nowrap` (drop the wrap +
max-width) and rebuild the sidepanel bundle → the command chip again extends past the
card's right edge at 320px and this guard goes RED ("the memory-health copy-command
chip overflows the card's right edge at 320px").

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


def _boot_panel(p, tmp_path, monkeypatch, *, width: int):
    """Boot the REAL side panel over a delegating capture-host stub, seeded with a
    STALE core.md (older than lens.md) so the memory-health card renders issue rows
    — each with the copy-command chip whose overflow we measure."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    # Force a STALE core.md (older than lens.md) -> _core_status == "stale" ->
    # the memory-health card renders the core.md/vocab/topics issue rows with the
    # copy-command chip. (The href backfill also gives them the Inspect link, but
    # we measure the COMMAND chip here.)
    mem = home / "memories"
    core = home / "core.md"
    core.write_text("# Core\n\nA distilled identity memory.\n" * 3, encoding="utf-8")
    old = time.time() - 86400
    import os

    os.utime(core, (old, old))
    now = time.time()
    os.utime(mem / "lens.md", (now, now))

    from trinity_local.launchpad_page import _assemble_page_data, build_launchpad_payload

    _, recent_sidebar = _assemble_page_data(force_live_page=False)
    payload = build_launchpad_payload()
    payload["recentSidebarHtml"] = recent_sidebar
    # Non-vacuous precondition: the seeded state actually produced memory-health
    # issues with a command chip (else the geometry assert would pass trivially).
    issues = (payload.get("pageData") or {}).get("memoryHealth", {}).get("issues") or []
    assert any(i.get("command") for i in issues), (
        "seed did not produce a memory-health issue with a copy-command chip — "
        f"the overflow guard would be vacuous. issues={issues!r}"
    )
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
    page.set_viewport_size({"width": width, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)
    return ctx, ext_id, page


def _probe_card(page):
    lf = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
    assert lf is not None, "the launchpad iframe never loaded in the panel"
    # The memory-health card is a stats-only card (.lp-view-home hides it). Switch
    # the root to the stats view (the in-place CSS toggle setLaunchpadView flips).
    lf.evaluate("""()=>{
      const root = document.getElementById('launchpad-app');
      if (root) { root.classList.remove('lp-view-home'); root.classList.add('lp-view-stats'); }
    }""")
    page.wait_for_timeout(500)
    return lf.evaluate("""()=>{
      const card = document.querySelector('.memory-health-card');
      if (!card) return { present: false };
      const cr = card.getBoundingClientRect();
      const lis = Array.from(card.querySelectorAll('.memory-health-list li'));
      // The worst (right-most) child across all issue rows, and whether it's a
      // copy-command chip. Scoped to the card so the founder-gated eval table /
      // stats canvas can't trip this.
      let worst = null;
      lis.forEach(li => {
        Array.from(li.children).forEach(c => {
          const r = c.getBoundingClientRect();
          if (!worst || r.right > worst.right) {
            worst = { right: Math.round(r.right), tag: c.tagName,
                      txt: (c.textContent || '').trim().slice(0, 40),
                      ws: getComputedStyle(c).whiteSpace };
          }
        });
      });
      return {
        present: true,
        cardVisible: getComputedStyle(card).display !== 'none',
        cardRight: Math.round(cr.right),
        cardWidth: Math.round(cr.width),
        liCount: lis.length,
        worstChild: worst,
      };
    }""")


def test_memory_health_command_chip_does_not_overflow_card_at_320(tmp_path, monkeypatch):
    """At 320px the memory-health copy-command chip must wrap inside the card, not
    push past its right edge (the un-fixed nowrap chip scrolled the sandbox doc)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _, page = _boot_panel(p, tmp_path, monkeypatch, width=320)
        try:
            info = _probe_card(page)
        finally:
            ctx.close()

    assert info["present"] and info["cardVisible"], (
        f"the memory-health card never rendered/visible on the panel /stats view: {info!r}"
    )
    # Non-vacuous: the card actually rendered issue rows (so the geometry below is
    # measuring real chips, not an empty list).
    assert info["liCount"] >= 1, f"the memory-health card rendered no issue rows: {info!r}"
    worst = info["worstChild"]
    assert worst is not None, f"no issue-row children to measure: {info!r}"
    # THE BITE: no child of any issue row may extend past the card's right edge.
    # 1px tolerance for sub-pixel rounding.
    assert worst["right"] <= info["cardRight"] + 1, (
        "the memory-health copy-command chip overflows the card's right edge at 320px "
        "(a nowrap, no-max-width command chip pushes past the narrow side-panel card "
        f"and scrolls the sandbox doc sideways): worst child right={worst['right']} > "
        f"card right={info['cardRight']}. offender={worst!r}"
    )
