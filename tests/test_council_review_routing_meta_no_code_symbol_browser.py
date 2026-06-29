"""UX sweep — the STATIC unified council REVIEW page's routing-label meta line
must not leak raw snake_case JSON keys / enum values into user-facing prose.

The chairman emits ``task_type`` / ``task_domain`` as raw snake_case enums
(``code_generation``, ``backend_systems``). The routing-label section painted
them VERBATIM behind snake_case ``task_type:`` / ``task_domain:`` LABELS:

    task_type: code_generation · task_domain: backend_systems

That is FOUR raw code symbols (two JSON-key labels + two un-humanized enum
values) in the user-facing prose of a PERSISTENT, SHAREABLE review artifact —
the same code-symbol-in-prose class as the launchpad council-card ``why_matters``
leak (Iter 164) and the memory-viewer ``per task_type`` tagline (Iter 165). The
routing-LESSON row one block below already ``.replace("_", " ")``-humanizes the
same enums ("prefer claude for code generation tasks"); this meta line was the
un-humanized sibling, AND it wore the raw JSON keys as its labels.

This guard drives the REAL static review page in a browser (petite-vue mounted,
no brace leak), reads the PAINTED routing-label card, and enforces:
  * the LABELS are plain English ("Task type:" / "Domain:"), not "task_type:" /
    "task_domain:";
  * the enum VALUES are humanized ("code generation" / "backend systems"), not
    the raw snake_case "code_generation" / "backend_systems";
  * NO snake_case identifier survives anywhere in the painted card.

Mutation-proven against reverting the humanization (raw ``task_type:`` label +
raw enum value).
"""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# A discriminating routing label: BOTH task_type and task_domain are multi-token
# snake_case enums, so a verbatim render leaks both the key-labels and the values.
_TASK_TYPE_ENUM = "code_generation"
_TASK_DOMAIN_ENUM = "backend_systems"
_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _render_static_review_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_routing_meta",
        task_cluster_id="cluster_routing_meta",
        task_text="How should I design the read path?",
        created_at="2026-06-01T12:00:00+00:00",
    )
    members = [
        CouncilMemberResult(
            provider="claude",
            model="claude-opus-4-8",
            output_text="Claude: use a queue for backpressure.",
        ),
        CouncilMemberResult(
            provider="codex",
            model="gpt-5.5",
            output_text="Codex: cache the read path.",
        ),
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type=_TASK_TYPE_ENUM,
        task_domain=_TASK_DOMAIN_ENUM,
        agreed_claims=["Use a queue for backpressure"],
        disagreed_claims=[],
        # routing_lesson intentionally OMITTED — so the only place these enums
        # can reach the painted prose is the meta line under test (the lesson row
        # already humanizes; we don't want it masking a meta-line regression).
    )
    outcome = CouncilOutcome(
        council_run_id="council_routing_meta",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        primary_model="claude-opus-4-8",
        member_results=members,
        synthesis_output="# Synthesis\n\nUse a queue; cache the read path.",
        routing_label=routing_label,
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_routing_meta_line_has_no_code_symbol_leak(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local import vendor

    review_dir = tmp_path / "review_pages"
    review_dir.mkdir()
    portal_dir = tmp_path / "portal_pages"
    portal_dir.mkdir()
    # publish_vendor_files(<arg>) creates <arg>/vendor/; the page references
    # ../portal_pages/vendor/* so petite-vue mounts and braces resolve.
    vendor.publish_vendor_files(portal_dir)

    page_file = review_dir / "council_routing_meta.html"
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

            # BITE PRECONDITION (non-vacuous): the routing-label card must be
            # present + visible + carry the populated task-meta line, else the
            # snake_case scan trivially passes on an absent element.
            card = page.locator("section.routing-label-card")
            assert card.count() == 1, (
                "routing-label card did not render — the meta-line invariant "
                "can't be checked (false-pass guard)"
            )
            assert card.first.is_visible(), "routing-label card not visible"
            card_text = card.first.inner_text()
            # Precondition (TRUE in BOTH the fixed and the leaking render, so the
            # BITE lands on the leak assertions below, not here): the card mounted
            # and painted its winner line + the populated task-meta row. We assert
            # on the always-present winner line + the enum VALUE being somewhere in
            # the card (humanized OR raw), not on the post-fix LABEL form.
            assert "runner-up" in card_text, (
                "routing-label card winner line did not paint — precondition for "
                f"the code-symbol scan not met. Card painted:\n{card_text}"
            )
            assert "generation" in card_text and "systems" in card_text, (
                "the task-meta line did not paint its enum values at all — "
                f"precondition for the code-symbol scan not met. Card painted:\n{card_text}"
            )

            # No user-visible petite-vue brace leak (page mounted cleanly).
            visible = page.inner_text("body")
            leaks = re.findall(r"\{\{[^}]{0,80}\}\}", visible)
        finally:
            browser.close()

    assert not errors, f"static review page raised JS errors: {errors}"
    assert not leaks, (
        "static review page leaked un-mounted petite-vue braces "
        f"(petite-vue did not mount): {leaks[:5]}"
    )

    # The two raw JSON-key LABELS must be gone.
    assert "task_type:" not in card_text and "task_domain:" not in card_text, (
        "the unified council REVIEW page's routing-label meta line painted a raw "
        "snake_case JSON KEY ('task_type:' / 'task_domain:') as a user-facing "
        "label on a PERSISTENT, shareable artifact — the why_matters/per-task_type "
        f"code-symbol-in-prose leak class. Card painted:\n{card_text}"
    )
    # The two raw enum VALUES must be humanized.
    assert _TASK_TYPE_ENUM not in card_text and _TASK_DOMAIN_ENUM not in card_text, (
        "the routing-label meta line painted the raw snake_case enum VALUE "
        f"('{_TASK_TYPE_ENUM}' / '{_TASK_DOMAIN_ENUM}') verbatim — the routing "
        "LESSON row already humanizes these enums ('.replace(\"_\", \" \")'); the "
        f"meta line was the un-humanized sibling. Card painted:\n{card_text}"
    )
    # The humanized forms must actually have painted.
    assert "code generation" in card_text and "backend systems" in card_text, (
        "the humanized enum values ('code generation' / 'backend systems') did "
        f"not paint — humanization missing. Card painted:\n{card_text}"
    )
    # Belt-and-braces: NO snake_case identifier survives anywhere in the card.
    residual = _SNAKE.findall(card_text)
    assert not residual, (
        "a raw snake_case code symbol leaked into the painted routing-label card: "
        f"{residual}. Card painted:\n{card_text}"
    )
