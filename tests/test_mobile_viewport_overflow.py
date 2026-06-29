"""First mobile-width regression guard (v1.7.220).

The browser smoke (scripts/browser_smoke.py) drives every surface, but ONLY at a
1280px desktop viewport. Mobile is a stated direction — the live council /
launchpad links are the "review-link companion" people open on phones
(desktop_app_acquisition_surface) — yet nothing exercises a narrow viewport, so a
CSS regression that breaks mobile (a fixed-width element, dropping the
design_system `@media (max-width: 768px)` stacks, or removing the answers-grid
`minmax(0, 1fr)` min-width:0 that lets wide tables shrink) would ship silently.

Verified 2026-06-01 that the launchpad, memory viewer, and live council page
have ZERO horizontal overflow at 375px. This pins that: a surface whose content
forces the page wider than the viewport forces horizontal scrolling, which is
the canonical broken-mobile symptom.

Two coverage tiers:
  • Cold-start (empty home) — deterministic + PII-free, exercises the cards /
    code blocks / topbar. But an EMPTY home has no scoreboards, so it never
    renders the wide tables that are the actual overflow risk (the original
    docstring's "incl. a real wide-table council answer" was aspirational —
    the cold-start render has no table at all).
  • Populated home — seeds synthetic council_outcomes so the launchpad paints
    the 'cheat-sheet · by task type' table (the wide-table risk), and asserts
    the table actually rendered before checking overflow (else schema drift
    would pass it vacuously). Verified clean at 375px 2026-06-02.

Slow-marked (spawns portal-html + chromium); runs in the slow shard, skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# portal-html subprocess + chromium → real-browser/subprocess test. Marked slow so
# the default `pytest -q` stays fast (runs via TRINITY_SLOW=1 / `pytest -m slow`).
pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# A few px of slack: sub-pixel rounding / scrollbar gutters shouldn't trip it, but
# a real overflowing element (a wide table, a fixed-width card) clears this easily.
_OVERFLOW_TOLERANCE_PX = 4
_MOBILE_WIDTH = 375
# The DESIGN-documented "tablet dead zone": ~768-1180px where the memory viewer's
# 240px side nav + a wide table forced sideways scroll before the media query that
# stacks the nav below 1180px (file_substrate_browser_testing memory). A 375px test
# can't catch a regression here — it's a different media query. These are the band's
# load-bearing widths (iPad-portrait, iPad-landscape/small-laptop, the fix's edge).
_TABLET_BAND = [768, 1024, 1180]


def _measure_overflow(page):
    """Return (overflow_px, widest_culprit_description). Horizontal overflow =
    scrollWidth past the viewport; the culprit string names the widest element so a
    failure is actionable instead of just a number. Shared by the width tests."""
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    inner_w = page.evaluate("window.innerWidth")
    culprit = page.evaluate(
        "(() => {"
        "  let worst=null, max=window.innerWidth;"
        "  for (const el of document.querySelectorAll('*')) {"
        "    const r = el.getBoundingClientRect();"
        "    if (r.right > max && r.width > 0) {"
        "      max = r.right;"
        "      worst = el.tagName + '.' + String(el.className||'').split(' ')[0]"
        "              + ' w=' + Math.round(r.width) + ' right=' + Math.round(r.right);"
        "    }"
        "  }"
        "  return worst;"
        "})()"
    )
    return scroll_w - inner_w, culprit


def _render_portal(home: Path) -> Path:
    """Render the launchpad in `home` and return the portal_pages dir. Whatever
    state was seeded under `home` first (empty → cold start; scoreboards →
    populated wide tables) drives what renders."""
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
    assert (pages / "launchpad.html").exists(), "portal-html didn't write launchpad.html"
    return pages


# Back-compat alias for the original cold-start test below.
_render_cold_start_portal = _render_portal


def _seed_populated_scoreboards(home: Path) -> None:
    """Seed schema-valid council_outcomes so the launchpad renders the wide
    'cheat-sheet · by task type' table — the real horizontal-overflow risk the
    empty-home test never exercises (cold start has no councils → no tables).

    NOTE the cheat-sheet recomputes from council_outcomes/ via
    compute_personal_routing_table(); it does NOT read scoreboard/routing.json
    (that's the memory viewer's source). Seeding routing.json renders nothing —
    a trap caught by the non-vacuous table-present assert in the test. Build
    valid CouncilOutcomes via the real dataclasses and write to_dict() straight
    into the home (avoids mutating TRINITY_HOME in this process; the subprocess
    render reads the same dir). PII-free. 3 outcomes per task_type clears the
    n>=2 cheat-sheet filter (#290) + the >=3-council min-samples gate."""
    import json

    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    outcomes_dir = home / "council_outcomes"
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    # >=200 chars so both members count as substantive (not a walkover).
    long_answer = (
        "This is a substantive synthetic council answer that exists only to clear "
        "the substantive-member length gate so the outcome counts as a real "
        "contest in the personal routing aggregation. No PII. " * 2
    )
    names = [
        "code_generation", "debugging_a_stack_trace", "architecture_decision",
        "sql_query_optimization", "api_design_review", "refactoring_legacy_code",
        "writing_documentation", "data_pipeline_design",
    ]
    for i, task in enumerate(names):
        for k in range(3):
            cid = f"syn_{i:02d}_{k}"
            label = CouncilRoutingLabel(
                winner="claude",
                task_type=task,
                provider_scores={
                    "claude": {"overall": 8.0},
                    "codex": {"overall": 6.5},
                    "antigravity": {"overall": 6.0},
                },
            )
            outcome = CouncilOutcome(
                council_run_id=cid,
                bundle_id=f"bundle_{cid}",
                task_cluster_id=f"cluster_{i:02d}",
                primary_provider="claude",
                winner_provider="claude",
                synthesis_output="Synthetic synthesis output.",
                routing_label=label,
                member_results=[
                    CouncilMemberResult(provider="claude", model="opus", output_text=long_answer),
                    CouncilMemberResult(provider="codex", model="gpt", output_text=long_answer),
                ],
                metadata={"task_type": task},
            )
            (outcomes_dir / f"{cid}.json").write_text(
                json.dumps(outcome.to_dict()), encoding="utf-8"
            )


def test_surfaces_have_no_horizontal_overflow_at_mobile_width():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_cold_start_portal(home)

    targets = {
        "launchpad": pages / "launchpad.html",
        "memory-viewer": f"{pages / 'memory.html'}?file=lens.md",
    }

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed in this env
            pytest.skip(f"no launchable chromium for the mobile overflow test: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": _MOBILE_WIDTH, "height": 812})
            page = ctx.new_page()
            for name, target in targets.items():
                page.goto(f"file://{target}")
                page.wait_for_timeout(900)
                scroll_w = page.evaluate("document.documentElement.scrollWidth")
                inner_w = page.evaluate("window.innerWidth")
                overflow = scroll_w - inner_w
                # Identify the widest element extending past the viewport. This is
                # NOT just for the error message — it's a SECOND, stronger assertion.
                # The page sets overflow-x:hidden, which clamps document.scrollWidth
                # to the viewport, so the doc-overflow check above passes VACUOUSLY
                # when a CHILD element clips past the edge (verified: a cheat-sheet
                # md-code-block command ran 78px past the 375px viewport while
                # scrollWidth stayed 375 — invisible to the doc check, and clipped
                # rather than scrollable, so its right half was unreadable on a
                # phone). Flag any element whose right edge exceeds the viewport
                # beyond tolerance.
                culprit = page.evaluate(
                    "(tol) => {"
                    "  let worst=null, max=window.innerWidth + tol;"
                    "  for (const el of document.querySelectorAll('*')) {"
                    "    const r = el.getBoundingClientRect();"
                    "    if (r.right > max && r.width > 0) {"
                    "      max = r.right;"
                    "      worst = el.tagName + '.' + String(el.className||'').split(' ')[0]"
                    "              + ' w=' + Math.round(r.width) + ' right=' + Math.round(r.right);"
                    "    }"
                    "  }"
                    "  return worst;"
                    "}",
                    _OVERFLOW_TOLERANCE_PX,
                )
                assert overflow <= _OVERFLOW_TOLERANCE_PX and culprit is None, (
                    f"{name} overflows {_MOBILE_WIDTH}px viewport: doc_overflow={overflow}px "
                    f"(scrollWidth={scroll_w}). Element clipped past the viewport: {culprit}. "
                    "overflow-x:hidden hides element clipping from the scrollWidth check — "
                    "the element is clipped (not scrollable), so its content is unreadable on "
                    "mobile. Wrap or scroll it (see .md-code-block in launchpad_template.py)."
                )
        finally:
            browser.close()


