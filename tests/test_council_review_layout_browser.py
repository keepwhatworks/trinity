"""Regression: a 2-member council's two responses must render SIDE BY SIDE.

Found 2026-06-01 while eyeballing live council pages: 2-member councils are the
DOMINANT shape (445 of 562 outcomes = 79% on the real corpus — most councils
dispatch a pair, not the full trio), but the layout only special-cases EXACTLY 3
members (`answers-grid-three` → fixed 3 columns). The 2-member case rides on the
base `.answers-grid` = `grid-template-columns: repeat(auto-fit, minmax(380px,
1fr))`. The side-by-side comparison IS the painkiller — seeing the two providers'
answers next to each other is the whole point — so a CSS regression that stacked
them (e.g. bumping the `minmax(380px…)` past half the content width, or a flex
refactor) would silently gut the dominant render, and the existing string-level
class test (which only checks `answers-grid-three` IS present for 3 members)
would stay green.

This renders the real `render_unified_council_page` HTML (the static review page
written after every council — council_runner.py) at a 1280px desktop viewport and
asserts the two `.answer-card`s share a row (same top, distinct columns, no
overflow). Slow-marked (chromium); skips when Playwright/chromium absent.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _two_member_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_2m",
        task_cluster_id="cluster_2m",
        task_text="Is diffusion the successor to transformers?",
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer for the user.",
        created_at="2026-06-01T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_2m",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                                output_text="Claude's answer. " * 40),
            CouncilMemberResult(provider="codex", model="gpt-5.5",
                                output_text="Codex's answer. " * 40),
        ],
        synthesis_output="# Compare\n\nClaude reframes; Codex enumerates.",
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_two_member_answers_render_side_by_side():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _two_member_html()
    tmp = Path(tempfile.mkdtemp()) / "council_2m.html"
    tmp.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            page.goto(f"file://{tmp}")
            page.wait_for_timeout(500)
            geom = page.evaluate(
                """() => {
                  const cards = Array.from(document.querySelectorAll('.answers-grid .answer-card'));
                  const r = cards.map(c => c.getBoundingClientRect());
                  return {
                    n: cards.length,
                    boxes: r.map(b => ({top: Math.round(b.top), left: Math.round(b.left),
                                        right: Math.round(b.right), width: Math.round(b.width)})),
                    overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                  };
                }"""
            )
        finally:
            browser.close()

    assert geom["n"] == 2, f"expected 2 answer cards, got {geom['n']}"
    a, b = geom["boxes"]
    # Same row: tops within a small tolerance (NOT stacked — stacked would put
    # b.top well below a.bottom).
    assert abs(a["top"] - b["top"]) <= 8, (
        f"the two answers are STACKED, not side by side (tops {a['top']} vs {b['top']}) "
        "— the dominant 2-member painkiller layout regressed"
    )
    # Distinct columns, left-to-right, no overlap.
    assert a["left"] < b["left"], f"cards not laid out left-to-right: {geom['boxes']}"
    assert b["left"] >= a["right"] - 2, f"answer columns overlap: {geom['boxes']}"
    # Each card is a real fraction of the width (not collapsed to a sliver).
    assert a["width"] > 300 and b["width"] > 300, f"answer columns too narrow: {geom['boxes']}"
    assert geom["overflow_x"] <= 4, f"2-member council overflows horizontally: {geom['overflow_x']}px"


def _three_member_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_3m",
        task_cluster_id="cluster_3m",
        task_text="Is diffusion the successor to transformers?",
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer for the user.",
        created_at="2026-06-02T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_3m",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                                output_text="Claude's answer. " * 40),
            CouncilMemberResult(provider="codex", model="gpt-5.5",
                                output_text="Codex's answer. " * 40),
            CouncilMemberResult(provider="antigravity", model="gemini-3.1-pro",
                                output_text="Gemini's answer. " * 40),
        ],
        synthesis_output="# Compare\n\nThree-way split.",
        created_at="2026-06-02T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_three_member_answers_stack_on_mobile():
    """Mobile guard (2026-06-02, browser-verified): the 3-member painkiller uses
    `answers-grid-three` (fixed 3 columns on desktop), and Trinity's mobile
    direction is the review-link companion — so on a phone the three answers MUST
    stack into a single readable column (via the `@media (max-width: 768px)`
    collapse), not stay 3 squished/overflowing columns. The council page had a
    desktop side-by-side guard but NO mobile guard (only the launchpad had one,
    #291). Renders the real `render_unified_council_page` at 375px and asserts the
    cards stack with no horizontal overflow — mutation: drop the 768px media query
    and the cards stay side-by-side (tops equal) → this reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _three_member_html()
    tmp = Path(tempfile.mkdtemp()) / "council_3m.html"
    tmp.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 375, "height": 812}).new_page()
            page.goto(f"file://{tmp}")
            page.wait_for_timeout(500)
            geom = page.evaluate(
                """() => {
                  const cards = Array.from(document.querySelectorAll('.answers-grid .answer-card'));
                  const r = cards.map(c => c.getBoundingClientRect());
                  return {
                    n: cards.length,
                    boxes: r.map(b => ({top: Math.round(b.top), left: Math.round(b.left),
                                        right: Math.round(b.right), width: Math.round(b.width)})),
                    overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                    viewport: window.innerWidth,
                  };
                }"""
            )
        finally:
            browser.close()

    assert geom["n"] == 3, f"expected 3 answer cards, got {geom['n']}"
    boxes = geom["boxes"]
    # Stacked: each card's top is well below the previous card's (single column).
    for i in range(1, 3):
        assert boxes[i]["top"] > boxes[i - 1]["top"] + 40, (
            f"3-member answers did NOT stack on a 375px phone (card {i} top "
            f"{boxes[i]['top']} not below card {i-1} top {boxes[i-1]['top']}) — "
            f"the answers-grid-three mobile collapse regressed: {boxes}"
        )
    # All cards left-aligned in the single column (same left edge).
    assert all(abs(b["left"] - boxes[0]["left"]) <= 2 for b in boxes), (
        f"stacked cards should share a left edge (one column): {boxes}"
    )
    # No horizontal overflow at mobile width.
    assert geom["overflow_x"] <= 4, (
        f"3-member council overflows horizontally at 375px: {geom['overflow_x']}px"
    )


