"""The /stats Chart.js charts must actually PAINT in the real Chrome side panel.

Iter-59 finding (2026-06-17). The side-panel sandbox lives at
``sandbox/launchpad.html``; ``renderChart()`` lazy-injects Chart.js via
``s.src = './vendor/chart.umd.min.js'`` (the ``CHART_JS_SRC`` string baked into
``launchpad-init.js``). The side-panel build (``build_extension_sidepanel.py``)
rewrites ``./vendor/`` → ``../vendor/`` in the **HTML** (so petite-vue + the fonts
resolve from the extension root), but it never touched the **init JS** — so the
chart src stayed ``./vendor/chart.umd.min.js``, which from ``sandbox/`` resolves to
the nonexistent ``sandbox/vendor/chart.umd.min.js`` → ``ERR_FILE_NOT_FOUND`` →
Chart.js never loads → BOTH /stats canvases (the routing-strength bars AND the
Local Elo chart) render BLANK in the actual Chrome side panel. A file:// render
(``vendor/`` alongside the page) resolves ``./vendor/`` fine, so every prior
file://-only verification stayed green while the real panel shipped blank charts.

A geometry check is blind to this: the canvas element is present and sized, the
page has no overflow, no ``{{ }}`` leaks — it's just an empty rectangle. This guard
reads the canvas BACKING-STORE pixels in the real opaque-origin sandbox and asserts
they're non-blank (Chart.js actually drew bars). Mutation-provable: revert the init
JS vendor rewrite in build_extension_sidepanel + rebuild → Chart.js 404s → the
canvas paints 0 pixels → this reds with the exact symptom.

Slow + browser marked; skips without Playwright/chromium; runs in CI ``browser``.
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
    """Seed a synthetic home (councils with provider_scores → both /stats charts),
    stub the native host, load the real extension, open the side panel, and return
    (ctx, ext_id, page) after the launchpad mounts."""
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
    except Exception as exc:  # pragma: no cover - env-dependent
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
    page.set_viewport_size({"width": 393, "height": 900})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


# Counts the non-white, opaque pixels in a canvas backing store. A Chart.js render
# draws teal bars + axes (thousands of such pixels); a 404'd Chart.js leaves the
# canvas fully transparent/blank → 0. Returned per-canvas so a regression in EITHER
# chart (routing strength OR Local Elo) is caught.
_PAINT_PROBE = """
()=>{
  function painted(id){
    const c = document.getElementById(id);
    if(!c) return {id, present:false};
    let nz = 0;
    try{
      const ctx = c.getContext('2d');
      const d = ctx.getImageData(0,0,c.width,c.height).data;
      for(let i=0;i<d.length;i+=4){
        if(d[i+3] > 0 && !(d[i]===255 && d[i+1]===255 && d[i+2]===255)) nz++;
      }
    }catch(e){ return {id, present:true, err:String(e).slice(0,60)}; }
    return {id, present:true, paintedPx:nz, visible:c.offsetParent!==null,
            w:c.width, h:c.height};
  }
  return {routing: painted('personal-preference-chart'),
          elo: painted('provider-elo-chart')};
}
"""


def test_stats_chartjs_canvases_paint_in_panel(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        errs: list[str] = []
        page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Flip to /stats in place (the routing chart auto-renders there).
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(1000)
            sf = page.frames[-1]
            # Expand the Local Elo <details> so its canvas is laid out + drawn.
            sf.evaluate(
                "()=>{for(const d of document.querySelectorAll('details')){"
                "const s=d.querySelector('summary');"
                "if(s && /Local Elo/i.test(s.textContent||'')) d.open=true;}}"
            )
            # Chart.js is lazy-injected on first stats render; give the 404-or-load +
            # draw time to settle, then re-probe.
            page.wait_for_timeout(2500)
            res = sf.evaluate(_PAINT_PROBE)

            routing = res.get("routing") or {}
            elo = res.get("elo") or {}
            assert routing.get("present"), "the routing-strength chart canvas never rendered on /stats"
            assert elo.get("present"), "the Local Elo chart canvas never rendered on /stats"

            # No file-load error must surface — the Chart.js 404 was exactly the
            # ERR_FILE_NOT_FOUND that left the canvases blank.
            chart_404 = [e for e in errs if "chart" in e.lower() or "ERR_FILE_NOT_FOUND" in e]
            assert not chart_404, (
                "Chart.js failed to load in the side-panel sandbox (the ./vendor vs "
                f"../vendor path bug) — console errors: {chart_404}"
            )

            routing_px = routing.get("paintedPx", 0)
            elo_px = elo.get("paintedPx", 0)
            assert routing_px > 200, (
                "the /stats routing-strength chart rendered BLANK in the real Chrome "
                "side panel — Chart.js 404'd from sandbox/vendor/ (the init.js "
                f"'./vendor/chart.umd.min.js' was not rewritten to '../vendor/'). painted={routing_px}px"
            )
            assert elo_px > 200, (
                "the /stats Local Elo chart rendered BLANK in the real Chrome side "
                "panel — Chart.js 404'd from sandbox/vendor/ (the init.js "
                f"'./vendor/chart.umd.min.js' was not rewritten to '../vendor/'). painted={elo_px}px"
            )
        finally:
            ctx.close()
