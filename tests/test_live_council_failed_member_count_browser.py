"""Browser guard: a COMPLETED council with one FAILED member must report the
honest RESPONDER count in the partial-failure banner — not the member-ROW count.

The live council page renders a disclosure banner when a member was dispatched but
failed:

    "⚠ N provider(s) attempted but failed and was/were excluded — this synthesis is
     over the M that responded."

There are two render paths that feed ``memberRowsFor``:

* ``?council_id=`` (post-hoc) — ``outcomeToRunState`` builds the members map ONLY
  from ``outcome.member_results`` (the runner EXCLUDES failures), so the failed
  member has no row and ``memberRowsFor().length`` already equals the responders.
* ``?status_token=`` (the live poll path EVERY launchpad-launched council takes) —
  ``normalizeStatus`` uses ``status.members``, which RETAINS the failed member
  (``update_member_failure`` writes ``status:'failed'``). At completion
  (``finalize_council_run_state`` sets ``status:'completed'``) the members map
  still holds the casualty's row. NOTE: the runner does NOT write
  ``metadata.failed_members`` into the status sidecar (it only writes it into the
  persisted OUTCOME) — this test seeds it for the responder-count assertion, but
  the absent-metadata case (the actual live shape) is covered by
  ``test_live_council_failed_member_disclosure_browser.py``, which proves the
  disclosure note still fires by counting failed member ROWS.

The banner used to read ``memberRowsFor(seg).length`` for "the M that responded".
On the poll path that COUNTED THE FAILED ROW, so a 2-of-3 council rendered
"1 provider attempted but failed and was excluded — this synthesis is over the
**3** that responded" — a self-contradicting count (excluding 1 of 3 leaves 2).
Found 2026-06-17 driving the live RUNNING/completed-via-poll grid in the UX sweep,
reproduced through the runner's exact ``finalize_council_run_state`` write path.

The fix added ``respondedMembersFor`` (count of ``status:'done'`` rows) and the
banner now reads that. This guard pins the poll path to the honest "2" and pins
the failed member's row to still render (honest disclosure, not hidden). It serves
an isolated, PII-free synthetic council over http (file:// can't carry the
``?status_token=`` query reliably). Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


_TOKEN = "tok_failed_member_count"

# A COMPLETED status exactly as the runner's finalize_council_run_state leaves it:
# status=completed, the members map RETAINS the failed member (codex), and
# metadata.failed_members records the casualty. 2 of 3 actually responded.
_COMPLETED_STATUS = {
    "status": "completed",
    "status_token": _TOKEN,
    "task_text": "Ship the in-panel reframe before launch?",
    "council_id": "c_failed_member_count",
    "memberOrder": ["claude", "codex", "antigravity"],
    "members": {
        "claude": {
            "status": "done",
            "model": "claude-opus-4-8",
            "response_text": "Claude responded.",
            "response_html": "<p>Claude responded.</p>",
        },
        # The casualty — retained in the poll-path members map with status:failed.
        "codex": {
            "status": "failed",
            "model": "gpt-5.5",
            "reasoning_summary": "Rate limited (429).",
        },
        "antigravity": {
            "status": "done",
            "model": "gemini-3.1-pro",
            "response_text": "Gemini responded.",
            "response_html": "<p>Gemini responded.</p>",
        },
    },
    "synthesis": {
        "status": "done",
        "response_text": "Synthesis verdict.",
        "response_html": "<p>Synthesis verdict.</p>",
        "routing_label": {"winner": "claude", "runner_up": "antigravity", "confidence": "high"},
    },
    "metadata": {
        "chairman_provider": "claude",
        "council_id": "c_failed_member_count",
        "members": ["claude", "codex", "antigravity"],
        "failed_members": ["codex"],
    },
}


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_failed_member_banner_reports_responder_count_not_row_count(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
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
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(_TOKEN)}] = "
        f"{json.dumps(_COMPLETED_STATUS)};\n"
    )
    (status_dir / f"council_status_{_TOKEN}.js").write_text(sidecar, encoding="utf-8")

    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={_TOKEN}"
                )
                page.wait_for_timeout(2600)
                assert not errs, f"JS pageerrors: {errs[:3]}"
                banner = page.evaluate(
                    "() => { const e = document.querySelector('.meta.status-error');"
                    " return e ? e.textContent.trim() : ''; }"
                )
                row_count = page.evaluate(
                    "() => document.querySelectorAll('.provider-status-row').length"
                )
                failed_badges = page.evaluate(
                    "() => [...document.querySelectorAll('.provider-status-badge')]"
                    ".filter(b => (b.textContent || '').trim() === 'Failed').length"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert banner, "the partial-failure disclosure banner did not render — guard would be vacuous"
    # The casualty's row is still shown (honest disclosure, not hidden) — 3 rows.
    assert row_count == 3, f"expected 3 member rows (2 done + 1 failed), got {row_count}"
    assert failed_badges == 1, f"expected 1 'Failed' badge on the casualty row, got {failed_badges}"
    # THE BUG: the banner must say "over the 2 that responded" — the RESPONDER
    # count (status:'done'), NOT "the 3 that responded" (the member-ROW count,
    # which includes the failed row on the poll path). Excluding 1 of 3 leaves 2.
    assert "over the 2 that responded" in banner, (
        "the partial-failure banner counted the FAILED member's row as a responder "
        "(read 'this synthesis is over the 3 that responded' on a 2-of-3 council — a "
        f"self-contradicting count: 1 excluded yet all 3 'responded'). Banner: {banner!r}"
    )
    assert "over the 3 that responded" not in banner, (
        "the banner still counts the failed member among the responders: "
        f"{banner!r}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
