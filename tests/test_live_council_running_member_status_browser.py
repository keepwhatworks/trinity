"""Browser guard: on the LIVE POLL path (``?status_token=``) while the council is
STILL RUNNING, each "Full Responses" member row must paint the status the sidecar
ACTUALLY carries — a ``done`` member shows its real answer, a ``running`` member
reads "Running" / "Working…", and a ``pending`` member reads "Queued". And NO
winner verdict (the 🏆 "the answer you'd have picked" trophy / "No clear winner")
may appear while the run is in flight.

THE GAP this closes (found 2026-06-21 driving the genuine in-flight RUNNING poll
state — clicked Launch, some members done, some still pending/running, synthesis
not yet produced — a cell no prior guard exercised for its per-member BINDING):
the existing running-state browser tests assert only aria-live semantics
(``test_live_council_status_aria_live_browser`` — the .status-message sits in a
role=status region, the mirror announces "running") and the TERMINAL coercions
(``test_live_council_terminal_member_rows_browser`` — failed/canceled rows must not
read "Queued"). NONE of them assert that a still-``running`` member reads "Running"
(not "Done") or that a ``pending`` member reads "Queued" (not "Done") on a LIVE,
non-terminal poll, nor that the verdict stays suppressed mid-run.

MUTATION-PROVEN (against ``src/trinity_local/council_review.py`` ``memberRowsFor``,
the SAME source ``render_live_council_page`` renders from — NOT the extension
bundle): inserting ``if (status === 'running') status = 'done';`` AFTER the terminal
coercion (so the terminal tests stay green) paints the in-flight ``codex`` member as
"Done" while it is still working — the founder symptom — and ALL 19 existing
running/terminal/value-proof live-council browser tests stay GREEN. This guard reds
on exactly that mutation.

Serves an isolated, PII-free synthetic RUNNING council over http (file:// can't
carry the ``?status_token=`` query reliably) and reads the rendered DOM. Slow-marked;
skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_MEMBERS = ["claude", "codex", "antigravity"]

# The DISCRIMINATING in-flight seed: one member done (real answer), one running
# (started, no answer yet), one pending (never started). Synthesis still pending.
# Top-level status 'running'. This is the exact shape the runner writes between
# the first member landing and synthesis (init_council_run_state +
# update_member_progress(claude) + start_member_progress(codex)).
_DONE_MEMBER = "claude"
_RUNNING_MEMBER = "codex"
_PENDING_MEMBER = "antigravity"
_DONE_ANSWER = "Monorepo wins for a small team: atomic cross-cutting changes, one CI."


def _running_status(token: str) -> dict:
    return {
        "status": "running",
        "status_token": token,
        "task_text": "Monorepo or split into per-service repos?",
        "memberOrder": _MEMBERS,
        "members": {
            _DONE_MEMBER: {
                "status": "done",
                "model": "claude-opus-4-8",
                "response_text": _DONE_ANSWER,
                "response_html": f"<p>{_DONE_ANSWER}</p>",
            },
            _RUNNING_MEMBER: {"status": "running", "model": "gpt-5.5"},
            _PENDING_MEMBER: {"status": "pending", "model": "gemini-3.1-pro"},
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
    # The live page loads ../portal_pages/vendor/petite-vue.iife.js relative to
    # review_pages/, so the IIFE must ALSO sit under portal_pages/vendor/.
    _vendor.publish_vendor_files(portal_pages_dir())

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
            page.wait_for_timeout(2600)  # let the first poll land + mount
            body = page.evaluate("() => document.body.innerText")
            rows = page.evaluate(
                """() => Array.from(document.querySelectorAll('.provider-status-row')).map(r => ({
                    name: (r.querySelector('.provider-status-name')||{}).textContent || '',
                    badge: (r.querySelector('.provider-status-badge')||{}).textContent || '',
                    detail: (r.querySelector('.provider-status-detail')||{}).textContent || '',
                    hasResponse: !!r.querySelector('.provider-status-response')
                }))"""
            )
            running_banner = page.evaluate(
                "() => Array.from(document.querySelectorAll('.launch-status strong'))"
                ".some(e => e.textContent.trim() === 'Council running')"
            )
            verdict_count = page.evaluate(
                "() => document.querySelectorAll('.winner-verdict').length"
            )
            return body, rows, running_banner, verdict_count, errs
        finally:
            browser.close()


def _row_for(rows, member_label_prefix):
    for r in rows:
        if r["name"].startswith(member_label_prefix):
            return r
    return None


def test_running_council_member_rows_match_seed_and_no_premature_verdict(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_running_member_status"
    status = _running_status(token)

    # --- BITE precondition B: the seed is the DISCRIMINATING running state,
    # checked render-independently on the fixture CONSTANTS (not the painted DOM),
    # so a real binding regression reds on the binding assertion, not on a
    # misleading "seed" message a dev could mis-fix to mask the bug.
    seeded = {p: status["members"][p]["status"] for p in _MEMBERS}
    assert seeded == {_DONE_MEMBER: "done", _RUNNING_MEMBER: "running", _PENDING_MEMBER: "pending"}, (
        f"fixture is not the discriminating done/running/pending running state: {seeded}"
    )
    assert status["status"] == "running" and status["synthesis"]["status"] == "pending", (
        "fixture must be an IN-FLIGHT council (top status running, synthesis pending)"
    )

    _seed(tmp_path, monkeypatch, status)
    httpd, port = _serve(tmp_path)
    try:
        body, rows, running_banner, verdict_count, errs = _drive(port, token)
    finally:
        httpd.shutdown()

    # --- BITE precondition A: the running progress UI actually PAINTED (mounted,
    # no raw {{ }} leak, no JS error, the "Council running" banner present) — so
    # the badge/verdict assertions below are non-vacuous (an un-mounted petite-vue
    # would leave the template braces and make every textContent check pass on the
    # raw mustache string).
    assert not errs, f"live RUNNING page raised JS pageerrors (un-mounted/poll broke): {errs[:3]}"
    assert "{{" not in body and "}}" not in body, (
        "raw petite-vue '{{ }}' leaked on the RUNNING live council page — it never "
        "mounted, so the per-member status below is unbound mustache text, not a real binding"
    )
    assert running_banner, (
        "the RUNNING live council did not paint the 'Council running' banner — the "
        "in-flight progress affordance is missing; fix the fixture/poll before trusting "
        "the per-member status asserts"
    )
    assert len(rows) == 3, f"expected 3 member rows on the running council, got {len(rows)}: {rows}"

    done_row = _row_for(rows, "Claude")
    running_row = _row_for(rows, "GPT")
    pending_row = _row_for(rows, "Gemini")
    assert done_row and running_row and pending_row, f"member rows mis-labeled: {rows}"

    # --- THE BINDING ASSERTION (the sole thing keyed on the seed→DOM binding):
    # each in-flight row must read the status the sidecar carries. Founder symptom:
    # a member painted "Done" while the seed says it's still pending/running.
    assert running_row["badge"].strip().lower() == "running", (
        "the live RUNNING council painted the in-flight member with the WRONG status "
        f"badge — the sidecar says '{_RUNNING_MEMBER}' is RUNNING (still working), but "
        f"the row badge read {running_row['badge']!r} (a 'Done' here is the founder "
        "symptom: a member shown finished while it's still streaming). It must read 'Running'."
    )
    assert not running_row["hasResponse"], (
        f"the still-RUNNING member ('{_RUNNING_MEMBER}') painted a response body while "
        "the sidecar carries no answer yet — a Done-bound mid-run member fabricates a "
        "finished answer that does not exist."
    )
    assert pending_row["badge"].strip().lower() == "queued", (
        "the live RUNNING council painted the not-yet-started member with the WRONG "
        f"status — the sidecar says '{_PENDING_MEMBER}' is PENDING, but the row badge "
        f"read {pending_row['badge']!r} (expected 'Queued'). A 'Done'/'Running' here "
        "claims a member started/finished that never ran."
    )
    assert done_row["hasResponse"] and _DONE_ANSWER[:24] in body, (
        f"the one DONE member ('{_DONE_MEMBER}') did not paint its real answer on the "
        "running council — a done row that reads 'Queued.' contradicts the landed response."
    )

    # --- NO premature verdict while in flight. The trophy / "the answer you'd have
    # picked" / "No clear winner" lines all gate on seg.completed; mid-run they must
    # be ABSENT (a verdict shown before synthesis lands reads as a finished council).
    assert verdict_count == 0, (
        "a winner-verdict element appeared on a STILL-RUNNING council (synthesis "
        "pending) — the premature verdict reads as 'the council finished and X won' "
        "while members are still working. The verdict must stay suppressed until "
        "seg.completed."
    )
    assert "the answer you'd have picked" not in body and "No clear winner" not in body, (
        "a verdict line ('… the answer you'd have picked' / 'No clear winner') leaked "
        "onto the in-flight RUNNING council before synthesis completed."
    )
