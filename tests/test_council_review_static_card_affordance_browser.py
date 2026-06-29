"""Real-browser affordance-honesty guard for the STATIC council review page.

The static, shareable review page (`render_unified_council_page`, the `?council_id=`
artifact — distinct from the LIVE streaming page) renders each member response as an
`.answer-card`. The click-to-pick UI was RETIRED 2026-05-22 — the chairman's pick is
the sole supervision signal now, and `CouncilApp` carries NO card-selection state
("no click flow ever mutates this", per the source). The `<article class="card
answer-card">` has no `@click`, no `tabindex`, no `role`, and no keyboard handler.

Yet the CSS shipped `cursor: pointer` + a hover LIFT (`translateY(-3px)`) with the
border shifting toward `var(--action)` (the CTA color) + a `:focus-visible` ring +
a `.selected` rule — all VESTIGIAL affordance-lies left over from that retired pick
UI. Driving the page (2026-06-18 UX sweep) confirmed clicking a card did NOTHING:
DOM unchanged, no dispatch, the card never receives focus, `.selected` is never
applied. A card that LOOKS clickable (pointer cursor + a lift toward the action
color) but isn't is a NO-OP affordance — the user hovers, sees it "respond", clicks,
and nothing happens.

This guard drives the REAL rendered page and asserts the static answer card does NOT
advertise interactivity it lacks: `cursor` must not be `pointer`, and hovering must
not lift it (no transform). It also pins the card's read-only nature (no wiring,
click is a no-op) so a future re-introduction of a clickable cursor WITHOUT real
behavior is caught. A bare source-string grep can't see the COMPUTED cursor or the
hover transform — only execution reveals the affordance. Synthetic data only; no PII.

Mirrors the prod-shaped layout of test_council_review_xss_browser.py so petite-vue
actually mounts (the page loads it from ../portal_pages/vendor/petite-vue.iife.js).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _render_static_council_page() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_aff",
        task_cluster_id="cluster_aff",
        task_text="Which caching strategy for the read path?",
        goal="Pick the strongest answer.",
        comparison_instructions="Prefer specificity.",
        created_at="2026-06-18T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_affxyz",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="antigravity",
        member_results=[
            CouncilMemberResult(provider="claude", model="opus-4-8",
                                output_text="Use an LRU cache keyed on the read path."),
            CouncilMemberResult(provider="antigravity", model="gemini-3.1-pro",
                                output_text="Memoize at the boundary; cache the rarely-changing state."),
            CouncilMemberResult(provider="codex", model="gpt-5.5",
                                output_text="Profile first, then cache the hot path."),
        ],
        synthesis_output="# Verdict\n\nThe **Gemini** answer wins on specificity.",
        routing_label=CouncilRoutingLabel(winner="antigravity", task_type="architecture"),
        created_at="2026-06-18T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_static_answer_card_does_not_fake_interactivity():
    """The static review page's `.answer-card` must NOT advertise click affordance
    it lacks. The click-to-pick UI was retired 2026-05-22 and the card has no
    @click/tabindex/role/keyboard handler, so:

      - computed `cursor` must NOT be `pointer` (a pointer cursor on a non-clickable
        card is the affordance-lie this guards), and
      - hovering must NOT LIFT the card (no negative-Y transform — that's the
        CTA-button hover treatment the card wore vestigially).

    And it pins that the card is genuinely read-only: no wiring, clicking is a
    no-op (DOM unchanged, no dispatch, never focusable) — so a future re-add of a
    clickable cursor WITHOUT real behavior is flagged.

    Founder symptom: "the answer card looks clickable (pointer cursor + hover lift
    toward the action color) but clicking it does nothing."
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.vendor import publish_vendor_files

    root = Path(tempfile.mkdtemp(prefix="trinity-card-aff-"))
    try:
        (root / "review_pages").mkdir()
        (root / "portal_pages").mkdir()
        publish_vendor_files(root / "portal_pages")
        page_path = root / "review_pages" / "council_aff.html"
        page_path.write_text(_render_static_council_page(), encoding="utf-8")

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # chromium not installed in this env
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                # Stub the dispatcher BEFORE any click so a stray handler would be
                # observable (it must NOT fire — the card has no dispatch path).
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: (o)=>{ "
                    "window.__cardDispatch = true; if (o.onResult) o.onResult({ok:true}); } };"
                )
                page.goto(f"file://{page_path}")
                page.wait_for_timeout(800)  # let petite-vue mount

                cards = page.query_selector_all(".answer-card")
                assert len(cards) == 3, (
                    f"expected 3 answer cards, got {len(cards)} — the page did not "
                    "render; the affordance assertions would be a false pass"
                )
                card = cards[0]

                cursor = page.evaluate("(el) => getComputedStyle(el).cursor", card)
                assert cursor != "pointer", (
                    f"the static answer card computed cursor is '{cursor}' — a pointer "
                    "cursor on a card with NO @click/tabindex/role/keyboard handler is "
                    "the affordance-lie left over from the retired click-to-pick UI: it "
                    "looks clickable but clicking it does nothing."
                )

                # Hover must not LIFT the card (the CTA-button hover treatment).
                card.hover()
                page.wait_for_timeout(250)
                transform = page.evaluate("(el) => getComputedStyle(el).transform", card)
                # A translateY(-3px) renders as matrix(1,0,0,1,0,-3); a lift means a
                # negative 6th matrix component. 'none' (no transform) is the pass.
                lifted = False
                if transform and transform.startswith("matrix"):
                    nums = transform[transform.find("(") + 1: transform.rfind(")")].split(",")
                    if len(nums) == 6:
                        try:
                            lifted = float(nums[5].strip()) < -0.5
                        except ValueError:
                            lifted = False
                assert not lifted, (
                    f"the static answer card LIFTS on hover (transform={transform}) — that "
                    "translateY lift toward var(--action) is the CTA-button affordance the "
                    "non-clickable card wore vestigially; a read-only card must not lift."
                )

                # Pin read-only: no wiring, click is a no-op.
                for attr in ("tabindex", "role"):
                    val = page.evaluate(f"(el) => el.getAttribute('{attr}')", card)
                    assert val is None, (
                        f"the static answer card carries {attr}={val!r} but has no handler — "
                        "either wire real behavior or drop the interactive attribute."
                    )
                before = page.evaluate("() => document.body.innerHTML")
                card.click()
                page.wait_for_timeout(150)
                after = page.evaluate("() => document.body.innerHTML")
                assert before == after, (
                    "clicking the static answer card MUTATED the DOM — but the card has "
                    "no documented click behavior; an undocumented handler shipped."
                )
                dispatched = page.evaluate("() => !!window.__cardDispatch")
                assert not dispatched, (
                    "clicking the static answer card fired a dispatch — the static review "
                    "page is a read-only artifact and must not dispatch from a card click."
                )
            finally:
                browser.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)
