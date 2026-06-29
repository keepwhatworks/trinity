"""Browser guard: the live-council "Auto-chain (up to 3 rounds)" button must
actually DISPATCH the round-count arg through a real click.

Coverage gap this fills (UX sweep — NO-FEEDBACK / cost-multiplier-arg cell):
the live council page's chain composer offers two cost-distinct actions —

    [Continue (one round)]   [Auto-chain (up to 3 rounds)]

Continue runs ONE more full council; Auto-chain runs up to THREE
(`council_auto_chain` → `max_rounds: 3` → `extensionAction.rounds = '3'`), so it
can cost up to 3x a single round's quota. The ONLY thing that distinguishes the
two dispatches on the wire is the `rounds` arg — Auto-chain folds `rounds:'3'`
onto the council-iterate payload, Continue folds nothing.

Yet that distinguishing arg was verified ONLY by a source-substring count
(`test_council_review_refine_dispatch_failure.py::test_chain_dispatch_payload_
shape_is_correct` asserts the line
`if (args.max_rounds) extensionAction.rounds = String(args.max_rounds);`
appears twice). That guard is BLIND to a regression in `startAutoChain` itself:
drop the `{ max_rounds: 3 }` arg from the live-page `startAutoChain` call and

  * the `if (args.max_rounds)...` fold line is UNCHANGED → the source count
    stays 2 → the source guard stays GREEN;
  * the cost-signal browser guard only reads the BUTTON LABEL copy (never
    clicks) → stays GREEN;
  * the host iterate-dispatch guards test capture_host, not the page → GREEN;

…while a real Auto-chain click now dispatches with NO `rounds` key — the button
labelled "up to 3 rounds" silently runs ONE round, quietly costing the user the
wrong thing. (Mutation-proven: stripping the live-page `max_rounds` arg keeps
all three existing guards green; only a real-click payload capture catches it.)

This guard DRIVES the real button: stubs the dispatcher to capture the
`extensionAction`, clicks "Auto-chain (up to 3 rounds)", and asserts the
captured payload carries `rounds:'3'` (the cost multiplier) alongside the
correct `kind`/`council`/underscore `status_token`. It also confirms the
immediate-feedback contract — the optimistic next-round segment appears — so the
deferred-result action reads as acknowledged, not dead.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_acrounds01"

# Dispatcher stub: captures the extensionAction synchronously, then ACKs ok:true
# so the page takes the success path (optimistic next-round segment). Full probe
# + onStateChange interface so the page's init never throws.
_STUB_OK = """
window.__TRINITY_DISPATCH__ = {
  probe: () => Promise.resolve({state: 'ready'}),
  onStateChange: (cb) => {},
  dispatch: ({extensionAction, onResult}) => {
    window.__captured = extensionAction;
    setTimeout(() => onResult({ok: true}), 50);
  }
};
"""


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_completed_duo_council():
    """A completed 2-member (real-contest) council so `canChainNext` is true and
    the chain composer (Continue / Auto-chain) actually renders."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    outcome = CouncilOutcome(
        council_run_id=_CID,
        bundle_id=_CID,
        task_cluster_id="cluster_acrounds",
        primary_provider="claude",
        winner_provider="claude",
        metadata={"task_text": "Cache the embedder in-process or per-call?"},
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text="In-process."),
            CouncilMemberResult(provider="codex", model="gpt", output_text="Per-call."),
        ],
        synthesis_prompt="Review the answers.",
        synthesis_output="In-process caching wins for latency.",
        routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
        created_at="2026-06-02T00:00:00+00:00",
    )
    save_council_outcome(outcome)
    write_portal_html()  # ensures portal_pages/vendor/petite-vue.iife.js
    write_live_council_page()


