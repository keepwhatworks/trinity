"""Guards for two lens-build launchpad honesty/robustness bugs (2026-06-02).

FINDING 1 — the contradiction. `_lens_build_for_launchpad` showed
"✓ Your lens is ready" on ``status === 'complete'`` keyed purely on the build
PROCESS finishing, never on whether the lens has CONTENT. A cold-start build
that completes empty (or the #295 preserved-degenerate path) therefore rendered
"✓ Your lens is ready" AND the "no lens yet, build one" empty-state CTA *at the
same time* — the launchpad's worst first-run moment. Verified live in a real
browser: pre-fix both rendered; post-fix the completed-but-empty build shows
"Lens build finished — no tensions yet". The fix threads ``lens_populated``
(the SAME signal the empty-state keys off) into the card so the two can never
contradict.

FINDING 2 — the crash. `load_lenses` / `load_orderings` did
``json.loads(...).get(key, [])`` with no isinstance guard, and
`_load_taste_lenses` calls them outside any try/except — so a ``lenses.json``
corrupted to the wrong type (or invalid JSON, or non-dict rows) crashed the
WHOLE launchpad build (guard_shape_not_just_parse class, v1.7.202 sweep missed
this pair). Now they degrade to [].
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local.me.pair_mining import (
    lenses_path,
    load_lenses,
    load_orderings,
    orderings_path,
)


# --------------------------------------------------------------------------
# FINDING 1 — the "ready" claim must track lens CONTENT, not process status
# --------------------------------------------------------------------------

def _seed_completed_build(home_has_tensions: bool):
    """Write a freshly-completed lens_build_progress + a lenses.json that is
    either populated or empty, into the patched TRINITY_HOME."""
    from trinity_local.lens_progress import progress_path
    from trinity_local.utils import now_iso

    lenses = (
        [{"pole_a": "concrete", "pole_b": "abstract", "failure_a": "vague",
          "failure_b": "brittle", "basins_spanned": ["b1", "b2"]}]
        if home_has_tensions else []
    )
    lenses_path().parent.mkdir(parents=True, exist_ok=True)
    lenses_path().write_text(json.dumps({"lenses": lenses}))
    orderings_path().write_text(json.dumps({"orderings": []}))
    now = now_iso()
    progress_path().write_text(json.dumps({
        "stage": "done", "label": "done", "pct": 100, "status": "complete",
        "started_at": now, "updated_at": now, "error": None,
    }))


@pytest.mark.usefixtures("patch_trinity_home")
def test_completed_empty_build_is_not_reported_populated():
    """A completed build with no tensions must report lensPopulated=False — the
    template then shows 'no tensions yet' instead of '✓ Your lens is ready'."""
    from trinity_local.launchpad_data import (
        _lens_build_for_launchpad,
        _load_taste_lenses,
    )
    _seed_completed_build(home_has_tensions=False)
    populated = _load_taste_lenses() is not None
    assert populated is False  # empty-state CTA would show
    card = _lens_build_for_launchpad(lens_populated=populated)
    assert card is not None and card["status"] == "complete"
    assert card["lensPopulated"] is False, (
        "completed-but-empty build must not claim the lens is ready while the "
        "empty-state CTA is showing — that's the FINDING-1 contradiction"
    )


@pytest.mark.usefixtures("patch_trinity_home")
def test_completed_populated_build_is_reported_ready():
    from trinity_local.launchpad_data import (
        _lens_build_for_launchpad,
        _load_taste_lenses,
    )
    _seed_completed_build(home_has_tensions=True)
    populated = _load_taste_lenses() is not None
    assert populated is True
    card = _lens_build_for_launchpad(lens_populated=populated)
    assert card is not None and card["lensPopulated"] is True


@pytest.mark.usefixtures("patch_trinity_home")
def test_page_data_wires_lens_populated_consistently_with_empty_state():
    """The wiring invariant: build_page_data must derive the progress card's
    lensPopulated from the SAME tasteLenses it feeds the empty-state card, so
    the two surfaces can never disagree."""
    from trinity_local.launchpad_data import build_page_data
    _seed_completed_build(home_has_tensions=False)
    pd = build_page_data(
        live_review_path=Path("../review_pages/live_council.html"),
        recent_councils=[],
    )
    assert pd["tasteLenses"] is None  # empty-state CTA renders
    assert pd["lensBuild"] is not None
    # not ready while the empty-state shows
    assert pd["lensBuild"]["lensPopulated"] is False


def test_template_keeps_both_complete_branches():
    """Structural mutation guard: both the populated 'ready' header AND the
    empty 'no tensions yet' header must exist, split on lensPopulated. If a
    refactor collapses them back to a single unconditional '✓ ready', the
    contradiction returns — this reds."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})
    assert "✓ Your lens is ready" in html
    assert "Lens build finished — no tensions yet" in html
    # the gating condition itself — a mutation that drops `&& ...lensPopulated`
    # (re-opening the contradiction) reds here
    assert "status === 'complete' && pageData.lensBuild.lensPopulated" in html


# --------------------------------------------------------------------------
# FINDING 2 — corrupt lenses/orderings must degrade to [], never crash
# --------------------------------------------------------------------------

@pytest.mark.usefixtures("patch_trinity_home")
@pytest.mark.parametrize("bad", [
    "[]",                         # valid JSON, WRONG top-level type (list not dict)
    '"a string"',                 # valid JSON, wrong type (str)
    "not json at all {{{",        # invalid JSON
    '{"lenses": "nope"}',         # key present but not a list
    '{"lenses": [1, "x", null]}', # non-dict rows
    '{"lenses": [{"pole_a": "a"}]}',  # dict row missing required LensPair keys
])
def test_load_lenses_tolerates_corruption(bad):
    lenses_path().parent.mkdir(parents=True, exist_ok=True)
    lenses_path().write_text(bad)
    assert load_lenses() == []  # no crash, graceful empty


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_orderings_tolerates_corruption():
    orderings_path().parent.mkdir(parents=True, exist_ok=True)
    orderings_path().write_text("[]")  # wrong top-level type
    assert load_orderings() == []


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_lenses_keeps_valid_rows_and_skips_bad_ones():
    """A partially-corrupt file yields the good rows, drops the bad — not all
    or nothing."""
    lenses_path().parent.mkdir(parents=True, exist_ok=True)
    lenses_path().write_text(json.dumps({"lenses": [
        {"pole_a": "a", "pole_b": "b", "failure_a": "x", "failure_b": "y"},
        "garbage",
        {"missing": "required keys"},
    ]}))
    out = load_lenses()
    assert len(out) == 1 and out[0].pole_a == "a"


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_taste_lenses_survives_corrupt_lenses_json():
    """The launchpad blast-radius guard: a corrupt lenses.json must not crash
    _load_taste_lenses (which runs OUTSIDE any try/except in build_page_data)."""
    from trinity_local.launchpad_data import _load_taste_lenses
    lenses_path().parent.mkdir(parents=True, exist_ok=True)
    lenses_path().write_text("[]")  # wrong type
    orderings_path().write_text("[]")
    # no crash; returns None (empty) so the empty-state CTA shows
    assert _load_taste_lenses() is None
