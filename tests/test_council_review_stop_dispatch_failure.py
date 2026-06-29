"""Regression: clicking Stop council on the live council page must surface
the dispatch failure when no extension is installed — not silently
swallow it via an empty arrow function.

Symptom this prevents: user clicks Stop on a running council, dispatcher
fails (no extension in browser, native host missing, etc.), council
keeps polling, no error indication. The stuck-token poll-counter
(commit 6d6052b) is the secondary safety net 12s later, but the
primary path needs explicit feedback.
"""
from __future__ import annotations

from pathlib import Path


def _src():
    return (Path(__file__).resolve().parent.parent
            / "src" / "trinity_local" / "council_review.py").read_text()


def test_stop_council_handler_surfaces_failure_via_chain_error():
    src = _src()
    start = src.index("stopCouncil()")
    block = src[start:start + 2500]
    # The empty arrow function must be gone.
    assert "onResult: () =>" not in block, (
        "stopCouncil must not swallow dispatch failures via an empty handler"
    )
    # The new branch routes failure to chainError (the persistent banner)
    # through the shared, reason-accurate helper — NOT a hardcoded "is the
    # extension installed?" (which was wrong when it WAS installed; founder
    # report 2026-05-31, the council page was rejected as an extension sender).
    assert "this.chainError = dispatchErrorMessage(r)" in block, (
        "stopCouncil onResult must set chainError via dispatchErrorMessage(r)"
    )


def test_no_dispatcher_branch_also_surfaces_chain_error():
    src = _src()
    start = src.index("stopCouncil()")
    block = src[start:start + 2500]
    # When the dispatcher itself isn't loaded, the user gets a distinct
    # error pointing at the launchpad reload remedy.
    assert "Trinity dispatcher not loaded" in block
