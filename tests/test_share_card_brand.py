"""Share-card PNGs name the MODELS that competed, using the model trio
(Claude / GPT / Gemini) — NOT the harness trio (Claude / Codex / Antigravity)
the launchpad + review web surfaces use.

The bug this guards (found 2026-05-31 by eyeballing the generated cards): the
friendly map was ``{"claude": "Claude", "codex": "GPT", "antigravity":
"Antigravity"}`` — codex got its MODEL brand ("GPT") but antigravity got its
HARNESS brand ("Antigravity"). So the eval card headlined "Antigravity scored
0.50" directly over a "Gemini 3.1 Pro" subhead — the same PNG contradicting
itself. These cards get posted to HN/Twitter, where "Antigravity" reads as a
model nobody's heard of. Per the #239 model-names-in-UI convention, the public
card brand must be the model a reader recognizes.

(The launchpad/review surfaces intentionally use the harness trio — that's a
different surface and is left as-is; see council_review.formatProviderLabel /
launchpad_template's labels map.)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trinity_local.council_card import _provider_display

# slug -> the MODEL brand a reader recognizes (not the harness brand)
MODEL_BRAND = {"claude": "Claude", "codex": "GPT", "antigravity": "Gemini"}
# Harness-only brands that must never headline a public share card.
HARNESS_ONLY = {"Antigravity", "Codex"}


# Glyphs the card font (serif/Helvetica via PIL) cannot render — they come out
# as tofu boxes in the PNG. Found 2026-05-31 eyeballing the eval card: the
# mixed-eval-set warning led with ⚠, which rendered as a box; the ↔ tension
# arrow had the same fate earlier (me_card now uses "vs."). Draw a vector icon
# (eval_card draws an amber triangle-with-bang) instead of embedding the glyph.
#
# U+2815 ⠕ (the former footer brand mark) is now BANNED here too: the founder's
# #276 call (2026-06-09) was to DROP it — the vendored brand fonts don't carry
# it, so it tofu'd on every card. It used to be EXCLUDED pending that decision;
# now the wordmark is glyph-free and the guard enforces it stays that way.
_TOFU_GLYPHS = {
    "⚠": "⚠ (U+26A0 warning sign)",
    "↔": "↔ (U+2194 arrow)",
    "⠕": "⠕ (U+2815 braille — the dropped footer logo, #276)",
}


@pytest.mark.parametrize("mod", ["me_card", "council_card", "share_card_base"])
def test_card_modules_avoid_unrenderable_glyphs(mod):
    """No card module may DRAW a glyph the card font renders as tofu.

    A box character on a card posted to HN/Twitter reads as broken. Use a
    drawn vector icon (PIL polygon/line) or a renderable text substitute.

    Scans STRING LITERALS only (via tokenize) — a glyph mentioned in a
    comment ("the font lacks ⚠") is documentation, never drawn, so it must
    not trip the guard.
    """
    import importlib
    import tokenize

    # f-strings are one STRING token pre-3.12, but split into FSTRING_* parts
    # on 3.12+. Cover both so the scan works across the project's 3.10+ range.
    string_token_types = {tokenize.STRING}
    if hasattr(tokenize, "FSTRING_MIDDLE"):
        string_token_types.add(tokenize.FSTRING_MIDDLE)

    src_path = Path(importlib.import_module(f"trinity_local.{mod}").__file__)
    drawn_glyphs: set[str] = set()
    with src_path.open(encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in string_token_types:
                for glyph in _TOFU_GLYPHS:
                    if glyph in tok.string:
                        drawn_glyphs.add(glyph)
    offending = {g: _TOFU_GLYPHS[g] for g in drawn_glyphs}
    assert not offending, (
        f"{mod}.py has a string literal containing {list(offending.values())}, "
        f"which tofus in the card font. Draw a vector icon or use a renderable "
        f"substitute (see the amber triangle in eval_card or the ↔→'vs.' fix in "
        f"me_card)."
    )


def test_load_font_fallback_honors_size_when_no_candidates(monkeypatch):
    """Cross-environment guard (2026-06-01): on a host where NONE of the
    candidate font paths resolve (Windows, or a minimal Linux container with no
    DejaVu/Liberation), load_font must still honor `size` so the card layout
    doesn't collapse. The old `ImageFont.load_default()` (no size) returned a
    fixed ~10px bitmap, so a 48px headline rendered identical to a 12px footer
    and every card became unreadable off the bundled-font happy path."""
    from trinity_local import share_card_base as scb

    monkeypatch.setattr(
        scb, "_FONT_CANDIDATES", {k: ["/nonexistent/font.ttf"] for k in scb._FONT_CANDIDATES}
    )
    h_big = scb.load_font("serif", 48).getmask("Trinity").getbbox()[3]
    h_small = scb.load_font("regular", 12).getmask("Trinity").getbbox()[3]
    assert h_big > h_small * 2, (
        f"fallback font ignored size: headline={h_big}px ≈ footer={h_small}px "
        f"(layout collapse) — load_font must pass size to load_default()"
    )


def test_font_candidates_cover_linux_and_windows():
    """Beyond macOS, the candidate list must offer a real font for Debian/Ubuntu
    (DejaVu/Liberation) and Windows — otherwise off-Mac hosts fall straight to
    the bitmap default and render an ugly/oversized-fallback card."""
    from trinity_local import share_card_base as scb

    allp = [p for paths in scb._FONT_CANDIDATES.values() for p in paths]
    assert any(("dejavu" in p.lower() or "liberation" in p.lower()) for p in allp)
    assert any(p.startswith("C:/Windows/Fonts") for p in allp)


@pytest.mark.parametrize("slug,brand", sorted(MODEL_BRAND.items()))
def test_council_card_member_uses_model_brand(slug, brand):
    assert _provider_display(slug) == brand


def test_council_card_does_not_headline_a_harness_brand():
    """The original bug let a harness brand leak onto a card. Lock the map to
    the model trio so antigravity can't regress to "Antigravity" (nor codex to
    "Codex")."""
    for slug in MODEL_BRAND:
        council_brand = _provider_display(slug)
        assert council_brand not in HARNESS_ONLY, (
            f"council card resolved {slug!r} -> {council_brand!r}, a harness "
            f"brand; public cards name models (Claude/GPT/Gemini)."
        )


# ---- Shared helper: the single source the card surfaces delegate to. ----


@pytest.mark.parametrize("slug,brand", sorted(MODEL_BRAND.items()))
def test_shared_helper_returns_model_brand(slug, brand):
    from trinity_local.council_schema import provider_model_brand

    assert provider_model_brand(slug) == brand


def test_shared_helper_canonicalizes_legacy_slugs():
    """Legacy web-capture slugs must resolve to the same brand as their CLI
    sibling (chatgpt→GPT, claude_ai→Claude, gemini→Gemini) so a fragmented
    slug never shows a different label than its canonical form."""
    from trinity_local.council_schema import provider_model_brand

    assert provider_model_brand("chatgpt") == "GPT"
    assert provider_model_brand("claude_ai") == "Claude"
    assert provider_model_brand("gemini") == "Gemini"


def test_shared_helper_empty_and_none_are_safe():
    from trinity_local.council_schema import provider_model_brand

    assert provider_model_brand(None) == ""
    assert provider_model_brand("") == ""