def _seed_wide_routing_json(home: Path) -> None:
    """Write a schema-valid scoreboard/routing.json with several task_types (each
    n>=2 to clear the #290 floor) so the memory viewer's routing.json reader paints
    a multi-row table — the wide-content overflow risk the cold-start memory-viewer
    tab (empty home → empty reader) never exercises. Shape per the reader:
    {by_task_type:{task:{provider:{n,overall}}}, best_per_task_type, computed_at}.
    Written straight to disk (the subprocess render inlines it); PII-free."""
    import json

    by_task_type = {}
    best = {}
    for i in range(8):
        task = f"refactoring_a_very_long_descriptive_task_type_name_{i}"
        by_task_type[task] = {
            "claude": {"n": 3, "overall": 8.0},
            "codex": {"n": 3, "overall": 6.5},
            "antigravity": {"n": 2, "overall": 6.0},
        }
        best[task] = "claude"
    (home / "scoreboard").mkdir(parents=True, exist_ok=True)
    (home / "scoreboard" / "routing.json").write_text(
        json.dumps({
            "by_task_type": by_task_type,
            "best_per_task_type": best,
            "computed_at": "2026-06-02T00:00:00",
        }),
        encoding="utf-8",
    )


def test_populated_memory_viewer_routing_no_overflow_at_mobile_width():
    """The cold-start memory-viewer test (above) only drives the lens.md tab on an
    EMPTY home — it never renders the routing.json reader's wide multi-row table,
    which is the memory viewer's analog of the launchpad cheat-sheet overflow risk.
    Seed a wide routing.json, render the routing.json tab, and assert no horizontal
    scroll at 375px (+ the rows actually painted, so schema drift fails loud).
    Verified clean 2026-06-02 across all 6 tabs on the real corpus; this pins the
    wide-table tab the cold-start guard misses."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_wide_routing_json(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed
            pytest.skip(f"no launchable chromium for the mobile overflow test: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": _MOBILE_WIDTH, "height": 812})
            page = ctx.new_page()
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json")
            page.wait_for_timeout(1000)

            # Non-vacuous: the seeded task_type rows must have actually rendered, or
            # an overflow-only assert passes for the wrong reason (reader didn't run).
            # The routing reader HUMANIZES the displayed task_type (underscores →
            # spaces, title-cased — commit 27f22925 / #196) but keeps the RAW
            # snake_case in `<tr data-task>`. Match the data-task attr so this
            # non-vacuous precondition survives the display humanization (it
            # checked body.innerText for the raw snake_case form, which the
            # humanization removed from the visible text — silently RED since #196).
            painted = page.evaluate(
                "() => document.querySelector('[data-task*=\"refactoring_a_very_long\"]') !== null"
            )
            assert painted, (
                "memory viewer routing.json tab rendered no seeded rows — the "
                "by_task_type reader didn't run (schema drift); test would pass vacuously"
            )

            scroll_w = page.evaluate("document.documentElement.scrollWidth")
            inner_w = page.evaluate("window.innerWidth")
            overflow = scroll_w - inner_w
            culprit = page.evaluate(
                "(() => {"
                "  let worst=null, max=window.innerWidth;"
                "  for (const el of document.querySelectorAll('*')) {"
                "    const r = el.getBoundingClientRect();"
                "    if (r.right > max && r.width > 0) {"
                "      max = r.right;"
                "      worst = el.tagName + '.' + String(el.className||'').split(' ')[0]"
                "              + ' w=' + Math.round(r.width) + ' right=' + Math.round(r.right);"
                "    }"
                "  }"
                "  return worst;"
                "})()"
            )
            assert overflow <= _OVERFLOW_TOLERANCE_PX, (
                f"memory viewer routing.json tab overflows {_MOBILE_WIDTH}px by "
                f"{overflow}px (scrollWidth={scroll_w}) — horizontal scroll on "
                f"mobile. Widest element: {culprit}"
            )
        finally:
            browser.close()


@pytest.mark.parametrize("width", _TABLET_BAND)
def test_memory_viewer_routing_no_overflow_across_tablet_band(width):
    """All three tests above pin 375px (mobile). The memory viewer's documented
    "tablet dead zone" — the 240px side nav + a wide routing table forcing sideways
    scroll across ~768-1180px, fixed by the media query that stacks the nav below
    1180px (file_substrate_browser_testing) — has NO guard at the breakpoint: a
    375px test can't catch it (the mobile stack is a different @media rule), so a
    change that drops or narrows the dead-zone media query would re-introduce the
    sideways scroll on every iPad/small-laptop silently. Render the wide routing.json
    tab (the exact dead-zone surface) at each band width and assert no horizontal
    overflow. Empirically clean 2026-06-05 across 375-1440 on real + synthetic data;
    this pins the band the fix created. Reuses the routing.json-only seed so the
    portal-html freeze (which recomputes routing.json from council_outcomes) has
    nothing to overwrite — the wide table survives to render."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_wide_routing_json(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed
            pytest.skip(f"no launchable chromium for the tablet-band overflow test: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": width, "height": 1000})
            page = ctx.new_page()
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json")
            page.wait_for_timeout(1000)

            # Non-vacuous: the seeded wide table must actually paint at this width,
            # else an overflow-only assert passes for the wrong reason.
            # The routing reader HUMANIZES the displayed task_type (underscores →
            # spaces, title-cased — commit 27f22925 / #196) but keeps the RAW
            # snake_case in `<tr data-task>`. Match the data-task attr so this
            # non-vacuous precondition survives the display humanization (it
            # checked body.innerText for the raw snake_case form, which the
            # humanization removed from the visible text — silently RED since #196).
            painted = page.evaluate(
                "() => document.querySelector('[data-task*=\"refactoring_a_very_long\"]') !== null"
            )
            assert painted, (
                f"viewer routing.json tab rendered no seeded rows at {width}px — "
                "the by_task_type reader didn't run (schema drift); vacuous pass"
            )

            overflow, culprit = _measure_overflow(page)
            assert overflow <= _OVERFLOW_TOLERANCE_PX, (
                f"memory viewer routing.json tab overflows {width}px (tablet band) "
                f"by {overflow}px — the 240px-nav dead-zone media query regressed, "
                f"sideways scroll on iPad/small-laptop. Widest element: {culprit}"
            )
        finally:
            browser.close()


