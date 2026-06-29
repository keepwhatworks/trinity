"""Guard: the retired user-rating vocabulary must stay out of the launchpad's
user-facing copy. Rating UX was sunset 2026-05-21/22 (chairman picks are the
verdict now); the routing/cortex cards lingered with 'Ratings' / 'rated' copy,
which sent new users hunting for a button that doesn't exist (#215 / review
MED ui-trust finding)."""
from __future__ import annotations

from trinity_local.launchpad_template import render_launchpad_html


def test_no_retired_rating_copy_in_launchpad():
    html = render_launchpad_html(page_data={})
    # Precise retired phrases — not a blanket "rating" ban (CSS comments and
    # the accurate "Trinity learns from redirects, not after-the-fact ratings"
    # line legitimately mention the retired feature).
    for phrase in ("Once you've rated", ">Ratings<", "with every rating", "the bars sharpen with every rating"):
        assert phrase not in html, f"retired rating copy resurfaced: {phrase!r}"
