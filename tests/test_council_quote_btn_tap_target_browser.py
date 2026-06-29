"""Browser guard: the per-member "Quote ↓" button must clear the 44px touch target.

The live council page (`render_live_council_page`) is the streaming "watch it"
companion link people open on PHONES (its own `.provider-status-row` comment says
so). Every completed member answer carries a "Quote ↓" button — a real content
action (it drops the member's answer into the refine composer). The `.button` base
CSS already floors EVERY action button to `min-height: 44px` on touch widths
(WCAG 2.5.5 / Apple HIG; founder: "every action button clear 44px on touch
widths"), but `.quote-member-btn` shipped at `font-size: 11px; padding: 2px 8px;
line-height: 1.4` → a rendered height of ~21px, HALF the floor — a fat-finger miss
on the exact surface it ships to (driven 2026-06-19 in the UX sweep at 320/375/393:
the Quote chip measured 60x21).

Fix: flex-center the chip + `min-height: 44px` so the HIT AREA clears 44px while the
compact font/padding keep it visually small (the `.button` + sidepanel icon-button
pattern), without bloating the member-row header.

This guard BITES on the un-fixed CSS (no `min-height`, no flex): the rendered Quote
button measures < 44px tall and the assertion fires with the founder symptom + the
measured height. It does NOT assert on source strings — it reads the REAL rendered
geometry in chromium, so a CSS regression (someone drops the floor, shrinks the
padding) re-reds it.

Slow + browser-marked (portal render + chromium); skips when they're absent.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_quoteTap_done"
_ROOT = "bundle_quoteTaproot"

# The minimum touch target. Match the project's existing 44px floor (.button,
# the sidepanel icon button). A genuine miss is 21px — well clear of rounding,
# so the floor is exact, not fuzzed.
_TOUCH_MIN = 44


def _seed_completed_council():
    """Seed ONE completed council so the live page renders done members (→ Quote
    buttons) AND, because it is the LAST segment, `canChainNext` is true → the
    refine composer is visible → the Quote buttons are not orphaned (they render
    in their normal, intended state)."""
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
        bundle_id=_ROOT,
        task_cluster_id="cluster_quoteTap",
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
    save_council_outcome(outcome)  # writes council_outcomes/<id>.js (the JSONP the page loads)

    write_portal_html()  # ensures portal_pages/vendor/petite-vue.iife.js
    write_live_council_page()


_PROBE = """
() => {
  const quoteBtns = [...document.querySelectorAll('.quote-member-btn')]
    .filter(b => b.offsetParent !== null);
  const refine = document.querySelector('.chain-refine-input');
  return {
    composerVisible: !!(refine && refine.offsetParent !== null),
    heights: quoteBtns.map(b => Math.round(b.getBoundingClientRect().height)),
    widths: quoteBtns.map(b => Math.round(b.getBoundingClientRect().width)),
  };
}
"""


@pytest.mark.parametrize("width", [320, 375, 393])
def test_quote_button_clears_44px_touch_target(tmp_path, monkeypatch, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_completed_council()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}&task=Cache"

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(url)
                page.wait_for_timeout(1500)

                state = page.evaluate(_PROBE)

                # Precondition (non-vacuous): the completed council rendered its
                # Quote buttons AND the composer they feed is visible — i.e. we are
                # measuring the button in its real, intended state, not a state where
                # it happens to be hidden.
                assert state["composerVisible"], (
                    f"harness gap @ {width}px: the 'Continue the thread' composer did not "
                    f"render for a completed council, so Quote buttons can't be measured ({state})"
                )
                assert len(state["heights"]) >= 2, (
                    f"harness gap @ {width}px: expected >=2 visible 'Quote ↓' buttons on a "
                    f"2-member completed council, got {state}"
                )

                # The bite: every Quote button must clear the 44px touch target. The
                # un-fixed chip rendered ~21px — half the floor — a fat-finger miss on
                # the phone companion surface.
                too_small = [h for h in state["heights"] if h < _TOUCH_MIN]
                assert not too_small, (
                    f"SUB-44px tap target @ {width}px: the per-member 'Quote ↓' button on the "
                    f"live council page (the streaming companion link opened on PHONES) is only "
                    f"{too_small} px tall — under the {_TOUCH_MIN}px touch-target floor every other "
                    f"action button clears (WCAG 2.5.5 / 'every action button clear 44px on touch "
                    f"widths'). Heights: {state['heights']}"
                )

                assert not errs, f"JS page errors on the completed-council page: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
