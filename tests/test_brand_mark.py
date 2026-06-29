"""The three-pip die brand mark — favicon + launchpad eyebrow.

The mark is the braille ⠕ (U+2815, dots-1-3-5): three pips at top-left,
middle-right, bottom-left — three providers, one roll, a judgment call. It's the
keepwhatworks.com favicon + the extension toolbar icon, drawn as a pure-geometry
SVG (the ARRANGEMENT, not the glyph) so it can't tofu the way the ⠕ char did.
Guard that the favicon + inline die carry all three pips in the ⠕ arrangement
(not a diagonal "3" face — founder 2026-06-12 "use this everywhere") and that
the launchpad eyebrow uses the die, not the old braille char.
"""
from __future__ import annotations

from trinity_local.design_system import DIE_MARK_INLINE_SVG, FAVICON_LINK

# The braille ⠕ pip centers on the 32-grid: top-left, middle-right, bottom-left.
# A diagonal "3" face would instead be (9,9)/(16,16)/(23,23) — explicitly NOT
# this; the left column shares x, the middle pip steps right.
_FAVICON_PIPS = ("cx='11.5' cy='9'", "cx='20.5' cy='16'", "cx='11.5' cy='23'")
_INLINE_PIPS = ('cx="11.5" cy="9"', 'cx="20.5" cy="16"', 'cx="11.5" cy="23"')


def test_favicon_is_three_pip_die():
    # All three ⠕ pips present, in the braille (not diagonal) arrangement.
    for circ in _FAVICON_PIPS:
        assert circ in FAVICON_LINK, f"favicon lost the {circ} pip — not the ⠕ die anymore"
    # A diagonal "3" face must NOT sneak back in (the 2026-06-12 regression).
    assert "cx='16' cy='16'" not in FAVICON_LINK, "favicon reverted to the diagonal '3' face"
    assert "⠕" not in FAVICON_LINK


def test_inline_die_is_three_pips_and_self_contained():
    for circ in _INLINE_PIPS:
        assert circ in DIE_MARK_INLINE_SVG, f"inline die lost the {circ} pip"
    assert 'cx="16" cy="16"' not in DIE_MARK_INLINE_SVG, "inline die reverted to the diagonal '3'"
    # No braces — safe to interpolate into the launchpad f-string template.
    assert "{" not in DIE_MARK_INLINE_SVG and "}" not in DIE_MARK_INLINE_SVG


def test_launchpad_eyebrow_uses_die_not_braille():
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data={"cold_start": {}, "memory": {}})
    assert "die-mark" in html, "launchpad eyebrow lost the die mark"
    assert "⠕" not in html, "the ⠕ braille tofu glyph is back in the launchpad"


def test_share_card_footer_carries_the_drawn_die():
    """The share-card footer wordmark paints the three-pip die (teal pixels in
    the bottom-right corner) — the #276 fix. Drawn vector, so the ⠕ tofu that
    forced the glyph's removal can never come back."""
    import io

    from PIL import Image

    from trinity_local.me_card import CardLensData, render_me_card
    from trinity_local.share_card_base import COLOR_ACCENT

    png = render_me_card(CardLensData(
        lens_pole_a="a", lens_pole_b="b", failure_a="c", failure_b="d",
        orderings=[("e", "f")],
    ))
    img = Image.open(io.BytesIO(png)).convert("RGB")
    W, H = img.size
    px = img.load()
    ar, ag, ab = COLOR_ACCENT
    teal = sum(
        1
        for y in range(H - 80, H - 20)
        for x in range(W - 400, W)
        if abs(px[x, y][0] - ar) < 45 and abs(px[x, y][1] - ag) < 45 and abs(px[x, y][2] - ab) < 45
    )
    assert teal > 30, f"share-card footer die not rendering (only {teal} teal px)"
