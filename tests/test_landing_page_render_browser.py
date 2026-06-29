"""Browser render guard for the PUBLIC pages (keepwhatworks.com = docs/, served by
GitHub Pages from main:/docs). `test_landing_page_links.py` pins that the internal
links/assets EXIST; this pins that the pages actually RENDER for a visitor: CSS
applies, every asset loads (no 4xx), and — the real risk — NO horizontal overflow
on a phone. `docs/style.css` has a `width: 100vw` full-bleed, the classic mobile
overflow cause (100vw includes the scrollbar; a `left:50%;margin-left:-50vw`
full-bleed overflows trivially if a parent regains padding). A broken mobile
landing loses the largest slice of marketing traffic, and nothing rendered these
pages — the static-HTML cousin of the launchpad mobile-overflow bug
([[file_substrate_browser_testing]]).

TWO overflow metrics, both load-bearing:
  - page-scroll (`documentElement.scrollW - clientW`) — the coarse "does the page
    scroll sideways" signal.
  - ELEMENT-level (any element whose `getBoundingClientRect().right` exceeds the
    viewport) — the metric with teeth. `docs/style.css` carries `overflow-x:
    hidden` on <html,body> (it clips a Linux-scrollbar full-bleed overflow the Mac
    can't reproduce). But that clip ALSO pins scrollW to clientW, so the page-scroll
    metric can no longer see an element that overflows — it just gets clipped
    (content lost at the edge, invisibly). `getBoundingClientRect` reports true
    layout geometry regardless of the clip, so the element-level scan still catches
    it. Without this scan the guard is blind to exactly the class of bug the clip
    hides (a long unbreakable inline-code token was overflowing 2px under the clip).

`browser`-marked → runs in the dedicated browser CI job; skips without chromium.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

DOCS = Path(__file__).resolve().parents[1] / "docs"

# Returns page-scroll overflow AND any elements laid out past the viewport's right
# edge (the latter survives `overflow-x: hidden`, which the former does not).
_MEASURE = """() => {
  const vw = document.documentElement.clientWidth;
  const wide = [];
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;  // hidden — no visual overflow
    if (r.right > vw + 1) {
      const cls = el.className ? '.' + String(el.className).slice(0, 24) : '';
      wide.push(el.tagName + cls + '@' + Math.round(r.right) + 'px "'
                + (el.textContent || '').trim().slice(0, 24) + '"');
    }
  }
  return {
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
    bodyLen: (document.body.innerText || '').trim().length,
    elemOverflow: wide.slice(0, 6),
  };
}"""


def _public_pages() -> list[tuple[str, Path]]:
    return [("index", DOCS / "index.html")] + [
        (f"essay:{p.stem}", p) for p in sorted((DOCS / "articles").glob("*.html"))
    ]


def test_public_pages_render_responsive_no_overflow_no_4xx():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    pages = _public_pages()
    assert len(pages) >= 6, f"expected the landing page + 5 essays, found {len(pages)}"

    problems: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium for the public-page render test: {exc}")
        try:
            for vw in (1280, 375):
                for name, path in pages:
                    ctx = browser.new_context(viewport={"width": vw, "height": 900})
                    page = ctx.new_page()
                    bad_assets: list[str] = []
                    errs: list[str] = []
                    page.on("requestfailed", lambda r: bad_assets.append(f"FAIL:{r.url.split('/')[-1]}"))
                    page.on("response", lambda r: bad_assets.append(f"{r.status}:{r.url.split('/')[-1]}") if r.status >= 400 else None)
                    page.on("console", lambda m: errs.append(m.text[:100]) if m.type == "error" else None)
                    page.on("pageerror", lambda e: errs.append(str(e)[:100]))
                    page.goto(f"file://{path}", wait_until="load")
                    page.wait_for_timeout(400)
                    m = page.evaluate(_MEASURE)
                    overflow = m["scrollW"] - m["clientW"]
                    label = f"{name}@{vw}px"
                    if m["bodyLen"] < 300:
                        problems.append(f"{label}: ~blank (body={m['bodyLen']}) — CSS/asset load failed")
                    if overflow > 1:
                        problems.append(f"{label}: horizontal overflow {overflow}px (scrollW {m['scrollW']} > clientW {m['clientW']})")
                    # element-level overflow — survives `overflow-x: hidden`, which
                    # the page-scroll metric above does not (see module docstring).
                    if m["elemOverflow"]:
                        problems.append(f"{label}: element(s) overflow the viewport "
                                        f"(clipped by overflow-x:hidden — invisible to page-scroll): {m['elemOverflow']}")
                    # favicon 404s are cosmetic + common on file://; only flag stylesheet/image loads
                    real_bad = [a for a in bad_assets if "favicon" not in a.lower()]
                    if real_bad:
                        problems.append(f"{label}: failed asset(s) {real_bad[:4]}")
                    if errs:
                        problems.append(f"{label}: console/page errors {errs[:3]}")
                    ctx.close()
        finally:
            browser.close()

    assert not problems, "public page render problems (keepwhatworks.com):\n  " + "\n  ".join(problems)
