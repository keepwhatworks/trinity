"""Editing the Bulk-import path AFTER a successful Probe must INVALIDATE the
"✓ Detected N export(s)" confirmation — otherwise Import pays the full embedding
cost against an UN-PROBED path under a stale confirmation.

USABILITY / wrong-target / data-integrity defect (UX sweep 2026-06-23): the
bulk-import card is a deliberate two-step probe→confirm flow whose whole reason to
exist is "we want the user to confirm what got detected before paying the embedding
cost" (the card's source comment). But the path `<input>` was a bare
`v-model="importPath"` with NO input handler, so:

  1. Probe folder A  →  banner "✓ Detected 3 export(s)" + "Import 3 source(s)" button.
  2. User edits the path to folder B (a DIFFERENT, un-probed folder).
  3. Banner STILL reads "✓ Detected 3 export(s)" (describing A); the Import button
     is still offered.
  4. Click Import  →  `confirmImport` dispatches the FULL (paid) `import-export`
     against `this.importPath` == folder B — a path that was never probed, while the
     UI claimed 3 exports were detected. Folder B might hold 0 exports, or different
     ones; the confirm-before-cost contract is broken.

The fix (`onImportPathInput`, wired via `@input` on the path input + an
`importProbedPath` field that pins the result to the path it describes): once the
typed path diverges from the probed path, clear `importProbeResult` / reset
`importStatus`, so the card falls back to "Probe". The user must re-probe the new
path before Import is offered again.

MUTATION-PROVEN to BITE: void the body of `onImportPathInput` (so an edit no longer
invalidates the stale banner) → REBUILD the bundles → this guard reds (the stale
"Detected 3 export(s)" banner survives the edit AND an Import dispatches the full
ingest against the un-probed folder B) with the founder symptom; restore + REBUILD
→ green.
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


def _detected_banner_text(page):
    return page.evaluate(
        "() => { const c = document.querySelector('section.import-export-card');"
        " const s = c && c.querySelector('strong');"
        " return (s && /Detected/.test(s.innerText)) ? s.innerText.trim() : null; }"
    )


def test_editing_path_invalidates_stale_probe_and_blocks_unprobed_import(tmp_path, monkeypatch):
    """A probe of folder A followed by an edit to folder B must clear A's "Detected"
    banner; Import must NOT dispatch the full ingest against the un-probed B."""
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

    # A dispatcher that RECORDS every dispatch (so we can prove what path Import paid
    # for) and answers the dry-run probe with 3 detected sources for ANY path.
    probe_ok = json.dumps(
        {
            "ok": True,
            "stdout": json.dumps(
                {
                    "detected": [
                        {"source": "chatgpt", "hint": "12 conversations"},
                        {"source": "claude_ai", "hint": "8 conversations"},
                        {"source": "gemini", "hint": "40 conversations"},
                    ]
                }
            ),
        }
    )
    init_dispatch = (
        "window.__DISPATCHED__ = [];"
        "window.__TRINITY_DISPATCH__ = { state: 'ready', extensionId: 'stub',"
        " onStateChange: function(){}, isAvailable: function(){return true;},"
        " probe: function(){ return Promise.resolve({state:'ready'}); },"
        " dispatch: function(o){"
        "  if (!o || !o.extensionAction || !o.onResult) return;"
        "  const a = o.extensionAction;"
        "  window.__DISPATCHED__.push({ kind: a.kind, path: a.path });"
        "  if (a.kind === 'import-export-dry-run') { o.onResult(" + probe_ok + "); }"
        "  else if (a.kind === 'import-export') { o.onResult({ ok: true }); }"
        " } };"
    )

    PATH_A = "/Users/me/Downloads/Takeout-A"
    PATH_B = "/Users/me/Downloads/SOME-OTHER-FOLDER-B"

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # 393 (panel/mobile) + 1280 (desktop) — the same widths the sibling
                # import-card browser guards drive.
                for width in (393, 1280):
                    page = browser.new_context(
                        viewport={"width": width, "height": 1100}
                    ).new_page()
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

                    sel = "section.import-export-card input[type=text]"

                    # STEP 1 — Probe folder A. PRECONDITION (non-vacuous): the banner
                    # mounts and reads "Detected 3 export(s)", and an Import button is
                    # offered. (If this fails the whole flow is broken upstream and the
                    # bite below would be vacuous.)
                    page.fill(sel, PATH_A)
                    page.evaluate(
                        "() => { document.querySelector('section.import-export-card')"
                        " .querySelector('button').click(); }"
                    )
                    page.wait_for_function(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const s = c.querySelector('strong');"
                        " return s && /Detected 3 export/.test(s.innerText); }",
                        timeout=4000,
                    )
                    assert _detected_banner_text(page), (
                        f"@{width}px PRECONDITION: the Probe success banner never "
                        "mounted — flow broken upstream, bite would be vacuous."
                    )
                    import_offered = page.evaluate(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " return Array.from(c.querySelectorAll('button'))"
                        "   .some(b => /^import\\b/i.test(b.innerText.trim())); }"
                    )
                    assert import_offered, (
                        f"@{width}px PRECONDITION: no Import button after a successful "
                        "probe — flow broken upstream, bite would be vacuous."
                    )

                    # STEP 2 — EDIT the path to a DIFFERENT, un-probed folder B.
                    page.fill(sel, PATH_B)
                    # let petite-vue react to the @input.
                    page.wait_for_timeout(250)

                    # THE BITE #1: the stale "Detected 3 export(s)" banner (describing
                    # folder A) must be GONE — it described a path the user has since
                    # replaced.
                    stale = _detected_banner_text(page)
                    assert stale is None, (
                        f"@{width}px: the bulk-import banner still reads {stale!r} after "
                        f"the user replaced the probed path ({PATH_A}) with a DIFFERENT, "
                        f"un-probed one ({PATH_B}). The 'Detected 3 exports' confirmation "
                        "now describes a folder that is no longer in the box — and Import "
                        "would pay the full embedding cost against the un-probed path. "
                        "Editing the path must invalidate the stale probe "
                        "(onImportPathInput)."
                    )

                    # THE BITE #2: with the banner invalidated, there is no Import button;
                    # attempting to click one must NOT dispatch the full ingest against the
                    # un-probed folder B. (Belt-and-suspenders: even if a stale button
                    # lingered, no `import-export` should fire against B without a re-probe.)
                    page.evaluate(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const btn = Array.from(c.querySelectorAll('button'))"
                        "   .find(b => /^import\\b/i.test(b.innerText.trim()));"
                        " if (btn) btn.click(); }"
                    )
                    page.wait_for_timeout(200)
                    dispatched = page.evaluate("window.__DISPATCHED__")
                    full_imports = [d for d in dispatched if d.get("kind") == "import-export"]
                    assert full_imports == [], (
                        f"@{width}px: a full (paid) import-export dispatched against an "
                        f"UN-PROBED path while the UI claimed 3 exports were detected for a "
                        f"DIFFERENT folder — {json.dumps(full_imports)}. Editing the path "
                        "must invalidate the confirmation so Import can't fire without a "
                        "re-probe (confirm-before-cost)."
                    )

                    # SANITY (flow still works): re-probe B, then Import → the full
                    # ingest now correctly targets B.
                    page.evaluate(
                        "() => { document.querySelector('section.import-export-card')"
                        " .querySelector('button').click(); }"
                    )
                    page.wait_for_function(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const s = c.querySelector('strong');"
                        " return s && /Detected 3 export/.test(s.innerText); }",
                        timeout=4000,
                    )
                    page.evaluate(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const btn = Array.from(c.querySelectorAll('button'))"
                        "   .find(b => /^import\\b/i.test(b.innerText.trim()));"
                        " if (btn) btn.click(); }"
                    )
                    page.wait_for_timeout(250)
                    dispatched = page.evaluate("window.__DISPATCHED__")
                    full_imports = [d for d in dispatched if d.get("kind") == "import-export"]
                    assert full_imports and full_imports[-1]["path"] == PATH_B, (
                        f"@{width}px SANITY: after a fresh re-probe of B the full import "
                        f"should target B; got {json.dumps(full_imports)}."
                    )

                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
