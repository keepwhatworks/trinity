"""CLASS guard: the side-panel sandbox must run EVERY interactive feature WITHOUT a
Content-Security-Policy violation in the REAL chrome-extension:// panel.

THE CLASS (2026-06-18). The MV3 sandbox CSP is STRICTER than file://. The manifest
declares ``sandbox.pages`` but no ``content_security_policy.sandbox``, so Chrome applies
the DEFAULT sandbox CSP — ``script-src 'self' 'unsafe-inline' 'unsafe-eval'; child-src
'self'``. Every interactive feature in the panel rides that grant:

  • petite-vue compiles ``{{ }}`` interpolations and ``v-…`` / ``@click`` directives via
    ``new Function`` → needs ``'unsafe-eval'``.
  • The settings toggle is an INLINE petite-vue EXPRESSION ``@click="settingsOpen =
    !settingsOpen"`` → also ``new Function``.
  • ``sandbox/live_council.html`` has an INLINE ``<script>`` block (no src) → needs
    ``'unsafe-inline'`` for scripts.
  • Inline ``<style>`` blocks + ``style="…"`` attrs + a ``data:image/svg+xml`` favicon.

The bug shape: a future change tightens ``content_security_policy.sandbox`` (or a feature
starts relying on a directive the sandbox CSP forbids — an inline ``onclick=``, a
``javascript:`` URL, a ``new Function`` under a strict policy) → Chrome REFUSES it and
emits "Refused to … because it violates the following Content Security Policy directive"
to the console, and the feature SILENTLY no-ops. A file:// render (no sandbox CSP) never
exposes it — so a control that works in every file:// test ships dead in the real panel.

This guard drives the REAL panel through mount → settings toggle → rail drawer → /stats
view switch → a brokered council page (inline-script mount) → a stubbed Launch → a copy,
collecting EVERY error/warning console message + page error, and asserts ZERO match
"Content Security Policy" / "Refused to" / "unsafe-eval" / "unsafe-inline" / "violates the
following". It ALSO asserts the POSITIVE petite-vue-ran proof (heroTitle interpolated, no
raw ``{{ }}`` leak, the lp-view-stats class applied, the settings modal visible) so a green
means "the eval-dependent features actually executed", not "nothing ran".

Mutation-provable: add a restrictive ``content_security_policy.sandbox`` WITHOUT
``'unsafe-eval'`` to browser-extension/manifest.json (e.g. ``"sandbox": "sandbox
allow-scripts; script-src 'self' 'unsafe-inline'; child-src 'self';"``) → petite-vue's
``new Function`` is refused → the panel emits a "Refused to evaluate a string as JavaScript
because 'unsafe-eval' is not an allowed source" CSP violation AND the raw ``{{ }}`` leak +
hero-not-interpolated positive checks fail → this reds with the CSP symptom. Restore → green.

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

_CSP_MARKERS = (
    "content security policy",
    "refused to",
    "unsafe-eval",
    "unsafe-inline",
    "violates the following",
)


def _is_csp(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _CSP_MARKERS)


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host, load the real extension, open the
    side panel, return (ctx, ext_id, page) after the launchpad mounts."""
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
    page.add_init_script(
        "window.__TRINITY_DISPATCH__ = { dispatch: function(o){ if(o&&o.onResult) o.onResult({ok:true}); },"
        " onStateChange: function(){}, warmProbe: function(){} };"
    )
    page.set_viewport_size({"width": 393, "height": 900})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4500)
    return ctx, ext_id, page


