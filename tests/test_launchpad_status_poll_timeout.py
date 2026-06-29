"""The launchpad's launch-status poller must give up when the status file
never materializes — the third sibling of the live_council pollers.

`startOperationPolling` (launchpad_template.py) probes
`council_status_<token>.js` via a <script> tag every 1.5s. An optimistic
dispatch whose native host / council process died or never started writes
NO status file, so every probe 404s. Before this guard the poller re-polled
the 404 forever and the launchpad spun "running" indefinitely.

council_review.py's two pollers got MAX_MISSING_POLLS=8 in v1.7.194; this
locks the same give-up onto the launchpad poller (value 20 ≈ 30s @ 1.5s,
larger because an initial council launch has more startup latency than a
chain action). Assertions follow the sibling's mutation-resistant shape:
declaration site + increment site + threshold site, all scoped to the
poller function so an orphan occurrence elsewhere can't satisfy them.
"""
from __future__ import annotations

from pathlib import Path


def _src() -> str:
    return (Path(__file__).resolve().parent.parent
            / "src" / "trinity_local" / "launchpad_template.py").read_text()


def _start_operation_polling_body(src: str) -> str:
    """Isolate the startOperationPolling method body so the assertions can't
    be satisfied by an unrelated occurrence elsewhere in the template."""
    start = src.find("startOperationPolling(token,")
    assert start != -1, "startOperationPolling must exist in launchpad_template"
    # The next method on the component object is launchCouncil().
    end = src.find("launchCouncil()", start)
    assert end != -1 and end > start, "could not bound startOperationPolling body"
    return src[start:end]


def test_launchpad_status_poller_has_giveup_counter():
    body = _start_operation_polling_body(_src())
    # Declaration site — without it the threshold check is an undeclared
    # ReferenceError at runtime (iter-18 mutation lesson).
    assert "let missingPollCount = 0;" in body, (
        "missingPollCount must be declared in startOperationPolling's closure"
    )
    assert "const MAX_MISSING_POLLS = 20;" in body, (
        "MAX_MISSING_POLLS must be declared as a const (20 ≈ 30s @ 1.5s); "
        "an orphan comparison against an undeclared constant would throw"
    )
    # Increment site — without it the threshold can never be reached.
    assert "missingPollCount++;" in body, (
        "missingPollCount must be incremented on each missing/404 status"
    )
    # Threshold site — the give-up comparison.
    assert "missingPollCount >= MAX_MISSING_POLLS" in body, (
        "the poller must compare the streak against the cap"
    )


def test_launchpad_giveup_stops_polling_and_surfaces_error():
    body = _start_operation_polling_body(_src())
    # Scope tightly to the give-up branch: from the threshold check to the
    # `return;` that closes the (!status) block. Bounding at that return keeps
    # the later failed/canceled branches (which also call stopOperationPolling)
    # from leaking in and falsely satisfying the assertion (mutation M3).
    thresh = body.find("missingPollCount >= MAX_MISSING_POLLS")
    assert thresh != -1
    giveup = body[thresh:body.find("return;", thresh)]
    assert "stopOperationPolling()" in giveup, (
        "give-up branch must stop the poll interval (else it keeps 404-spinning)"
    )
    assert "this.launchError" in giveup, (
        "give-up branch must surface launchError so the user isn't left "
        "watching a frozen 'running' card"
    )
    assert "status: 'failed'" in giveup, (
        "the stuck operation should be normalized to a failed state"
    )


def test_fresh_launch_skips_synchronous_first_probe():
    """A FRESH launch must NOT fire the synchronous first status probe. At
    beginOperation() time the dispatch hasn't run yet, so council_status_<token>.js
    provably does not exist — the immediate <script> probe is a guaranteed 404 on
    the user's very FIRST action (net::ERR_FILE_NOT_FOUND, found 2026-06-06
    dogfooding the cold-home launch; a no-route dispatch is then rolled back before
    the 1.5s interval ticks, so the interval never 404s either). The RESUME path
    (init() re-attaching to an in-flight council whose status file already exists)
    KEEPS the immediate probe via the default. Mutation: drop the `false` arg on
    beginOperation's call → the t=0 404 returns; drop the `if (immediate)` gate →
    every fresh launch probes synchronously again."""
    src = _src()
    # The opt-in param with a resume-preserving default (true).
    assert "startOperationPolling(token, immediate = true)" in src, (
        "startOperationPolling must take an `immediate` flag defaulting to true"
    )
    # The synchronous first probe is gated on the flag (template uses {{ for {).
    body = _start_operation_polling_body(src)
    assert "if (immediate) {{" in body, (
        "the synchronous first check() must be guarded by `if (immediate)`"
    )
    # Fresh launch (beginOperation) opts OUT — status can't exist pre-dispatch.
    assert "this.startOperationPolling(operation.statusToken, false)" in src, (
        "beginOperation (fresh launch) must skip the immediate probe (pass false)"
    )
    # Resume (init) keeps the immediate probe (no explicit false → default true).
    assert "this.startOperationPolling(this.operation.statusToken)" in src, (
        "the resume path must keep the immediate probe (default immediate=true)"
    )


def test_launchpad_status_poller_resets_counter_on_success():
    """A real 'running' status mid-stream must reset the miss streak — a
    council that takes a while to write its first status frame must not be
    declared dead just because the first few probes 404'd."""
    body = _start_operation_polling_body(_src())
    # The reset assignment must exist and sit BEFORE the running-branch so a
    # genuine status frame clears the streak.
    assert "missingPollCount = 0;" in body
    running_idx = body.find("status.status === 'running'")
    reset_idx = body.find("missingPollCount = 0;")
    assert 0 < reset_idx < running_idx, (
        "the counter reset must run before the running-branch on a real status"
    )
