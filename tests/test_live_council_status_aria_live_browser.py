"""Browser guard: the LIVE COUNCIL page's PRIMARY dynamic status must live inside
an aria-live region (WCAG 4.1.3 Status Messages, Level AA).

THE DEFECT (found 2026-06-18 driving the live page in the UX sweep, Iter 98): the
live council page updates its status VISUALLY as the poll advances — the spinner +
"Council running", the rotating ".status-message" ("Synthesizing the strongest
answer…"), the "Stopping…" ack, "Council failed/stopped", and on completion the
"🏆 … — the answer you'd have picked" winner verdict. NONE of it sat in an aria-live /
role=status / role=alert region (``all_live_regions`` was ``[]`` on both a running
and a completed council). A sighted user sees the spinner stop and the verdict appear;
a screen-reader user was told NOTHING that the council finished, a round transitioned,
or who won. (The only existing role=alert regions are the chainError FAILURE banner,
absent on a normal run.)

THE FIX (council_review.py + design_system.py):
  • a PERSISTENT visually-hidden ``.sr-only`` ``role=status aria-live=polite`` mirror
    at the top of the live ``<main>`` whose text is the ``liveAnnouncement`` getter —
    present from first render (so the region is reliably announced) and mutating on
    every transition (running → complete → failed/stopped). This is what carries the
    running→completed transition, which the visible cards can't (the .launch-status
    card is destroyed and the .winner-verdict created by v-if at that moment).
  • role=status/aria-live=polite on the visible status containers too (.launch-status,
    .winner-verdict, .chain-loading) for correct semantics + in-card text rotation.

This guard drives a RUNNING council and a COMPLETED council and asserts:
  (1) running: the rotating ".status-message" sits inside a role=status/aria-live
      region (an ancestor carries it), AND the persistent mirror announces "running";
  (2) completed: the ".winner-verdict" sits inside a live region, AND the persistent
      mirror announces "complete" + the winner.
The persistent-mirror text assertions are the load-bearing ones — they prove the
running→complete TRANSITION is spoken, not just that a region exists.

Serves an isolated, PII-free synthetic council over http (file:// can't carry the
``?status_token=`` query reliably) and reads the RENDERED DOM. Slow-marked; skips
without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _running_status(token: str) -> dict:
    """Council still RUNNING: top-level status=running, synthesis running. Renders
    the .launch-status card with the spinner + the rotating .status-message line."""
    return {
        "status": "running",
        "status_token": token,
        "task_text": "Should I ship the aria-live fix?",
        "council_id": "c_run_arialive",
        "memberOrder": ["claude", "antigravity", "codex"],
        "members": {
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "Claude done.", "response_html": "<p>Claude done.</p>"},
            "antigravity": {"status": "running", "model": "gemini-3.1-pro"},
            "codex": {"status": "running", "model": "gpt-5.5"},
        },
        "synthesis": {"status": "running"},
        "metadata": {"chairman_provider": "claude", "council_id": "c_run_arialive",
                     "members": ["claude", "antigravity", "codex"]},
    }


def _completed_status(token: str) -> dict:
    """A completed 2-member council — the .winner-verdict appears."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Should I ship the aria-live fix?",
        "council_id": "c_done_arialive",
        "memberOrder": ["claude", "antigravity"],
        "members": {
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "Claude.", "response_html": "<p>Claude.</p>"},
            "antigravity": {"status": "done", "model": "gemini-3.1-pro",
                            "response_text": "Gemini.", "response_html": "<p>Gemini.</p>"},
        },
        "synthesis": {
            "status": "done",
            "response_text": "Synthesis.", "response_html": "<p>Synthesis.</p>",
            "routing_label": {"winner": "claude", "confidence": "high",
                              "agreed_claims": ["X"], "disagreed_claims": []},
        },
        "metadata": {"chairman_provider": "claude", "council_id": "c_done_arialive",
                     "members": ["claude", "antigravity"]},
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


