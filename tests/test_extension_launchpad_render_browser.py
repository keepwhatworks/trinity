"""Real-browser render guard for the IN-EXTENSION launchpad (slices 1-3): the
MV3 page `browser-extension/launchpad.html` + external `launchpad-init.js` must
mount the Vue app from data fetched over Native Messaging — not the file://
baked-data path the other launchpad browser tests exercise.

`test_extension_launchpad_build.py` pins the BUILD (in-sync, no inline-script
body, valid JS), but nothing rendered the page in a real browser. This does:
serve the generated artifacts with `chrome.runtime.sendMessage` stubbed to return
a hermetic cold-start payload (the exact `query_kind='launchpad_data'` contract),
and assert the app mounts — no fallback box, the fetched sidebar is injected, and
zero JS/console errors. The stub also means no real extension / Native Messaging /
council is touched. Mirrors the manual verification done 2026-06-12.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"

_SIDEBAR_MARKER = "SOAK-SIDEBAR-OK"


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _build_render_dir(dst: Path, payload: dict) -> None:
    """Lay out the generated extension launchpad + a chrome.runtime stub that
    feeds `payload` to the page's fetch bootstrap (no live extension touched)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import build_extension_launchpad

    html, init_js = build_extension_launchpad.build()
    (dst / "launchpad-init.js").write_text(init_js, encoding="utf-8")
    shutil.copytree(EXT / "vendor", dst / "vendor")
    stub = (
        "window.chrome={runtime:{"
        "sendMessage:function(){return Promise.resolve(" + json.dumps(payload) + ");},"
        "getURL:function(p){return p;}}};"
    )
    (dst / "_stub.js").write_text(stub, encoding="utf-8")
    html = html.replace(
        '<script src="./launchpad-init.js"></script>',
        '<script src="./_stub.js"></script>\n<script src="./launchpad-init.js"></script>',
    )
    (dst / "launchpad.html").write_text(html, encoding="utf-8")


