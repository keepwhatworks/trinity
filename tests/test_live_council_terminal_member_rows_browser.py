"""Browser guard: on the LIVE POLL path (``?status_token=``), a TERMINAL council —
one whose status flips to ``failed`` or ``canceled`` — must NOT leave the "Full
Responses" member rows reading "Queued · Queued.". That's a flat contradiction:
the banner says "Council failed" / "Council stopped" while three member cards below
it still say "QUEUED" as if the council were running.

THE BUG (found 2026-06-19 driving the live page with a seeded FAILED single council
in the UX sweep — a state no prior cell had exercised): the live poller's
``failed``/``canceled`` branches set ``failed:true`` / ``canceled:true`` + the
``errorText`` banner but — unlike the ``running``/``completed`` branches — NEVER
folded the terminal status payload INTO ``runState`` (no ``normalizeStatus`` call).
So the segment's ``runState.members`` kept the STALE pre-failure map that
``makeSegment`` seeds with every member ``status:'pending'``. ``memberRowsFor`` then
rendered three "QUEUED · Queued." rows directly below a "Council failed — all members
failed to respond" banner. Even after folding the payload in, a council that died
EARLY (the ``_coerce_stale_running_status`` runner-exit shape) legitimately carries
``pending`` members in the payload — so "Queued." on a DEAD council is still a lie
("it's still working"). ``memberRowsFor`` now coerces a ``pending``/``running``
member on a TERMINAL segment to "Didn't run" (failed) / "Stopped" (canceled).

THE FIX (council_review.py):
  * the ``failed``/``canceled`` poll branches now call
    ``normalizeStatus(status, ref.runState)`` like the live branches do; and
  * ``memberRowsFor`` relabels a still-``pending``/``running`` member on a terminal
    segment as "Didn't run" / "Stopped" (muted badge, NOT red — they didn't error,
    so they stay OUT of ``failedMembersFor``'s count).

This guard drives THREE terminal shapes on the poll path and reads the RENDERED DOM:
  (A) all-failed     → every row reads "Failed", none "Queued";
  (B) early-death     → every row reads "Didn't run", none "Queued";
  (C) canceled mid-run → the one ``done`` member keeps its real answer, the
      never-ran members read "Stopped", none "Queued";
and asserts NO terminal segment fires the partial-completed "attempted but failed"
disclosure (it's for a completed council over its responders, not a dead one).

Serves an isolated, PII-free synthetic council over http (file:// can't carry the
``?status_token=`` query reliably) and reads the rendered DOM. Slow-marked; skips
without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_MEMBERS = ["claude", "codex", "antigravity"]


def _all_failed_status(token: str) -> dict:
    """Every member dispatched and errored; overall status failed."""
    return {
        "status": "failed",
        "status_token": token,
        "task_text": "Should I migrate the build from Make to Bazel?",
        "error": "All members failed to respond (dispatch error: provider quota exhausted).",
        "memberOrder": _MEMBERS,
        "members": {p: {"status": "failed", "reasoning_summary": "Provider returned an error."} for p in _MEMBERS},
        "synthesis": {"status": "failed"},
    }


def _early_death_status(token: str) -> dict:
    """The _coerce_stale_running_status runner-exit shape: the council died before
    any member started, so every member is still 'pending' in the payload while the
    overall status is 'failed'."""
    return {
        "status": "failed",
        "status_token": token,
        "task_text": "Should I migrate the build from Make to Bazel?",
        "error": "Council runner exited before completion.",
        "memberOrder": _MEMBERS,
        "members": {p: {"status": "pending"} for p in _MEMBERS},
        "synthesis": {"status": "pending"},
    }


def _canceled_midrun_status(token: str) -> dict:
    """Stopped after one member landed: claude done, the other two never ran."""
    return {
        "status": "canceled",
        "status_token": token,
        "task_text": "Should I migrate the build from Make to Bazel?",
        "error": "Council stopped.",
        "memberOrder": _MEMBERS,
        "members": {
            "claude": {"status": "done", "response_text": "Bazel pays off above ~30 modules; below that Make is fine.",
                       "response_html": "<p>Bazel pays off above ~30 modules; below that Make is fine.</p>"},
            "codex": {"status": "pending"},
            "antigravity": {"status": "pending"},
        },
        "synthesis": {"status": "pending"},
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed(tmp_path, monkeypatch, status):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    token = status["status_token"]
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(status)};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(sidecar, encoding="utf-8")


def _drive(port, token):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 393, "height": 1200}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html"
                f"?status_token={token}&members=claude,codex,antigravity"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            body = page.evaluate("() => document.body.innerText")
            badges = page.evaluate(
                "() => Array.from(document.querySelectorAll('.provider-status-row "
                ".provider-status-badge')).map(b => b.textContent.trim())"
            )
            return body, badges
        finally:
            browser.close()


def test_all_failed_council_member_rows_read_failed_not_queued(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_term_allfail"
    _seed(tmp_path, monkeypatch, _all_failed_status(token))
    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    assert "Council failed" in body, f"expected the 'Council failed' banner; body: {body[:300]!r}"
    # THE BUG: stale 'Queued' member rows under a 'Council failed' banner.
    assert "Queued" not in badges, (
        "an all-failed council rendered stale 'Queued' member rows under the "
        "'Council failed' banner — the poller never folded the terminal status "
        f"payload into runState, so the rows kept the seeded pending map. badges={badges!r}"
    )
    assert badges and all(b == "Failed" for b in badges), (
        f"every member of an all-failed council should read 'Failed', got {badges!r}"
    )


def test_early_death_council_member_rows_read_didnt_run_not_queued(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_term_earlydeath"
    _seed(tmp_path, monkeypatch, _early_death_status(token))
    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    assert "Council failed" in body, f"expected the 'Council failed' banner; body: {body[:300]!r}"
    # The runner-exit shape legitimately carries pending members in the payload —
    # so even after folding it in, 'Queued.' on a dead council is still a lie.
    assert "Queued" not in badges, (
        "a council that died before any member started (runner-exit / early-death) "
        "rendered stale 'Queued' rows under 'Council failed' — a never-ran member on "
        f"a terminal segment must read 'Didn't run', not 'Queued'. badges={badges!r}"
    )
    assert badges and all(b == "Didn't run" for b in badges), (
        f"every never-ran member of a failed council should read \"Didn't run\", got {badges!r}"
    )
    assert "Didn't run" in body and "the council failed before this provider responded" in body, (
        f"the row detail must explain the never-ran member honestly; body: {body[:500]!r}"
    )
    # A never-ran provider is NOT an attempted-and-failed casualty: the partial
    # "attempted but failed" disclosure must NOT fire on a terminal segment.
    assert "attempted but failed" not in body, (
        "a never-ran member on a FAILED council was miscounted as an "
        "'attempted but failed' casualty — that disclosure is for a COMPLETED "
        f"council over its responders, not a dead one. body: {body[:500]!r}"
    )


def test_canceled_council_keeps_answer_marks_others_stopped_not_queued(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_term_canceled"
    _seed(tmp_path, monkeypatch, _canceled_midrun_status(token))
    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    assert "Council stopped" in body, f"expected the 'Council stopped' banner; body: {body[:300]!r}"
    # The one member that landed before the stop keeps its real answer.
    assert "Bazel pays off above ~30 modules" in body, (
        f"a canceled council must preserve the answer that DID land; body: {body[:500]!r}"
    )
    # The never-ran members read 'Stopped', never 'Queued'.
    assert "Queued" not in badges, (
        "a canceled council rendered stale 'Queued' rows for members that never ran "
        f"under the 'Council stopped' banner. badges={badges!r}"
    )
    assert badges.count("Stopped") == 2, (
        f"the two never-ran members of a canceled council should read 'Stopped', got {badges!r}"
    )
    assert "attempted but failed" not in body, (
        "a stopped (never-ran) member was miscounted as an 'attempted but failed' "
        f"casualty on a canceled council. body: {body[:500]!r}"
    )
