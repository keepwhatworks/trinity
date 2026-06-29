"""The RUNNING-council card in the REAL side panel must not blow out a 320px panel.

Founder-class symptom (UX sweep iter 116): the Chrome side panel can be dragged to
~320px. With a council RUNNING, the launch-status card renders a per-member status
grid (`.provider-status-row { grid-template-columns: 88px 84px 1fr }`). A CSS grid
track's implicit minimum is `auto` = the cell's min-content, so the two fixed
name/badge columns + the `1fr` detail's longest word demand a row min-width that, at
320px, pushed the whole launch-status CARD ~30px past the viewport — horizontal scroll
on the running-council surface (the card content "frontier and local —" and the
`trinity-local install-mcp` snippet ran off the right edge). Worse, when the `1fr`
detail track was naively made shrinkable it collapsed to a ~30px sliver that wrapped
the status sentence ONE CHARACTER PER LINE.

Root-cause fix (launchpad_template.py):
  * `.provider-status-row` tracks → `minmax(0, …)` so they can shrink below
    min-content (the card stays inside the column — no horizontal scroll), and
  * a `@media (max-width: 360px)` STACK so the detail drops to its own full-width
    line (readable sentence, never a vertical strip).

This drives the REAL extension panel at 320px (the only thing that exercises the
sandbox opaque origin a file:// render can't), CLICKS Launch with a STUBBED dispatcher
(so nothing hits a real council), and asserts on the running card:
  1. NO horizontal overflow — documentElement.scrollWidth <= clientWidth, and the
     launch-status card's right edge is inside the viewport.
  2. The per-member detail text is READABLE — at 320px it is STACKED full-width below
     the name/badge (left-aligned to the row, top below the name), never squeezed into
     a 1-char-per-line sliver.

Mutation-provable: revert the row tracks to the bare `88px 84px 1fr` and the overflow
assertion reds (card right edge past the viewport / scrollWidth > clientWidth); drop
the @media stack and the detail-readability assertion reds (detail stays in the
sliver column, not stacked).

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

# A dispatcher whose dispatch() NEVER calls onResult => the optimistic operation
# stays 'running' so the per-member status grid renders and can be measured.
_STUB_RUNNING = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) { window.__lastDispatch = opts && opts.extensionAction; },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch, width):
    """Seed a synthetic home, stub the native host, load the real extension, open the
    side panel at `width`, and return (ctx, ext_id, page) after the launchpad mounts."""
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
    page.set_viewport_size({"width": width, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def test_running_council_card_fits_320px_panel_and_detail_is_readable(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch, width=320)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Drive the RUNNING council state (stubbed — never hits a real council).
            lf.evaluate(_STUB_RUNNING)
            lf.fill("#council-prompt", "Compare three database designs for a chat app")
            lf.locator(".actions button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(1500)

            geo = lf.evaluate(
                r"""()=>{
                  const cw = document.documentElement.clientWidth;
                  const sw = document.documentElement.scrollWidth;
                  const card = document.querySelector('.launch-grid .card');
                  const list = document.querySelector('.provider-status-list');
                  const rows = list ? [...list.querySelectorAll('.provider-status-row')] : [];
                  // Pick a row whose detail cell actually carries status text (the
                  // pending rows have an empty detail; the active one has a sentence).
                  let detail = null, name = null;
                  for (const r of rows) {
                    const d = r.querySelector('.provider-status-detail');
                    if (d && (d.textContent || '').trim().length > 8) {
                      detail = d; name = r.querySelector('.provider-status-name'); break;
                    }
                  }
                  const rect = (el)=>{ if(!el) return null; const b=el.getBoundingClientRect();
                    return {x:Math.round(b.x), top:Math.round(b.top), bottom:Math.round(b.bottom),
                            w:Math.round(b.width), h:Math.round(b.height), right:Math.round(b.right)}; };
                  return {
                    clientW: cw, scrollW: sw,
                    cardRight: card ? Math.round(card.getBoundingClientRect().right) : null,
                    rowsVisible: list ? list.offsetParent !== null : false,
                    rowCount: rows.length,
                    detailText: detail ? (detail.textContent || '').trim() : null,
                    detailRect: rect(detail),
                    nameRect: rect(name),
                  };
                }"""
            )

            # The running card must actually be on screen.
            assert geo["rowsVisible"] and geo["rowCount"] >= 1, (
                f"the per-member status grid never rendered in the running panel: {geo}"
            )

            # BITE 1 — no horizontal overflow. The bare `88px 84px 1fr` row tracks
            # blew the launch-status card ~30px past a 320px panel (founder symptom:
            # horizontal scroll on the running-council card).
            assert geo["scrollW"] <= geo["clientW"] + 1, (
                "the RUNNING council card overflows the 320px side panel horizontally — "
                f"scrollWidth {geo['scrollW']} > clientWidth {geo['clientW']} "
                "(the provider-status-row's fixed grid tracks force the card past the "
                "viewport; tracks must be minmax(0, …))."
            )
            assert geo["cardRight"] is not None and geo["cardRight"] <= geo["clientW"] + 1, (
                "the launch-status card's right edge spills past the 320px viewport "
                f"(right {geo['cardRight']} > clientWidth {geo['clientW']})."
            )

            # BITE 2 — the per-member detail sentence must be READABLE, not a 1-char
            # sliver. At 320px the @media stack drops it to its own full-width line:
            # left-aligned to the row start and BELOW the name (not beside it in a
            # ~30px column). A wide-and-short box, never tall-and-narrow.
            d, n = geo["detailRect"], geo["nameRect"]
            assert d is not None and n is not None, (
                f"no per-member detail text rendered to measure: {geo}"
            )
            assert d["top"] >= n["bottom"] - 2, (
                "the running-council per-member detail is NOT stacked below the name at "
                f"320px (detail top {d['top']} vs name bottom {n['bottom']}) — it's wedged "
                "into the squeezed third column where the status sentence wraps one "
                "character per line."
            )
            assert d["w"] >= 120, (
                "the per-member detail column collapsed to an unreadable sliver "
                f"(width {d['w']}px) — the status sentence wraps a character per line on "
                "the 320px running-council card."
            )
            assert d["right"] <= geo["clientW"] + 1, (
                f"the per-member detail spills past the 320px viewport (right {d['right']})."
            )
        finally:
            ctx.close()


def test_running_council_long_member_detail_does_not_overflow_3col_panel(tmp_path, monkeypatch):
    """In the 3-column band (>360px) a long unbreakable member-error detail (a path
    or URL with no spaces) must WRAP inside its track, never blow the running-council
    card past the panel. Guards the base `minmax(0, …)` tracks + `overflow-wrap` —
    with the bare `88px 84px 1fr` tracks an unbreakable token forced the panel ~500px
    wide at 375px."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch, width=375)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            lf.evaluate(_STUB_RUNNING)
            lf.fill("#council-prompt", "compare three database designs")
            lf.locator(".actions button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(1500)

            # Simulate a member-failure detail carrying a long unbreakable token (the
            # real shape when a provider call fails with a path/URL in the message).
            injected = lf.evaluate(
                r"""()=>{
                  const d = document.querySelector('.provider-status-detail');
                  if (!d) return false;
                  d.textContent = 'Failed:/Users/x/aVeryLongUnbreakablePathThatHasNoSpacesAtAllToWrapOnAAAAAAAAAA';
                  return true;
                }"""
            )
            assert injected, "no provider-status-detail cell to inject a long token into"
            page.wait_for_timeout(200)

            geo = lf.evaluate(
                r"""()=>{
                  const cw = document.documentElement.clientWidth;
                  const sw = document.documentElement.scrollWidth;
                  const d = document.querySelector('.provider-status-detail');
                  const b = d ? d.getBoundingClientRect() : null;
                  return { clientW: cw, scrollW: sw,
                           detailRight: b ? Math.round(b.right) : null };
                }"""
            )
            assert geo["scrollW"] <= geo["clientW"] + 1, (
                "a long unbreakable member-error detail blew the running-council card "
                f"past the 375px panel — scrollWidth {geo['scrollW']} > clientWidth "
                f"{geo['clientW']} (the provider-status-row tracks must be minmax(0, …) "
                "and the detail must overflow-wrap so an unbreakable token wraps, not "
                "widens the card)."
            )
            assert geo["detailRight"] is not None and geo["detailRight"] <= geo["clientW"] + 1, (
                f"the long detail token spills past the 375px viewport (right {geo['detailRight']})."
            )
        finally:
            ctx.close()
