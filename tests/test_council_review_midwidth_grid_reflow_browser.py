"""Browser guard: the 3-member ``answers-grid-three`` on the STATIC council review
page must REFLOW correctly across the mid-width tablet band — 3 columns on a wide
desktop, 2 columns through the 769–1200px tablet band, 1 column at/below 768px — and
NEVER overflow the page horizontally at any of those widths.

The pre-existing council layout coverage drove the 3-member grid at only TWO points:
1280px (3 columns, ``test_three_member`` sibling) and 375px (1-column mobile stack,
``test_three_member_answers_stack_on_mobile``). The entire MID-WIDTH tablet band — the
769–1200px range where the grid is supposed to drop to 2 columns, plus the 768px
1-column boundary for a 3-member grid — was NEVER driven with a real geometry
assertion (the only mid-band viewports in the council browser suite, 1100/1200, are the
2-member ``.answers-grid`` auto-fit case). Driving the real
``render_unified_council_page`` 3-member grid across 1280/1024/900/768 (UX sweep Iter
86, 2026-06-18) confirmed the reflow + overflow are CLEAN — this guard locks that in.

The reflow is governed by two media queries in ``render_unified_council_page``:
``@media (max-width: 1200px) { .answers-grid-three { ...repeat(2,...) } }`` (3→2) and
``@media (max-width: 768px) { .answers-grid, .answers-grid-three { 1fr } }`` (→1). A
regression that breaks either rule — or that drops ``.answer-card { min-width: 0 }`` so
a wide code block / long token stretches a grid track past the viewport — would leave a
real tablet user stuck on a cramped/overflowing 3-up while the two EXISTING 3-member
guards (1280 + 375) stay green. This guard bites that whole class.

Renders the real page into ``review_pages/`` with vendor assets published into a
sibling ``portal_pages/vendor/`` (so petite-vue mounts cleanly — no ``{{ }}`` leak),
seeds a realistic 3-member coding council (long prose + a WIDE code block + a long
unbreakable token + structured claims), and asserts at each width: the expected column
count (distinct grid-item left edges), no page-level horizontal overflow
(``documentElement.scrollWidth <= clientWidth`` AND no element's right edge past the
viewport), no raw-template leak. Synthetic data only; no PII. Slow + browser marked.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# A WIDE code block (the real coding-council shape) + a long UNBREAKABLE token: the two
# things that historically stretched an answer-card grid track past the viewport.
_WIDE_CODE = (
    "```python\n"
    "def build_cache_key(tenant_id, namespace, payload_hash, region, version, replica):\n"
    "    return f'{tenant_id}:{namespace}:{payload_hash}:{region}:{version}:{replica}:cache'\n"
    "```\n"
)
_LONG_TOKEN = (
    "supercalifragilisticexpialidocious_REALLY_long_unbreakable_token_1234567890ABCDEF"
)


def _render_three_member_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_3m_mid",
        task_cluster_id="cluster_3m_mid",
        task_text=(
            "Fix the multi-tenant caching crash where two tenants with the same payload "
            "hash collide on a shared cache key"
        ),
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-06-01T12:00:00+00:00",
    )
    members = [
        CouncilMemberResult(
            provider="claude",
            model="claude-opus-4-8",
            output_text=("Claude reframes around tenancy isolation. " * 8)
            + "\n\n"
            + _WIDE_CODE,
        ),
        CouncilMemberResult(
            provider="codex",
            model="gpt-5.5",
            output_text="Codex enumerates the failure modes under concurrency. " * 8,
        ),
        CouncilMemberResult(
            provider="antigravity",
            model="gemini-3.1-pro-preview",
            output_text=("Gemini flags an eviction-policy concern at peak. " * 8)
            + "\n\n"
            + _LONG_TOKEN,
        ),
    ]
    routing_label = CouncilRoutingLabel(
        winner="antigravity",
        runner_up="claude",
        confidence="high",
        task_type="design",
        agreed_claims=[
            "Namespace the cache key per tenant",
            "Prefer deterministic key derivation",
            "Add an explicit eviction policy with a measured budget",
        ],
        disagreed_claims=[
            {
                "claim": "Per-call vs shared in-process cache for multi-tenant isolation",
                "providers_for": ["claude"],
                "providers_against": ["codex", "antigravity"],
                "why_matters": (
                    "A shared in-process cache leaks across tenants when two requests "
                    "collide on the key " + _LONG_TOKEN
                ),
            }
        ],
        routing_lesson="prefer_per_call_for_multi_tenant_isolation",
    )
    outcome = CouncilOutcome(
        council_run_id="council_3m_mid",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="antigravity",
        member_results=members,
        synthesis_output="# Synthesis\n\nNamespace the key per tenant. " + _WIDE_CODE,
        routing_label=routing_label,
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


# (viewport width, expected column count) across the reflow boundaries.
#   1280 → 3 columns (wide desktop)
#   1024 → 2 columns (mid of the 769–1200 tablet band; the UNDER-DRIVEN cell)
#    900 → 2 columns (lower tablet band)
#    768 → 1 column  (the 3-member mobile-stack boundary)
_CASES = [(1280, 3), (1024, 2), (900, 2), (768, 1)]


@pytest.mark.parametrize("width,expected_cols", _CASES)
def test_three_member_grid_reflows_and_does_not_overflow_at_midwidths(
    tmp_path, width, expected_cols
):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local import vendor

    # review_pages/<page>.html with a sibling portal_pages/vendor/ so the page's
    # ``../portal_pages/vendor/*`` references resolve (petite-vue mounts → no leak).
    # publish_vendor_files() creates ``<arg>/vendor/`` itself, so pass portal_pages.
    review_dir = tmp_path / "review_pages"
    review_dir.mkdir()
    portal_dir = tmp_path / "portal_pages"
    portal_dir.mkdir()
    vendor.publish_vendor_files(portal_dir)

    page_file = review_dir / "council_3m_mid.html"
    page_file.write_text(_render_three_member_html(), encoding="utf-8")

    errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": width, "height": 1000}
            ).new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"file://{page_file}", wait_until="load", timeout=15000)
            page.wait_for_timeout(700)  # petite-vue mount
            geom = page.evaluate(
                r"""() => {
                    const de = document.documentElement;
                    const vw = window.innerWidth;
                    const cards = [...document.querySelectorAll('.answers-grid .answer-card')];
                    // column count = distinct rounded left edges of the grid items.
                    const xs = new Set(cards.map(c => Math.round(c.getBoundingClientRect().left)));
                    // worst page-level offender: any element whose right edge passes the
                    // viewport (names the culprit so a regression points at the element).
                    let rightWorst = {right: 0, sel: ''};
                    for (const el of document.querySelectorAll('body *')) {
                        const cs = getComputedStyle(el);
                        if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
                        const r = el.getBoundingClientRect();
                        if (r.right > vw + 1 && el.scrollWidth > el.clientWidth
                            && r.right > rightWorst.right) {
                            rightWorst = {right: Math.round(r.right),
                                sel: el.tagName.toLowerCase() + '.' +
                                     String(el.className || '').slice(0, 40)};
                        }
                    }
                    return {
                        scrollW: de.scrollWidth, clientW: de.clientWidth, vw,
                        n: cards.length, cols: xs.size, rightWorst,
                        braceLeak: /\{\{|\}\}/.test(document.body.innerText),
                    };
                }"""
            )
        finally:
            browser.close()

    assert not errors, f"static review page threw at {width}px: {errors}"
    # False-pass guard: the widest 3-member grid must actually render all 3 cards, else
    # both the column-count and overflow assertions would be vacuous.
    assert geom["n"] == 3, (
        f"static review page didn't render the 3-member grid at {width}px "
        f"(cards={geom['n']}) — the reflow/overflow assertions would be a false pass"
    )
    assert not geom["braceLeak"], (
        f"raw {{{{ }}}} leaked at {width}px — petite-vue didn't mount (vendor not "
        "published next to the page?)"
    )
    # THE reflow guard: the 3-member answers-grid-three must drop to the expected
    # column count at each mid-width boundary. A broken @media (max-width:1200px) /
    # (max-width:768px) rule leaves a tablet user stuck on a cramped 3-up while the
    # 1280px + 375px guards stay green.
    assert geom["cols"] == expected_cols, (
        f"3-member answers-grid-three has {geom['cols']} columns at {width}px, expected "
        f"{expected_cols} — the mid-width reflow (@media max-width:1200px→2col, "
        f"max-width:768px→1col) regressed; a tablet user is stuck on a cramped/wrong grid"
    )
    # THE overflow guard: no page-level horizontal scroll at any mid width. A dropped
    # .answer-card{min-width:0} lets a wide code block / long token stretch a track.
    assert geom["scrollW"] <= geom["clientW"] + 1, (
        f"static review page OVERFLOWS horizontally at {width}px: "
        f"documentElement.scrollWidth={geom['scrollW']} > clientWidth={geom['clientW']} "
        f"(worst right-edge offender: {geom['rightWorst']['sel']} @ "
        f"{geom['rightWorst']['right']}px) — a 3-member council answer with a wide code "
        "block / long token stretches the tablet viewport; .answer-card needs min-width:0"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
