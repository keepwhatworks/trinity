"""Regression: the personal routing cheat-sheet must suppress single-council
task types.

Found 2026-06-01 by eyeballing the real rendered launchpad: the page was
36,556px tall because the cheat-sheet table (`Who the chairman picks, by task
type`) rendered EVERY task_type in `personalRoutingTable.by_task_type` — 430
rows on the founder's corpus, 352 of them n=1 (a single council, whose `Best`
column is just "—"). A lone council is one data point, not a routing PATTERN;
the `best_per_task_type` PICK column was already confidence-filtered
(confidence_honesty_arc), but the ROWS were the missed sibling.

The fix is a display-only computed getter `routingCheatSheetMap` that suppresses
n_personal < 2 and sorts by evidence — scoped to the cheat-sheet table so the
personal-preference CHART (which aggregates the FULL by_task_type into
categories) is untouched. These pin the structural choice so a revert to the raw
v-for (or dropping the n>=2 floor) reds a fast test, not just the out-of-pytest
browser smoke.
"""
from __future__ import annotations


def _launchpad_html() -> str:
    from trinity_local.launchpad_template import render_launchpad_html

    return render_launchpad_html(page_data={})


def test_cheatsheet_iterates_the_filtered_map_not_raw_by_task_type():
    html = _launchpad_html()
    # The cheat-sheet table's row loop must go through the filtered/sorted map.
    assert "(scores, taskType) in routingCheatSheetMap" in html, (
        "the routing cheat-sheet must iterate routingCheatSheetMap (n>=2, sorted)"
    )
    # The raw unfiltered loop (the bug — renders all 430 task types incl. n=1)
    # must be gone. This is the mutation-catcher: revert the v-for and it reds.
    assert "(scores, taskType) in personalRoutingTable.by_task_type" not in html, (
        "the cheat-sheet still iterates the RAW by_task_type — single-council "
        "rows (n=1) leak back in, re-inflating the page to ~36k px"
    )


def test_cheatsheet_getter_filters_single_council_rows():
    html = _launchpad_html()
    # The getter exists and applies the n>=2 evidence floor.
    assert "get routingCheatSheetMap()" in html
    assert "nOf(tt) >= 2" in html, (
        "routingCheatSheetMap must suppress single-council task types (n_personal < 2)"
    )
    # It sorts by evidence so the strongest patterns lead.
    assert "nOf(b) - nOf(a)" in html, "cheat-sheet rows must sort by evidence (n) descending"


def test_hidden_singletons_are_surfaced_not_silently_dropped():
    html = _launchpad_html()
    # Suppression is never silent (data_sampling_principle): the count of hidden
    # single-council task types is surfaced with a routing.json escape hatch.
    assert "get routingHiddenSingletons()" in html
    assert "routingHiddenSingletons > 0" in html
    assert "single council hidden" in html
    assert "file=routing.json" in html, "the note must link to the full routing table"
