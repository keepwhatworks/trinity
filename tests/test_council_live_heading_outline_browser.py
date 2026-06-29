"""Real-browser HEADING-OUTLINE guard for the LIVE council page.

WCAG 1.3.1 (Info & Relationships) / 2.4.6 (Headings & Labels). A screen-reader user
navigates a page by heading; the page must present a single, navigable outline with
exactly ONE top-level (h1) heading and no sibling competing for that slot.

The LIVE council page (`render_live_council_page` → `review_pages/live_council.html`,
the page a launching council actually opens — NOT the dead `render_unified_council_page`
#311) carried TWO literal <h1>s in the COMMON populated state (found by reading the
REAL rendered AX tree, not a source grep):

  1. `<h1 class="topbar-title">Council</h1>` — the topbar/page title (level 1), AND
  2. `<h1>{{ threadTaskTextDisplay }}</h1>` — the hero task question, which ALSO
     rendered as a literal <h1> whenever the task is <= 240 chars (the dominant case).

So a screen-reader user walking the headings landed on TWO "level 1" headings and
could not tell which is the page title — a broken outline with no single top. The
fix demotes the task header's ANNOUNCED level to 2 with `aria-level="2"` (keeping the
<h1> tag so the clamp(38px..56px) hero-question SIZE is untouched) — the exact
pattern the memory viewer uses so a content "# Lens" doesn't become a second h1
competing with its topbar "Your lens".

This guard DRIVES the real rendered live page (short-task state, where the second h1
renders) and reads the heading tree exactly as AT sees it (announced level = aria-level
when present, else tag level), asserting exactly ONE announced-level-1 heading and no
skipped levels. A source grep can't see the COMPUTED announced level or the v-if
visibility — only execution against the rendered DOM reveals the outline a screen
reader walks. Synthetic data only; no PII; dispatch stubbed (no council fired).
"""
from __future__ import annotations

import functools
import http.server
import tempfile
import threading
import urllib.parse
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


# Reads every visible heading and returns, for each, the ANNOUNCED level a screen
# reader uses: aria-level if present, else the tag level.
_OUTLINE_JS = """
() => {
  const out = [];
  document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]').forEach(el => {
    const cs = getComputedStyle(el);
    const fixed = cs.position === 'fixed' || cs.position === 'sticky';
    const visible = !(cs.display === 'none' || cs.visibility === 'hidden' ||
                      (el.offsetParent === null && !fixed));
    if (!visible) return;
    const ar = el.getAttribute('aria-level');
    let announced;
    if (el.getAttribute('role') === 'heading') {
      announced = parseInt(ar || '0', 10) || 0;
    } else {
      announced = ar ? (parseInt(ar, 10) || parseInt(el.tagName[1], 10))
                     : parseInt(el.tagName[1], 10);
    }
    out.push({tag: el.tagName.toLowerCase(), announced: announced,
              text: (el.textContent || '').trim().slice(0, 60),
              cls: el.className || ''});
  });
  return {
    headings: out,
    mainCount: document.querySelectorAll('main, [role=main]').length,
  };
}
"""

_TASK = "Should we pick Rust or Go for the parser"


def _serve(directory: Path) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_live_council_page_has_one_h1_and_clean_outline():
    """The live council page must present a single, navigable heading outline:

      - exactly ONE announced-level-1 heading (the topbar "Council" page title), AND
      - the hero task question announced BELOW level 1, AND
      - no skipped heading levels, AND
      - a <main> landmark.

    Founder symptom: "the live council page had TWO competing <h1>s — the topbar
    'Council' AND the hero task question — so a screen-reader user navigating by
    heading lands on two 'top-level' headings and can't tell which is the page title
    (WCAG 1.3.1 / 2.4.6)."
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.council_review import render_live_council_page
    from trinity_local.vendor import publish_vendor_files

    root = Path(tempfile.mkdtemp(prefix="trinity-live-outline-"))
    (root / "review_pages").mkdir()
    (root / "portal_pages").mkdir()
    publish_vendor_files(root / "portal_pages")
    page_path = root / "review_pages" / "live_council.html"
    page_path.write_text(render_live_council_page(), encoding="utf-8")

    httpd, port = _serve(root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # chromium not installed in this env
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                # Stub the dispatcher so no real council can fire from this page.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: (o)=>{ "
                    "if (o.onResult) o.onResult({ok:true}); } };"
                )
                # `?task=` (<= 240 chars) populates threadTaskTextDisplay so the hero
                # task <h1> renders — the discriminating state where the second h1
                # would compete for the page's level-1 slot.
                url = (
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?task={urllib.parse.quote(_TASK)}"
                )
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(1200)  # let petite-vue mount

                data = page.evaluate(_OUTLINE_JS)
                headings = data["headings"]
                outline = [(h["announced"], h["text"]) for h in headings]

                # PRECONDITION (discriminating): the hero task question rendered, so a
                # competing-h1 would actually be PRESENT if the demotion regressed.
                # Without this the no-competing-h1 assertion could pass vacuously on a
                # page that never mounted / never showed the task.
                task_heads = [h for h in headings if _TASK.startswith(h["text"][:20])
                              and h["text"]]
                assert task_heads, (
                    f"the hero task question '{_TASK}' did not render as a heading "
                    f"(petite-vue did not mount, or the task <h1> is absent) — the "
                    f"discriminating fixture is missing, so the outline assertions "
                    f"would be vacuous. Headings seen: {outline}"
                )

                # ASSERTION 1: exactly ONE announced-level-1 heading.
                level1 = [h for h in headings if h["announced"] == 1]
                assert len(level1) == 1, (
                    f"the live council page presents {len(level1)} announced-level-1 "
                    f"headings, expected exactly 1 (the topbar 'Council' page title). "
                    f"Got h1s: {[h['text'] + ' [' + h['cls'] + ']' for h in level1]}. "
                    "Founder symptom: the topbar 'Council' was an <h1> AND the hero "
                    "task question rendered as a SECOND literal <h1> — two competing "
                    "top-level headings a screen reader can't disambiguate "
                    "(WCAG 1.3.1 / 2.4.6)."
                )
                assert level1[0]["text"] == "Council", (
                    f"the single <h1> is '{level1[0]['text']}', expected the topbar "
                    "'Council' page title to be the page's primary heading"
                )

                # ASSERTION 2: the hero task question is announced BELOW level 1.
                assert task_heads[0]["announced"] > 1, (
                    f"the hero task question announces at level "
                    f"{task_heads[0]['announced']} — the task header must NOT announce "
                    "as a page-level <h1> competing with the topbar 'Council' title "
                    "(aria-level demotion regressed; WCAG 1.3.1)."
                )

                # ASSERTION 3: no skipped heading levels in the announced outline.
                levels = [h["announced"] for h in headings if h["announced"]]
                skips = [
                    (levels[i - 1], levels[i])
                    for i in range(1, len(levels))
                    if levels[i] > levels[i - 1] + 1
                ]
                assert not skips, (
                    f"the live council outline skips heading levels {skips} (e.g. an "
                    f"h1 followed by an h3 with no h2) — a screen-reader user hits a "
                    f"broken outline. Full announced sequence: {levels} (WCAG 1.3.1)."
                )

                # ASSERTION 4: a <main> landmark so AT can jump to content.
                assert data["mainCount"] == 1, (
                    f"expected exactly one <main> landmark, got {data['mainCount']} — "
                    "a page with no main landmark is unnavigable by 'jump to main "
                    "content' (WCAG 1.3.1)."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
