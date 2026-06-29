"""Browser guard: a DONE member that returned an EMPTY response must read
"Returned an empty response." on the live council page — never "Queued."

A council member that EXITS CLEANLY (rc 0) but produces no usable stdout/stderr is
recorded as a SUCCESSFUL member_result with ``output_text == ""``. The dispatch
guard is LENIENT by design — ``providers.result_hard_failed`` only flags a HARD
failure when ``returncode != 0 AND empty stdout``, so a rc0/empty-stdout completion
falls through as a "done" member (``council_runner``: ``output_text =
result.stdout or result.stderr or ""``). The ``.js`` outcome sidecar then carries
that member with ``output_text == ""`` and ``output_html == ""``.

On the live council page (``render_live_council_page``), the member row renders the
markdown response, else the pre-text response, else the ``.provider-status-detail``
line. For a done-but-empty member BOTH response branches are empty, so the detail
line shows — and the ``detail`` ternary had NO ``status === 'done'`` branch, so it
fell through every clause to the final ``: 'Queued.'``. The result: on a COMPLETED
council (verdict already shown, another model already crowned) the empty member's
card read **"Queued."** — a flat self-contradiction. A user reasonably reads it as
"this model never ran / is still pending" when in fact it ran and returned nothing.

The launchpad's running-card detail (``launchpad_template.py``:
``status === 'done' ? ... : 'Response ready.'``) already carried a ``done`` branch;
this live-page sibling was the asymmetric one that drifted. The fix adds the
``status === 'done'`` branch → "Returned an empty response.".

This guard drives the REAL ``?council_id=`` outcome path over http (so the whole
pipeline runs: ``save_council_outcome`` writes the empty member into the sidecar,
``outcomeToRunState`` rebuilds it as ``done`` + empty, ``memberRowsFor`` computes the
``detail``, the template renders it) and reads the RENDERED detail text — geometry/
text-content, not a source-string check.

Mutation-proven: dropping the ``status === 'done' ? 'Returned an empty response.' :``
branch from the detail ternary in council_review.py re-introduces "Queued." on the
empty member's card and REDS this guard.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _seed_council_with_empty_member(cid: str) -> None:
    """A completed 2-member council: claude answered with real text, codex
    returned a clean-but-EMPTY response (rc0/empty-stdout → recorded done)."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(
            provider="claude",
            model="opus-4-8",
            output_text="Use a bounded queue with backpressure — it caps memory under load.",
        ),
        # The defect's input: a DONE member with empty output_text. Mirrors a
        # provider CLI that exits rc0 but emits nothing (the lenient
        # result_hard_failed lets it through as a recorded member_result).
        CouncilMemberResult(provider="codex", model="gpt-5.5", output_text=""),
    ]
    save_council_outcome(
        CouncilOutcome(
            council_run_id=cid,
            bundle_id=cid,
            task_cluster_id="cluster_empty_member",
            primary_provider="claude",
            primary_model="opus-4-8",
            winner_provider="claude",
            metadata={
                "task_text": "How should I size the work queue between ingest and embed?",
                "chairman_provider": "claude",
                "chairman_model": "opus-4-8",
            },
            member_results=members,
            synthesis_prompt="Synthesize.",
            synthesis_output="Claude gave the actionable answer.",
            routing_label=CouncilRoutingLabel(
                winner="claude", confidence="high", runner_up="codex", task_type="architecture"
            ),
            created_at="2026-06-22T12:00:00+00:00",
        )
    )
    write_portal_html()  # publishes vendor next to review_pages/
    write_live_council_page()


def _serve(tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


_READ_ROWS = """() => {
  const rows = [...document.querySelectorAll('.provider-status-row')];
  return rows.map(r => ({
    name: ((r.querySelector('.provider-status-name')||{}).textContent || '').trim(),
    detail: ((r.querySelector('.provider-status-detail')||{}).textContent || '').trim(),
    hasResponse: !!r.querySelector('.provider-status-response'),
    responseText: ((r.querySelector('.provider-status-response')||{}).textContent || '').trim(),
  }));
}"""


def test_done_but_empty_member_reads_empty_response_not_queued(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    cid = "council_emptymember01"
    _seed_council_with_empty_member(cid)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 393, "height": 1200}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                # Stub the dispatcher so the page's onStateChange/probe wiring
                # never reaches a real native host (chain controls only).
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    "dispatch:(a,cb)=>{if(cb)cb({ok:true});},"
                    "probe:()=>Promise.resolve({state:'ready'}),"
                    "subscribe:()=>{},onStateChange:()=>{}};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={cid}"
                )
                page.wait_for_timeout(1100)

                rows = page.evaluate(_READ_ROWS)
                by_name = {r["name"]: r for r in rows}
                # Names render as brand · model ("Claude · opus-4-8", "GPT · gpt-5.5").
                claude = next((r for r in rows if r["name"].startswith("Claude")), None)
                gpt = next((r for r in rows if r["name"].startswith("GPT")), None)

                # ---- Preconditions (non-vacuous bite) -----------------------
                assert not errs, f"JS errors rendering the live council page: {errs[:3]}"
                assert len(rows) == 2, f"expected 2 member rows, got {len(rows)}: {rows}"
                assert claude is not None, f"claude member row missing: {by_name}"
                assert gpt is not None, f"empty-output member row missing: {by_name}"
                # The empty member must genuinely fall to the DETAIL line — no
                # response branch rendered (otherwise the detail wouldn't show and
                # the bite would be vacuous).
                assert not gpt["hasResponse"], (
                    "the empty-output member unexpectedly rendered a response element — "
                    f"the detail line wouldn't show, bite is vacuous: {gpt!r}"
                )
                assert gpt["detail"], (
                    "the empty-output member rendered no detail text at all — "
                    f"expected the honest empty-response line: {gpt!r}"
                )
                # Positive control: the REAL responder still renders its answer —
                # so the fix isn't a blanket suppression of member output.
                assert claude["hasResponse"] and "bounded queue" in claude["responseText"], (
                    "the real responder lost its answer — the fix over-suppressed: "
                    f"{claude!r}"
                )

                # ---- The defect ---------------------------------------------
                assert gpt["detail"] != "Queued.", (
                    "FOUNDER SYMPTOM: a DONE member that returned an EMPTY response "
                    "reads 'Queued.' on a COMPLETED council (verdict already shown, "
                    "Claude already crowned) — a flat self-contradiction that reads "
                    "as 'this model never ran / is still pending'. The live-page "
                    "member-detail ternary was missing the `status === 'done'` "
                    "branch the launchpad running-card already had, so an empty-"
                    f"output responder fell through to 'Queued.': {gpt!r}"
                )
                assert gpt["detail"] == "Returned an empty response.", (
                    "the done-but-empty member's detail should honestly say it "
                    "returned an empty response (it ran, it answered nothing), got "
                    f"{gpt['detail']!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