def test_populated_launchpad_no_overflow_at_mobile_width():
    """The cold-start test above renders an EMPTY home, so it never exercises the
    wide scoreboard tables — yet a wide table is the canonical mobile-overflow
    culprit (it's what the design_system min-width:0 / answers-grid minmax(0,1fr)
    rules exist to tame). Cover the populated case: seed routing.json, render the
    launchpad with its 'cheat-sheet · by task type' table, and assert no
    horizontal scroll at 375px. Asserts the table actually painted first, so a
    schema drift that drops the table fails LOUD instead of passing vacuously."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_populated_scoreboards(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed in this env
            pytest.skip(f"no launchable chromium for the mobile overflow test: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": _MOBILE_WIDTH, "height": 812})
            page = ctx.new_page()
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1200)

            # Non-vacuous: the wide cheat-sheet table must actually be on the page.
            # (If synthetic routing drifts out of schema, the table won't render and
            # an overflow-only assert would pass for the wrong reason.)
            n_tables = page.evaluate(
                "document.querySelectorAll('table.routing-table').length"
            )
            assert n_tables >= 1, (
                "populated launchpad rendered no routing-table — synthetic "
                "routing.json no longer triggers the cheat-sheet (schema drift); "
                "this test would otherwise pass vacuously"
            )

            scroll_w = page.evaluate("document.documentElement.scrollWidth")
            inner_w = page.evaluate("window.innerWidth")
            overflow = scroll_w - inner_w
            culprit = page.evaluate(
                "(() => {"
                "  let worst=null, max=window.innerWidth;"
                "  for (const el of document.querySelectorAll('*')) {"
                "    const r = el.getBoundingClientRect();"
                "    if (r.right > max && r.width > 0) {"
                "      max = r.right;"
                "      worst = el.tagName + '.' + String(el.className||'').split(' ')[0]"
                "              + ' w=' + Math.round(r.width) + ' right=' + Math.round(r.right);"
                "    }"
                "  }"
                "  return worst;"
                "})()"
            )
            assert overflow <= _OVERFLOW_TOLERANCE_PX, (
                f"populated launchpad ({n_tables} scoreboard table(s)) overflows "
                f"{_MOBILE_WIDTH}px by {overflow}px (scrollWidth={scroll_w}) — "
                f"horizontal scroll on mobile. Widest element: {culprit}"
            )
        finally:
            browser.close()


def test_launch_button_meets_touch_target_at_mobile_width():
    """The primary CTA (and every `.button`) must clear the 44px touch-target
    minimum (WCAG 2.5.5 / Apple HIG). It was 43px — 1px under — until a
    `min-height: 44px` floor was added to `.button` in the shared CSS
    (founder-flagged in the UX sweep). Measure the rendered Launch Council button
    on a populated home at 375px."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_populated_scoreboards(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": _MOBILE_WIDTH, "height": 812})
            page = ctx.new_page()
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1000)
            h = page.evaluate(
                "(() => { const b = document.querySelector('.button.primary');"
                " return b ? Math.round(b.getBoundingClientRect().height) : 0; })()"
            )
            assert h >= 44, (
                f"the Launch Council button is {h}px tall — under the 44px touch "
                "target. The `.button` min-height floor regressed."
            )
        finally:
            browser.close()


