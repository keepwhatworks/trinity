"""Browser guard: the launchpad's brand @font-face URLs must RESOLVE to the
published woff2 in the production serving layout — not just exist on disk.

Coverage gap this fills. ``test_vendor_integrity.py`` is thorough about the font
*files*: it pins their SHA-256, asserts they ship in the wheel's package-data
(the v1.7.310 latent bug — fonts present only via editable installs), and that
every ``VENDORED_FILES`` entry publishes into ``portal_pages/vendor/``. But ALL
of those verify the bytes exist and get copied. NONE verifies the cross-component
contract that actually paints the brand: that the rendered page's ``@font-face``
``src:url(...)`` resolves to those bytes in the layout the page is *served from*.

That URL is a load-bearing coupling between two files that don't know about each
other: the CSS string in ``design_system.py`` hard-codes
``url("../portal_pages/vendor/HankenGrotesk-400.woff2")``, and that ``../`` is
only correct because ``handle_serve`` roots the HTTP server at ``~/.trinity/`` and
serves ``/portal_pages/launchpad.html`` (so ``../portal_pages/vendor/`` climbs to
the home root and back down — the same path that also works for sibling
``review_pages/*.html`` and for ``file://`` opens). Change the prefix, move the
page, or rename ``vendor/`` and every font silently 404s → the browser falls back
to system fonts on every user, with no error and no failing test. That is
Trinity's signature bug shape (a silent degradation behind a green suite) and the
mechanical substrate of the footer brade-mark tofu (#276): a glyph only renders
if its font actually loaded.

This pins it end-to-end in a real browser, reproducing the ``handle_serve`` path
exactly (HTTP server rooted at the home dir, page at ``/portal_pages/...``): it
renders the REAL launchpad (``render_launchpad_html`` — what ``write_portal_html``
ships), publishes the real vendor assets, then asserts every brand face actually
reaches the browser by ``document.fonts.load(...)``-ing each one and requiring a
non-empty FontFaceSet (which a 404'd or undecodable URL cannot produce).

Mutation-proven: rewrite the ``../portal_pages/vendor/`` prefix in
``design_system.py`` to a wrong directory and every ``document.fonts.load`` rejects
with a network error → ``loaded`` drops to 0 → this reds. (Verified by hand during
authoring against both the correct path and a ``WRONGDIR`` mutation: 6/6 vs 0/6.)

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# The six self-hosted brand faces the launchpad CSS declares (Hanken Grotesk for
# display/body, JetBrains Mono for code). Each must resolve in the served layout.
BRAND_FACES = [
    ("Hanken Grotesk", "400"),
    ("Hanken Grotesk", "500"),
    ("Hanken Grotesk", "600"),
    ("Hanken Grotesk", "700"),
    ("JetBrains Mono", "400"),
    ("JetBrains Mono", "500"),
]


def _render_prod_layout(home: Path) -> None:
    """Render the REAL launchpad into the production directory shape
    (``home/portal_pages/launchpad.html`` + ``home/portal_pages/vendor/*``), the
    exact layout ``write_portal_html`` produces and ``handle_serve`` serves."""
    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pp = home / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(), encoding="utf-8")
    publish_vendor_files(pp)


def _serve_from_home(home: Path):
    """An HTTP server rooted at ``home`` — i.e. ``handle_serve``'s document root
    (NOT ``portal_pages/``). This is what makes ``../portal_pages/vendor/`` the
    correct relative path; serving from ``portal_pages/`` would (correctly) 404
    the fonts, which is the whole point of pinning the real root."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(home)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_launchpad_brand_fonts_resolve_in_prod_serving_layout(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _render_prod_layout(tmp_path)

    httpd, port = _serve_from_home(tmp_path)
    url = f"http://127.0.0.1:{port}/portal_pages/launchpad.html"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                font_404s: list[str] = []
                page.on(
                    "response",
                    lambda r: font_404s.append(r.url.split("/")[-1])
                    if r.status >= 400 and r.url.endswith(".woff2")
                    else None,
                )
                page.goto(url, wait_until="networkidle")
                # Explicitly resolve each declared face. document.fonts.load returns
                # the matched FontFaceSet once the woff2 is fetched + decoded; it
                # rejects (caught → 'ERR:…') when the @font-face src 404s. This tests
                # URL RESOLUTION directly, decoupled from whether the cold page
                # happens to paint every weight.
                results = page.evaluate(
                    """async (faces) => {
                      await document.fonts.ready;
                      const out = [];
                      for (const [fam, wt] of faces) {
                        try {
                          const got = await document.fonts.load(`${wt} 16px "${fam}"`);
                          out.push([fam, wt, got.length]);
                        } catch (e) {
                          out.push([fam, wt, 'ERR:' + e.message]);
                        }
                      }
                      return out;
                    }""",
                    BRAND_FACES,
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    unresolved = [r for r in results if not (isinstance(r[2], int) and r[2] > 0)]
    assert not unresolved, (
        "brand @font-face URLs did not resolve to a loadable woff2 in the prod "
        "serving layout (home root → /portal_pages/launchpad.html) — every user "
        "would silently fall back to system fonts. The ../portal_pages/vendor/ "
        f"path in design_system.py likely broke. Unresolved: {unresolved}"
    )
    assert not font_404s, f"woff2 font requests 404'd in the served layout: {font_404s}"
