"""CLASS guard: the side-panel sandbox must load EVERY relative asset it references
— from HTML *or* from JS-injected src/href — with zero failed loads, across home,
/stats, AND a council page.

THE CLASS (Iter-59, 2026-06-17). The side-panel pages live in ``sandbox/`` but their
vendor assets live at the extension ROOT (``vendor/``). ``build_extension_sidepanel``
rewrites ``./vendor/`` → ``../vendor/`` so they resolve one dir up. The bug shape: ANY
relative asset path the sandbox page loads that the build did NOT rewrite for the
``sandbox/`` subdirectory → a 404 that ONLY the real opaque-origin panel exposes,
invisible to every file:// render (where ``vendor/`` sits alongside the page and
``./vendor/`` resolves fine). Iter 59 found ONE instance: ``renderChart()`` lazy-injects
``s.src = './vendor/chart.umd.min.js'`` from ``launchpad-init.js`` (a JS string the
HTML-only rewrite missed) → both /stats charts shipped BLANK in the real panel.

``test_sidepanel_stats_charts_paint_browser`` guards that ONE instance (canvas pixels
on /stats). This guard closes the CLASS: it drives the REAL panel across home → /stats
→ a council page and asserts NO failed asset load of ANY kind (network requestfailed,
HTTP 4xx, or an ``ERR_FILE_NOT_FOUND`` console error) for ANY asset — so a FUTURE missed
build-path (a second JS-injected vendor ref, a renamed font, a new sibling html the
build forgot to rewrite) is caught automatically, not just chart.js. It ALSO asserts the
key vendor assets actually loaded with HTTP 200 (positive proof: not-404 because it was
genuinely served, not because it was never requested).

Mutation-provable: revert the ``init_js = init_js.replace("./vendor/", "../vendor/")``
rewrite in ``build_extension_sidepanel.build()`` + rebuild the sandbox → Chart.js 404s on
/stats → this reds with the ``ERR_FILE_NOT_FOUND`` failed-asset symptom.

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
    """Seed a synthetic home (councils → /stats charts + a council rail), stub the
    native host, load the real extension, open the side panel, return (ctx, ext_id,
    page) after the launchpad mounts."""
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
    # Stub dispatch so any rail/composer interaction never reaches a real council.
    page.add_init_script(
        "window.__TRINITY_DISPATCH__ = { dispatch: function(o){ if(o&&o.onResult) o.onResult({ok:true}); },"
        " onStateChange: function(){}, warmProbe: function(){} };"
    )
    page.set_viewport_size({"width": 393, "height": 900})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4500)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def test_no_failed_asset_load_across_home_stats_council(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        console_errs: list[str] = []
        failed: list[str] = []
        responses: list[tuple[int, str]] = []
        # Context-level listeners catch SUB-FRAME (iframe) asset loads too, not just
        # the top page — the sandbox pages load inside the shell iframe.
        ctx.on("requestfailed", lambda r: failed.append(f"REQFAIL {r.failure} :: {r.url}"))

        def _on_response(r) -> None:
            responses.append((r.status, r.url))
            if r.status >= 400:
                failed.append(f"HTTP {r.status} :: {r.url}")

        ctx.on("response", _on_response)
        page.on(
            "console",
            lambda m: console_errs.append(m.text[:200]) if m.type == "error" else None,
        )
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # ── /stats view (lazy Chart.js + any stats-only assets) ──
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(1200)
            sf = page.frames[-1]
            sf.evaluate(
                "()=>{for(const d of document.querySelectorAll('details')){"
                "const s=d.querySelector('summary');"
                "if(s && /Local Elo/i.test(s.textContent||'')) d.open=true;}}"
            )
            page.wait_for_timeout(2500)

            # ── council page (brokered nav swaps the shell iframe to live_council.html) ──
            # The rail link can sit off-viewport in the narrow panel; click it in-page
            # so the capture-phase interceptor fires the nav broker.
            clicked = sf.evaluate(
                "()=>{const a=document.querySelector('a.rail-council[href*=\"live_council.html\"]')"
                "|| document.querySelector('a[href*=\"live_council.html\"]');"
                "if(a){a.click(); return a.getAttribute('href');} return null;}"
            )
            assert clicked and "live_council.html" in clicked, (
                f"could not drive a council-rail link to load the council page (got {clicked!r})"
            )
            page.wait_for_timeout(4500)
            cf = page.frames[-1]
            assert "sandbox/live_council.html" in (cf.url or ""), (
                f"council page never loaded into the panel (frame={cf.url})"
            )
            page.wait_for_timeout(1000)

            # ── THE CLASS ASSERT: zero failed asset loads of ANY kind ──
            # A favicon 404 is the only acceptable miss (Chrome auto-requests one the
            # extension intentionally doesn't ship; it never affects rendering).
            asset_fail = [
                x for x in failed
                if "favicon" not in x.lower()
                # data: URLs (the inline SVG brand mark) never hit the network as a
                # failure; nothing else relative should fail.
            ]
            err_not_found = [e for e in console_errs if "ERR_FILE_NOT_FOUND" in e or "net::ERR" in e]
            assert not asset_fail, (
                "a sandbox asset FAILED to load in the real Chrome side panel (the "
                "build-script missed rewriting a relative path for sandbox/ — the "
                f"chart.umd.min.js ./vendor vs ../vendor class). failed loads: {asset_fail}"
            )
            assert not err_not_found, (
                "a sandbox asset 404'd with ERR_FILE_NOT_FOUND in the real Chrome side "
                "panel (an un-rewritten relative asset path the build missed — the "
                f"chart.js class). console: {err_not_found}"
            )

            # ── POSITIVE proof: the key vendor assets actually loaded (HTTP 200), so a
            # green here means "served", not "never requested". chart.umd.min.js is the
            # exact file Iter-59 found 404'ing; petite-vue + a font prove the HTML +
            # CSS-url() rewrites also hold. ──
            loaded_200 = {r[1].rsplit("/", 1)[-1].split("?")[0] for r in responses if r[0] == 200}
            for needed in ("chart.umd.min.js", "petite-vue.iife.js"):
                assert needed in loaded_200, (
                    f"the vendor asset {needed} never loaded with HTTP 200 in the panel "
                    f"(it should resolve from ../vendor/). 200-loaded: {sorted(loaded_200)}"
                )
            assert any(f.endswith(".woff2") for f in loaded_200), (
                "no vendor font (.woff2) loaded with HTTP 200 — the CSS url(../vendor/…) "
                f"rewrite may be broken. 200-loaded: {sorted(loaded_200)}"
            )
        finally:
            ctx.close()
