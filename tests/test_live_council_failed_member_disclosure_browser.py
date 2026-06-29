"""Browser guard: a PARTIAL council on the LIVE POLL path (``?status_token=``) —
where one member FAILED but two others responded — must fire the honest-degradation
disclosure note (``⚠ N provider(s) attempted but failed … this synthesis is over the
M that responded``), the same #238 lineage the eval card's ``excluded_runs`` and the
walkover's "Sole entrant" carry.

THE BUG (found 2026-06-18 driving the live page with a seeded 2-of-3 council in the
UX sweep): the disclosure note read its count ONLY from
``seg.runState.metadata.failed_members``. That array is written into the persisted
OUTCOME (so the ``?council_id=`` post-hoc page is correct) but is NEVER written into
the live STATUS sidecar — ``update_member_failure`` only flips the dead member to
``status:'failed'`` in the members map and leaves ``metadata`` untouched. So on the
live poll path a 2-of-3 council rendered a VISIBLE "Failed" badge row for the dead
provider, yet the aggregate "⚠ 1 provider attempted but failed" note SILENTLY
VANISHED — the page contradicted what it had just shown one row up.

THE FIX: ``failedMembersFor`` now takes the MAX of
``metadata.failed_members.length`` (the outcome channel) and the count of member
rows with ``statusClass === 'failed'`` (the poll channel) — recovering the count from
the SAME source the row badge already trusts. MAX (not sum) because the two channels
are mutually exclusive by construction, so it can't double-count.

This guard drives BOTH:
  (1) a 2-of-3 council via the POLL path WITHOUT metadata.failed_members (the exact
      live shape ``update_member_failure`` produces) — the note MUST fire and read
      "over the 2 that responded";
  (2) a clean 2-of-2 council — the note must NOT fire (it's not a partial council),
      proving the gate is the failure condition, not an always-on banner.

Serves an isolated, PII-free synthetic council over http (file:// can't carry the
``?status_token=`` query reliably) and reads the RENDERED DOM. Slow-marked; skips
without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _partial_2of3_poll_status(token: str) -> dict:
    """A completed 3-member council on the POLL path: claude + antigravity
    responded, codex FAILED. This is exactly what update_member_failure writes:
    the codex member is RETAINED in the members map with status:'failed', and
    metadata has NO failed_members key (the runner only writes that into the
    persisted outcome, never the status sidecar)."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Should I ship the partial council?",
        "council_id": "c_partial_2of3",
        "memberOrder": ["claude", "antigravity", "codex"],
        "members": {
            "claude": {
                "status": "done", "model": "claude-opus-4-8",
                "response_text": "Claude's answer.", "response_html": "<p>Claude's answer.</p>",
            },
            "antigravity": {
                "status": "done", "model": "gemini-3.1-pro",
                "response_text": "Gemini's answer.", "response_html": "<p>Gemini's answer.</p>",
            },
            "codex": {
                "status": "failed", "model": "gpt-5.5",
                "reasoning_summary": "codex exec exited with code 1: quota exceeded.",
            },
        },
        "synthesis": {
            "status": "done",
            "response_text": "Synthesis over two answers.",
            "response_html": "<p>Synthesis over two answers.</p>",
            "routing_label": {
                "winner": "claude", "confidence": "high",
                "agreed_claims": ["Both agree on X"],
                "disagreed_claims": [],
            },
        },
        # NB: NO failed_members in metadata — this is the live poll shape.
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_partial_2of3",
            "members": ["claude", "antigravity", "codex"],
        },
    }


