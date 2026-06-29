"""The memory viewer's RICHEST view — topics.json (the topology SVG + the
basin-detail panel + the Reader representative cards) — must not spill a single
element past the viewport on the NARROWEST phone width (320px).

Why this guard exists. The memory viewer ships its OWN private <style> block (it
does NOT use design_system.SHARED_CSS), so no launchpad geometry guard protects
it. The one existing mobile-overflow check (test_mobile_viewport_overflow.py) opens
the viewer at `?file=lens.md` ONLY, and only at 375px — the plain markdown view.
The topics.json view is a categorically heavier layout: a fixed-viewBox topology
SVG, a basin-detail panel that grows with terms/percentages, and the Reader's
representative-thread cards with long headlines + Replay/Launch chips. Its
no-overflow at narrow widths hangs entirely on the `@media (max-width: 1179px)`
rule `.content { min-width: 0; max-width: 100% }` — the grid content item's default
`min-width: auto` would otherwise pin it to its widest child (the SVG / a long
unbreakable top-term token) and shove the whole page sideways. That rule was
UNGUARDED for this view: a CSS regression on `.content` (or on the SVG/detail
caps) would silently reintroduce the phone-sideways-scroll the 1179px breakpoint
was added to kill, while every existing viewer test (all at 1280px) stayed green.

Founder symptom this pins: "the topology / basin reader scrolls sideways on a
phone" — content that escapes the 320px viewport.

Mutation-proven (2026-06-20): revert the `@media (max-width: 1179px)` rule's
`.content { min-width: 0 }` to `min-width: auto` and this guard reds with a 225px
overflow (the nav + content spill to right=545 past the 320px viewport); baseline
is scrollOver=0 with zero escaping elements. An `overflow-x:auto` scroll container
(the wrapped markdown table / wide <pre>) is intentional and explicitly excluded —
this asserts a real escape, not a designed scroll region.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# portal-html subprocess + chromium → real-browser/subprocess test. Slow-marked so
# default `pytest -q` stays fast (runs via TRINITY_SLOW=1 / `pytest -m browser`).
pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# 320px = the narrowest documented phone breakpoint, and the worst case for a grid
# whose content item could refuse to shrink below its widest child.
_NARROW = 320


def _topics_shape() -> dict:
    """Several basins (so the topology force layout has spread) with a long,
    UNBREAKABLE top-term token + a long headline — the content that would escape a
    non-shrinking `.content` grid item. One basin carries a multi-turn rep so the
    Reader detail renders the expandable card path too."""
    basins = []
    for i in range(6):
        basins.append(
            {
                "id": f"b{i:02d}",
                "label": f"basin-label-{i}",
                "size": 100 - i * 8,
                "thread_count": 8 - i,
                # A genuinely unbreakable token: the classic "widest child pins the
                # grid item" overflow source on a phone.
                "top_terms": [
                    f"verylongunbreakabletoken{i}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "vector",
                    "cosine",
                ],
                "centroid": [1.0 if j == i else 0.0 for j in range(6)],
                "representatives": [
                    {
                        "transcript_id": f"tx-{i}-0",
                        "headline": (
                            f"A representative headline for basin {i} that runs long "
                            "enough to test wrapping inside a 320px column"
                        ),
                        "turn_count": 2,
                        "turns": [
                            {"turn_index": 0, "snippet": "first turn snippet here"},
                            {"turn_index": 1, "snippet": "second turn snippet here"},
                        ],
                    }
                ],
            }
        )
    return {"basins": basins}


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps(_topics_shape()), encoding="utf-8"
    )
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


# Click the first basin node so the basin-DETAIL panel + the Reader reps render —
# the richest layout state (an empty graph alone wouldn't exercise the detail/cards).
_OPEN_DETAIL = """() => {
    const c = document.querySelector('.topics-graph-svg circle');
    if (c) c.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
}"""

# Find every element whose right edge escapes the viewport, EXCLUDING any element
# inside an intentional overflow-x:auto/scroll container (the wrapped table / wide
# <pre> the viewer designs as horizontal scroll regions). This catches the
# overflow-x:hidden-masked case the bare scrollWidth check is blind to.
_ESCAPING = """(W) => {
    const out = [];
    for (const el of document.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.right > W + 1 && r.width > 0 && r.height > 0) {
            let n = el, scroll = false;
            while (n && n !== document.body) {
                const s = getComputedStyle(n);
                if (s.overflowX === 'auto' || s.overflowX === 'scroll') { scroll = true; break; }
                n = n.parentElement;
            }
            if (!scroll) {
                out.push(el.tagName + '.' + String(el.className || '').split(' ')[0]
                         + ' w=' + Math.round(r.width) + ' right=' + Math.round(r.right));
            }
        }
    }
    // dedupe identical descriptors
    return [...new Set(out)].slice(0, 8);
}"""


def test_topics_view_has_no_horizontal_overflow_at_320():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    target = f"file://{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": _NARROW, "height": 740})
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on(
            "console",
            lambda m: errs.append(m.text)
            if m.type == "error" and "woff2" not in m.text and "404" not in m.text
            else None,
        )
        page.goto(target, wait_until="load")
        page.wait_for_timeout(1100)

        # Precondition (non-vacuous): the topology graph actually rendered nodes —
        # otherwise an empty page would pass the overflow check trivially.
        n_circles = page.evaluate(
            "() => document.querySelectorAll('.topics-graph-svg circle').length"
        )
        assert n_circles >= 2, (
            "the topics.json topology graph rendered no basin nodes at 320px — the "
            f"overflow check would be vacuous (circles={n_circles}). Seed/render broke."
        )

        # Open the basin detail so the heaviest layout state is on screen.
        page.evaluate(_OPEN_DETAIL)
        page.wait_for_timeout(450)
        detail_visible = page.evaluate(
            "() => { const d = document.querySelector('.topics-graph-detail');"
            " return !!d && d.getBoundingClientRect().height > 0; }"
        )
        assert detail_visible, (
            "clicking a basin node at 320px did not open the basin-detail panel — the "
            "richest layout state never rendered, so this overflow guard would be "
            "vacuous."
        )

        scroll_over = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        escaping = page.evaluate(_ESCAPING, _NARROW)

        assert not escaping and scroll_over <= 0, (
            "the memory viewer's topics.json view (topology + basin detail + Reader "
            f"reps) SCROLLS SIDEWAYS on a 320px phone: scrollWidth over by "
            f"{scroll_over}px; elements escaping the viewport (not in an "
            f"overflow-x:auto scroll region): {escaping}. The "
            "`@media (max-width: 1179px) .content {{ min-width: 0; max-width: 100% }}` "
            "rule that shrinks the content grid item below its widest child regressed."
        )
        assert not errs, f"console/page errors on the 320px topics view: {errs[:5]}"

        browser.close()
