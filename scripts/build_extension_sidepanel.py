#!/usr/bin/env python3
"""Generate the SANDBOXED side-panel launchpad
(browser-extension/sandbox/launchpad.html + launchpad-init.js).

WHY a sandbox: the side panel can only load a `chrome-extension://` page, and MV3
extension pages run under `script-src 'self'` — which forbids `'unsafe-eval'`, the
`new Function()` petite-vue uses to evaluate templates. So the launchpad shows raw
`{{ }}` as a normal extension page. A manifest `sandbox.pages` entry gets a relaxed
CSP that ALLOWS eval, so petite-vue runs there. The shell page (sidepanel.html)
iframes this sandboxed page and bridges chrome.runtime for it (sidepanel-bridge.js
↔ sandbox/_bridge.js), since a sandboxed page can't touch chrome.* itself.

This reuses build_extension_launchpad.build() (same fetch-over-Native-Messaging
launchpad) and then:
  1. rewrites the vendor paths up one level (the page lives in sandbox/, vendor/
     lives at the extension root), and
  2. injects <script src="./_bridge.js"> BEFORE the app so window.chrome.runtime
     is shimmed when the launchpad's fetch bootstrap + dispatch first read it.

The static shell + bridge files (sidepanel.html, sidepanel-bridge.js,
sandbox/_bridge.js) are hand-maintained, not generated. Regenerate on any
launchpad change:

    .venv/bin/python scripts/build_extension_sidepanel.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
EXT = REPO / "browser-extension"
SANDBOX = EXT / "sandbox"

_PETITE_VUE_TAG = '<script src="../vendor/petite-vue.iife.js"></script>'
_BRIDGE_TAG = '<script src="./_bridge.js"></script>'


def build() -> tuple[str, str]:
    """Return (sandbox/launchpad.html, sandbox/launchpad-init.js). Pure: no writes."""
    import build_extension_launchpad

    html, init_js = build_extension_launchpad.build()

    # The page moves from the extension ROOT (vendor/ alongside it) into sandbox/,
    # so every vendor reference needs one more `../`. The built launchpad only ever
    # uses `./vendor/` (scripts) and `"vendor/` / `'vendor/` / `(vendor/` (CSS url()),
    # never `../vendor/`, so these replacements can't double-prefix.
    for needle, repl in (
        ("./vendor/", "../vendor/"),
        ('"vendor/', '"../vendor/'),
        ("'vendor/", "'../vendor/"),
        ("(vendor/", "(../vendor/"),
    ):
        html = html.replace(needle, repl)

    # launchpad-init.js ALSO embeds a vendor path: renderChart() lazy-injects
    # `s.src = './vendor/chart.umd.min.js'` (CHART_JS_SRC). That JS string is NOT in
    # the HTML, so the HTML-only rewrite above misses it — and the sandbox page lives
    # in sandbox/, so `./vendor/chart.umd.min.js` resolved to the nonexistent
    # `sandbox/vendor/chart.umd.min.js` → ERR_FILE_NOT_FOUND → BOTH /stats charts
    # (the routing-strength bars + the Local Elo chart) rendered BLANK in the real
    # Chrome side panel, while file:// renders (vendor/ alongside) stayed green.
    # Rewrite the init JS too so any `./vendor/X` it injects resolves from sandbox/.
    init_js = init_js.replace("./vendor/", "../vendor/")
    # Green-gate: the chart vendor ref MUST be present + rewritten — a refactor that
    # renames CHART_JS_SRC or drops the renderChart injection would silently re-blank
    # the in-panel charts, so fail loudly instead of shipping the regression.
    if "../vendor/chart.umd.min.js" not in init_js:
        raise SystemExit(
            "build_extension_sidepanel: the Chart.js vendor path was not rewritten to "
            "../vendor/ in launchpad-init.js — renderChart()'s CHART_JS_SRC injection "
            "changed; the side-panel /stats charts will 404 (blank). Update this build."
        )

    if _PETITE_VUE_TAG not in html:
        raise SystemExit(
            "build_extension_sidepanel: couldn't find the petite-vue <script> after the "
            "vendor rewrite — the launchpad's vendor refs changed; update this build."
        )
    # The bridge shim MUST run before the app (and before any code that reads
    # chrome.runtime). Inject it just ahead of petite-vue + launchpad-init.js.
    html = html.replace(_PETITE_VUE_TAG, _BRIDGE_TAG + "\n  " + _PETITE_VUE_TAG, 1)
    return html, init_js


def build_council() -> str:
    """Return the sandboxed live council page. Its data loaders already use the
    host bridge when __TRINITY_HOST_FETCH__ is set (sandbox/_bridge.js), so this
    just repoints vendor to ../vendor/ and injects the bridge shim. Inline scripts
    stay inline — the sandbox CSP allows them, unlike the extension-page CSP."""
    import os

    os.environ.setdefault("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.council_review import render_live_council_page

    html = render_live_council_page()
    # The page moves from review_pages/ to sandbox/; its vendor lives at the
    # extension root, so ../portal_pages/vendor/ → ../vendor/.
    html = html.replace("../portal_pages/vendor/", "../vendor/")
    # In-panel nav: the council page sits beside launchpad.html in sandbox/. Point
    # the back-link (pageData.launchpadUrl) + the thread-view base derived from it
    # at ./launchpad.html so both stay inside the sandbox (chrome-extension nav,
    # query preserved) instead of the file:// review_pages path.
    html = html.replace("../portal_pages/launchpad.html", "./launchpad.html")
    if _PETITE_VUE_TAG not in html:
        raise SystemExit(
            "build_extension_sidepanel: couldn't find the petite-vue <script> in the council "
            "page after the vendor rewrite — its vendor refs changed; update this build."
        )
    html = html.replace(_PETITE_VUE_TAG, _BRIDGE_TAG + "\n  " + _PETITE_VUE_TAG, 1)
    return html


def main() -> int:
    SANDBOX.mkdir(exist_ok=True)
    html, init_js = build()
    (SANDBOX / "launchpad.html").write_text(html, encoding="utf-8")
    (SANDBOX / "launchpad-init.js").write_text(init_js, encoding="utf-8")
    council = build_council()
    (SANDBOX / "live_council.html").write_text(council, encoding="utf-8")
    print(
        f"wrote {SANDBOX / 'launchpad.html'} ({len(html):,} B) + "
        f"launchpad-init.js ({len(init_js):,} B) + "
        f"live_council.html ({len(council):,} B)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
