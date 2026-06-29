"""The launchpad page-data must not embed the per-(task,provider) `wins` field.

Found 2026-06-06 measuring the real launchpad render: `personalRoutingTable` is
81% of the embedded page-data (113KB on the founder's home), and every
`by_task_type[task][provider]` entry carried a `wins` int the launchpad CLIENT
never reads — the cheat-sheet's "picked X of Y" uses the SEPARATE top-level
`wins_per_task_type`, the ELO chart uses `.overall`, cold_start uses `.n`. So
`wins` was ~10KB of dead weight in every launchpad.html, scaling with task-type
count. `_load_personal_routing_table` now strips it from the EMBED (the on-disk
routing.json — the memory-viewer "Raw JSON" — keeps the full per-entry data).

Mutation: drop the strip in `_load_personal_routing_table` → `wins` reappears in
the embed and this fails.
"""
from __future__ import annotations


def test_page_data_routing_strips_unused_per_entry_wins(monkeypatch):
    from trinity_local import launchpad_data

    fake = {
        "by_task_type": {
            "code_review": {"claude": {"overall": 8.0, "n": 3, "wins": 2}},
        },
        "wins_per_task_type": {"code_review": {"wins": 2, "total": 3}},
        "councils_aggregated": 3,
    }
    # _load_personal_routing_table imports compute_personal_routing_table locally,
    # so patch it at the source module.
    monkeypatch.setattr(
        "trinity_local.personal_routing.compute_personal_routing_table",
        lambda: fake,
    )
    out = launchpad_data._load_personal_routing_table()
    assert out is not None
    entry = out["by_task_type"]["code_review"]["claude"]

    assert "wins" not in entry, (
        "per-(task,provider) `wins` must be stripped from the launchpad embed — "
        "the client never reads it (cheat-sheet uses wins_per_task_type)"
    )
    # The fields the chart (.overall) + cold_start (.n) need MUST survive.
    assert entry["overall"] == 8.0 and entry["n"] == 3
    # The top-level wins_per_task_type (the cheat-sheet's "picked X of Y") stays.
    assert out["wins_per_task_type"] == {"code_review": {"wins": 2, "total": 3}}, (
        "the top-level wins_per_task_type must be kept — it's a DIFFERENT field "
        "the cheat-sheet reads for 'picked X of Y'"
    )
    # The mtime-cached source table must NOT be mutated (we build a fresh dict).
    assert fake["by_task_type"]["code_review"]["claude"].get("wins") == 2, (
        "stripping mutated the in-process-cached source table in place"
    )
