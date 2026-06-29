"""Regression: the launchpad TASTE card ("YOUR TASTE, DISTILLED") must not blow the
launchpad out horizontally when a long UNBREAKABLE token — a dev identifier, file
path, URL, or config key the user keeps citing — appears in the lens prose it renders.

Found 2026-06-22 driving the populated launchpad across the breakpoint ladder
(UX sweep iter 288, completing the iter-287 long-unbreakable-token class). The taste
card renders three kinds of free LLM/corpus prose:

  * `.taste-vocab-chip` — a VERBATIM phrase the user keeps using (v.phrase from
    vocabulary.md), frequently a single unbreakable monospace token; and
  * `.taste-list-quotes li` — a VERBATIM abstract-lens statement lifted straight
    from lens.md; and
  * the paired-lens title/why/failure lines (.taste-list li descendants).

Those boxes shipped with the browser default `overflow-wrap: normal`, so a single
long token never wrapped: a vocabulary phrase of `render_unified_council_page…` and
an abstract-lens statement citing it each demanded their full intrinsic width and
pinned the whole launchpad's documentElement scrollWidth to ~610px regardless of the
viewport — a horizontal scrollbar across the entire page on a 320px side panel. This
is the SAME unbreakable-token class the design system already fixed for `h1,h2,h3`,
`code,pre`, and the post-hoc review `.alert-box` (iter 287); the taste card's verbatim
corpus prose was the launchpad-side gap. A user who keeps citing a function/path/key
by name has exactly such a token in their lens, so this is the common case.

Fix: `overflow-wrap: break-word; word-break: break-word;` on `.taste-list li`
(covers the title/why/failure/quote descendants via inheritance) and
`min-width:0; max-width:100%; overflow-wrap:anywhere; word-break:break-word;` on
`.taste-vocab-chip` (a flex child whose monospace token otherwise pins the row).

This guard DRIVES THE REAL LAUNCHPAD in Chromium over http at the narrowest
production breakpoints (the side-panel widths) and reads geometry (documentElement
scroll vs client width; each prose box's content scrollWidth vs its clientWidth) —
NOT a CSS string-presence check on the source.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import tempfile
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# A run-together identifier with no break opportunity, planted in BOTH a verbatim
# vocabulary phrase and an abstract-lens statement. Long enough that at <=393px it
# cannot fit on one line — so without a break rule it forces a horizontal scrollbar.
_LONG_TOKEN = "render_unified_council_page_with_a_really_long_unbreakable_identifier_token"

# The narrow production widths (side-panel / phone) where the token bites hardest.
_NARROW_WIDTHS = [393, 375, 320]


def _seed_home_with_long_lens(home: Path) -> None:
    """Seed the synthetic home, then plant the long token in the lens prose."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_synthetic_home",
        str(Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"),
    )
    assert spec and spec.loader
    seedmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seedmod)
    seedmod.seed(home)

    # parse_taste_lenses() reads these two lens.md sections — vocabulary (the
    # .taste-vocab-chip row) and abstract lenses (the .taste-list-quotes block).
    lens_md = (
        "# Lens\n\n"
        "## Tensions\n\n"
        "- **concrete vs abstract**: leans concrete\n"
        "- **action vs description**: leads with the action\n\n"
        "## Vocabulary the user uses\n\n"
        f'- "{_LONG_TOKEN}" — the function the user keeps citing by full name\n'
        '- "ship" — to merge what works\n\n'
        "## Abstract lenses\n\n"
        f"- prefer the concrete worked example over the abstract framing in {_LONG_TOKEN} [tactical]\n"
        "- keep what works [strategic]\n"
    )
    (home / "memories" / "lens.md").write_text(lens_md, encoding="utf-8")


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch()
    except Exception as exc:  # chromium not installed
        sp.stop()
        pytest.skip(f"no launchable chromium for the taste-card-overflow test: {exc}")
    return sp, browser


