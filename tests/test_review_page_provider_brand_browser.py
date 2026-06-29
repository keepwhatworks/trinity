"""Regression: the post-hoc REVIEW HTML page (render_review_html) must name the
MODEL BRAND (Claude / GPT / Gemini) in its provider-attribution prose, never the
raw dispatch SLUG (claude / codex / antigravity).

Found 2026-06-21 by a raw-slug-display sweep of the less-trafficked shareable
artifacts (UX sweep iter 193). The review page's `.meta` line painted

    "antigravity reviewing codex · 2026-06-21T12:00:00"

— two raw dispatch slugs sitting in user-facing prose on a persistent, shareable
HTML artifact. The correct attribution per the #275-resolved convention (council /
launchpad / live-council / unified-review surfaces were all flipped to the model
brand) reads

    "Gemini reviewing GPT · ..."

render_review_html was the one council-family surface the #275 sweep missed:
run_review stores the dispatch slug (the --reviewer arg help literally says
"e.g. antigravity, codex") and the renderer interpolated it straight into the meta
line. CLAUDE.md's naming rule: slugs stay in code/config/paths; user-facing UI uses
the model name.

This guard DRIVES THE REAL PAGE in Chromium and reads the PAINTED `.meta` text (not
a string-presence check on the source) — it asserts the rendered attribution reads
in brands and that no raw dispatch slug survived into the visible pixels.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch()
    except Exception as exc:  # chromium not installed
        sp.stop()
        pytest.skip(f"no launchable chromium for the review-brand test: {exc}")
    return sp, browser


# (original_provider, reviewer_provider, expected painted attribution).
_CASES = [
    ("codex", "antigravity", "Gemini reviewing GPT"),
    ("antigravity", "claude", "Claude reviewing Gemini"),
    ("claude", "codex", "GPT reviewing Claude"),
]


def test_review_page_meta_reads_in_model_brands():
    from trinity_local.review import ReviewResult
    import trinity_local.review as review_mod

    tmp = Path(tempfile.mkdtemp(prefix="trinity-review-brand-"))
    review_mod.review_pages_dir = lambda: tmp  # type: ignore[assignment]

    rendered: list[tuple[str, str, Path]] = []
    for original, reviewer, want in _CASES:
        rev = ReviewResult(
            review_id=f"rev-{original}-{reviewer}",
            task_id="task-xyz-0001",
            original_provider=original,
            reviewer_provider=reviewer,
            verdict="Partially correct: an edge case is unhandled.",
            issues=["Does not handle empty input."],
            suggestions=["Add a guard clause."],
            reviewed_at="2026-06-21T12:00:00",
        )
        rendered.append((want, original, review_mod.render_review_html(rev)))

    sp, browser = _browser()
    leaks: list[str] = []
    try:
        page = browser.new_context(
            viewport={"width": 1280, "height": 900}
        ).new_page()
        for want, original_slug, path in rendered:
            page.goto(f"file://{path}")
            page.wait_for_timeout(300)
            # The attribution meta is the FIRST .meta line on the page (under the
            # "Post-Hoc Review" header). Read the PAINTED text, not the source.
            meta_text = page.locator("p.meta").first.inner_text()
            if want not in meta_text:
                leaks.append(
                    f"painted meta {meta_text!r} did not read {want!r} "
                    f"(#275 raw-slug-display leak on the post-hoc review page)"
                )
            # No raw dispatch slug may survive into the visible meta line.
            for slug in ("antigravity", "codex"):
                if slug in meta_text:
                    leaks.append(
                        f"raw dispatch slug {slug!r} painted in the review "
                        f"meta line {meta_text!r} — user prose must name the "
                        f"model brand (Gemini / GPT)"
                    )
    finally:
        browser.close()
        sp.stop()

    assert not leaks, "review-page provider attribution leaked slugs:\n" + "\n".join(leaks)
