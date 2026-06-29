"""Browser guard: a multi-round chain thread must number its segments
Round 1 / Round 2 / Round 3 by POSITION — never let a degenerate
``metadata.round_number`` collapse the whole chain to "Round 1".

THE BUG (found 2026-06-18 driving the live ?thread_id= page in the UX sweep):
real chained councils carry ``round_number == 1`` on EVERY segment's outcome
metadata AND status sidecar (a long-standing manifest-writer bug the comment at
``council_review.py`` line ~2081 documents — "a 5-round thread mis-render as
'Round 1 ×5'"). The MANIFEST-build path already defends against this with
``Math.max(entry.round_number || 0, idx + 1)`` (position floor). But the two
SEGMENT-COMPLETION paths threw that away and trusted the degenerate field FIRST:

  • ``_loadOutcomeIntoSegment`` (the ?thread_id= path where every segment is
    already completed) → ``roundNumber: rs.metadata?.round_number || current.roundNumber``
    → ``1 || 3`` → **1**, clobbering the correctly position-floored Round 3.
  • the poll-completion branch (a live-streaming last round flips to completed) →
    ``roundNumber: (status.metadata && status.metadata.round_number) || 1`` → **1**,
    and the follow-on ``_loadOutcomeIntoSegment`` then sees the clobbered 1 as its
    floor, so the floor fix alone wouldn't save it.

So a 3-round chain rendered "Round 1 ×3" the instant each segment loaded — the
exact symptom the build path was written to prevent, present in the two sibling
completion paths.

THE FIX: both completion sites now mirror the build-path discipline —
``Math.max(field || 0, existingPositionFlooredRoundNumber || 1)`` — taking the
metadata field ONLY when it runs AHEAD of the position floor (a gap, e.g. a
deleted middle round), never when it's behind.

This guard drives the REAL ``?thread_id=`` page with a 3-segment manifest where
EVERY outcome carries the degenerate ``round_number == 1`` and reads the RENDERED
segment-divider headings. Both an all-completed thread (the outcome-load path) and
a thread whose last round completes via the poll path are exercised. Seeds an
isolated, PII-free synthetic thread over http and reads the rendered DOM.
Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _outcome(cid: str) -> dict:
    """An outcome JSON consumed by outcomeToRunState. The metadata.round_number is
    the DEGENERATE 1 that real chained councils all carry — the value that clobbered
    a correctly position-floored Round 3 back to Round 1."""
    return {
        "council_run_id": cid,
        "primary_provider": "claude",
        "primary_model": "claude-opus-4-8",
        "member_results": [
            {"provider": "claude", "model": "claude-opus-4-8",
             "output_text": "Claude answer.", "output_html": "<p>Claude answer.</p>"},
            {"provider": "antigravity", "model": "gemini-3.1-pro",
             "output_text": "Gemini answer.", "output_html": "<p>Gemini answer.</p>"},
        ],
        "routing_label": {"winner": "claude", "confidence": "high",
                          "agreed_claims": ["x"], "disagreed_claims": []},
        "synthesis_text": "Syn.", "synthesis_html": "<p>Syn.</p>",
        # round_number == 1 on EVERY segment — the manifest-writer bug.
        "metadata": {
            "council_id": cid, "round_number": 1, "task_text": "Should I ship?",
            "chairman_provider": "claude", "members": ["claude", "antigravity"],
            "synthesis": {
                "status": "done", "response_text": "Syn.", "response_html": "<p>Syn.</p>",
                "routing_label": {"winner": "claude", "confidence": "high",
                                  "agreed_claims": ["x"], "disagreed_claims": []},
            },
        },
    }


def _completed_status(token: str, cid: str) -> dict:
    """A live STATUS sidecar for a completed council whose metadata.round_number is
    the same degenerate 1 — drives the poll-completion path."""
    return {
        "status": "completed", "status_token": token, "task_text": "Should I ship?",
        "council_id": cid, "memberOrder": ["claude", "antigravity"],
        "members": {
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "Claude answer.", "response_html": "<p>Claude answer.</p>"},
            "antigravity": {"status": "done", "model": "gemini-3.1-pro",
                            "response_text": "Gemini answer.", "response_html": "<p>Gemini answer.</p>"},
        },
        "synthesis": {"status": "done", "response_text": "Syn.", "response_html": "<p>Syn.</p>",
                      "routing_label": {"winner": "claude", "confidence": "high",
                                        "agreed_claims": ["x"], "disagreed_claims": []}},
        "metadata": {"council_id": cid, "round_number": 1,
                     "members": ["claude", "antigravity"], "chairman_provider": "claude"},
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_base(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())
    rp = review_pages_dir()
    co = rp.parent / "council_outcomes"  # outcomeScriptBaseUrl = "../council_outcomes"
    co.mkdir(parents=True, exist_ok=True)
    return rp, co, portal_pages_dir()


def _write_outcome(co, cid):
    (co / f"{cid}.js").write_text(
        "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
        f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(cid)}] = {json.dumps(_outcome(cid))};\n",
        encoding="utf-8",
    )


def _write_manifest(co, thread_id, segments):
    manifest = {"thread_id": thread_id, "task_text": "Should I ship?", "segments": segments}
    (co / f"_thread_{thread_id}.js").write_text(
        "window.__TRINITY_COUNCIL_THREAD__ = window.__TRINITY_COUNCIL_THREAD__ || {};\n"
        f"window.__TRINITY_COUNCIL_THREAD__[{json.dumps(thread_id)}] = {json.dumps(manifest)};\n",
        encoding="utf-8",
    )


def _drive_headings(port, thread_id):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?thread_id={thread_id}"
            )
            page.wait_for_timeout(3200)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            headings = page.evaluate(
                "() => Array.from(document.querySelectorAll('.chain-segment-divider'))"
                ".map(d => d.innerText.replace(/\\n/g,' ').trim().toUpperCase())"
            )
            return headings
        finally:
            browser.close()


def test_chain_outcome_path_numbers_rounds_by_position(tmp_path, monkeypatch):
    """All three segments already completed (?thread_id= → _loadOutcomeIntoSegment).
    Every outcome carries round_number == 1; the headings MUST read Round 1/2/3."""
    pytest.importorskip("playwright.sync_api")
    rp, co, _ = _seed_base(tmp_path, monkeypatch)
    thread_id = "bundle_outcomepath"
    cids = ["c_op1", "c_op2", "c_op3"]
    for cid in cids:
        _write_outcome(co, cid)
    _write_manifest(co, thread_id,
                    [{"council_id": c, "round_number": 1, "running": False} for c in cids])

    httpd, port = _serve(rp.parent)
    try:
        headings = _drive_headings(port, thread_id)
    finally:
        httpd.shutdown()

    assert len(headings) == 3, f"expected 3 chain segments, got {headings!r}"
    for i, want in enumerate(("ROUND 1", "ROUND 2", "ROUND 3"), start=0):
        assert want in headings[i], (
            f"chain segment {i + 1} must read '{want}' — a 3-round chain whose outcomes "
            f"all carry the degenerate round_number==1 collapsed to 'Round 1 ×3' because "
            f"_loadOutcomeIntoSegment trusted metadata.round_number over the position "
            f"floor. headings={headings!r}"
        )


def test_chain_poll_path_numbers_last_round_by_position(tmp_path, monkeypatch):
    """The last round completes via the POLL path (running segment + status sidecar).
    The status metadata.round_number is the degenerate 1; the heading MUST still read
    Round 3, and the follow-on outcome load must not re-clobber it."""
    pytest.importorskip("playwright.sync_api")
    rp, co, portal = _seed_base(tmp_path, monkeypatch)
    status_dir = portal / "status"
    status_dir.mkdir(parents=True, exist_ok=True)

    thread_id = "bundle_pollpath"
    done = ["c_pp1", "c_pp2"]
    for cid in done:
        _write_outcome(co, cid)
    tok3, cid3 = "tok_pp3", "c_pp3"
    st = _completed_status(tok3, cid3)
    (status_dir / f"council_status_{tok3}.js").write_text(
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(tok3)}] = {json.dumps(st)};\n",
        encoding="utf-8",
    )
    _write_outcome(co, cid3)  # the poll-completion path re-loads the outcome (line ~2492)
    _write_manifest(co, thread_id, [
        {"council_id": "c_pp1", "round_number": 1, "running": False},
        {"council_id": "c_pp2", "round_number": 1, "running": False},
        {"status_token": tok3, "round_number": 1, "running": True},
    ])

    httpd, port = _serve(rp.parent)
    try:
        headings = _drive_headings(port, thread_id)
    finally:
        httpd.shutdown()

    assert len(headings) == 3, f"expected 3 chain segments, got {headings!r}"
    assert "ROUND 1" in headings[0] and "ROUND 2" in headings[1], (
        f"prior completed rounds mis-numbered: {headings!r}"
    )
    assert "ROUND 3" in headings[2], (
        "the live-streaming last round completed via the POLL path and the heading "
        "collapsed to 'Round 1' — the poll-completion branch trusted "
        "status.metadata.round_number (degenerate 1) over the position floor (and the "
        f"follow-on outcome load then saw the clobbered 1 as its floor). headings={headings!r}"
    )
