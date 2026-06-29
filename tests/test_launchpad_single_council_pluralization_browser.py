"""Browser guard: at n=1, the /stats count-bearing captions + the cheat-sheet
margin title must read "1 council" (singular), NEVER the ungrammatical
"1 councils".

USEFULNESS / unclear-or-misleading-copy sweep (Iter 101). The /stats page has
THREE count-bearing strings that hardcoded the plural literal ` councils` while
the count can legitimately be 1 (a fresh user with a single aggregated council,
or a routing basin tallied from one real-contest council):

  1. the Routing chart caption  — "From your own {N} councils — the bars sharpen…"
  2. the by-task-type caption    — "…from your own {N} councils. The chairman…"
  3. the cheat-sheet margin TITLE — "Margin {m} over the runner-up, from {N} councils"

All three rendered "from your own 1 councils" / "from 1 councils" at n=1 —
ungrammatical English, and INCONSISTENT with the SAME row's visible cheat cell
6px away ("· 1 council") + the chip title ("from 1 real-contest council"), both
of which already pluralize via `r.count === 1 ? '' : 's'`. So the pattern was
known; these three just missed it. (Driven in real Chrome: the rendered titles
read "1 councils" pre-fix.)

The fix mirrors the visible cell's ternary onto all three strings. This guard
DRIVES the real /stats DOM with a SINGLE-council fixture (councils_aggregated=1
+ a count:1 cortex basin) and asserts each rendered string contains "1 council"
and NOT "1 councils". Mutation-provable: revert any of the three ternaries to a
bare " councils" literal and the matching assertion reds with "1 councils".

A pure string-presence test on the source would NOT bite — petite-vue evaluates
the ternary at render time, so only a JS engine produces "1 council" vs
"1 councils". This is a rendered-DOM assertion.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# A SINGLE-council fixture: councils_aggregated == 1 drives both routing
# captions; a cortexRules block with a count:1 basin (margin above the floor so
# the row isn't dimmed, which doesn't change the count text) drives the
# cheat-sheet margin title.
_PAGE_DATA = {
    "benchmarkProviders": ["claude", "codex"],
    "providerModels": {"claude": "Opus 4.8", "codex": "gpt-5.5"},
    "cortexRules": {
        "rules": [
            {
                "basin_id": "b00",
                "winner": "claude",
                "margin": 0.42,
                "count": 1,
                "n_episodes": 1,
                "evidence": ["bundle_aaa11111"],
            },
        ],
        "total_basins": 1,
        "winner_margin_floor": 0.15,
    },
    "personalRoutingTable": {
        "councils_aggregated": 1,
        "by_task_type": {
            "code_review": {
                "claude": {"overall": 7.4, "n": 1, "wins": 1},
                "codex": {"overall": 8.1, "n": 1, "wins": 0},
            },
        },
        "cold_start": {"code_review": {"n_personal": 1, "alpha": 0.5, "personalization_pct": 50}},
        "best_per_task_type": {"code_review": "claude"},
        "wins_per_task_type": {"code_review": {"wins": 1, "total": 1}},
    },
}

# Collect every count-bearing rendered string we pluralize at n=1: the two
# `p.meta` captions that say "from your own N council(s)", and the cheat-sheet
# margin `title` attribute.
# Scope to captions phrased "from your own N council(s)" — NOT the cheat-sheet
# intro ("…from your own 1 basin of council outcomes"), which counts BASINS and
# correctly says "1 basin".
_PROBE = """() => {
  const metas = [...document.querySelectorAll('p.meta')]
    .map(p => (p.textContent || '').replace(/\\s+/g, ' ').trim())
    .filter(t => /from your own \\d+ council/i.test(t));
  const marginMeta = document.querySelector('.cortex-cheat-sheet td .meta');
  const marginTitle = marginMeta ? (marginMeta.getAttribute('title') || '') : null;
  return { metaCaptions: metas, cheatMarginTitle: marginTitle };
}"""


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


def test_single_council_captions_and_title_read_singular(tmp_path):
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
                page = browser.new_context(viewport={"width": 1280, "height": 1600}).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000
                )
                page.wait_for_selector(".cortex-cheat-sheet td .meta", timeout=10000)
                s = page.evaluate(_PROBE)

                captions = s["metaCaptions"]
                # Both routing captions must have rendered (fixture drove them).
                assert any("from your own" in c.lower() for c in captions), (
                    "no 'from your own N council(s)' caption rendered — the routing "
                    f"table fixture didn't mount: {captions}"
                )
                for cap in captions:
                    assert "1 councils" not in cap, (
                        "a /stats count caption rendered ungrammatical '1 councils' at "
                        f"n=1 (the hardcoded plural literal regressed): {cap!r}"
                    )
                    # The fixture is exactly n=1, so the singular form must appear.
                    assert "1 council" in cap, (
                        "a /stats count caption at n=1 must read '1 council' "
                        f"(singular): {cap!r}"
                    )

                title = s["cheatMarginTitle"]
                assert title is not None, (
                    "the cheat-sheet margin .meta carried no title — the row didn't mount"
                )
                assert "1 councils" not in title, (
                    "the cheat-sheet margin title rendered ungrammatical '1 councils' "
                    f"at count=1 (the hardcoded plural literal regressed): {title!r}"
                )
                assert "1 council" in title, (
                    "the cheat-sheet margin title at count=1 must read '1 council' "
                    f"(singular): {title!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


# A fixture where routing data EXISTS but EVERY task_type is a single council
# (n=1) — the founder's real shape (349 of 424 task_types n=1-only). The
# routingCheatSheetMap (n>=2 filtered) is therefore empty. personalRoutingTable
# is non-null so the cold "Run a few councils" empty card does NOT fire — the
# by-task-type section IS shown, and pre-fix it rendered a 5-column HEADER row
# with ZERO body rows (a header-only ghost table) above a faint "+N hidden" note.
_ALL_N1_PAGE_DATA = {
    "benchmarkProviders": ["claude", "codex"],
    "providerModels": {"claude": "Opus 4.8", "codex": "gpt-5.5"},
    "personalRoutingTable": {
        "councils_aggregated": 8,
        "by_task_type": {
            f"task_type_{i:02d}": {
                "claude": {"overall": 8.0, "n": 1, "wins": 1},
                "codex": {"overall": 6.0, "n": 1, "wins": 0},
            }
            for i in range(8)
        },
        "cold_start": {
            f"task_type_{i:02d}": {"n_personal": 1, "alpha": 0.1, "personalization_pct": 10}
            for i in range(8)
        },
        # MIN_BEST_SAMPLES=3 → no confident best at n=1.
        "best_per_task_type": {},
        "wins_per_task_type": {},
    },
}


def test_all_single_council_routing_suppresses_header_only_ghost_table(tmp_path):
    """The by-task-type cheat-sheet must NOT render a header-only ghost table when
    EVERY task_type is a single council (n=1). Pre-fix: routingCheatSheetMap (the
    n>=2 filter) was empty, but the <table> rendered unconditionally — a 5-column
    header bar ('Task type | Best | Personalization | Claude | GPT') with ZERO body
    rows, reading as broken/loading, with the real 'all single-council' explanation
    buried in a faint '+N hidden' note. Fix: gate the <table> on
    routingCheatSheetHasRows and show an honest 'No routing pattern yet' empty-state.

    Rendered-DOM (offsetParent visibility + body-row count), NOT a source-string
    check. Mutation-proven: drop the `v-if="routingCheatSheetHasRows"` on the
    <table> → the ghost header table reappears (visible, 0 body rows) and this reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data=_ALL_N1_PAGE_DATA, view="stats")
    rel = _write_prod_layout(html, tmp_path)
    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1600}).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000
                )
                # Wait for the by-task-type section to mount (its heading is unique).
                page.wait_for_function(
                    """() => [...document.querySelectorAll('section.card h2')]
                        .some(h => /who the chairman picks, by task type/i.test(h.textContent))""",
                    timeout=10000,
                )
                info = page.evaluate(
                    """() => {
                      const secs = [...document.querySelectorAll('section.card')];
                      const sec = secs.find(s => {
                        const h = s.querySelector('h2');
                        return h && /who the chairman picks, by task type/i.test(h.textContent);
                      });
                      if (!sec) return {sectionFound: false};
                      const tbl = sec.querySelector('table.routing-table');
                      const tableVisible = !!(tbl && tbl.offsetParent !== null);
                      const bodyRows = tbl ? tbl.querySelectorAll('tbody tr').length : -1;
                      const headerCells = tbl ? tbl.querySelectorAll('thead th').length : 0;
                      const text = (sec.textContent || '').replace(/\\s+/g, ' ');
                      return {
                        sectionFound: true,
                        tableVisible,
                        bodyRows,
                        headerCells,
                        emptyState: /No routing pattern yet/i.test(text),
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert info["sectionFound"], (
        "the by-task-type cheat-sheet section didn't mount — the all-n=1 routing "
        "fixture failed to render"
    )
    # The ghost table: a visible <table> with a multi-column header and NO body rows.
    assert not (info["tableVisible"] and info["headerCells"] >= 2 and info["bodyRows"] == 0), (
        "the by-task-type cheat-sheet rendered a HEADER-ONLY GHOST TABLE at all-n=1 "
        f"({info['headerCells']} header cells, {info['bodyRows']} body rows, visible="
        f"{info['tableVisible']}) — a column header bar with no data reads as broken; "
        "gate the <table> on routingCheatSheetHasRows"
    )
    # The honest empty-state must be present instead.
    assert info["emptyState"], (
        "the all-single-council cheat-sheet must show an honest 'No routing pattern "
        f"yet' empty-state, not a ghost table: {info}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