def test_extension_launchpad_mounts_from_native_messaging(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Hermetic cold-start payload (isolated home → no real ~/.trinity / PII), with
    # a synthetic sidebar so we can assert the fetch→inject path painted the rail.
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    payload["recentSidebarHtml"] = f'<div class="rail-item">{_SIDEBAR_MARKER}</div>'
    wire = {"ok": True, **payload}

    render_dir = tmp_path / "ext"
    render_dir.mkdir()
    _build_render_dir(render_dir, wire)

    httpd, port = _serve(render_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env without chromium
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                errs: list[str] = []
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
                page.goto(f"http://127.0.0.1:{port}/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                # The app mounts asynchronously after the stubbed fetch resolves.
                page.wait_for_selector("#recent-sidebar-mount", timeout=10000)
                s = page.evaluate(
                    "() => ({"
                    " fallback: (document.body.innerText||'').includes('reach the local Trinity engine'),"
                    " sidebar: (document.getElementById('recent-sidebar-mount')||{}).innerHTML||'',"
                    " blob: (document.getElementById('page-data')||{}).textContent||'',"
                    " composer: !!document.getElementById('council-prompt'),"
                    " bodyLen: (document.body.innerText||'').length,"
                    " inlineScripts: [...document.querySelectorAll('script:not([src])')].filter(x=>x.textContent.trim() && !/json/.test(x.type||'')).length"
                    "})"
                )
                assert not s["fallback"], "extension launchpad showed the Native-Messaging fallback box — the fetch→mount path is broken"
                assert _SIDEBAR_MARKER in s["sidebar"], "fetched recentSidebarHtml was not injected into the rail mount"
                # Mount invariant = the council COMPOSER, not a char count: the chat-UI
                # redesign made the cold-start home a minimal focal column, so a bodyLen
                # floor is a stale proxy (was 2000; the cold home is now ~1800 chars).
                assert s["composer"], "the council composer (#council-prompt) never rendered — Vue app didn't mount"
                assert s["bodyLen"] > 800, f"page rendered almost nothing (bodyLen={s['bodyLen']}) — Vue app didn't mount"
                assert s["blob"].strip() in ("{}", ""), "page-data blob must stay empty (data comes from the fetch, not baked)"
                assert s["inlineScripts"] == 0, "MV3 violation: an inline <script> body would not run in the extension"
                assert not errs, f"JS/console errors rendering the extension launchpad: {errs[:5]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_extension_launchpad_host_unreachable_fallback_names_install_extension(tmp_path):
    """When the side panel can't reach the capture host (`launchpad_data` returns
    !ok), the fallback box must point the user at the verb that ACTUALLY wires up
    the Native Messaging capture host — `install-extension` — not `install-mcp`,
    which only registers the MCP server in the CLI harnesses and does NOTHING for
    this panel. Naming `install-mcp` was a wrong-CTA dead-end: a user who ran it
    reloaded to the exact same error. Drive the real fallback at panel width.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(REPO / "scripts"))
    import build_extension_launchpad

    html, init_js = build_extension_launchpad.build()
    render_dir = tmp_path / "ext"
    render_dir.mkdir()
    (render_dir / "launchpad-init.js").write_text(init_js, encoding="utf-8")
    shutil.copytree(EXT / "vendor", render_dir / "vendor")
    # chrome.runtime present (so chrome.runtime.id auto-fills) but the host query
    # resolves to {ok:false} — the literal "capture host not wired up" state.
    fake_id = "caaojjhmoffapbcfhmeelpfmnoljjjjj"
    stub = (
        "window.chrome={runtime:{id:'" + fake_id + "',"
        "sendMessage:function(){return Promise.resolve({ok:false});},"
        "getURL:function(p){return p;}}};"
    )
    (render_dir / "_stub.js").write_text(stub, encoding="utf-8")
    html = html.replace(
        '<script src="./launchpad-init.js"></script>',
        '<script src="./_stub.js"></script>\n<script src="./launchpad-init.js"></script>',
    )
    (render_dir / "launchpad.html").write_text(html, encoding="utf-8")

    httpd, port = _serve(render_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env without chromium
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                errs: list[str] = []
                page = browser.new_context(viewport={"width": 393, "height": 760}).new_page()
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
                page.goto(f"http://127.0.0.1:{port}/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(600)
                s = page.evaluate(
                    "() => ({"
                    " fallbackShown: (document.body.innerText||'').includes('reach the local Trinity engine'),"
                    " text: document.body.innerText||'',"
                    " codes: [...document.querySelectorAll('code')].map(c=>c.textContent),"
                    " scrollW: document.documentElement.scrollWidth,"
                    " clientW: document.documentElement.clientWidth"
                    "})"
                )
                # Precondition: the {ok:false} stub actually triggered the fallback
                # box (else the assertions below would pass vacuously).
                assert s["fallbackShown"], (
                    "the host-unreachable fallback box never rendered — the {ok:false} "
                    "path did not fire, so this guard would be vacuous"
                )
                cmd = " ".join(s["codes"])
                # THE founder symptom: a user who hits this fallback and runs the
                # named command must actually wire up the capture host. install-mcp
                # does NOT — it would dead-end them back to the same error.
                assert "install-extension" in cmd, (
                    "host-unreachable fallback must name `install-extension` (wires the "
                    f"Native Messaging capture host); got code: {s['codes']!r}"
                )
                assert "install-mcp" not in s["text"], (
                    "host-unreachable fallback names `install-mcp` — the wrong-CTA "
                    "dead-end: install-mcp registers the MCP server, NOT the capture "
                    "host this panel needs; the user reloads to the same error"
                )
                assert fake_id in cmd, (
                    "the install-extension command must auto-fill --extension-id with "
                    f"chrome.runtime.id so it's copy-pasteable; got code: {s['codes']!r}"
                )
                assert s["scrollW"] <= s["clientW"] + 1, (
                    f"fallback box overflows horizontally at 393px "
                    f"(scrollW={s['scrollW']} clientW={s['clientW']})"
                )
                assert not errs, f"JS/console errors rendering the fallback box: {errs[:5]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
