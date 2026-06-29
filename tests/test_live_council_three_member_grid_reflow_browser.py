"""Browser guard: the 3-member ``answers-grid-three`` on the LIVE council page
(``render_live_council_page`` → ``review_pages/live_council.html``, the page every
council link redirects to via ``write_unified_council_page``) must REFLOW correctly
across the full responsive band — 3 columns on a wide desktop, 2 columns through the
769–1200px tablet band, 1 column at/below 768px — and never push a horizontal scroll.

WHY THIS EXISTS (the coverage gap it closes): the ``.answers-grid-three`` reflow media
queries are DUPLICATED. The DEAD ``render_unified_council_page`` (#311, excluded from
reachability) carries its own copy at council_review.py:416-615, and the LIVE
``render_live_council_page`` carries a SEPARATE copy at council_review.py:1058-1079.
The pre-existing reflow guards — ``test_council_review_midwidth_grid_reflow_browser.py``
and ``test_council_review_layout_browser.py`` — drive ONLY the dead page. So a
regression to the LIVE copy ships silently today, and becomes fully unguarded the
moment #311's dead code (and its guards) are removed. UX-sweep Iter 419 drove the live
page's 3-member grid across 1280/1024/900/769/768/560/393/320 with realistic 3-member
markdown content + an unbreakable long token and found it CLEAN; this guard locks the
LIVE copy in, mutation-proven to discriminate the live media queries from the dead ones.

The LIVE copy's media queries (council_review.py:1058-1079, READ — not assumed equal to
the dead page's):
  ``.answers-grid-three { grid-template-columns: repeat(3, minmax(0, 1fr)); }``
  ``@media (max-width: 1200px) { .answers-grid-three { repeat(2, minmax(0,1fr)) } }``
  ``@media (max-width: 1023px) { .answers-grid-three { repeat(2, minmax(0,1fr)) } }``
  ``@media (max-width: 768px)  { .answers-grid, .answers-grid-three { 1fr } }``
So column counts: 1280→3, 1024→2 (≤1200, >1023), 900→2, 769→2, 768→1, 560→1, 393→1,
320→1. (1024 is 2-col via the max-width:1200 rule, NOT the max-width:1023 one.)

This serves an isolated TRINITY_HOME over http (the live page reads ``?council_id=``,
which file:// can't carry), seeds a real 3-member completed council via the production
writer (no PII), and reads the rendered grid's COMPUTED ``grid-template-columns`` +
documentElement overflow at each width. Slow-marked; skips when chromium is absent.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_grid3reflow"

# (viewport width, expected column count) across the LIVE copy's reflow boundaries.
#   1280 → 3 columns (wide desktop)
#   1024 → 2 columns (≤1200 rule; the dead-page guard's 1024 case, on the LIVE page)
#    900 → 2 columns (lower tablet band)
#    769 → 2 columns (just above the 768→1col boundary — the discriminating 2-col edge)
#    768 → 1 column  (the 3-member mobile-stack boundary)
#    560 → 1 column  (small tablet / large phone)
#    393 → 1 column  (modern phone)
#    320 → 1 column  (smallest supported phone)
_CASES: list[tuple[int, int]] = [
    (1280, 3),
    (1024, 2),
    (900, 2),
    (769, 2),
    (768, 1),
    (560, 1),
    (393, 1),
    (320, 1),
]


def _seed_three_member_council() -> None:
    """Write a synthetic completed 3-member council (no PII) via the production
    writers, then render the LIVE council page. Realistic markdown content + an
    unbreakable long token in one card so the grid carries real card weight at the
    reflow band (a dropped min-width:0 would stretch a track)."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    long_a = (
        "In-process caching is the right call. The embedder model load dominates "
        "cold-start latency, so amortizing it across calls within a session is a "
        "clear win.\n\n- pro: one load per process\n- con: ~62MB resident"
    )
    # An unbreakable long token + long URL — the structured-claims-overflow worst case.
    long_b = (
        "Per-call instantiation keeps memory flat but pays the model-load tax every "
        "call. " + "x" * 60 + " "
        "https://example.com/a/very/long/unbreakable/path/segment/embeddings-benchmark-2026"
    )
    long_c = (
        "Both miss the middle path: a per-call cache keyed on the model id with LRU "
        "eviction. Measure the real path before choosing."
    )

    outcome = CouncilOutcome(
        council_run_id=_CID,
        bundle_id=_CID,  # chain_root_id falls back to bundle_id → clean single council
        task_cluster_id="cluster_grid3reflow",
        primary_provider="claude",
        winner_provider="claude",
        metadata={"task_text": "Cache the embedder in-process or per-call?"},
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text=long_a),
            CouncilMemberResult(provider="codex", model="gpt", output_text=long_b),
            CouncilMemberResult(
                provider="antigravity", model="gemini", output_text=long_c
            ),
        ],
        synthesis_prompt="Review the answers.",
        synthesis_output="In-process caching wins for latency on a long-lived session.",
        routing_label=CouncilRoutingLabel(
            winner="claude", confidence="high", task_type="design"
        ),
        created_at="2026-06-02T00:00:00+00:00",
    )
    save_council_outcome(outcome)
    write_portal_html()  # publishes portal_pages/vendor/petite-vue.iife.js
    write_live_council_page()


