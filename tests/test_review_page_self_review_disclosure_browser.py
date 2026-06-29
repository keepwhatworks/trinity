"""Regression: the post-hoc REVIEW HTML page (render_review_html) must DISCLOSE a
self-review — a model grading its OWN output — and must NOT paint that disclosure
on a genuine cross-provider review.

Found 2026-06-23 by a UX sweep driving the post-hoc review page (iter 448). The
review CLI (`trinity-local review --task <id> --reviewer <provider>`) puts NO guard
between the task's `source_provider` and the `--reviewer` arg, so

    trinity-local review --task <claude-sourced-task> --reviewer claude

renders a "Post-Hoc Review" headed "Claude reviewing Claude" — a model critiquing
its own output — with ZERO indication that it is not the independent second opinion
the whole feature exists to provide. That is the SAME disclosure-inversion shape the
eval surfaces already flag: the single `eval-run` terminal and (since iter 446) the
public cross-provider leaderboard PNG both annotate a self-judge "(self)" because,
in their own words, a self-grade "can still look like a conflict of interest
externally" (#35 green-while-degenerate on a saved/shareable artifact). The review
page — also a persistent, openable artifact under `review_pages/` — was the missing
sibling: it disclosed nothing.

This guard DRIVES THE REAL PAGE in Chromium and reads the PAINTED disclosure (not a
string-presence check on the source f-string, which would still match the CSS
comment that names the feature). It asserts:

  * a same-family review (claude/claude, and the web-era fold claude_ai/claude)
    PAINTS a visible self-review note naming the conflict, and
  * a genuine cross-provider review (claude/codex) paints NO such note, and
  * an unknown-source review ("" → never trips it) paints NO such note.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

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
        pytest.skip(f"no launchable chromium for the self-review-disclosure test: {exc}")
    return sp, browser


# (original_provider, reviewer_provider, expect_self_review_note).
_CASES = [
    ("claude", "claude", True),        # same slug → self-review
    ("claude_ai", "claude", True),     # web-era fold → still a self-review
    ("chatgpt", "codex", True),        # GPT family self-review (folded slugs)
    ("claude", "codex", False),        # genuine cross-provider → NO note
    ("", "claude", False),             # unknown source → never a self-review
]


def test_review_page_discloses_self_review_and_only_self_review(tmp_path, monkeypatch):
    from trinity_local.review import ReviewResult
    import trinity_local.review as review_mod

    # Isolate the review_pages output dir via monkeypatch (auto-restored after the
    # test) + tmp_path (auto-cleaned) — never a bare module-attr rebind, which would
    # leave review_pages_dir pointing at a deleted tmp for the rest of the process
    # and pollute any later test that resolves it (the #265 clobber-global class).
    tmp = tmp_path
    monkeypatch.setattr(review_mod, "review_pages_dir", lambda: tmp)

    rendered: list[tuple[str, str, bool, Path]] = []
    for original, reviewer, want_note in _CASES:
        rev = ReviewResult(
            review_id=f"rev-self-{original or 'none'}-{reviewer}",
            task_id="task-self-0001",
            original_provider=original,
            reviewer_provider=reviewer,
            verdict="Partially correct: an edge case is unhandled.",
            issues=["Does not handle empty input."],
            suggestions=["Add a guard clause."],
            reviewed_at="2026-06-23T12:00:00",
        )
        rendered.append((f"{original or 'none'}->{reviewer}", reviewer, want_note,
                         review_mod.render_review_html(rev)))

    sp, browser = _browser()
    failures: list[str] = []
    try:
        page = browser.new_context(
            viewport={"width": 393, "height": 900}
        ).new_page()
        for label, reviewer, want_note, path in rendered:
            page.goto(f"file://{path}")
            page.wait_for_timeout(200)
            # Read the PAINTED note element (not the source / CSS comment): the
            # disclosure is a `.self-review-note` paragraph that exists in the DOM
            # ONLY when the renderer judged it a self-review.
            note = page.locator("p.self-review-note")
            present = note.count() > 0 and note.first.is_visible()
            note_text = note.first.inner_text() if present else ""
            if want_note and not present:
                failures.append(
                    f"[{label}] a self-review (same model grading its own output) "
                    f"painted NO disclosure — 'Post-Hoc Review' reads as a neutral "
                    f"independent critique (the iter-446 self-judge-disclosure shape, "
                    f"now on the review page)"
                )
            if want_note and present and "Self-review" not in note_text:
                failures.append(
                    f"[{label}] disclosure present but did not name the conflict: "
                    f"{note_text!r}"
                )
            if (not want_note) and present:
                failures.append(
                    f"[{label}] a GENUINE cross-provider review painted a spurious "
                    f"self-review disclosure {note_text!r} — the note must fire ONLY "
                    f"when reviewer family == source family"
                )
    finally:
        browser.close()
        sp.stop()

    assert not failures, (
        "post-hoc review self-review disclosure is wrong:\n" + "\n".join(failures)
    )
