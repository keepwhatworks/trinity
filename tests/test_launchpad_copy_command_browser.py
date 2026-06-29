"""Browser guard: the PRIMARY cold-start command code blocks (council / lens /
extension install) are one-tap copyable — a real Copy button that puts the exact
command on the clipboard and flashes "✓ Copied".

Found 2026-06-07 mobile-dogfooding the cold launchpad: the lead actions (`council
--task …`, `lens`) rendered as bare <pre> code blocks with NO copy affordance,
while the SECONDARY eval/setup commands were one-tap copy-chips. So the first-run
actions were harder to copy than the lesser ones — and on a phone they required a
horizontal scroll just to read. The fix wraps each primary block in
`.code-copy-wrap` with a `copy-badge` button wired to `copyCodeBlock`, which reads
the rendered <code> text (so "&lt;your question&gt;" copies as the literal
"<your question>") and reuses copyText's clipboard + flash path.

This pins it in a real browser (clipboard + petite-vue reactivity can't be checked
from a static string). Mutation-provable: delete a copy button and the count
assertion reds; break copyCodeBlock and the clipboard assertion reds.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Cold page_data: no personalRoutingTable → the "Run a few councils" card paints
# the `council --task` CTA; the lens card paints the `lens` CTA. Both carry a
# code-copy-wrap. (The extension-install block needs browserCapture, exercised by
# the live cold-home render elsewhere; two blocks are enough to pin the contract.)
_PAGE_DATA: dict = {}

_COUNCIL_CMD = 'trinity-local council --task "<your question>"'


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _write_prod_layout(html: str, serve_root: Path) -> str:
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return "portal_pages/launchpad.html"


def test_primary_command_blocks_are_one_tap_copyable(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data=_PAGE_DATA, view="stats")
    rel = _write_prod_layout(html, tmp_path)
    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                ctx = browser.new_context(
                    viewport={"width": 1180, "height": 2000},
                    permissions=["clipboard-read", "clipboard-write"],
                )
                page = ctx.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_selector(".code-copy-wrap .copy-badge", timeout=10000)

                badges = page.eval_on_selector_all(".code-copy-wrap .copy-badge", "els => els.length")
                assert badges >= 2, (
                    f"expected the primary cold-start command blocks (council + lens) to "
                    f"each carry a Copy button; found {badges}"
                )

                # The council CTA button must copy the EXACT command (placeholder
                # decoded) and flip its badge to the copied state.
                btn = page.query_selector(
                    "xpath=//div[contains(@class,'code-copy-wrap')]"
                    "[.//code[contains(text(),'council --task')]]//button"
                )
                assert btn, "council command block has no Copy button"
                btn.click()
                page.wait_for_function(
                    "() => { const w=[...document.querySelectorAll('.code-copy-wrap')]"
                    ".find(x=>/council --task/.test(x.innerText)); "
                    "return w && /Copied/.test(w.querySelector('.copy-badge').innerText); }",
                    timeout=3000,
                )
                clip = page.evaluate("() => navigator.clipboard.readText()")
                assert clip.strip() == _COUNCIL_CMD, (
                    f"council Copy button put {clip!r} on the clipboard, expected {_COUNCIL_CMD!r} "
                    "(the rendered <code> text, with the &lt;&gt; placeholder decoded)"
                )

                assert not errs, f"JS errors on the copy-command launchpad: {errs[:3]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
