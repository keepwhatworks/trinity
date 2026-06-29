"""Browser guard: the per-member "Quote ↓" button must NOT orphan.

On the live council page (`render_live_council_page`), every completed member
answer carries a "Quote ↓" button whose tooltip promises it drops a quoted
fragment "into the refinement input below". That input — `.chain-refine-input`,
the "Continue the thread" composer — only EXISTS when `canChainNext && !chainBusy`
(`canChainNext` = the LAST segment is completed with a council_id).

In a mid-chain state — a prior round completed, the NEXT round still running —
`canChainNext` is false, so the composer is hidden. But the earlier completed
round's members still rendered their "Quote ↓" buttons (the button gated only on
the per-row `done` status). Clicking one then appended to an OFF-SCREEN
`refinePrompt` and tried to focus an input that wasn't in the DOM: the user
clicked, the page did nothing they could see, and the tooltip lied ("input below"
— there is none). Driven 2026-06-19 in the UX sweep: a mixed-manifest thread
rendered 2 visible Quote buttons with NO `.chain-refine-input` on the page;
clicking changed nothing visible (`activeEl` stayed on the button, no textarea
held the quote).

Fix: gate the button on the SAME `canChainNext && !chainBusy` the composer needs,
so a Quote button only exists when there is a visible input to receive it.

This guard BITES on the unfixed `v-if="row.statusClass === 'done' && ..."` (no
`canChainNext` gate): that renders the orphan buttons, and the assertion below —
"a Quote button is visible while the refine composer it feeds is absent" — fires.

Slow + browser-marked (portal render + chromium); skips when they're absent.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID_DONE = "council_orphanquote_done"
_ROOT = "bundle_orphanquoteroot"


def _seed_mixed_chain():
    """Seed a thread whose first round COMPLETED and whose second round is still
    RUNNING (a `running` manifest entry with a status_token, no council_id).

    The completed round gives the page done members (→ Quote buttons); the running
    final round makes `canChainNext` false (→ the refine composer is hidden). That
    is exactly the mid-chain state where the orphan Quote button surfaces.
    """
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import _write_thread_manifest, save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    outcome = CouncilOutcome(
        council_run_id=_CID_DONE,
        bundle_id=_ROOT,  # chain_root_id falls back to bundle_id → thread keyed by _ROOT
        task_cluster_id="cluster_orphanquote",
        primary_provider="claude",
        winner_provider="claude",
        metadata={"task_text": "Cache the embedder in-process or per-call?", "chain_root_id": _ROOT},
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text="In-process caching wins."),
            CouncilMemberResult(provider="codex", model="gpt", output_text="Per-call is simpler."),
        ],
        synthesis_prompt="Review the answers.",
        synthesis_output="In-process caching wins for latency.",
        routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
        created_at="2026-06-02T00:00:00+00:00",
    )
    save_council_outcome(outcome)  # writes the completed segment into the manifest

    # Append a still-RUNNING round 2 so canChainNext stays false (the composer hides).
    segments = [
        {"council_id": _CID_DONE, "bundle_id": _ROOT, "round_number": 1,
         "started_at": "2026-06-02T00:00:00+00:00"},
        {"status_token": "tok_orphanquote_running", "running": True, "round_number": 2,
         "started_at": "2026-06-02T00:01:00+00:00"},
    ]
    _write_thread_manifest(_ROOT, segments)

    write_portal_html()  # ensures portal_pages/vendor/petite-vue.iife.js
    write_live_council_page()


# A visible Quote button while the composer it feeds (`.chain-refine-input`) is
# absent == the orphan. Counts only buttons the user can actually see.
_ORPHAN_PROBE = """
() => {
  const refineInput = document.querySelector('.chain-refine-input');
  const composerVisible = refineInput && refineInput.offsetParent !== null;
  const quoteBtns = [...document.querySelectorAll('.quote-member-btn')];
  const visibleQuotes = quoteBtns.filter(b => b.offsetParent !== null);
  return {
    composerVisible: !!composerVisible,
    visibleQuoteCount: visibleQuotes.length,
    segCount: document.querySelectorAll('.chain-segment[data-seg-key]').length,
    runningCard: !!document.querySelector('.launch-status .spinner-row'),
  };
}
"""


def test_quote_button_does_not_orphan_mid_chain(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_mixed_chain()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/live_council.html?thread_id={_ROOT}"

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context().new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(url)
                page.wait_for_timeout(1500)

                state = page.evaluate(_ORPHAN_PROBE)

                # The state we engineered: a 2-segment chain whose last round is
                # still running (so the composer is correctly hidden).
                assert state["segCount"] == 2, (
                    f"mixed chain did not boot to 2 segments (got {state}); harness gap, not the guard"
                )
                assert state["runningCard"], (
                    f"running round 2 did not render its spinner (got {state}); harness gap"
                )
                assert not state["composerVisible"], (
                    "the 'Continue the thread' composer should be HIDDEN while round 2 is "
                    f"still running (canChainNext false), but it is visible: {state}"
                )

                # The bite: with the composer hidden, NO Quote button may be visible —
                # else clicking it silently appends to an off-screen refinePrompt and
                # the tooltip's "into the refinement input below" points at nothing.
                assert state["visibleQuoteCount"] == 0, (
                    "ORPHAN 'Quote ↓' button: a per-member Quote button is visible while the "
                    "'.chain-refine-input' composer it feeds is absent (mid-chain, round still "
                    f"running) — clicking it dead-ends with no visible feedback. State: {state}"
                )

                assert not errs, f"JS page errors on the mixed-chain page: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