_CODE_HEAVY_LENS = (
    "# Your taste\n\n"
    "## privileged: clarity over cleverness\n\n"
    "You rewrote this verbose dispatch into a one-liner:\n\n```python\n"
    "result = await client.dispatch_council_member_with_a_really_long_method_name("
    "provider=provider, task=task, timeout=600, retries=3)\n"
    "VERY_LONG = 'https://example.com/some/really/long/path/that/keeps/going/and/"
    "going/and/going/forever/until/it/overflows/the/viewport'\n"
    "```\n\nand a bare token: "
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
)


def test_memory_viewer_code_heavy_lens_no_overflow_at_mobile_width():
    """The memory viewer renders corpus markdown (lens.md / generators.md /
    core.md) — dev content that contains CODE BLOCKS and long unbreakable tokens.
    The cold-start mobile test (above) drives lens.md but on a synthetic lens
    with no code, so it never exercised the real overflow risk. With a code-heavy
    lens the viewer overflowed ~247px at 375px: `.markdown-body pre` had
    `overflow-x: auto` but no `max-width: 100%`, so the <pre> grew to fit a wide
    code line and stretched the page; a long bare token also needed
    `overflow-wrap: break-word` on `.markdown-body`.

    Third corpus-markdown surface in the same bug class as the unified review
    (fcfbc473) + live council (66b095fb) pages — found by completing the sibling
    sweep. Mutation: drop `.markdown-body { overflow-wrap: break-word }` (the
    load-bearing line; it breaks the wide code line + the long token) → the page
    overflows ~246px at 375px → this reds. (`max-width:100%` on the pre is
    belt-and-suspenders so it still scrolls if overflow-wrap is ever overridden.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "lens.md").write_text(_CODE_HEAVY_LENS, encoding="utf-8")
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": _MOBILE_WIDTH, "height": 812})
            page = ctx.new_page()
            page.goto(f"file://{pages / 'memory.html'}?file=lens.md")
            page.wait_for_timeout(900)
            # Don't measure vacuously — the code block must have rendered.
            n_pre = page.evaluate("document.querySelectorAll('.markdown-body pre').length")
            assert n_pre >= 1, "the code-heavy lens didn't render a <pre> — overflow check would be vacuous"
            overflow, culprit = _measure_overflow(page)
        finally:
            browser.close()

    assert overflow <= _OVERFLOW_TOLERANCE_PX, (
        f"memory viewer lens.md tab overflows {_MOBILE_WIDTH}px by {overflow}px with "
        f"a code-heavy lens — the wide <pre> must scroll within the column, not "
        f"stretch the page. Widest element: {culprit}"
    )
