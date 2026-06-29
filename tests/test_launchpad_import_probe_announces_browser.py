"""The Bulk-import PROBE / IMPORT result must be ANNOUNCED to a screen reader
(WCAG 4.1.3 status messages) — not painted silently.

A11Y defect (UX sweep 2026-06-22): a user who pastes a Takeout/export path and
clicks Probe lands on the visible success banner

    ✓ Detected 2 export(s)

(or, on a bad path, the "⚠ <error>" warning banner). Both render into a plain
`<div>` inside the bulk-import `<details>` — no `role=status`, no `aria-live`, and
the handler never mirrored the outcome into the launchpad's persistent
`#live-announcement` sr-only region. A SIGHTED user sees the banner; a SCREEN-READER
user who clicked Probe / Import hears SILENCE — the success / error / dispatched
outcome was VISUAL-ONLY.

WHY THE GLOBAL A11Y GUARD (the launchpad 4.1.3 sweep) MISSED IT: those guards read
each surface's INITIAL DOM. This banner is a CONDITIONAL state
(`v-if="importProbeResult && ..."`) that only mounts AFTER a successful Probe
dispatch, so the live region was never re-checked post-interaction. This guard
DRIVES the real dispatch and reads `#live-announcement` after the banner mounts.

The fix: `probeImportPath` / `confirmImport` call `announceImportResult()`, which
stashes the outcome text in `importAnnouncement`; `liveAnnouncement` (the getter
bound to the persistent `#live-announcement` role=status region) surfaces it at the
lowest precedence (just above a copy ack, below a live dispatch/council).

MUTATION-PROVEN to BITE: early-return the BODY of `announceImportResult` (so the
outcome is never stashed) → REBUILD the bundles → this guard reds (the live region
stays empty after Probe AND after Import) with the founder symptom; restore +
REBUILD → green (byte-identical bundle).
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _live_region_text(page):
    return page.evaluate(
        "() => { const e = document.getElementById('live-announcement');"
        " return e ? e.innerText.trim() : '(no #live-announcement)'; }"
    )


def test_import_probe_and_import_announce_to_screen_reader(tmp_path, monkeypatch):
    """After a successful Probe (then a successful Import), the launchpad's
    persistent #live-announcement region must carry the outcome text — otherwise
    the result is silent to a screen-reader user (WCAG 4.1.3)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    # A dispatcher that answers BOTH the dry-run probe (2 detected sources) and the
    # full import (ok) so we can drive Probe → Import end-to-end.
    probe_ok = json.dumps(
        {
            "ok": True,
            "stdout": json.dumps(
                {"detected": [{"source": "chatgpt", "hint": "12 conversations"},
                              {"source": "gemini", "hint": "40 conversations"}]}
            ),
        }
    )
    init_dispatch = (
        "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
        "  if (!o || !o.extensionAction || !o.onResult) return;"
        "  if (o.extensionAction.kind === 'import-export-dry-run') { o.onResult(" + probe_ok + "); }"
        "  else if (o.extensionAction.kind === 'import-export') { o.onResult({ ok: true }); }"
        " }, onStateChange: function(){}, isAvailable: function(){return true;} };"
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                for width in (393, 1280):
                    page = browser.new_context(viewport={"width": width, "height": 1100}).new_page()
                    page.add_init_script(init_dispatch)
                    page.goto(
                        f"http://127.0.0.1:{port}/portal_pages/stats.html",
                        wait_until="networkidle",
                        timeout=20000,
                    )
                    page.wait_for_function(
                        "() => { const r = document.getElementById('launchpad-app');"
                        " return r && !r.hasAttribute('v-cloak'); }",
                        timeout=10000,
                    )

                    # PRECONDITION: a persistent role=status live region exists and is
                    # empty (no stale text) before any interaction.
                    region_role = page.evaluate(
                        "() => { const e = document.getElementById('live-announcement');"
                        " return e ? (e.getAttribute('role') + '|' + e.getAttribute('aria-live')) : null; }"
                    )
                    assert region_role == "status|polite", (
                        f"@{width}px: #live-announcement is not a polite role=status "
                        f"region (got {region_role!r}) — the announce mechanism this "
                        "guard relies on is missing."
                    )
                    assert _live_region_text(page) == "", (
                        f"@{width}px: #live-announcement carried stale text before Probe."
                    )

                    # Paste a path + Probe → the success banner mounts.
                    page.fill("section.import-export-card input[type=text]", "/Users/you/Downloads/exports")
                    page.evaluate(
                        "() => { document.querySelector('section.import-export-card')"
                        " .querySelector('button').click(); }"
                    )
                    page.wait_for_function(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const s = c.querySelector('strong');"
                        " return s && /Detected/.test(s.innerText); }",
                        timeout=4000,
                    )

                    # THE BITE: the probe outcome must have landed in the live region.
                    probe_live = _live_region_text(page)
                    assert "Detected 2 export" in probe_live, (
                        f"@{width}px: a screen-reader user who clicked Probe heard "
                        f"SILENCE — #live-announcement is {probe_live!r}, not the "
                        "'Detected N export(s)' outcome. The probe-SUCCESS banner is a "
                        "plain <div> with no role=status; route its text through the "
                        "existing liveAnnouncement (importAnnouncement / "
                        "announceImportResult), don't leave it visual-only (WCAG 4.1.3)."
                    )

                    # Now click Import → the dispatched ack must ALSO announce. The
                    # Import button is the one inside the success banner whose label
                    # starts "Import" (the Probe button reads "Probe"). innerText is
                    # CSS-uppercased ("IMPORT 2 SOURCE(S)"), so match case-insensitively.
                    page.evaluate(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const btn = Array.from(c.querySelectorAll('button'))"
                        "   .find(b => /^import\\b/i.test(b.innerText.trim()));"
                        " if (btn) btn.click(); }"
                    )
                    page.wait_for_function(
                        "() => { const e = document.getElementById('live-announcement');"
                        " return e && /dispatch/i.test(e.innerText); }",
                        timeout=4000,
                    )
                    import_live = _live_region_text(page)
                    assert "dispatch" in import_live.lower(), (
                        f"@{width}px: the Import 'Dispatched' ack is silent to a screen "
                        f"reader — #live-announcement is {import_live!r}. Route the "
                        "import outcome through liveAnnouncement too (WCAG 4.1.3)."
                    )
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
