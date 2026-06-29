"""Browser guard: a COMPLETED council must not flip to "Council failed" when the
secondary outcome-refresh fetch 404s.

The live council page polls a ``status_token``; when it sees ``status==='completed'``
it renders the FULL result straight from the status payload (synthesis prose +
routing_label + member answers) and flips the segment to completed. It THEN fires a
secondary ``_loadOutcomeIntoSegment`` to re-hydrate from the canonical
``council_outcomes/<id>.json`` — a SEPARATE file written by a different code path that
(a) races the status flip, (b) is pruned after 14d, (c) can transiently 404. The old
code treated that 404 as a council failure and clobbered the good completed segment with
``failed:true / "Could not load council outcome."`` — so a SUCCESSFUL council rendered a
red "Council failed" banner ABOVE its own synthesis verdict (driven 2026-06-17, the
USEFULNESS sweep on the live-council panels).

This pins the fix: with a completed STATUS but a MISSING outcome file, the page shows the
synthesis + routing label and NEVER the "Council failed" / "Could not load council
outcome" text. The companion ``test_council_id_init_missing_outcome_still_fails_honestly``
guards the OTHER direction — a ``?council_id=`` page whose outcome is the PRIMARY source
must still fail honestly when it's missing — so the fix can't be over-broadened into
swallowing real failures.

Serves an isolated, PII-free synthetic council over http (file:// can't carry the
``?status_token=`` query reliably). Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


_COMPLETED_STATUS = {
    "status": "completed",
    "status_token": "tok_no_outcome",
    "task_text": "In-process cache or per-call?",
    "council_id": "c_missing_outcome",
    "memberOrder": ["claude", "codex"],
    "members": {
        "claude": {"status": "done", "model": "m", "response_text": "Cache in-process."},
        "codex": {"status": "done", "model": "m", "response_text": "Cache per-call."},
    },
    "synthesis": {
        "status": "done",
        "response_text": "In-process caching wins for this read path.",
        "routing_label": {
            "winner": "claude",
            "confidence": "high",
            "agreed_claims": ["Some cache layer is warranted."],
        },
    },
    "metadata": {
        "chairman_provider": "claude",
        "council_id": "c_missing_outcome",
        "members": ["claude", "codex"],
    },
}


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _page_text(tmp_path, monkeypatch, url_query: str) -> str:
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()  # publishes vendor/petite-vue under review_pages/
    write_live_council_page()
    # The live page loads vendor from ./vendor (review_pages/vendor) — mirror it.
    from trinity_local import vendor as _vendor

    _vendor.publish_vendor_files(review_pages_dir())

    # statusScriptBaseUrl is '../portal_pages/status' relative to review_pages/.
    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps('tok_no_outcome')}] = "
        f"{json.dumps(_COMPLETED_STATUS)};\n"
    )
    (status_dir / "council_status_tok_no_outcome.js").write_text(sidecar, encoding="utf-8")
    # Deliberately DO NOT write council_outcomes/c_missing_outcome.* — the outcome
    # refresh must 404, reproducing the race.

    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?{url_query}"
                )
                # Let the status poll land completed AND the outcome refresh 404.
                page.wait_for_timeout(2600)
                assert not errs, f"JS pageerrors: {errs[:3]}"
                text = page.inner_text("body")
                has_routing = page.evaluate(
                    "() => !!document.querySelector('.routing-label-grid')"
                )
                return json.dumps({"text": text, "has_routing": has_routing})
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_completed_council_survives_missing_outcome_refresh(tmp_path, monkeypatch):
    """A completed STATUS + missing outcome file → result stays; NO false failure."""
    result = json.loads(_page_text(tmp_path, monkeypatch, "status_token=tok_no_outcome"))
    text, has_routing = result["text"], result["has_routing"]
    assert "Council failed" not in text, (
        "a SUCCESSFUL completed council showed a red 'Council failed' banner because the "
        "secondary outcome-refresh 404'd (the 2026-06-17 false-failure bug)"
    )
    assert "Could not load council outcome" not in text, (
        "the redundant outcome-refresh failure leaked its error onto a completed council"
    )
    # The actual result must still be on screen (the status payload carried it).
    assert has_routing, "routing-label panel vanished — the completed result was clobbered"
    # The winner verdict only renders for a segment that stayed completed.
    assert "the answer you'd have picked" in text, (
        "winner verdict vanished — the completed segment was reset by the outcome-refresh 404"
    )


def test_council_id_init_missing_outcome_still_fails_honestly(tmp_path, monkeypatch):
    """The OTHER direction: ?council_id= whose outcome is the PRIMARY source and is
    missing MUST still fail honestly — the fix can't swallow real failures."""
    result = json.loads(_page_text(tmp_path, monkeypatch, "council_id=c_never_existed"))
    assert "Could not load council outcome" in result["text"], (
        "a ?council_id= page with no backing outcome (its only source) must still surface "
        "the honest failure — the false-failure fix was over-broadened into swallowing it"
    )
