"""Regression: live council page must surface a clear "council never
started" message when the status_token URL has no backing status file
after sustained polling.

User-reported symptom (2026-05-26, launch_mpm0bght_gx1y9v): clicked
Launch from the launchpad, dispatcher silently failed because the
Chrome extension wasn't installed in that browser. The launchpad
fix (aeba2cd) prevents construction of these URLs going forward,
but stale tabs / bookmarks / shared links can still land users on
a status_token URL whose status file was never written. Before
this fix, the page polled the missing file every 1.5s indefinitely,
showing "Council running / Generating witty dialog..." with no
indication that nothing was actually happening.

After this fix, MAX_MISSING_POLLS (=8 ~= 12s @ 1.5s/poll) consecutive
404s flips the segment to failed=true with a self-explanatory message
referencing install-extension.
"""
from __future__ import annotations

from pathlib import Path


def _src():
    return (Path(__file__).resolve().parent.parent
            / "src" / "trinity_local" / "council_review.py").read_text()


def test_missing_status_poll_counter_exists():
    src = _src()
    assert "missingPollCount" in src
    assert "MAX_MISSING_POLLS" in src
    # MAX must be reachable in a reasonable timeframe; 8 polls @ 1.5s = 12s.
    # That's long enough for a slow first launch but short enough to give
    # the user feedback before they walk away frustrated.
    assert "MAX_MISSING_POLLS = 8" in src, "stuck-timeout count drifted"
    # Mutation-test catch: the variable + threshold both need a DECLARATION
    # site AND an INCREMENT site. Iter-18 mutation testing surfaced that
    # checking only substrings let an orphan threshold-check + dead error
    # branch survive after the declaration was deleted.
    assert "let missingPollCount = 0;" in src, (
        "missingPollCount must be declared in startPolling's closure; "
        "an orphan threshold-check with no declaration would make this "
        "variable an unhandled ReferenceError at runtime"
    )
    assert "missingPollCount += 1;" in src, (
        "missingPollCount must be incremented somewhere; without the "
        "increment the threshold check can never fire"
    )
    assert "const MAX_MISSING_POLLS = 8" in src, (
        "MAX_MISSING_POLLS must be declared as a const; an orphan "
        "comparison against an undeclared constant would also throw"
    )


def test_missing_status_resets_counter_on_success():
    """If status file shows up mid-poll-stream, the counter must reset —
    otherwise a slow-starting council that takes 13s to write its first
    status frame would be incorrectly declared dead."""
    src = _src()
    # The reset comment + assignment must both exist
    assert "missingPollCount = 0" in src
    assert "Reset the missing-poll counter" in src


def test_missing_status_patches_segment_failed_with_install_hint():
    """When the threshold is hit, the segment is patched to failed=true
    with an errorText that names install-extension as the most likely
    cause — that's the specific cause that produced the user-reported
    stuck token."""
    src = _src()
    assert 'failed: true' in src
    assert "This council never started" in src
    # The error message must mention install-extension since that's
    # the most common cause (Chrome extension not installed).
    assert "install-extension" in src


def test_polling_stops_after_threshold():
    """Don't keep polling after we've declared failure — the give-up branch must
    stop the poller (startPolling → this.clearPolling()). The live council page
    also polls chain segments (Continue / Refine) THROUGH startPolling, so this
    one give-up covers the chain-action path too. (The static render_unified page
    carried a separate _pollChainStatus poller with its own give-up; it was
    removed with render_unified, #311/#8, so there is one give-up branch now, not
    two.)"""
    src = _src()
    idx, found = 0, 0
    while True:
        threshold_idx = src.find("missingPollCount >= MAX_MISSING_POLLS", idx)
        if threshold_idx == -1:
            break
        found += 1
        branch_excerpt = src[threshold_idx:threshold_idx + 400]
        assert (
            "this.clearPolling()" in branch_excerpt
            or "clearInterval(this._chainPollHandle)" in branch_excerpt
        ), "each give-up branch must stop its own poller, not keep ticking"
        idx = threshold_idx + 1
    assert found >= 1, "the live council page's poller must have a give-up branch"


# (test_chain_action_poller_also_has_missing_status_giveup retired with
# render_unified_council_page, #311/#8: `_pollChainStatus` was that dead static
# page's SEPARATE chain-action poller. On the live council page the Continue /
# Refine chain action reuses startPolling — whose give-up test_polling_stops_
# after_threshold above already guards — so there is no separate poller left to
# spin forever.)


def test_synthesis_section_hidden_on_failed_or_canceled_segment():
    """Found 2026-06-02 driving a missing council_id in the browser: a council
    that FAILED to load showed "Council failed — Could not load council outcome"
    AND, right below it, a contradictory optimistic stage tracker —
    "Analysis · QUEUED · Ready to start final comparison". Root cause:
    analysisRowFor(seg) ALWAYS returns a (stub) row (falling through to
    'Ready to start final comparison.' when there's no runState), and the
    synthesis-section's v-if only checked `analysisRowFor(seg)` (always truthy),
    with no `!seg.failed` guard. The routing-label + member sections were
    already safe (they require real runState data). On a terminal failure/cancel,
    only the failure card should show.

    Mutation-robust: bound to the synthesis-section element and assert BOTH the
    failed and canceled guards live in its v-if — reverting either reds this.
    """
    src = _src()
    marker = '<section class="card synthesis-section mb-lg" v-if='
    idx = src.find(marker)
    assert idx != -1, "synthesis-section must exist on the live council page"
    vif = src[idx:idx + 160]  # the opening tag through its v-if expression
    assert "!seg.failed" in vif, (
        "synthesis-section v-if must hide on a failed segment — else a council "
        "that couldn't load still renders the 'Ready to start final comparison' "
        "stub beside 'Council failed'"
    )
    assert "!seg.canceled" in vif, (
        "synthesis-section v-if must also hide on a canceled segment (same "
        "contradiction next to the 'Council stopped' card)"
    )
    # The fall-through stub string still exists in analysisRowFor (it's correct
    # for a genuine pre-run/in-progress segment) — guard only HIDES it on a
    # terminal segment, it doesn't delete it.
    assert "Ready to start final comparison." in src
