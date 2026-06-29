"""Real-browser guard: the memory viewer renders lens.md / core.md as STRUCTURED
markdown — headings, nested lists, inline code, fenced code, blockquotes, tables —
not as raw paragraph text, and never leaks literal markdown syntax.

WHY THIS, AND WHY THE EXISTING TESTS DON'T COVER IT
---------------------------------------------------
lens.md (paired tensions) and core.md (the singular distillation) are the user's
primary retention surface — the chairman writes their synthesis verbatim into
lens.md, so the file carries rich structure (`# /me`, `## Implicit rejections`,
`### 1. concrete ↔ abstract`, bold poles, inline `code`, fenced examples,
blockquotes, an abstract-lenses table). The viewer renders it via `renderMarkdown`
(marked → DOMParser → sanitize). Two regressions would silently strand the user on
RAW markdown while every existing test stays green:

  • `window.marked` fails to load (CDN cut, vendor rename, MV3 CSP) → renderMarkdown
    falls through to `el("pre", "body", mdText)` — the WHOLE file dumped as raw text
    (`## Implicit rejections`, `**concrete**`, ```` ``` ```` all literal).
  • the sanitizer's `querySelectorAll("*")` attribute loop over-strips, or a marked
    config change drops block elements → headings/lists collapse to plain text.

The existing browser coverage MISSES both: test_memory_viewer_all_file_views only
asserts the markdown body is `>= 60` chars (a raw `<pre>` dump is 700+ chars → PASS);
test_memory_viewer_xss asserts no SCRIPT executes + the body isn't the raw fallback
for ITS fixture, but pins no per-element structure (a heading rendered as a `<p>`
would still pass its `h1||h2||h3 || strong || li` OR-check). Neither asserts the
DISTINCTIVE markdown elements are present AND that no literal `## ` / `**` / fence
survives in the rendered text — the exact "green while the value is gone" shape.

This drives the REAL file:// render (production portal-html path, vendored marked)
and asserts structural fidelity + zero raw-markdown leak, at 1280 and a 375 phone
width (where the fenced code + table must scroll INSIDE their container, not spill
the page).

Mutation-proven (see the module-level note in the test): forcing the raw `<pre>`
fallback (delete window.marked before render) keeps the all-file-views `>=60` check
green but reds THIS guard with "rendered raw markdown, not structured HTML".

Slow + browser marked; skips without Playwright/chromium; runs in the CI browser job.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# A realistic, richly-structured lens.md mirroring the chairman's emitted shape
# (me_builder: "# /me" / "## Implicit rejections" / "### N. pole ↔ pole"), plus the
# full block-element battery the viewer's CSS styles (h1/h2/h3, nested ul, inline +
# fenced code, blockquote, table). Unicode arrows (↔ →) and apostrophes are part of
# the real lens — they must survive the round trip.
_LENS_MD = """# /me

## Recurring topics

You keep returning to a handful of subjects. The chairman reads these first.

- **launchpad UX** — paint + usability sweeps
- **lens pipeline** — embedding, basins, generators
  - sub: TF-IDF abstain-gates
  - sub: corpus-purity filters

## Implicit rejections (the moat)

### 1. concrete ↔ abstract

You lean **concrete**: you want a worked example before the principle.

> A fallback that emits *wrong* output is worse than none.

Run `trinity-local lens` to rebuild this.

### 2. action ↔ description

You lead with the `action`, then the rationale.

```python
def ship(answer):
    return answer  # decision first, rationale after
```

## Abstract lenses

| pole | leans | failure |
|------|-------|---------|
| concrete | concrete | vague |
| action | action | reckless |

Projects task-tensions: 2
"""

_CORE_MD = """# Core

You prefer **concrete, action-first** answers and lead with the decision before
the rationale. You distrust *abstraction without a worked example*.

- ship the fix, then explain
- guard the invariant, not the proxy

