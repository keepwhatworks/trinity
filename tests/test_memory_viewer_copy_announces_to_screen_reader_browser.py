"""Regression: the memory viewer's copy chips announce to a screen reader (WCAG 4.1.3).

Found 2026-06-20 driving the memory-viewer topics.json Reader. Every copy chip in the
viewer — the per-rep "Replay", the basin "Launch council on this topic", the per-file
"↻ Build/Rebuild", the per-file health-fix command, and the stale-basin
"trinity-local lens"/"trinity-local consolidate" chips — only swaps its own label to
"✓ Copied" on click. That label swap (and the clipboard write itself) is a VISUAL
change a screen reader never hears: copying a command is a Status Message (WCAG 4.1.3),
and the viewer shipped with ZERO aria-live / role=status / .sr-only regions in the
entire document, so a screen-reader user who copies a command hears SILENCE.

This is the EXACT class the launchpad fixed in the UX sweep (Iter 130 →
test_launchpad_status_aria_live_browser.py): the launchpad routes "Copied to clipboard"
through a persistent sr-only role=status region. The memory viewer ships its OWN style
block + its OWN vanilla-JS render path (not the launchpad's petite-vue), so it never
inherited that fix — it was the un-fixed sibling surface. The fix adds one shared
#sr-status region + an announceCopy() helper that every "✓ Copied" site calls.

The only prior coverage (test_memory_viewer_topics_rep_expand_browser::TestRepReplayChip
and friends) asserts the chip FLASHES "✓ Copied" — a VISUAL check that stays GREEN while
the AT announcement is absent. This drives the REAL viewer and reads the live aria-live
region after a real click, so a severed announce (or a deleted region) BITES.

Slow-marked (spawns portal-html + chromium); runs in the slow shard, skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _topics_shape() -> dict:
    """One thread-shaped basin so the topics Reader detail panel renders a per-rep
    Replay chip + a basin Launch-council chip (the two copy chips we drive)."""
    return {
        "basins": [
            {
                "id": "b00",
                "label": "floor-plan engine",
                "size": 120,
                "thread_count": 8,
                "top_terms": ["floorplan", "prefab", "layout"],
                "centroid": [1.0, 0.0, 0.0],
                "representatives": [
                    {
                        "transcript_id": "tx-aaa",
                        "headline": "Generate a floor plan for a 3-bed prefab",
                        "turn_count": 3,
                        "turns": [
                            {"turn_index": 0, "snippet": "Generate a floor plan for a 3-bed prefab"},
                            {"turn_index": 1, "snippet": "make the kitchen open-plan"},
                            {"turn_index": 2, "snippet": "now export to DXF"},
                        ],
                    }
                ],
            },
            {
                "id": "b01",
                "label": "embeddings",
                "size": 80,
                "thread_count": 5,
                "top_terms": ["embedding", "vector", "cosine"],
                "centroid": [0.0, 1.0, 0.0],
                "representatives": [
                    {
                        "transcript_id": "tx-ccc",
                        "headline": "Compare MLX vs torch embedding speed",
                        "turn_count": 2,
                        "turns": [
                            {"turn_index": 0, "snippet": "Compare MLX vs torch embedding speed"},
                            {"turn_index": 1, "snippet": "and on a tight budget"},
                        ],
                    }
                ],
            },
        ]
    }


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
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


# Open the topics Reader detail panel by clicking the first basin node.
_OPEN_DETAIL = """() => {
    const c = document.querySelector('circle');
    if (c) c.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
}"""


def test_topics_reader_copy_chip_announces_to_screen_reader():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    target = f"file://{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(target, wait_until="load")
        page.wait_for_timeout(900)

        # PRECONDITION / non-vacuous BITE: the persistent aria-live status region
        # exists, is a visually-hidden role=status[aria-live=polite], and starts EMPTY.
        region = page.evaluate(
            """() => {
                const r = document.getElementById('sr-status');
                if (!r) return {present: false};
                const cs = getComputedStyle(r);
                return {
                  present: true,
                  role: r.getAttribute('role'),
                  ariaLive: r.getAttribute('aria-live'),
                  text: r.textContent,
                  // visually hidden but in the a11y tree (NOT display:none)
                  display: cs.display,
                  width: cs.width,
                };
            }"""
        )
        assert region["present"], (
            "the memory viewer has NO persistent #sr-status aria-live region — every "
            "copy chip's '✓ Copied' swap is mute to a screen reader (WCAG 4.1.3 Status "
            "Messages). The launchpad has this (sr-only role=status); the viewer was the "
            "un-fixed sibling."
        )
        assert region["role"] == "status" and region["ariaLive"] == "polite", (
            f"the #sr-status region must be role=status aria-live=polite to announce a "
            f"copy: got role={region['role']!r} aria-live={region['ariaLive']!r}"
        )
        assert region["display"] != "none", (
            "the #sr-status region is display:none — that removes it from the "
            "accessibility tree, so the announcement is never spoken (use the clip+1px "
            ".sr-only technique, not display:none)."
        )
        assert region["text"] == "", (
            f"the #sr-status region isn't empty at rest (text={region['text']!r}) — it "
            "would announce stale text or mask a later copy."
        )

        # Open the detail panel so the per-rep Replay chip + basin Launch chip render.
        page.evaluate(_OPEN_DETAIL)
        page.wait_for_timeout(400)
        chips = page.evaluate(
            """() => ({
                replay: document.querySelectorAll('.topics-rep-replay').length,
                launch: document.querySelectorAll('.topics-launch-chip').length,
            })"""
        )
        assert chips["replay"] >= 1 and chips["launch"] >= 1, (
            f"the topics Reader detail panel didn't render the copy chips to drive: {chips}"
        )

        # Click the Replay chip. announceCopy() sets the region on a microtask, so read
        # AFTER a short wait (matches the launchpad's ~180ms aria-live guard).
        page.evaluate(
            "() => document.querySelector('.topics-rep-replay')"
            ".dispatchEvent(new MouseEvent('click', {bubbles:true, view:window}))"
        )
        page.wait_for_timeout(180)
        after_replay = page.evaluate("() => document.getElementById('sr-status').textContent")
        assert after_replay and "copied" in after_replay.lower(), (
            "clicking the per-rep Replay chip swapped its label to '✓ Copied' but the "
            f"#sr-status aria-live region stayed empty (text={after_replay!r}) — the copy "
            "is mute to a screen reader (WCAG 4.1.3). The label swap is visual-only."
        )
        # The announcement should carry the actual command (the value, not a bare ack).
        assert "trinity-local council" in after_replay, (
            f"the Replay announcement doesn't name the copied command: {after_replay!r}"
        )

        # Independently verify a SECOND copy chip (the basin Launch chip) — proves the
        # fix is class-wide (shared announceCopy()), not wired to one chip.
        page.evaluate(
            "() => document.querySelector('.topics-launch-chip')"
            ".dispatchEvent(new MouseEvent('click', {bubbles:true, view:window}))"
        )
        page.wait_for_timeout(180)
        after_launch = page.evaluate("() => document.getElementById('sr-status').textContent")
        assert after_launch and "copied" in after_launch.lower(), (
            "clicking the basin 'Launch council on this topic' chip did NOT announce to "
            f"the #sr-status region (text={after_launch!r}) — a second copy chip is mute "
            "to AT, so the fix isn't class-wide."
        )

        # Self-clears so it doesn't sit announcing stale text.
        page.wait_for_timeout(2200)
        cleared = page.evaluate("() => document.getElementById('sr-status').textContent")
        assert cleared == "", (
            f"the #sr-status region didn't self-clear after the copy (text={cleared!r}) — "
            "it would keep announcing a stale 'Copied' or mask the next status."
        )

        assert not errs, f"the copy-announce interaction threw JS errors: {errs}"
        browser.close()