def test_launchpad_taste_card_long_token_does_not_overflow(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trinity-taste-ovf-"))
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    _seed_home_with_long_lens(home)

    # Render the REAL launchpad via the live builder (no page_data — the live path).
    from trinity_local.launchpad_data import _load_taste_lenses
    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local import vendor

    # Discriminating-seed precondition (render-independent): the data layer must
    # actually surface the long token in BOTH prose boxes, else the page can't bite.
    tl = _load_taste_lenses() or {}
    vocab_phrases = [v.get("phrase", "") for v in tl.get("vocabulary", [])]
    abstract_stmts = [l.get("statement", "") for l in tl.get("abstract_lenses", [])]
    assert any(_LONG_TOKEN in p for p in vocab_phrases), (
        "seed precondition failed: the long token is not in the vocabulary phrases "
        f"({vocab_phrases!r}) — the .taste-vocab-chip box would not carry it"
    )
    assert any(_LONG_TOKEN in s for s in abstract_stmts), (
        "seed precondition failed: the long token is not in the abstract-lens "
        f"statements ({abstract_stmts!r}) — the .taste-list-quotes box would not carry it"
    )

    portal = home / "portal_pages"
    portal.mkdir(parents=True, exist_ok=True)
    (portal / "launchpad.html").write_text(
        render_launchpad_html(view="full"), encoding="utf-8"
    )
    vendor.publish_vendor_files(portal / "vendor")

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(home)
    )
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/portal_pages/launchpad.html"

    sp, browser = _browser()
    problems: list[str] = []
    try:
        for width in _NARROW_WIDTHS:
            page = browser.new_context(
                viewport={"width": width, "height": 900}
            ).new_page()
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(500)
            # Scroll the taste card into the layout so the boxes are measured.
            page.evaluate(
                "() => { const el = document.querySelector('.taste-vocab-chip, "
                ".taste-list-quotes li'); if (el) el.scrollIntoView(); }"
            )
            page.wait_for_timeout(200)
            geo = page.evaluate(
                """(LONG) => {
                    const de = document.documentElement;
                    const pick = (sel) => {
                        for (const el of document.querySelectorAll(sel)) {
                            const t = el.innerText || '';
                            if (t.includes(LONG)) {
                                return {sw: el.scrollWidth, cw: el.clientWidth,
                                        txt: t.trim()};
                            }
                        }
                        return null;
                    };
                    return {
                        docScrollW: de.scrollWidth,
                        docClientW: de.clientWidth,
                        rawLeak: (document.body.innerText || '').includes('{{'),
                        chip: pick('.taste-vocab-chip'),
                        quote: pick('.taste-list-quotes li'),
                    };
                }""",
                _LONG_TOKEN,
            )
            page.close()

            # PRECONDITION A (so a no-render / un-mounted page can't vacuously
            # pass): petite-vue mounted (no raw {{ leak) and BOTH prose boxes
            # painted carrying the long token.
            assert not geo["rawLeak"], (
                f"@{width}px: raw template '{{{{' leaked — the launchpad did not "
                "mount, so the taste-card overflow guard cannot run"
            )
            assert geo["chip"] is not None, (
                f"@{width}px: no .taste-vocab-chip carries the long token — "
                "precondition failed (the verbatim-phrase box did not render)"
            )
            assert geo["quote"] is not None, (
                f"@{width}px: no .taste-list-quotes li carries the long token — "
                "precondition failed (the abstract-lens box did not render)"
            )

            # BITE 1: the whole launchpad must fit the viewport (no horizontal
            # scrollbar). The unbroken token pinned docScrollW to ~610px.
            if geo["docScrollW"] > geo["docClientW"] + 1:
                problems.append(
                    f"@{width}px: documentElement scrollWidth {geo['docScrollW']} > "
                    f"clientWidth {geo['docClientW']} — a long unbreakable lens token "
                    "blew the launchpad out horizontally (missing overflow-wrap on "
                    ".taste-vocab-chip / .taste-list li)"
                )
            # BITE 2: each prose box's content must wrap inside it.
            for name in ("chip", "quote"):
                b = geo[name]
                if b["sw"] > b["cw"] + 1:
                    problems.append(
                        f"@{width}px: taste {name} content scrollWidth {b['sw']} > "
                        f"clientWidth {b['cw']} — the long token did not wrap inside "
                        "its box (missing word-break/overflow-wrap)"
                    )
    finally:
        browser.close()
        sp.stop()
        httpd.shutdown()

    assert not problems, (
        "launchpad taste card overflowed on a long unbreakable lens token:\n"
        + "\n".join(problems)
    )