> Keep what works.
"""


def _seed_and_render(home: Path) -> Path:
    """Seed lens.md + core.md and render the viewer via the production
    `portal-html` CLI (publishes vendor/marked.min.js — without it renderMarkdown
    would itself fall to the raw `<pre>`, so the guard must verify marked is
    present, else it would pass for the WRONG reason)."""
    mem = home / "memories"
    mem.mkdir(parents=True)
    (mem / "lens.md").write_text(_LENS_MD, encoding="utf-8")
    (home / "core.md").write_text(_CORE_MD, encoding="utf-8")
    # Minimal companions so the nav is fully populated and portal-html doesn't bail.
    (mem / "topics.json").write_text('{"basins": []}', encoding="utf-8")
    (mem / "vocabulary.md").write_text("# Vocabulary\n\n## Anchors\n- ship\n", encoding="utf-8")

    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    mv = home / "portal_pages" / "memory.html"
    assert mv.exists(), "portal-html didn't write memory.html"
    assert (home / "portal_pages" / "vendor" / "marked.min.js").exists(), (
        "vendored marked.min.js missing — renderMarkdown would fall to the raw "
        "<pre> fallback, so this guard would pass for the wrong reason"
    )
    return mv


_PROBE = """() => {
  const md = document.querySelector('.markdown-body');
  if (!md) return {nomd: true};
  const n = (s) => md.querySelectorAll(s).length;
  const txt = md.innerText;
  // Literal markdown syntax surviving into the RENDERED text = the raw <pre>
  // fallback (marked failed) or a renderer regression.
  const leak = [];
  if (/(^|\\n)#{1,3} /.test(txt)) leak.push('heading-hash');
  if (/\\*\\*/.test(txt)) leak.push('bold-stars');
  if (/```/.test(txt)) leak.push('code-fence');
  if (/(^|\\n)\\| /.test(txt)) leak.push('table-pipe');
  // Page-level h-overflow (the <pre>/table scroll INSIDE their own container).
  const docOverflow = document.documentElement.scrollWidth
                    - document.documentElement.clientWidth;
  const pre = md.querySelector('pre');
  const preRight = pre ? pre.getBoundingClientRect().right : 0;
  return {
    h1: n('h1'), h2: n('h2'), h3: n('h3'),
    ul: n('ul'), nestedUl: n('ul ul'), li: n('li'),
    strong: n('strong'), em: n('em'),
    code: n('code'), pre: n('pre'), blockquote: n('blockquote'),
    table: n('table'), tr: n('tr'),
    leak, docOverflow, preRight, vw: window.innerWidth,
  };
}"""


@pytest.mark.parametrize("fname,width", [("lens.md", 1280), ("lens.md", 375)])
def test_lens_md_renders_structured_markdown_no_raw_leak(tmp_path, monkeypatch, fname, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    mv = _seed_and_render(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 1400})
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(f"file://{mv}?file={fname}", wait_until="load")
            page.wait_for_timeout(1200)
            d = page.evaluate(_PROBE)
        finally:
            browser.close()

    assert not d.get("nomd"), "no .markdown-body — the lens.md view never rendered"
    # STRUCTURE: the chairman's lens.md uses every block element. If marked failed
    # (raw <pre> fallback) or a renderer regressed, these collapse — the all-file-
    # views >=60-char check would STILL pass, so this is the load-bearing assertion.
    assert d["h1"] >= 1 and d["h2"] >= 3 and d["h3"] >= 2, (
        f"lens.md headings did not render as <h1>/<h2>/<h3> "
        f"(h1={d['h1']} h2={d['h2']} h3={d['h3']}) — renderMarkdown fell to the raw "
        f"<pre> fallback or marked broke; the user sees raw '## ' markdown"
    )
    assert d["nestedUl"] >= 1 and d["li"] >= 4, (
        f"lens.md nested bullets did not render (nestedUl={d['nestedUl']} li={d['li']})"
    )
    assert d["strong"] >= 3, f"**bold** poles did not render as <strong> (strong={d['strong']})"
    assert d["code"] >= 2 and d["pre"] >= 1, (
        f"inline `code` / fenced code did not render (code={d['code']} pre={d['pre']})"
    )
    assert d["blockquote"] >= 1, f"the > blockquote did not render (blockquote={d['blockquote']})"
    assert d["table"] >= 1 and d["tr"] >= 3, (
        f"the abstract-lenses table did not render (table={d['table']} tr={d['tr']})"
    )
    # NO RAW-MARKDOWN LEAK: not one literal '## ' / '**' / fence / '| ' survives.
    assert d["leak"] == [], (
        f"raw markdown leaked into the rendered lens.md: {d['leak']} — the viewer "
        f"painted literal syntax instead of structured HTML"
    )
    # PAINT @375: the page must not h-overflow (the fenced code + table scroll
    # INSIDE their own overflow-x:auto container, not spill the phone width).
    assert d["docOverflow"] <= 1, (
        f"lens.md @{width} overflowed the page by {d['docOverflow']}px — a code "
        f"block or table spilled past the viewport instead of scrolling internally"
    )
    assert not errs, f"console/page errors on the lens.md view: {errs[:2]}"


# Reads the ANNOUNCED heading level (aria-level if present, else the tag level)
# of every visible heading — what a screen reader navigates by.
_OUTLINE_PROBE = """() => {
  const out = [];
  document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]').forEach(el => {
    const cs = getComputedStyle(el);
    const fixed = cs.position === 'fixed' || cs.position === 'sticky';
    const visible = !(cs.display === 'none' || cs.visibility === 'hidden' ||
                      (el.offsetParent === null && !fixed));
    if (!visible) return;
    const ar = el.getAttribute('aria-level');
    let announced;
    if (el.getAttribute('role') === 'heading') announced = parseInt(ar || '0', 10) || 0;
    else announced = ar ? (parseInt(ar, 10) || parseInt(el.tagName[1], 10))
                        : parseInt(el.tagName[1], 10);
    out.push({tag: el.tagName.toLowerCase(), announced,
              text: (el.textContent || '').trim().slice(0, 40),
              cls: el.className || ''});
  });
  return {headings: out, mainCount: document.querySelectorAll('main,[role=main]').length};
}"""


def test_memory_viewer_content_headings_nest_under_the_page_h1(tmp_path, monkeypatch):
    """A lens file's content markdown headings must announce BELOW the page <h1>.

    The viewer's topbar carries the page heading `<h1 class="topbar-title">Your
    lens</h1>` and the file name is an `<h2>`. The lens file BODY is markdown
    rendered client-side by `marked`, and the chairman's lens.md opens with a
    top-level "# /me" — which parses to a literal `<h1>`. So the rendered page had
    TWO `<h1>`s (the topbar page title AND the content "/me"), and the content
    `<h1>` broke the outline a screen-reader user walks under the `<h2>` file-name
    section.

    The fix (Iter 238) demotes every content heading's aria-level by 2 in
    renderMarkdown — the visible `<hN>` tag (and font-size) is unchanged, only the
    announced level moves — so content headings nest at level 3+ and there is
    exactly ONE announced-level-1 heading on the page (the topbar page title).

    Founder symptom: "lens.md's '# /me' rendered a SECOND <h1> competing with the
    topbar page <h1> 'Your lens' and broke the heading outline under the <h2> file
    section (WCAG 1.3.1 / 2.4.6)."
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    mv = _seed_and_render(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1400})
            page.goto(f"file://{mv}?file=lens.md", wait_until="load")
            page.wait_for_timeout(1300)  # let marked render + the aria-level pass run
            marked_loaded = page.evaluate("() => !!window.marked")
            d = page.evaluate(_OUTLINE_PROBE)
        finally:
            browser.close()

    headings = d["headings"]
    outline = [(h["announced"], h["text"]) for h in headings]

    # PRECONDITION (render): marked rendered the markdown (not the raw <pre>
    # fallback) AND the content heading "Lens" IS present — without both, the
    # demotion assertion would be vacuous (a <pre> fallback emits no <h1>).
    assert marked_loaded, "window.marked never loaded — renderMarkdown fell to <pre>"
    content_lens = [h for h in headings if h["text"] == "/me"]
    assert content_lens, (
        f"the content markdown heading '/me' (from '# /me' in lens.md) did not "
        f"render as a heading element — outline was {outline}; the demotion "
        "assertion would be vacuous"
    )
    # The page heading "Your lens" must be present (the discriminating page <h1>).
    page_h1 = [h for h in headings if h["text"] == "Your lens"]
    assert page_h1 and page_h1[0]["announced"] == 1, (
        f"the topbar page heading 'Your lens' is not an announced-level-1 heading "
        f"(outline {outline}) — the page lost its h1"
    )

    # ASSERTION 1: exactly ONE announced-level-1 heading (the page title).
    level1 = [h for h in headings if h["announced"] == 1]
    assert len(level1) == 1, (
        f"the memory viewer presents {len(level1)} announced-level-1 headings, "
        f"expected exactly 1 (the topbar page title 'Your lens'). Got: "
        f"{[h['text'] + ' [' + h['cls'] + ']' for h in level1]}. Founder symptom: "
        "lens.md's '# /me' rendered a SECOND content <h1> competing with the page "
        "<h1> and broke the outline (WCAG 1.3.1 / 2.4.6)."
    )

    # ASSERTION 2: the content "/me" heading is announced BELOW level 1.
    assert content_lens[0]["announced"] > 1, (
        f"the content markdown heading '/me' announces at level "
        f"{content_lens[0]['announced']} — a content '# Heading' must NOT announce "
        "as a page-level <h1> (the renderMarkdown aria-level demotion regressed)."
    )

    # ASSERTION 3: a <main> landmark exists.
    assert d["mainCount"] == 1, (
        f"expected exactly one <main> landmark, got {d['mainCount']}"
    )
