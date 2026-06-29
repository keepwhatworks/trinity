"""Guard: the eval-card helper PROSE must brand result examples (Claude / GPT /
Gemini), never the raw dispatch slug — matching the leaderboard ROWS directly
above it, which already render `formatProviderLabel` brands.

Found 2026-06-07 dogfooding the rendered eval card: the leaderboard listed
"Claude / GPT / Gemini" but the helper line right under it said "claude wins
REFRAME, codex wins COMPRESSION" — the same provider (codex == GPT) named two
ways on ONE card. That's the [[raw_slug_display_275_scope]] split, but in a
STATIC template string the dynamic-binding browser guard
(test_launchpad_provider_label_brand_browser) can't see — it only checks the
vue-bound table headers + chart legend, not the prose.

This is a fast render-level assertion (no browser): `render_launchpad_html`
emits the prose verbatim, so a plain substring check pins it. Mutation-provable:
revert "GPT wins COMPRESSION" → "codex wins COMPRESSION" and this reds.

Scope discipline (the #275 trap): a CLI ARGUMENT is not a display label. The
`eval-show --target <slug>` / `eval-run --target <slug>` examples resolve to the
actual target SLUG (the literal token a user types) and correctly stay slugs —
so this guard targets ONLY the result-description parenthetical ("wins
COMPRESSION"), never the command examples.
"""
from __future__ import annotations

import re

from trinity_local.launchpad_template import render_launchpad_html


def test_eval_card_result_prose_uses_brand_not_slug():
    html = render_launchpad_html(page_data={})

    # The per-axis matrix EXAMPLE describes which provider wins which axis — a
    # result-display context → must read the brand. The leaderboard rows above
    # render GPT/Gemini; the prose must agree.
    assert "GPT wins COMPRESSION" in html, (
        "eval-card per-axis prose must brand the result example as 'GPT wins "
        "COMPRESSION' (matching the branded leaderboard rows), not the raw slug."
    )
    # The exact slug-leak the dogfood caught must be gone.
    assert "codex wins COMPRESSION" not in html, (
        "eval-card prose leaked the dispatch slug 'codex wins COMPRESSION' — brand "
        "it GPT to match the leaderboard (same provider, one name per card; #275)."
    )

    # No result-description sentence ("<x> wins <AXIS>") may use a brand-split slug
    # (codex/antigravity). Belt-and-suspenders against a sibling example creeping in.
    leaked = re.findall(r"\b(codex|antigravity)\s+wins\s+[A-Z]", html)
    assert not leaked, (
        f"eval-card result prose names a provider by its dispatch slug: {leaked}. "
        "Result examples render the brand (GPT/Gemini); only CLI args stay slugs."
    )
