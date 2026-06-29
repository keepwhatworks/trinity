"""Real-browser guard: a STALE / deleted `?council_id=` link degrades to a clean
failure card in the RENDERED DOM — failure message present, the contradictory
optimistic synthesis stub ABSENT — not just present as a source string.

Why this exists. A user lands on `live_council.html?council_id=<id>` from a
bookmark, a shared link, or a launchpad card whose council was since deleted —
the outcome script (`../council_outcomes/<id>.js`) 404s, `loadOutcomeScript`'s
onerror fires, and the segment must flip to `failed: true` with
"Could not load council outcome." The #132 bug class
(test_council_review_missing_status_timeout::
test_synthesis_section_hidden_on_failed_or_canceled_segment) was: right below
that failure card, a contradictory optimistic stage tracker rendered —
"Analysis · QUEUED · Ready to start final comparison" — because
`analysisRowFor(seg)` always returns a stub row and the synthesis-section's
`v-if` lacked a `!seg.failed` guard.

That bug is guarded today ONLY by grepping `_src()` for the `!seg.failed` /
`!seg.canceled` substring in the v-if. A source-string check can pass while the
RENDERED DOM is broken — petite-vue reactivity (the splice-to-replace pattern in
`_loadOutcomeIntoSegment`), the `analysisRowFor` data shape, or the `seg.failed`
assignment could each regress and leave the stub visible with the string guard
still green ([[e2e_chrome_dogfood_finds_real_bugs]]: "always pair string-presence
asserts with one real-browser smoke"). This drives the actual stale-link path in
a real browser and asserts the DOM, closing that pair.

Uses the file:// substrate deliberately: the missing outcome script genuinely
404s there, so onerror fires the failure path deterministically — and file:// is
the documented `portal-html --open-browser` prod path ([[file_substrate_browser_testing]]),
the one MCP browser tools can't reach. Slow + browser marked; skips without
Playwright/chromium; runs in the CI `browser` job.

Mutation-proven: delete the `!seg.failed` guard from the synthesis-section v-if
(the exact #132 regression) → `.synthesis-section` reappears in the DOM beside
the failure card → the `synthesis_in_dom` assertion reds. (Verified by hand
during authoring.)
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_STALE_ID = "council_deleted_or_typod_nonexistent"


def test_stale_council_id_renders_clean_failure_card_no_stub(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # Render the real live-council page into an isolated, EMPTY home — no council
    # is seeded, so any council_id the page is handed is a stale/missing one (the
    # exact bookmark-to-a-deleted-council scenario). write_portal_html first so the
    # vendored petite-vue under ../portal_pages/vendor/ exists — the live page
    # loads it relatively, and without it the app never mounts (no failure card).
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html

    write_portal_html()
    page_path = write_live_council_page()

    url = f"file://{page_path}?council_id={_STALE_ID}"
    page_errs: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.on("pageerror", lambda e: page_errs.append(str(e)[:200]))
            page.goto(url, wait_until="networkidle")
            # The failure card is set from the outcome-script onerror callback —
            # wait for it rather than a fixed sleep so the test isn't timing-flaky.
            page.wait_for_selector("text=Could not load council outcome", timeout=8000)

            body = page.inner_text("body")
            # The synthesis-section element must be REMOVED from the DOM by the
            # `!seg.failed` v-if — not merely visually hidden, and not present
            # with a contradictory "Ready to start final comparison" stub.
            synthesis_in_dom = page.query_selector(".synthesis-section") is not None
            failure_card_in_dom = page.query_selector(".launch-status") is not None
        finally:
            browser.close()

    # The clean terminal state: the failure card is shown.
    assert "Could not load council outcome" in body, (
        "a stale/deleted council_id must surface the explicit failure message, "
        f"not a blank page — body was {body!r}"
    )
    assert failure_card_in_dom, "the .launch-status failure card must be in the DOM"

    # The #132 invariant, asserted on the RENDERED DOM (the source-string guard's
    # real-browser pair): NO contradictory optimistic synthesis stub beside the
    # failure card.
    assert not synthesis_in_dom, (
        "the synthesis-section element is still in the DOM next to 'Council "
        "failed' — the `!seg.failed` v-if guard regressed (#132): the user sees "
        "'Ready to start final comparison' contradicting the failure card"
    )
    for stub in ("Ready to start final comparison", "Analysis ·"):
        assert stub not in body, (
            f"the optimistic stage stub {stub!r} rendered beside the failure card"
        )

    assert not page_errs, f"JS errors on the stale-link page: {page_errs[:4]}"