def _code_heavy_html() -> str:
    """A council whose member answers carry a WIDE code block + a long
    unbreakable token — what a coding council actually returns, vs the trivial
    wrapping prose the stack test uses. The fenced <pre> renders `white-space:
    nowrap`-ish (pre), so without the answer-card `min-width:0` + markdown-body
    containment, the no-wrap line stretches the grid card past the viewport."""
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        PromptBundle,
    )

    code_answer = (
        "Here's the fix:\n\n```python\n"
        "result = await client.dispatch_council_member_with_a_really_long_method_name("
        "provider=provider, task=task, timeout=600, retries=3)\n"
        "VERY_LONG_CONSTANT = 'https://example.com/some/really/long/path/that/keeps/"
        "going/and/going/and/going/forever/until/it/overflows'\n"
        "```\n\nAnd a bare token: "
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    )
    bundle = PromptBundle(
        bundle_id="bundle_code", task_cluster_id="cluster_code",
        task_text="How should I dispatch a council member with a timeout?",
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-06-07T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_code", bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id, primary_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="claude-opus-4-8", output_text=code_answer),
            CouncilMemberResult(provider="codex", model="gpt-5.5", output_text=code_answer),
        ],
        synthesis_output="# Compare\n\n```\n" + ("x = " + "y" * 120) + "\n```\n",
        created_at="2026-06-07T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_code_heavy_answer_no_horizontal_overflow_at_mobile():
    """The unified council review page is the "review-link companion" people open
    on phones. A member answer with a wide code block / long unbreakable token
    must NOT force horizontal page scroll at 375px.

    Found 2026-06-07 dogfooding: the existing mobile guard
    (test_three_member_answers_stack_on_mobile) used trivial wrapping prose, so
    it never exercised the real overflow risk — a coding council's code blocks.
    With a realistic code-heavy answer the page overflowed by ~857px (the
    `.answer-card` grid item expanded to ~1214px to fit the no-wrap <pre>: the
    classic grid `min-width:auto` trap, which `pre { overflow-x: auto }` can't
    fix because the CARD itself grew). Fixed with `.answer-card { min-width: 0 }`
    + `.markdown-body pre/table { max-width:100%; overflow-x:auto }` + word-break.

    Mutation: drop `.answer-card { min-width: 0 }` → the page overflows
    horizontally at 375px → this reds. The wide code still scrolls WITHIN its
    <pre> (that element's intrinsic width exceeds the viewport, which is fine —
    only document scrollWidth past the viewport means a page-level scroll)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _code_heavy_html()
    tmp = Path(tempfile.mkdtemp()) / "council_code.html"
    tmp.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 375, "height": 812}).new_page()
            page.goto(f"file://{tmp}")
            page.wait_for_timeout(500)
            geom = page.evaluate(
                """() => {
                  let worst=null, max=window.innerWidth;
                  for (const el of document.querySelectorAll('*')) {
                    const r = el.getBoundingClientRect();
                    if (r.right > max && r.width > 0) {
                      max = r.right;
                      worst = el.tagName + '.' + String(el.className||'').split(' ')[0]
                              + ' w=' + Math.round(r.width);
                    }
                  }
                  return {
                    overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    worst,
                  };
                }"""
            )
        finally:
            browser.close()

    assert geom["overflow_x"] <= 4, (
        f"code-heavy council answer overflows the 375px phone viewport by "
        f"{geom['overflow_x']}px (scrollWidth={geom['scrollWidth']}). The wide "
        f"<pre> must scroll within the card, not stretch the page. "
        f"Widest off-viewport element: {geom['worst']}"
    )


def _claims_long_token_html() -> str:
    """A council whose structured agreed/disagreed claims carry a long unbreakable
    token — what dev/code council content actually produces (a long identifier or
    URL in a claim/why_matters). The claims section was added 2026-06-07; this is
    its mobile-overflow risk."""
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    long_token = "averylongunbreakableidentifier_dispatch_council_member_with_timeout_and_retries_that_never_wraps_on_a_phone"
    bundle = PromptBundle(
        bundle_id="b_cl", task_cluster_id="c_cl", task_text="Cache per-call or in-process?",
        goal="g", comparison_instructions="cmp", created_at="2026-06-07T00:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="r_cl", bundle_id="b_cl", task_cluster_id="c_cl",
        primary_provider="claude", primary_model="claude-opus-4-8", winner_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="m", output_text="A"),
            CouncilMemberResult(provider="codex", model="m", output_text="B"),
        ],
        synthesis_output="# Compare\n\nBoth fine.",
        created_at="2026-06-07T00:05:00+00:00",
        routing_label=CouncilRoutingLabel(
            winner="claude", runner_up="codex", confidence="high", task_type="design",
            agreed_claims=[f"They concur the config flag is {long_token}"],
            disagreed_claims=[{
                "claim": f"Per-call vs in-process when {long_token} is set",
                "providers_for": ["claude"], "providers_against": ["codex"],
                "why_matters": f"leaks across tenants under load — {long_token}",
            }],
        ),
    )
    return render_unified_council_page(bundle, outcome)


def test_structured_claims_long_token_no_horizontal_overflow_at_mobile():
    """The static review page's NEW structured agreed/disagreed-claims section
    (shipped 2026-06-07) renders corpus text on the review-link companion people
    open on phones. A claim or why_matters carrying a long unbreakable token (a
    dev identifier / URL) overflowed the 375px viewport by ~490px (the claim
    <span> didn't wrap). Fixed with `.routing-label-card { overflow-wrap:
    break-word }`. Asserts the sections render AND no page overflow at 375px.
    Mutation: drop the overflow-wrap rule → ~490px overflow → reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _claims_long_token_html()
    tmp = Path(tempfile.mkdtemp()) / "council_claims.html"
    tmp.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 375, "height": 812}).new_page()
            page.goto(f"file://{tmp}")
            page.wait_for_timeout(500)
            geom = page.evaluate(
                """() => {
                  const txt = document.body.innerText || '';
                  let worst=null, max=window.innerWidth;
                  for (const el of document.querySelectorAll('*')) {
                    const r = el.getBoundingClientRect();
                    if (r.right > max && r.width > 0) { max=r.right;
                      worst = el.tagName + '.' + String(el.className||'').split(' ')[0] + ' w=' + Math.round(r.width); }
                  }
                  return {
                    agreedShown: /Where they agreed/.test(txt),
                    disagreedShown: /Where they disagreed/.test(txt),
                    overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    worst,
                  };
                }"""
            )
        finally:
            browser.close()

    assert geom["agreedShown"] and geom["disagreedShown"], (
        f"the structured claims sections didn't render — overflow check would be vacuous: {geom}"
    )
    assert geom["overflow_x"] <= 4, (
        f"structured claims with a long token overflow the 375px phone viewport by "
        f"{geom['overflow_x']}px (scrollWidth={geom['scrollWidth']}) — the claim must "
        f"wrap inside the routing card. Widest element: {geom['worst']}"
    )
