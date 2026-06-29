"""Real-browser HEADING-OUTLINE / LANDMARK guard for the STATIC council review page.

WCAG 1.3.1 (Info & Relationships) / 2.4.6 (Headings & Labels). Screen-reader users
navigate a page by heading and by landmark. The static council review page
(`render_unified_council_page`, the `?council_id=` shareable artifact) had TWO
structural breaks a screen-reader user hits navigating by heading (found Iter 238 by
reading the REAL rendered AX/heading tree, not a source grep):

  1. A COMPETING SECOND/THIRD <h1>. The sticky topbar carried `<h1 class="topbar-
     title">Council review</h1>` — chrome/branding, NOT the page's primary heading —
     which competed with the council-question `<h1>` in <main> for the sole-h1 slot.
     Worse, the chairman synthesis is markdown-rendered (`render_markdown`): a
     synthesis that opens with "# Verdict" emitted a LITERAL <h1> mid-page — a THIRD
     <h1> AND a top-level outline break under the <h2> "Comparative Analysis" section.

  2. Fixes (Iter 238, both visually byte-identical):
       - the topbar label was demoted from <h1> to <p> (the `.topbar-title` CSS keys
         on the class, not the tag, so the rendered pixels are unchanged); and
       - `render_markdown` now ships every content heading with a DEMOTED aria-level
         (the visible <hN> tag — and its font-size — is unchanged, only the announced
         level moves), so a content "# Heading" never announces as a page-level <h1>.

This guard DRIVES the real rendered page and reads the heading tree exactly as AT
sees it (the ANNOUNCED level = aria-level when present, else the tag level), and
asserts:
  * exactly ONE announced-level-1 heading on the page (the council question), and
  * a <main> landmark exists, and
  * the markdown-rendered synthesis "# Verdict" is announced BELOW level 1 (it does
    not compete for the page's h1).

A bare source-string grep can't see the COMPUTED announced level (it would have to
re-implement the AccName/aria-level resolution) or the view-state visibility — only
execution against the rendered DOM reveals the outline a screen reader walks.
Synthetic data only; no PII; no dispatch fired.

Mirrors the prod-shaped layout of test_council_review_static_card_affordance_browser.py
so petite-vue mounts (the page loads it from ../portal_pages/vendor/petite-vue.iife.js).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


# Reads every real heading (h1-h6 + role=heading) and returns, for each, the
# ANNOUNCED level a screen reader uses: aria-level if present, else the tag level.
_OUTLINE_JS = """
() => {
  const out = [];
  document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]').forEach(el => {
    const cs = getComputedStyle(el);
    const fixed = cs.position === 'fixed' || cs.position === 'sticky';
    const visible = !(cs.display === 'none' || cs.visibility === 'hidden' ||
                      (el.offsetParent === null && !fixed));
    if (!visible) return;
    let announced;
    const ar = el.getAttribute('aria-level');
    if (el.getAttribute('role') === 'heading') {
      announced = parseInt(ar || '0', 10) || 0;
    } else {
      announced = ar ? (parseInt(ar, 10) || parseInt(el.tagName[1], 10))
                     : parseInt(el.tagName[1], 10);
    }
    out.push({tag: el.tagName.toLowerCase(), announced: announced,
              text: (el.textContent || '').trim().slice(0, 50),
              cls: el.className || ''});
  });
  return {
    headings: out,
    mainCount: document.querySelectorAll('main, [role=main]').length,
  };
}
"""


def _render_static_council_page() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_outline",
        task_cluster_id="cluster_outline",
        task_text="Which caching strategy for the read path?",
        goal="Pick the strongest answer.",
        comparison_instructions="Prefer specificity.",
        created_at="2026-06-18T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_outline",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="antigravity",
        member_results=[
            CouncilMemberResult(provider="claude", model="opus-4-8",
                                output_text="## Approach\nUse an LRU cache."),
            CouncilMemberResult(provider="antigravity", model="gemini-3.1-pro",
                                output_text="Memoize at the boundary."),
            CouncilMemberResult(provider="codex", model="gpt-5.5",
                                output_text="Profile first, then cache."),
        ],
        # The chairman synthesis OPENS with a markdown H1 — the exact content that
        # used to emit a literal competing <h1>. This is the discriminating fixture.
        synthesis_output="# Verdict\n\nThe **Gemini** answer wins on specificity.\n\n## Why\n\nIt names the boundary.",
        routing_label=CouncilRoutingLabel(winner="antigravity", task_type="architecture"),
        created_at="2026-06-18T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_static_council_review_has_one_h1_and_a_main_landmark():
    """The static review page must present a single, navigable heading outline:

      - exactly ONE announced-level-1 heading (the council question), AND
      - a <main> landmark, AND
      - the markdown synthesis "# Verdict" announced BELOW level 1.

    Founder symptom (Iter 238): "the council review page had THREE competing <h1>s —
    the topbar chrome label 'Council review', the question, AND the synthesis
    '# Verdict' (a content markdown heading rendered as a literal <h1>) — so a
    screen-reader user navigating by heading lands on three 'top-level' headings and
    can't tell which is the page title (WCAG 1.3.1 / 2.4.6)."
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.vendor import publish_vendor_files

    root = Path(tempfile.mkdtemp(prefix="trinity-cr-outline-"))
    (root / "review_pages").mkdir()
    (root / "portal_pages").mkdir()
    publish_vendor_files(root / "portal_pages")
    page_path = root / "review_pages" / "council_outline.html"
    page_path.write_text(_render_static_council_page(), encoding="utf-8")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed in this env
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.add_init_script(
                "window.__TRINITY_DISPATCH__ = { dispatch: (o)=>{ "
                "if (o.onResult) o.onResult({ok:true}); } };"
            )
            page.goto(f"file://{page_path}")
            page.wait_for_timeout(900)  # let petite-vue mount + markdown render

            data = page.evaluate(_OUTLINE_JS)
            headings = data["headings"]
            outline = [(h["announced"], h["text"]) for h in headings]

            # PRECONDITION A: the page rendered a heading tree at all.
            assert len(headings) >= 4, (
                f"expected the council review heading tree to render (question + "
                f"sections + members), got {len(headings)} headings: {outline} — the "
                "page did not mount; the outline assertions would be a false pass"
            )

            # PRECONDITION B (discriminating): the synthesis "# Verdict" markdown
            # heading IS rendered — so an h1-competition would actually be present
            # if the demotion regressed. Without this, the test could pass on a page
            # that simply never rendered the synthesis.
            verdict = [h for h in headings if h["text"] == "Verdict"]
            assert verdict, (
                "the markdown synthesis heading 'Verdict' did not render — the "
                "discriminating fixture (a synthesis opening with '# Verdict') is "
                "absent, so the no-competing-h1 assertion would be vacuous"
            )

            # ASSERTION 1: exactly one announced level-1 heading (the question).
            level1 = [h for h in headings if h["announced"] == 1]
            assert len(level1) == 1, (
                f"the council review page presents {len(level1)} announced-level-1 "
                f"headings, expected exactly 1 (the council question). Got h1s: "
                f"{[h['text'] + ' [' + h['cls'] + ']' for h in level1]}. Founder "
                "symptom: the topbar chrome label 'Council review' was an <h1> AND "
                "the synthesis '# Verdict' rendered as a literal <h1> — three "
                "competing top-level headings a screen reader can't disambiguate "
                "(WCAG 1.3.1 / 2.4.6)."
            )
            assert "caching strategy" in level1[0]["text"], (
                f"the single <h1> is '{level1[0]['text']}', expected the council "
                "question to be the page's primary heading"
            )

            # ASSERTION 2: the synthesis markdown heading is announced BELOW level 1.
            assert verdict[0]["announced"] > 1, (
                f"the markdown synthesis heading 'Verdict' announces at level "
                f"{verdict[0]['announced']} — a content '# Heading' must NOT announce "
                "as a page-level <h1> competing with the question (render_markdown "
                "heading_offset demotion regressed; WCAG 1.3.1)."
            )

            # ASSERTION 3: a <main> landmark exists so AT can jump to content.
            assert data["mainCount"] == 1, (
                f"expected exactly one <main> landmark, got {data['mainCount']} — a "
                "page with no main landmark is unnavigable by 'jump to main content' "
                "(WCAG 1.3.1)."
            )
        finally:
            browser.close()
