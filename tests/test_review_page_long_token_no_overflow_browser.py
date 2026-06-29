"""Regression: the post-hoc REVIEW HTML page (render_review_html) must not blow the
page out horizontally when a reviewer emits a long UNBREAKABLE token — a file path,
URL, regex, or run-together identifier — in the verdict, an issue, or a suggestion.

Found 2026-06-22 driving the post-hoc review page across the breakpoint ladder
(UX sweep iter 287). The reviewer prose lands in `.verdict-box` and `.alert-box`
boxes that shipped with the browser default `overflow-wrap: normal`, so a single
long token never wrapped: a verdict naming a deep `/Users/.../handler_…py` path and
a run-together suggestion each stretched a 393px phone to a 1054px document
(scrollWidth 1054 vs clientWidth 393 → a 661px horizontal scroll the user must drag
across). This is the SAME unbreakable-token class the design system already fixed
for `h1,h2,h3` and `code,pre` — the `.alert-box` / `.verdict-box` review boxes were
the one council-family surface that carries the most LLM-emitted free prose yet had
no break rule. A code reviewer routinely cites paths / regexes / URLs / identifiers,
so this is the common case, not a corner.

Fix: `overflow-wrap: break-word; word-break: break-word;` on `.alert-box`
(design_system.SHARED_CSS) and `.verdict-box` (review.py local style).

This guard DRIVES THE REAL PAGE in Chromium at the narrowest production breakpoints
and reads geometry (documentElement scroll vs client width; each box's content
scrollWidth vs its clientWidth) — NOT a CSS string-presence check on the source.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# A run-together token with no break opportunity in each of the three reviewer
# regions the page paints. Each is long enough that at <=393px it cannot fit on
# one line — so without a break rule it forces a horizontal scrollbar.
_LONG_VERDICT = (
    "Incorrect at "
    "/Users/someverylongusername/projects/deeply/nested/module/submodule/"
    "handler_implementation_with_a_very_long_filename.py here."
)
_LONG_ISSUE = (
    "Replacethewholeregexwithaurllibparsecallwhichhandlesalledgecasescorrectly"
    "andcannotcatastrophicallybacktrackonadversarialinputstrings."
)
_LONG_SUGGESTION = (
    "Anotherextremelylongunbreakablesuggestiontokenthatmustwrapinsideitsbox"
    "ratherthanblowingoutthewholepagehorizontallyonanarrowphoneviewport."
)

# The narrow production widths where an unbreakable token bites hardest.
_NARROW_WIDTHS = [393, 375, 320]


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch()
    except Exception as exc:  # chromium not installed
        sp.stop()
        pytest.skip(f"no launchable chromium for the review-overflow test: {exc}")
    return sp, browser


def test_review_page_long_token_does_not_overflow():
    from trinity_local.review import ReviewResult
    import trinity_local.review as review_mod

    tmp = Path(tempfile.mkdtemp(prefix="trinity-review-ovf-"))
    review_mod.review_pages_dir = lambda: tmp  # type: ignore[assignment]

    rev = ReviewResult(
        review_id="rev-long-token",
        task_id="task-overflow-0001",
        original_provider="codex",
        reviewer_provider="claude",
        verdict=_LONG_VERDICT,
        issues=[_LONG_ISSUE],
        suggestions=[_LONG_SUGGESTION],
        reviewed_at="2026-06-22T00:00:00",
    )
    path = review_mod.render_review_html(rev)

    sp, browser = _browser()
    problems: list[str] = []
    try:
        for width in _NARROW_WIDTHS:
            page = browser.new_context(
                viewport={"width": width, "height": 900}
            ).new_page()
            page.goto(f"file://{path}")
            page.wait_for_timeout(250)
            geo = page.evaluate(
                """() => {
                    const de = document.documentElement;
                    const v = document.querySelector('.verdict-box');
                    const issue = document.querySelector('.alert-box.danger');
                    const sugg = document.querySelector('.alert-box.success');
                    const box = (el) => el ? {
                        sw: el.scrollWidth, cw: el.clientWidth,
                        txt: (el.innerText || '').trim(),
                    } : null;
                    return {
                        docScrollW: de.scrollWidth,
                        docClientW: de.clientWidth,
                        verdict: box(v),
                        issue: box(issue),
                        sugg: box(sugg),
                    };
                }"""
            )
            page.close()

            # PRECONDITION (so a no-render can't vacuously pass): the three boxes
            # painted and actually carry the long tokens.
            for name, want_substr in (
                ("verdict", "handler_implementation_with_a_very_long_filename.py"),
                ("issue", "Replacethewholeregexwithaurllibparse"),
                ("sugg", "Anotherextremelylongunbreakable"),
            ):
                b = geo[name]
                assert b is not None, (
                    f"@{width}px: review .{name} box did not render — the long-token "
                    "overflow guard cannot run (precondition failed)"
                )
                assert want_substr in b["txt"], (
                    f"@{width}px: the long token did not paint in .{name} "
                    f"(got {b['txt'][:60]!r}) — precondition failed"
                )

            # BITE 1: the whole document must fit the viewport (no horizontal
            # scrollbar). The unbroken token blew this to ~1054px at 393.
            if geo["docScrollW"] > geo["docClientW"] + 1:
                problems.append(
                    f"@{width}px: documentElement scrollWidth "
                    f"{geo['docScrollW']} > clientWidth {geo['docClientW']} — a long "
                    "unbreakable reviewer token blew the review page out horizontally "
                    "(missing overflow-wrap on .verdict-box / .alert-box)"
                )
            # BITE 2: each box's content must wrap inside it (its own content
            # scrollWidth may not exceed its client width). This catches the case
            # where the box is clipped but the document happens to fit.
            for name in ("verdict", "issue", "sugg"):
                b = geo[name]
                if b["sw"] > b["cw"] + 1:
                    problems.append(
                        f"@{width}px: review .{name} content scrollWidth "
                        f"{b['sw']} > clientWidth {b['cw']} — the long token did not "
                        "wrap inside its box (missing word-break/overflow-wrap)"
                    )
    finally:
        browser.close()
        sp.stop()

    assert not problems, "review page overflowed on a long reviewer token:\n" + "\n".join(
        problems
    )
