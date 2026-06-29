"""Browser guard: the /stats "Who the chairman picks, by task type" routing
cheat-sheet (``table.routing-table``) must paint its rows in COUNCIL-COUNT
DESCENDING order — the most-evidenced task type on top.

WHY THIS GUARD EXISTS (the recurring Trinity surface-binding gap):
The row ORDER is a value DERIVED from data. The launchpad template computes it
client-side in the ``routingCheatSheetMap`` getter (``launchpad_template.py``):

    Object.keys(t.by_task_type)
      .filter((tt) => nOf(tt) >= 2)                       # n>=2 floor
      .sort((a, b) => (nOf(b) - nOf(a))                   # council count DESC
                      || String(a).localeCompare(String(b)))  # name ASC tie-break
      .forEach((tt) => { out[tt] = t.by_task_type[tt]; });

(``nOf(tt)`` = ``cold_start[tt].n_personal``.) petite-vue then paints the rows
RAW via ``v-for="(scores, taskType) in routingCheatSheetMap"`` — NO JS re-sort —
so the painted DOM row order IS the sort the getter computed. A first-time user
reads the TOP row as "the task type Trinity knows the most about."

THE GAP THIS CLOSES (audited 2026-06-21, the same class as Iter 219's picks
cheat-sheet + Iter 220's eval leaderboard): the sibling routing-table browser
tests read ``.routing-table tbody tr`` but build an ORDER-AGNOSTIC dict keyed by
task name (``test_routing_best_tie_demotion_browser`` — tie demotion), or check
grain labels / column brands / row counts (``test_launchpad_value_wedge_grain_*``,
``test_launchpad_provider_label_brand_browser``, ``test_sidepanel_*``). NONE
asserts the painted ROW SEQUENCE. The memory-viewer's SEPARATE routing-table
renderer IS order-guarded (``test_memory_viewer_routing_filter`` →
``firstTask == "t_big"``), but the LAUNCHPAD cheat-sheet was not. A regression
that drops the ``nOf(b) - nOf(a)`` sign (ascending), keys the sort on the wrong
field, or removes the sort (falling back to ``Object.keys`` insertion order)
would paint the LEAST-evidenced task type on top while every existing
routing-table test stays green.

MUTATION-PROVEN: flip the sort to ascending / drop the sort in
``routingCheatSheetMap`` (src/trinity_local/launchpad_template.py) → this test
REDS with the founder symptom while the order-agnostic sibling tests stay GREEN.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _cell(overall: float, n: int) -> dict:
    # Each provider cell paints `scores[provider].overall.toFixed(1)` + `.n`, so
    # both fields must be present or the row errors (fixture-completeness, not the
    # surface under test).
    return {"overall": overall, "n": n, "wins": 1}


# DISCRIMINATING page_data: the `by_task_type` INSERTION order is deliberately
# the REVERSE of the council-count (cold_start.n_personal) DESCENDING order the
# cheat-sheet must paint. So a broken/absent sort paints insertion order
# (bug_fix on top, n=2 — the LEAST evidenced); only the correct `nOf DESC` sort
# REVERSES it to paint code_review on top (n=20 — the MOST evidenced).
#
#   insertion order : bug_fix(2)  → code_review(20) → data_analysis(8)
#   correct paint   : code_review(20) → data_analysis(8) → bug_fix(2)
#
# All n distinct and >=2 (clears the n>=2 floor), so the order assertion can
# never pass vacuously on equal/absent counts.
_PAGE_DATA = {
    "benchmarkProviders": ["claude", "codex"],
    "providerModels": {"claude": "Opus 4.8", "codex": "gpt-5.5"},
    "personalRoutingTable": {
        "councils_aggregated": 30,
        "by_task_type": {
            "bug_fix": {"claude": _cell(7.0, 2), "codex": _cell(6.0, 2)},
            "code_review": {"claude": _cell(8.0, 20), "codex": _cell(7.5, 20)},
            "data_analysis": {"claude": _cell(6.5, 8), "codex": _cell(7.0, 8)},
        },
        "cold_start": {
            "bug_fix": {"n_personal": 2, "alpha": 0.3, "personalization_pct": 30},
            "code_review": {"n_personal": 20, "alpha": 0.9, "personalization_pct": 90},
            "data_analysis": {"n_personal": 8, "alpha": 0.6, "personalization_pct": 60},
        },
        "best_per_task_type": {
            "bug_fix": "claude",
            "code_review": "claude",
            "data_analysis": "codex",
        },
        "pick_is_tie": {},
        "wins_per_task_type": {
            "bug_fix": {"wins": 2, "total": 2},
            "code_review": {"wins": 15, "total": 20},
            "data_analysis": {"wins": 5, "total": 8},
        },
    },
}

# RENDER-INDEPENDENT discriminating-seed check (BITE precondition B): assert the
# FIXTURE itself (the constants above, NEVER the painted DOM) is discriminating —
# insertion order != n-descending order. Keying this on the fixture (not the
# render) means a real ORDER regression reds on the ORDER assertion with the
# founder symptom, not on a misleading "seed" message.
_INSERTION_ORDER = list(_PAGE_DATA["personalRoutingTable"]["by_task_type"].keys())
_N_OF = {
    tt: blk["n_personal"]
    for tt, blk in _PAGE_DATA["personalRoutingTable"]["cold_start"].items()
}
_N_DESC_ORDER = sorted(_INSERTION_ORDER, key=lambda tt: (-_N_OF[tt], tt))
# Expected painted task LABELS (the template title-cases task_type, "_"→" ").
_EXPECTED_LABELS = [tt.replace("_", " ").title() for tt in _N_DESC_ORDER]


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
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


def test_routing_cheatsheet_paints_rows_in_council_count_descending_order(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.launchpad_template import render_launchpad_html

    # BITE precondition B (on the FIXTURE CONSTANTS — render-independent): the
    # seed must genuinely discriminate (insertion order != sorted order) so the
    # order assertion below can't pass vacuously.
    assert _INSERTION_ORDER != _N_DESC_ORDER, (
        "non-discriminating fixture: by_task_type insertion order already equals "
        "the council-count-descending order, so the row-order assertion would "
        f"pass even with the sort removed. insertion={_INSERTION_ORDER} "
        f"n_desc={_N_DESC_ORDER}"
    )
    assert len(set(_N_OF.values())) == len(_N_OF), (
        "non-discriminating fixture: two task types share an n_personal, so the "
        f"order is ambiguous on the tie-break. n_of={_N_OF}"
    )

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
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_selector("table.routing-table tbody tr", timeout=10000)
                page.wait_for_timeout(400)
                rows = page.evaluate(
                    """() => [...document.querySelectorAll('table.routing-table tbody tr')].map(tr => {
                      const tds = tr.querySelectorAll('td');
                      return {
                        task: (tds[0]?.innerText || '').replace(/\\s+/g,' ').trim(),
                        personalization: (tds[2]?.innerText || '').replace(/\\s+/g,' ').trim(),
                      };
                    })"""
                )
                body = page.inner_text("body")

                # BITE precondition A (the element actually PAINTS): all 3 seeded
                # rows mounted, no raw template leak, no JS error.
                assert not errs, f"JS errors rendering the routing cheat-sheet: {errs[:3]}"
                assert "{{" not in body, "raw petite-vue template leaked on /stats (un-mounted)"
                labels = [r["task"] for r in rows]
                assert len(labels) == 3, (
                    "the routing cheat-sheet didn't paint all 3 seeded rows "
                    f"(n>=2 each, so none should be filtered): saw {labels!r}"
                )

                # THE GUARD: painted ROW ORDER == council-count descending. The
                # sole assertion keyed on the binding under test; reds with the
                # founder symptom if the sort is dropped/inverted/mis-keyed.
                assert labels == _EXPECTED_LABELS, (
                    "the /stats routing cheat-sheet painted its rows in the WRONG "
                    "order — the card promises the most-evidenced task type on top "
                    "(council count descending), but the painted sequence does not "
                    f"match. expected (n descending) {_EXPECTED_LABELS!r}, "
                    f"painted {labels!r}. A dropped/inverted `nOf(b) - nOf(a)` sort "
                    "in routingCheatSheetMap paints the LEAST-evidenced task "
                    "(Bug Fix, n=2) at the top."
                )
                # Order agrees with the n the row itself displays: top row carries
                # the largest n_personal, bottom the smallest.
                assert "n=20" in rows[0]["personalization"], (
                    "the TOP routing-cheat-sheet row must carry the largest "
                    f"council count (n=20); saw {rows[0]['personalization']!r}"
                )
                assert "n=2" in rows[-1]["personalization"] and "n=20" not in rows[-1]["personalization"], (
                    "the BOTTOM routing-cheat-sheet row must carry the smallest "
                    f"council count (n=2); saw {rows[-1]['personalization']!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
