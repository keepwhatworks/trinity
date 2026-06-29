"""A chairman-emitted memory TABLE with a long unbreakable token must not blow the
memory viewer out horizontally at the WIDE (desktop) breakpoints.

WHY THIS GUARD EXISTS. The memory viewer's `.md` tabs (lens.md tensions, core.md,
generators.md) render whatever markdown the chairman synthesis emits — verbatim,
free LLM text. A chairman commonly emits a markdown TABLE (a tidy grid of paired
tensions / invariants), and a table CELL routinely carries a long unbreakable token
from an evidence citation: a file path, a URL, a regex, a hash — exactly the kind of
identifier the user asked the council about.

A `<table>` is NOT bounded by `.markdown-body { overflow-wrap: break-word }` the way
prose is: with `display: table; width: 100%` a long-token cell pushes the whole table
past its content column and blows the DOCUMENT out horizontally — the page scrolls
sideways on a normal desktop. The viewer's only table-scroll-container rule used to
live inside `@media (max-width: 1179px)` (`display:block; overflow-x:auto`), so it
protected phones/tablets but NOT the >=1180px desktop range where most users open the
viewer. The page-out shipped GREEN because:
  • the GLOBAL long-token guard (test_long_token_overflow_global_browser.py) drives
    the memory viewer ONLY at 320/375 — exactly the widths where the narrow inner-
    scroll rule already applied — and it seeds the .md files with long tokens in
    PROSE, never in a markdown table; and
  • every other memory-viewer browser test opens at 1280px but with non-table content.
So the (wide-width × markdown-table × long-token) cell was doubly uncovered.

Founder symptom this pins: "my lens / core memory page scrolls sideways on the
desktop" — a markdown table in a memory file overflowing the document at 1280/1440.

The fix (memory_viewer.py): hoist `display:block; overflow-x:auto; max-width:100%`
into the BASE `.markdown-body table` rule so the table is its own bounded horizontal
scroll container at EVERY width — only the table scrolls, never the page.

Mutation-proven to BITE: strip the base-rule `display:block; overflow-x:auto` (revert
to the media-query-only scroll container) and this guard reds at 1280 + 1440 with the
table-driven document overflow; the narrow widths stay green (the media rule still
covers them). An `overflow-x:auto` scroll container is the INTENDED bounded case — the
assertion is on the DOCUMENT (scrollWidth <= clientWidth), not the table's inner box.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# The WIDE breakpoints where the `@media (max-width: 1179px)` inner-scroll rule does
# NOT apply — the exact range the existing memory-viewer overflow coverage skipped.
_WIDE = [1280, 1440]

# A genuinely unbreakable token: ~150 chars, zero separators — a cell can only fit it
# if the table is bounded as its own scroll container. This is the file-path/URL/hash
# shape a chairman emits in a tension's evidence citation.
_LONG = "a" * 150

# A real chairman-emitted lens.md: a markdown TABLE of paired tensions, one cell
# carrying the long token. Plus prose around it so the rest of the page is normal.
_LENS_MD = (
    "# Lens\n\n"
    "Your paired tensions, distilled from how you rephrase and decide.\n\n"
    "| Tension | Leans toward | Evidence |\n"
    "|---|---|---|\n"
    f"| abstraction vs concreteness | concrete first | reaches for {_LONG} |\n"
    "| speed vs correctness | correctness | abstain-gates everywhere |\n\n"
    "## Trajectories\n\n- You moved concrete to action over 2025.\n"
)


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "lens.md").write_text(_LENS_MD, encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


def test_md_table_long_token_no_document_overflow_at_wide_widths():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    target = f"file://{pages / 'memory.html'}?file=lens.md"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for width in _WIDE:
                page = browser.new_page(viewport={"width": width, "height": 900})
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.goto(target, wait_until="load")
                page.wait_for_timeout(700)

                # Precondition (non-vacuous): the markdown table actually rendered and
                # actually carries the long token — otherwise the overflow check passes
                # trivially on an empty / non-table page.
                table_state = page.evaluate(
                    """() => {
                        const t = document.querySelector('.markdown-body table');
                        if (!t) return {present: false};
                        const cs = getComputedStyle(t);
                        return {
                            present: true,
                            hasLongToken: t.innerText.includes('aaaaaaaaaa'),
                            innerScroll: t.scrollWidth > t.clientWidth,
                            display: cs.display,
                            overflowX: cs.overflowX,
                        };
                    }"""
                )
                assert table_state.get("present"), (
                    f"the lens.md markdown table did not render at {width}px — the "
                    "wide-width overflow check would be vacuous (seed/render broke)."
                )
                assert table_state.get("hasLongToken"), (
                    f"the long unbreakable token never reached the table cell at {width}px "
                    "— the overflow check would be vacuous."
                )

                # The fix makes the table its own bounded horizontal scroll container.
                # That inner scroll is INTENDED — assert on the DOCUMENT, not the table.
                metrics = page.evaluate(
                    """() => {
                        const de = document.documentElement;
                        return {
                            scrollOver: de.scrollWidth - de.clientWidth,
                            scrollW: de.scrollWidth,
                            clientW: de.clientWidth,
                        };
                    }"""
                )
                assert metrics["scrollOver"] <= 0, (
                    "the memory viewer's lens.md markdown TABLE blew the DOCUMENT out "
                    f"horizontally at {width}px (a chairman table with a long unbreakable "
                    "token in an evidence cell): documentElement.scrollWidth over by "
                    f"{metrics['scrollOver']}px ({metrics['scrollW']} > {metrics['clientW']}). "
                    "The base `.markdown-body table` must be its own bounded "
                    "`display:block; overflow-x:auto` scroll container at WIDE widths too — "
                    "not only inside the `@media (max-width: 1179px)` rule (the founder "
                    "symptom: 'my lens page scrolls sideways on the desktop')."
                )
                # The table IS the intended scroll region — confirm the fix is the
                # bounded-inner-scroll shape, not a content-truncation accident.
                assert table_state.get("display") == "block", (
                    f"the markdown table at {width}px is not a block scroll container "
                    f"(display={table_state.get('display')}) — the inner-scroll fix regressed."
                )
                assert table_state.get("overflowX") in ("auto", "scroll"), (
                    f"the markdown table at {width}px has overflow-x="
                    f"{table_state.get('overflowX')}; the long token must scroll WITHIN "
                    "the table, not spill the page."
                )
                assert not errs, f"page errors on the {width}px lens.md table view: {errs[:5]}"
                page.close()
        finally:
            browser.close()