def _clean_2of2_status(token: str) -> dict:
    """A clean 2-member council — no failure. The disclosure note must NOT fire
    (negative control: the note is gated on a real casualty, not always-on)."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Clean two-member council.",
        "council_id": "c_clean_2of2",
        "memberOrder": ["claude", "antigravity"],
        "members": {
            "claude": {
                "status": "done", "model": "claude-opus-4-8",
                "response_text": "Claude's answer.", "response_html": "<p>Claude's answer.</p>",
            },
            "antigravity": {
                "status": "done", "model": "gemini-3.1-pro",
                "response_text": "Gemini's answer.", "response_html": "<p>Gemini's answer.</p>",
            },
        },
        "synthesis": {
            "status": "done",
            "response_text": "Synthesis over two answers.",
            "response_html": "<p>Synthesis over two answers.</p>",
            "routing_label": {
                "winner": "claude", "confidence": "high",
                "agreed_claims": ["Both agree on X"],
                "disagreed_claims": [],
            },
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_clean_2of2",
            "members": ["claude", "antigravity"],
        },
    }


def _total_failure_status(token: str) -> dict:
    """A council where EVERY member failed (0 responders) on the POLL path.
    update_member_failure flips both members to status:'failed' in the map and
    leaves metadata untouched, so failedMembersFor == 2 and respondedMembersFor
    == 0. This is the degenerate input that exposed the self-contradicting
    'this synthesis is over the 0 that responded' copy (there is NO synthesis when
    nobody answered)."""
    return {
        "status": "failed",
        "status_token": token,
        "task_text": "What is the best caching strategy here?",
        "council_id": "c_total_fail",
        "memberOrder": ["claude", "codex"],
        "members": {
            "claude": {"status": "failed", "model": "claude-opus-4-8",
                       "reasoning_summary": "claude -p exited with code 1: timed out."},
            "codex": {"status": "failed", "model": "gpt-5.5",
                      "reasoning_summary": "codex exec exited with code 1: timed out."},
        },
        "synthesis": {"status": "error"},
        "error": "All members failed to respond.",
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_total_fail",
            "members": ["claude", "codex"],
        },
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _drive(port, token):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1200}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            body = page.evaluate("() => document.body.innerText")
            rows = page.evaluate(
                "() => Array.from(document.querySelectorAll('.provider-status-badge'))"
                ".map(b => b.textContent.trim())"
            )
            return body, rows
        finally:
            browser.close()


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


def test_partial_council_poll_path_fires_failure_disclosure(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_partial_poll"
    _seed(tmp_path, monkeypatch, _partial_2of3_poll_status(token))
    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    # The page itself proves a member died: a visible "Failed" badge row.
    assert "Failed" in badges, (
        f"expected a 'Failed' member badge on a 2-of-3 council, got badges {badges!r}"
    )
    # THE BUG: the aggregate disclosure note silently vanished on the poll path
    # because it read metadata.failed_members (absent in the status sidecar) and
    # ignored the failed member ROW the page had already shown.
    assert "attempted but failed" in body, (
        "the live POLL-path 2-of-3 council rendered a visible 'Failed' member row but "
        "SUPPRESSED the '⚠ N provider attempted but failed' disclosure note — the "
        "honest-degradation note contradicted what the same page showed, because "
        "failedMembersFor only read metadata.failed_members (never written into the "
        "status sidecar) instead of also counting failed member rows."
    )
    # The count must be coherent: 1 failed, 2 responded.
    m = re.search(r"this synthesis is over the (\d+) that responded", body)
    assert m and m.group(1) == "2", (
        "the disclosure note must read 'this synthesis is over the 2 that responded' "
        f"on a 2-of-3 council; body excerpt: {body[:400]!r}"
    )
    assert "1 provider attempted but failed" in body, (
        "the disclosure must count exactly 1 failed provider (not double-counted via "
        f"MAX); body excerpt: {body[:400]!r}"
    )


def test_total_failure_does_not_claim_synthesis_over_zero(tmp_path, monkeypatch):
    """TOTAL failure (every member failed, 0 responders): the disclosure must NOT
    read 'this synthesis is over the 0 that responded' — a flat self-contradiction
    (it asserts a synthesis exists AND that nobody answered it), sitting directly
    under the 'Council failed — all members failed to respond' banner. The honest
    branch says 'there's no synthesis to show'.

    Found 2026-06-21 driving the live page with a seeded all-failed council in the
    UX sweep: respondedMembersFor == 0 but the over-N copy still fired because its
    only gate was failedMembersFor > 0."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_total_fail"
    _seed(tmp_path, monkeypatch, _total_failure_status(token))
    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    # Sanity: this really is a total failure — two Failed member rows.
    assert badges.count("Failed") == 2, (
        f"expected two 'Failed' member badges on an all-failed council, got {badges!r}"
    )
    # THE BUG: the over-N copy is degenerate when 0 responded.
    assert "this synthesis is over the 0 that responded" not in body, (
        "the all-members-failed council rendered the self-contradicting "
        "'this synthesis is over the 0 that responded' disclosure — there is NO "
        "synthesis when 0 members responded, and the 'Council failed' banner already "
        "says so one card up. The over-N wording must gate on respondedMembersFor > 0."
    )
    # And it must still TELL the user honestly (not just drop the line silently).
    assert "no synthesis to show" in body, (
        "the all-failed council must render the honest 'there's no synthesis to show' "
        f"disclosure; body excerpt: {body[:500]!r}"
    )


def test_clean_council_does_not_fire_failure_disclosure(tmp_path, monkeypatch):
    """Negative control: a clean 2-of-2 council (no casualty) must NOT render the
    disclosure note — otherwise a passing partial test could be an always-on banner."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_clean_poll"
    _seed(tmp_path, monkeypatch, _clean_2of2_status(token))
    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    assert "Failed" not in badges, f"clean council unexpectedly shows a Failed badge: {badges!r}"
    assert "attempted but failed" not in body, (
        "a clean 2-of-2 council rendered the '⚠ provider attempted but failed' "
        "disclosure note — the note must gate on a real casualty (a failed member "
        "row OR metadata.failed_members), not fire on every council."
    )