def test_auto_chain_click_dispatches_rounds_three(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_completed_duo_council()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.add_init_script(_STUB_OK)
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?council_id={_CID}",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_timeout(900)

                # Precondition (non-vacuous): the Auto-chain button must render
                # with its cost label, and exactly ONE round segment exists.
                pre = page.evaluate(
                    """() => {
                      const btn = [...document.querySelectorAll('button')]
                        .find(x => /Auto-chain/.test(x.textContent));
                      return {
                        btnText: btn ? btn.textContent.trim() : null,
                        btnVisible: btn ? getComputedStyle(btn).display !== 'none' : false,
                        segs: document.querySelectorAll('.chain-segment[data-seg-key]').length,
                      };
                    }"""
                )
                assert pre["btnVisible"] and pre["btnText"] and "up to 3 rounds" in pre["btnText"], (
                    "the 'Auto-chain (up to 3 rounds)' button did not render on a completed "
                    f"duo council — cannot drive the dispatch (got {pre!r})"
                )
                assert pre["segs"] == 1, (
                    f"expected exactly one round segment before the click, got {pre['segs']}"
                )

                # DRIVE: click the real Auto-chain button.
                page.evaluate(
                    """() => [...document.querySelectorAll('button')]
                        .find(x => /Auto-chain/.test(x.textContent)).click()"""
                )
                page.wait_for_function(
                    "() => window.__captured !== undefined", timeout=3000
                )
                captured = page.evaluate("() => window.__captured")

                assert not errs, f"the Auto-chain click threw JS errors: {errs[:4]}"

                # ── THE COST-MULTIPLIER ARG (the un-driven invariant) ──
                # Auto-chain MUST fold rounds:'3' onto the payload — that is the
                # ONLY thing distinguishing it from Continue on the wire. Without
                # it the button labelled "up to 3 rounds" silently runs ONE round.
                assert captured.get("rounds") == "3", (
                    "clicking 'Auto-chain (up to 3 rounds)' dispatched WITHOUT "
                    f"rounds:'3' (got rounds={captured.get('rounds')!r}) — the cost "
                    "multiplier was dropped, so the button labelled 'up to 3 rounds' "
                    "silently runs a single round and quietly costs the wrong quota. "
                    f"Full payload: {captured!r}"
                )
                # The rest of the council-iterate contract still holds on the click.
                assert captured.get("kind") == "council-iterate", (
                    "Auto-chain dispatched a kind other than 'council-iterate' — the "
                    f"only kind capture_host's ACTION_ALLOWLIST accepts: {captured!r}"
                )
                assert captured.get("council") == _CID, (
                    f"Auto-chain targeted the wrong council id: {captured!r}"
                )
                # Underscore key — the host reads payload['status_token']; the hyphen
                # spelling silently drops the token (the 2026-06-12 "council never
                # started" bug). A real click must emit the underscore form.
                assert isinstance(captured.get("status_token"), str) and captured["status_token"], (
                    "Auto-chain dispatched without a status_token (underscore key) — the "
                    f"chain round writes status under nothing the page can poll: {captured!r}"
                )

                # ── IMMEDIATE FEEDBACK (deferred-result ack) ──
                # The success path optimistically appends the next-round segment, so
                # the user gets an instant, visible acknowledgment (the composer card
                # hides while the round runs — that's by design, not a dead control).
                page.wait_for_function(
                    "() => document.querySelectorAll('.chain-segment[data-seg-key]').length === 2",
                    timeout=3000,
                )
                feedback = page.evaluate(
                    """() => {
                      const segs = [...document.querySelectorAll('.chain-segment[data-seg-key]')];
                      const last = segs[segs.length - 1];
                      return {
                        count: segs.length,
                        lastHasSpinner: last ? !!last.querySelector('.spinner, [role=status]') : false,
                      };
                    }"""
                )
                assert feedback["count"] == 2 and feedback["lastHasSpinner"], (
                    "clicking Auto-chain produced NO immediate feedback — the optimistic "
                    "next-round segment (with its running spinner) did not appear, so the "
                    f"deferred-result action reads as a dead button: {feedback!r}"
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