def test_no_csp_violation_across_panel_interactions(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        warnings_errors: list[str] = []
        page_errors: list[str] = []

        # context-level listeners catch SUB-FRAME (sandbox iframe) messages, not just top.
        def _on_console(m) -> None:
            if m.type in ("error", "warning"):
                warnings_errors.append(f"[{m.type}] {m.text[:240]}")

        def _on_pageerror(e) -> None:
            page_errors.append(str(e)[:240])

        ctx.on("console", _on_console)
        # pageerror is a PAGE event (not a BrowserContext event) — attach to the
        # top page. CSP refusals in the sandbox iframe still surface via the
        # context-level console listener above.
        page.on("pageerror", _on_pageerror)

        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # ── POSITIVE proof petite-vue's new Function ran under the sandbox CSP ──
            hero = lf.evaluate("()=>{const h=document.querySelector('h1');return h?h.textContent:'';}")
            assert hero and "{{" not in hero, (
                f"hero title not interpolated — petite-vue new Function may be CSP-blocked: {hero!r}"
            )
            home_braces = lf.evaluate(
                "()=>document.body.innerText.includes('{{') && document.body.innerText.includes('}}')"
            )
            assert not home_braces, (
                "raw {{ }} leaked on the launchpad home — petite-vue did not compile its "
                "templates (new Function refused by the sandbox CSP?)."
            )

            # ── settings toggle: inline petite-vue EXPRESSION via new Function ──
            lf.evaluate(
                "()=>{const b=document.querySelector('button[aria-label=\"Open settings\"]');if(b)b.click();}"
            )
            page.wait_for_timeout(500)
            modal_vis = lf.evaluate(
                "()=>{const m=document.querySelector('.settings-modal');if(!m)return 'no-el';"
                "const r=m.getBoundingClientRect();return (r.width>0&&r.height>0)?'visible':'hidden';}"
            )
            assert modal_vis == "visible", (
                "settings modal did not open — the inline @click expression "
                "(settingsOpen=!settingsOpen) is compiled via new Function; a CSP that "
                f"forbids unsafe-eval would silently no-op it. modal={modal_vis!r}"
            )
            lf.evaluate(
                "()=>{const b=document.querySelector('button[aria-label=\"Open settings\"]');if(b)b.click();}"
            )
            page.wait_for_timeout(300)

            # ── rail drawer toggle (petite-vue @click method → body class) ──
            lf.evaluate(
                "()=>{const b=[...document.querySelectorAll('button,[role=button]')]"
                ".find(x=>/menu|rail|recent|councils/i.test((x.getAttribute('aria-label')||'')+(x.title||'')));"
                "if(b)b.click();}"
            )
            page.wait_for_timeout(300)
            # close it again so it doesn't overlay the stats link
            lf.evaluate(
                "()=>{const b=[...document.querySelectorAll('button,[role=button]')]"
                ".find(x=>/menu|rail|recent|councils/i.test((x.getAttribute('aria-label')||'')+(x.title||'')));"
                "if(b)b.click();}"
            )
            page.wait_for_timeout(300)

            # ── /stats view switch (interceptor → setLaunchpadView classList) ──
            lf.evaluate("()=>{const a=document.querySelector('a[href$=\"stats.html\"]');if(a)a.click();}")
            page.wait_for_timeout(1500)
            sf = page.frames[-1]
            root_cls = sf.evaluate(
                "()=>{const r=document.getElementById('launchpad-app');return r?r.className:'no-root';}"
            )
            assert "lp-view-stats" in (root_cls or ""), (
                "the /stats view class was not applied — the @click → setLaunchpadView path "
                f"may be CSP-blocked. #launchpad-app class={root_cls!r}"
            )

            # ── brokered council page (inline <script> mount under CSP) ──
            clicked = sf.evaluate(
                "()=>{const a=document.querySelector('a.rail-council[href*=\"live_council.html\"]')"
                "||document.querySelector('a[href*=\"live_council.html\"]');"
                "if(a){a.click();return a.getAttribute('href');}return null;}"
            )
            assert clicked and "live_council.html" in clicked, (
                f"could not drive a council-rail link (got {clicked!r})"
            )
            page.wait_for_timeout(4500)
            cf = page.frames[-1]
            assert "sandbox/live_council.html" in (cf.url or ""), (
                f"council page never loaded into the panel (frame={cf.url})"
            )
            council_braces = cf.evaluate(
                "()=>document.body.innerText.includes('{{') && document.body.innerText.includes('}}')"
            )
            assert not council_braces, (
                "raw {{ }} leaked on the council page — its inline <script> + petite-vue "
                "mount was blocked (inline-script / unsafe-eval refused by the sandbox CSP?)."
            )
            page.wait_for_timeout(800)

            # ── back to home, drive a stubbed Launch + a copy (petite-vue @click methods) ──
            cf.evaluate("()=>{const a=document.querySelector('a[href$=\"launchpad.html\"]');if(a)a.click();}")
            page.wait_for_timeout(3000)
            hf = page.frames[-1]
            hf.evaluate("()=>{const a=document.querySelector('a[href$=\"launchpad.html\"]');if(a)a.click();}")
            page.wait_for_timeout(600)
            hf.evaluate(
                "()=>{const b=[...document.querySelectorAll('button')]"
                ".find(x=>/launch/i.test(x.textContent||''));if(b)b.click();}"
            )
            page.wait_for_timeout(600)
            hf.evaluate(
                "()=>{const b=[...document.querySelectorAll('button,[role=button]')]"
                ".find(x=>/copy|rebuild/i.test((x.getAttribute('aria-label')||'')+(x.title||'')+x.textContent));"
                "if(b)b.click();}"
            )
            page.wait_for_timeout(600)

            # ── THE CLASS ASSERT: zero CSP violations anywhere in the round trip ──
            csp_console = [m for m in warnings_errors if _is_csp(m)]
            csp_perr = [e for e in page_errors if _is_csp(e)]
            assert not csp_console, (
                "the side-panel sandbox emitted a Content-Security-Policy VIOLATION in the "
                "real Chrome panel — a feature that works on file:// is being REFUSED by the "
                "stricter sandbox CSP (inline handler / unsafe-eval / unsafe-inline). "
                f"violations: {csp_console}"
            )
            assert not csp_perr, (
                "a page error in the side-panel sandbox referenced a CSP violation — an "
                "eval/Function the panel needs is refused by the sandbox CSP. "
                f"page errors: {csp_perr}"
            )
        finally:
            ctx.close()
