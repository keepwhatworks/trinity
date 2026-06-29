"""UX sweep Iter 87 — the STATIC council review page's refine-input placeholder
must not promise a "Quote ↓" affordance that does not exist on that surface.

The "Quote ↓" buttons (one per member answer card) live ONLY on the LIVE council
page (``render_live_council_page``), where the answer cards are rendered client-side
from the polled ``member_results`` and each carries a ``quoteMember`` button. The
STATIC review page (``render_unified_council_page``) renders the answer cards
SERVER-SIDE with no Quote button and defines no ``quoteMember`` handler — yet its
refine textarea placeholder was copy-pasted from the live page and told the user
"Quote ↓ stacks each member's answer here". A user on the static review page was
instructed to use a button that is nowhere on the page (an UNCLEAR/MISLEADING-COPY
usefulness defect — the label promises a feature the surface doesn't have).

This guard drives the REAL static review page in a browser (petite-vue mounted, no
brace leak) and enforces the placeholder↔button consistency invariant: if there is
no rendered "Quote" button on this surface, the refine placeholder must not reference
one. Mutation-proven against restoring the live page's "Quote ↓ stacks each member's
answer here" clause in the static placeholder.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _render_static_review_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_quote_copy",
        task_cluster_id="cluster_quote_copy",
        task_text="Pick the strongest fix for a multi-tenant caching collision.",
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-06-01T12:00:00+00:00",
    )
    members = [
        CouncilMemberResult(
            provider="claude",
            model="claude-opus-4-8",
            output_text="Claude reframes around tenancy isolation.",
        ),
        CouncilMemberResult(
            provider="codex",
            model="gpt-5.5",
            output_text="Codex enumerates the concurrency failure modes.",
        ),
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type="design",
        agreed_claims=["Namespace the cache key per tenant"],
        disagreed_claims=[],
        routing_lesson="prefer_per_call_for_multi_tenant_isolation",
    )
    outcome = CouncilOutcome(
        council_run_id="council_quote_copy",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="claude",
        member_results=members,
        synthesis_output="# Synthesis\n\nNamespace the key per tenant.",
        routing_label=routing_label,
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_static_review_refine_placeholder_matches_available_controls(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local import vendor

    review_dir = tmp_path / "review_pages"
    review_dir.mkdir()
    portal_dir = tmp_path / "portal_pages"
    portal_dir.mkdir()
    # publish_vendor_files(<arg>) creates <arg>/vendor/, and the page references
    # ../portal_pages/vendor/* — so petite-vue mounts and braces resolve.
    vendor.publish_vendor_files(portal_dir)

    page_file = review_dir / "council_quote_copy.html"
    page_file.write_text(_render_static_review_html(), encoding="utf-8")

    errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1024, "height": 1100}
            ).new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"file://{page_file}", wait_until="load", timeout=15000)
            page.wait_for_timeout(400)

            # The refine textarea must actually be mounted (v-if="!chainBusy") so
            # we're reading the real rendered placeholder, not an absent element.
            placeholders = page.eval_on_selector_all(
                "textarea.chain-refine-input",
                "els => els.map(e => e.placeholder)",
            )
            assert placeholders, (
                "static review page rendered NO chain-refine textarea — the refine "
                "section did not mount, so the placeholder invariant can't be checked "
                "(false-pass guard)"
            )

            # How many "Quote" buttons actually render on THIS surface. The static
            # review page renders answer cards server-side with no Quote button, so
            # this must be 0.
            quote_buttons = page.eval_on_selector_all(
                "button",
                "els => els.filter(e => (e.textContent || '').includes('Quote')).length",
            )

            placeholder_mentions_quote = any(
                "Quote" in (ph or "") for ph in placeholders
            )

            # No user-visible petite-vue brace leak (page mounted cleanly).
            import re

            visible = page.inner_text("body")
            leaks = re.findall(r"\{\{[^}]{0,80}\}\}", visible)
        finally:
            browser.close()

    assert not errors, f"static review page raised JS errors: {errors}"
    assert not leaks, (
        "static review page leaked un-mounted petite-vue braces "
        f"(petite-vue did not mount): {leaks[:5]}"
    )

    # The invariant: the refine placeholder must not instruct the user to use a
    # "Quote ↓" affordance that has no button on this surface.
    assert quote_buttons == 0, (
        "harness assumption broken: the static review page now renders Quote "
        f"button(s) ({quote_buttons}) — update this guard (the placeholder may "
        "legitimately reference Quote once a button exists here)"
    )
    assert not placeholder_mentions_quote, (
        "the static council REVIEW page's refine placeholder references a "
        '"Quote ↓" button that does not exist on this surface (0 Quote buttons '
        "render here — Quote ↓ lives only on the LIVE council page). A user on the "
        "static review page is told to use a control that is nowhere on the page. "
        f"Placeholders seen: {placeholders}"
    )