# Walk a selector's self-or-ancestor chain for any aria-live / role=status / role=alert.
_LIVE_ANCESTOR = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return {found: false};
  let node = el;
  while (node && node !== document.documentElement) {
    const role = (node.getAttribute && node.getAttribute('role')) || '';
    const live = (node.getAttribute && node.getAttribute('aria-live')) || '';
    if (live === 'polite' || live === 'assertive' || role === 'status' || role === 'alert') {
      return {found: true, inLiveRegion: true, tag: node.tagName, role, live};
    }
    node = node.parentElement;
  }
  return {found: true, inLiveRegion: false};
}
"""


def _probe(port, token, selector):
    """Returns (status_ancestor_dict, persistent_mirror_text)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1100}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            ancestor = page.evaluate(_LIVE_ANCESTOR, selector)
            mirror = page.evaluate(
                "() => { const e = document.querySelector('.sr-only[role=status][aria-live]');"
                " return e ? e.textContent.trim() : null; }"
            )
            return ancestor, mirror
        finally:
            browser.close()


def test_running_council_status_message_in_live_region(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_run_arialive"
    _seed(tmp_path, monkeypatch, _running_status(token))
    httpd, port = _serve(tmp_path)
    try:
        ancestor, mirror = _probe(port, token, ".status-message")
    finally:
        httpd.shutdown()

    assert ancestor.get("found"), (
        "the running council did not render the rotating .status-message at all — "
        "the seed/poll shape changed; fix the fixture before trusting the a11y assert"
    )
    assert ancestor.get("inLiveRegion"), (
        "the rotating live status line (.status-message, e.g. 'Synthesizing the "
        "strongest answer…') on a RUNNING council is NOT inside any aria-live / "
        "role=status region — a screen-reader user hears nothing as the poll advances "
        "(WCAG 4.1.3 Status Messages). It must sit in a role=status/aria-live container."
    )
    assert mirror is not None, (
        "the persistent visually-hidden .sr-only[role=status][aria-live] mirror is "
        "MISSING from the live page — it must exist from first render so a screen "
        "reader reliably announces every poll transition (running → complete → fail)."
    )
    assert "running" in (mirror or "").lower(), (
        "the persistent aria-live mirror does not announce the council is RUNNING; "
        f"its text was {mirror!r} (expected something like 'Council round 1 running. …')."
    )


def test_completed_council_verdict_in_live_region_and_mirror_announces_completion(
    tmp_path, monkeypatch
):
    """The running→COMPLETE transition is the load-bearing case: the .launch-status
    live region is destroyed on completion and the .winner-verdict is created, so the
    persistent mirror is what carries 'the council finished' to a screen reader."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_done_arialive"
    _seed(tmp_path, monkeypatch, _completed_status(token))
    httpd, port = _serve(tmp_path)
    try:
        ancestor, mirror = _probe(port, token, ".winner-verdict")
    finally:
        httpd.shutdown()

    assert ancestor.get("found"), (
        "the completed council did not render a .winner-verdict — fixture drift; "
        "fix the seed before trusting the a11y assert"
    )
    assert ancestor.get("inLiveRegion"), (
        "the completion WINNER VERDICT (🏆 … — the answer you'd have picked) is NOT "
        "inside any aria-live / role=status region — a screen-reader user is never "
        "told the council COMPLETED or who won (WCAG 4.1.3 Status Messages)."
    )
    assert mirror is not None, (
        "the persistent .sr-only[role=status][aria-live] mirror is MISSING on a "
        "completed council — the running→complete transition would go unannounced."
    )
    low = (mirror or "").lower()
    assert "complete" in low, (
        "the persistent aria-live mirror does not announce the council COMPLETED; its "
        f"text was {mirror!r} (expected 'Council round 1 complete. …'). This is the "
        "transition a sighted user sees (spinner stops, verdict appears) and a screen "
        "reader otherwise misses entirely."
    )
    assert "claude" in low, (
        "the completion announcement does not name the winner; mirror text was "
        f"{mirror!r} (expected it to include the winning model brand)."
    )