_GRID_GEOM = r"""() => {
    const de = document.documentElement;
    const grid = document.querySelector('.answers-grid-three')
              || document.querySelector('.answers-grid');
    const cs = grid ? getComputedStyle(grid) : null;
    // Column count = number of tracks in the computed grid-template-columns
    // (e.g. "468px 468px" = 2). Works at 1-col too (single track), unlike a
    // distinct-left-edge count which can't tell 1 card from a 1-col stack.
    const cols = cs
        ? cs.gridTemplateColumns.split(' ').filter(Boolean).length
        : 0;
    const cards = grid
        ? [...grid.querySelectorAll('.provider-status-row')]
        : [];
    // Worst page-level offender: any element whose right edge passes the viewport
    // (excluding intentional overflow-x scrollers + <pre> code blocks, whose own
    // intrinsic width is fine — only documentElement scroll past the viewport is a bug).
    const vw = window.innerWidth;
    let rightWorst = {right: 0, sel: ''};
    for (const el of document.querySelectorAll('body *')) {
        const st = getComputedStyle(el);
        if (st.overflowX === 'auto' || st.overflowX === 'scroll') continue;
        if (el.tagName === 'PRE') continue;
        const r = el.getBoundingClientRect();
        if (r.right > vw + 1 && el.scrollWidth > el.clientWidth
            && r.right > rightWorst.right) {
            rightWorst = {right: Math.round(r.right),
                sel: el.tagName.toLowerCase() + '.'
                     + String(el.className || '').slice(0, 40)};
        }
    }
    return {
        gridClass: grid ? grid.className : '',
        cols,
        nCards: cards.length,
        scrollW: de.scrollWidth, clientW: de.clientWidth, vw,
        rightWorst,
        braceLeak: /\{\{|\}\}/.test(document.body.innerText),
    };
}"""


@pytest.mark.parametrize("width,expected_cols", _CASES)
def test_live_council_three_member_grid_reflows_and_does_not_overflow(
    tmp_path, width: int, expected_cols: int
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import os

    os.environ["TRINITY_HOME"] = str(tmp_path)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    _seed_three_member_council()

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = (
        f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}"
    )

    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": width, "height": 1100}
                ).new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url, wait_until="load", timeout=15000)
                # The 3-member grid only paints once the council JSONP hydrates +
                # petite-vue mounts the segment.
                page.wait_for_function(
                    "() => document.querySelectorAll("
                    "'.answers-grid-three .provider-status-row').length === 3",
                    timeout=8000,
                )
                geom = page.evaluate(_GRID_GEOM)
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errors, f"live council page threw at {width}px: {errors}"
    # NON-VACUOUS PRECONDITION: the live 3-member grid must actually carry the
    # answers-grid-three class with all 3 cards, else the column-count + overflow
    # assertions below would be a false pass.
    assert "answers-grid-three" in geom["gridClass"], (
        f"live council page didn't render answers-grid-three at {width}px "
        f"(class={geom['gridClass']!r}) — the reflow assertion would be vacuous"
    )
    assert geom["nCards"] == 3, (
        f"live council page didn't render the 3-member grid at {width}px "
        f"(cards={geom['nCards']}) — reflow/overflow assertions would be a false pass"
    )
    assert not geom["braceLeak"], (
        f"raw {{{{ }}}} leaked at {width}px on the live council page — petite-vue "
        "didn't mount (vendor not published next to the page?)"
    )
    # THE reflow guard (LIVE copy, council_review.py:1058-1079): the 3-member
    # answers-grid-three must drop to the expected column count at each boundary.
    # A broken LIVE @media (max-width:1200px→2col / max-width:768px→1col) rule leaves
    # a tablet/phone user stuck on a cramped 3-up while the dead-page guards stay green.
    assert geom["cols"] == expected_cols, (
        f"LIVE council 3-member answers-grid-three has {geom['cols']} columns at "
        f"{width}px, expected {expected_cols} — the LIVE reflow copy "
        f"(council_review.py:1058-1079) regressed (NOT the dead #311 page's copy); "
        f"a tablet/phone user is stuck on a cramped/wrong grid"
    )
    # THE overflow guard: no page-level horizontal scroll at any width. A dropped
    # minmax(0,1fr) track floor lets a wide code block / long token stretch a track.
    assert geom["scrollW"] <= geom["clientW"] + 1, (
        f"LIVE council page OVERFLOWS horizontally at {width}px: "
        f"documentElement.scrollWidth={geom['scrollW']} > clientWidth={geom['clientW']} "
        f"(worst right-edge offender: {geom['rightWorst']['sel']} @ "
        f"{geom['rightWorst']['right']}px) — a 3-member council answer with a wide "
        "code block / long token stretches the viewport; the grid track needs minmax(0,1fr)"
    )
