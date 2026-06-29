"""Dynamic card text (chairman claims, lens poles/failures, orderings) must
never paint a font-tofu box onto a PUBLICLY-shared share-card PNG.

Founder symptom (found 2026-06-19 by eyeballing the rendered PNGs): a council
``agreed_claim`` of "Validate the webhook signature 🔐 before trusting any
field" rendered the 🔐 as a hollow □ box, because the vendored brand font
(Hanken Grotesk / JetBrains Mono) has no emoji glyph. Emoji in LLM-/corpus-
derived text is common (🔐 ✅ ⚠️ 🚀), so a council/me/eval card shared to
HN/Twitter showed a row of broken boxes.

The pre-existing ``test_share_card_brand.test_card_modules_avoid_unrenderable_glyphs``
only scans STATIC string literals the CODE draws (↔ ⚠ ⠕) — it is structurally
blind to DYNAMIC text. ``share_card_base.strip_unrenderable`` drops font-tofu
codepoints at the shared text-shaping boundary; these guards are the
PIXEL-level proof on the actually-rendered PNG.

The card-level guards render the emoji card and assert it is PIXEL-IDENTICAL to
the same card with the emoji removed by hand — if the strip works, the emoji
leaves no ink, so the two PNGs match to the pixel; if the strip is bypassed
(the tofu boxes are drawn), they differ. A bare source-string check could not
catch this — it has to be measured on the raster.
"""
from __future__ import annotations

import io

import pytest

np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from trinity_local.share_card_base import (  # noqa: E402
    load_font,
    strip_unrenderable,
    NON_LATIN_PLACEHOLDER,
)
from trinity_local.council_card import render_council_card, CouncilCardData  # noqa: E402
from trinity_local.me_card import render_me_card, CardLensData  # noqa: E402
from trinity_local.eval_card import render_eval_card, EvalCardData  # noqa: E402


def _arr(png_bytes: bytes):
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))


def _diff_px(a, b) -> int:
    """Count pixels that differ between two same-shape RGB arrays."""
    assert a.shape == b.shape, "card geometry changed between the two renders"
    return int((a != b).any(axis=2).sum())


# ── Unit: the strip helper itself removes the tofu codepoints + cleans space ──

@pytest.mark.parametrize("kind,size", [("regular", 32), ("serif", 44), ("mono", 22)])
def test_strip_unrenderable_drops_emoji_keeps_ascii(kind, size):
    font = load_font(kind, size)
    # An emoji (no glyph) between two ASCII words → gone, single space, no box.
    assert strip_unrenderable("webhook signature 🔐 before", font) == \
        "webhook signature before"
    # A combining variation selector left dangling is also dropped.
    assert "️" not in strip_unrenderable("warning ⚠️ here", font)
    # Renderable text is returned UNCHANGED (em-dash, %, accents, bullet).
    keep = "analysis — 80% café • done"
    assert strip_unrenderable(keep, font) == keep
    # And no codepoint in the cleaned output still tofus.
    from trinity_local.share_card_base import _glyph_tofus
    cleaned = strip_unrenderable("rocket 🚀 ship 🛳️ done 字", font)
    assert not any(_glyph_tofus(c, font) for c in cleaned), (
        f"strip left a tofu codepoint in {cleaned!r}"
    )


# ── Council card: a chairman claim with emoji paints no extra ink ──

def test_council_card_emoji_claim_paints_no_tofu_box():
    emoji = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex", "antigravity"], winner="claude",
        agreed_claims=["Validate the webhook signature 🔐 before trusting any field"],
        disagreed_claim="Retry charges 💳 or surface them",
        disagreed_why="auto-retry risks a duplicate ⚠️ charge",
    )))
    clean = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex", "antigravity"], winner="claude",
        agreed_claims=["Validate the webhook signature before trusting any field"],
        disagreed_claim="Retry charges or surface them",
        disagreed_why="auto-retry risks a duplicate charge",
    )))
    diff = _diff_px(emoji, clean)
    assert diff == 0, (
        "council share card drew a TOFU BOX for an emoji in a chairman "
        f"agreed/disagreed claim ({diff} stray pixels vs the emoji-free card) — "
        "the 'webhook signature 🔐' founder symptom. Dynamic card text must be "
        "run through strip_unrenderable before it is drawn."
    )


# ── Me card: lens poles + failures + orderings with emoji ──

def test_me_card_emoji_lens_text_paints_no_tofu_box():
    emoji = _arr(render_me_card(CardLensData(
        lens_pole_a="move fast 🚀 now", lens_pole_b="measure twice ✂️ cut",
        failure_a="reckless breakage 💥", failure_b="frozen 🧊",
        orderings=[("a worked example 📝", "an abstract spec")],
    )))
    clean = _arr(render_me_card(CardLensData(
        lens_pole_a="move fast now", lens_pole_b="measure twice cut",
        failure_a="reckless breakage", failure_b="frozen",
        orderings=[("a worked example", "an abstract spec")],
    )))
    diff = _diff_px(emoji, clean)
    assert diff == 0, (
        "me share card drew a TOFU BOX for an emoji in a lens pole / failure / "
        f"ordering ({diff} stray pixels vs the emoji-free card) — the lens text "
        "is corpus-derived and must be run through strip_unrenderable."
    )


# ── Eval card: an emoji in the model name (provider slug) ──

def test_eval_card_emoji_model_name_paints_no_tofu_box():
    emoji = _arr(render_eval_card(EvalCardData(
        target_provider="rocket-🚀-model", target_model="rocket-🚀-model",
        aggregate_score=0.74, items_total=20, items_completed=20,
        by_axis=[("REFRAME", 0.81, 5), ("COMPRESSION", 0.70, 5)],
    )))
    clean = _arr(render_eval_card(EvalCardData(
        target_provider="rocket--model", target_model="rocket--model",
        aggregate_score=0.74, items_total=20, items_completed=20,
        by_axis=[("REFRAME", 0.81, 5), ("COMPRESSION", 0.70, 5)],
    )))
    # The headline runs the slug through _provider_display_name → _fit_one_line,
    # so the emoji must be stripped there. (The model-id subhead is the same
    # string; both must come out box-free.)
    diff = _diff_px(emoji, clean)
    assert diff == 0, (
        "eval share card drew a TOFU BOX for an emoji in the target model name "
        f"({diff} stray pixels vs the emoji-free card) — model names flow onto "
        "the headline + identity line and must pass through strip_unrenderable."
    )


# ── ALL-NON-LATIN honest degradation (Iter 246) ──────────────────────────────
# An emoji *inside* otherwise-Latin text strips clean (above). But a field that
# is ENTIRELY non-Latin — an all-Japanese / Korean / Arabic / Hebrew lens pole, a
# wholly-non-Latin custom model name, an emoji-only chairman claim — strips to ""
# and used to paint a VOID on the public PNG: the me-card's whole tension block
# empty under a floating "vs.", the council headline reading "won." with NO
# winner named (broken AND dishonest), a naked "•" bullet, an unlabelled eval bar.
# The brand font (Hanken / JetBrains) has no CJK/emoji glyphs and bundling one is
# a deliberate FOUNDER call, so the light honest fix substitutes a READABLE
# placeholder. Each guard renders the all-non-Latin card and proves TWO things on
# the raster: (1) the field now carries INK (the placeholder rendered) — it must
# NOT match the VOID card (the same card with the field genuinely blank), which is
# exactly the pixels the un-fixed code paints; (2) that ink is the placeholder
# (not a row of tofu boxes) — it matches a card built with the literal placeholder
# string through the SAME draw path. (2) is asserted where the field path is
# transform-stable (claim/pole/axis text); the winner/roster — whose path adds
# title-casing — is proven by (1) + the dangling-separator check. Mutation-proven
# against the strip/draw source: revert the placeholder and the card collapses to
# the void it's compared against.

_CJK = "日本語の設計思想"   # all-Japanese — strips to "" without the placeholder
_KO = "한국어 디자인 철학"  # all-Korean


def test_me_card_all_non_latin_pole_degrades_to_placeholder_not_void():
    # Pole text flows pole_a/pole_b through _wrap → _fit_one_line (transform-stable
    # for a placeholder that already fits one line), so the placeholder reference
    # is pixel-exact AND distinct from the void.
    rendered = _arr(render_me_card(CardLensData(
        lens_pole_a=_CJK, lens_pole_b=_KO,
        failure_a="日本語の失敗", failure_b="",
    )))
    void = _arr(render_me_card(CardLensData(
        lens_pole_a="", lens_pole_b="", failure_a="", failure_b="",
    )))
    placeholder = _arr(render_me_card(CardLensData(
        lens_pole_a=NON_LATIN_PLACEHOLDER, lens_pole_b=NON_LATIN_PLACEHOLDER,
        failure_a=NON_LATIN_PLACEHOLDER, failure_b="",
    )))
    # (1) the all-non-Latin card must NOT collapse to the void the un-fixed code drew
    assert _diff_px(rendered, void) > 1000, (
        "me share card painted a VOID for an all-non-Latin lens — the whole tension "
        "block stripped to '' and rendered empty under a floating 'vs.' divider on "
        "the PUBLIC PNG. strip_unrenderable must substitute NON_LATIN_PLACEHOLDER."
    )
    # (2) the ink IS the placeholder (not tofu boxes), pixel-exact
    assert _diff_px(rendered, placeholder) == 0, (
        "me share card's all-non-Latin lens did not render the readable "
        f"placeholder ({_diff_px(rendered, placeholder)} px differ) — it drew "
        "something other than NON_LATIN_PLACEHOLDER (tofu / partial)."
    )


def test_council_card_all_non_latin_winner_names_someone_not_void():
    # Roster has one un-renderable member (must DROP, no dangling '·') + an
    # all-non-Latin WINNER (must read as the placeholder, never a bare "won.").
    rendered = _arr(render_council_card(CouncilCardData(
        members=[_CJK, "claude", "codex"], winner=_CJK,
        agreed_claims=["use embeddings not LLM calls", "中文的主张"],
        disagreed_claim="🚀✅🎯", disagreed_why=_KO,
    )))
    # VOID baseline: the un-fixed code joined the un-renderable winner/roster/claims
    # raw, so they stripped to nothing — the winner line was a bare "won.", the 2nd
    # AGREED a naked bullet, the DISAGREED a dangling em-dash. Reproduce that void
    # by blanking those fields (winner "" still takes the winner+roster branch via
    # a placeholder so the BRANCH matches; the difference is the painted name).
    void = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex"], winner="?",  # "? won." ~ a nameless winner line
        agreed_claims=["use embeddings not LLM calls", ""],
        disagreed_claim="", disagreed_why="",
    )))
    # (1) the fixed card must paint MORE than the nameless/void baseline — the
    #     winner placeholder + the 2nd AGREED placeholder + the DISAGREED block.
    assert _diff_px(rendered, void) > 2000, (
        "council share card painted a VOID for an all-non-Latin winner/roster/claim "
        "— the winner line read 'won.' with NO NAME (broken AND dishonest about who "
        "won), the 2nd AGREED was a naked '•' bullet, the DISAGREED a dangling em-"
        "dash. The un-renderable member must drop from the join and the winner must "
        "fall back to NON_LATIN_PLACEHOLDER."
    )
    # (2) the AGREED claim ink IS the placeholder (claims don't title-case, so the
    #     placeholder reference is pixel-exact for that field): build a card whose
    #     only non-Latin field is the agreed claim and confirm it reads as the
    #     placeholder, not a naked bullet.
    claim_rendered = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex"], winner="claude",
        agreed_claims=["中文的主张"],
    )))
    claim_placeholder = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex"], winner="claude",
        agreed_claims=[NON_LATIN_PLACEHOLDER],
    )))
    assert _diff_px(claim_rendered, claim_placeholder) == 0, (
        "council share card's all-non-Latin AGREED claim did not render the "
        f"placeholder ({_diff_px(claim_rendered, claim_placeholder)} px differ) — a "
        "naked '•' bullet was painted instead of '• non-Latin — view in app'."
    )
    # (3) WINNER-SPECIFIC: a council whose ONLY non-Latin field is the WINNER must
    #     still NAME someone on the winner line — never the bare "won." the un-fixed
    #     code painted (winner stripped to ""). Crop the winner-line row band (the
    #     2nd headline line, accent-coloured) and assert the all-non-Latin winner
    #     paints the SAME ink as a Latin winner of equal length — i.e. a name is
    #     present. Compared in the band only, so the (identical) roster/claims/CTA
    #     rows don't mask a nameless winner line.
    WIN_BAND = slice(180, 224)  # the "<winner> won." headline row (accent ink)
    win_rendered = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex"], winner=_CJK,
        agreed_claims=["use embeddings not LLM calls"],
    )))[WIN_BAND]
    # Reference: the winner placeholder painted through the literal-string path.
    # _provider_display title-cases it ("Non-Latin — View In App"), so this is a
    # DIFFERENT painted form than the fix's lowercase — but it is unambiguously a
    # NAMED winner line (lots of accent ink). The un-fixed bare "won." band carries
    # only the 3-glyph "won." — far less ink. Assert the fixed band has the ink of a
    # named winner, NOT the sparse bare-"won." band.
    win_bare = _arr(render_council_card(CouncilCardData(
        members=["claude", "codex"], winner="x",  # 1-char winner → "X won." (sparse band)
        agreed_claims=["use embeddings not LLM calls"],
    )))[WIN_BAND]

    def _ink(band):
        # count non-background pixels (any channel far from the cool-mist BG)
        bg = np.array([234, 236, 239])
        return int((np.abs(band.astype(int) - bg).sum(axis=2) > 40).sum())

    assert _ink(win_rendered) > _ink(win_bare) + 1500, (
        "council share card painted a NAMELESS winner line for an all-non-Latin "
        f"winner (winner-band ink {_ink(win_rendered)} vs a 1-char name "
        f"{_ink(win_bare)}) — '<winner> won.' collapsed to a bare 'won.' with no "
        "name, broken AND dishonest about who won on the PUBLIC PNG. The winner "
        "must fall back to NON_LATIN_PLACEHOLDER, not strip to ''."
    )


def test_eval_card_all_non_latin_axis_label_is_named_not_blank():
    # The second axis label is all-non-Latin → must read as the placeholder, not
    # an unlabelled bar floating in space.
    rendered = _arr(render_eval_card(EvalCardData(
        target_provider="antigravity", target_model="standard",
        aggregate_score=0.83, items_total=5, items_completed=5,
        by_axis=[("REFRAME", 0.90, 3), (_KO, 0.70, 2)],
    )))
    void = _arr(render_eval_card(EvalCardData(
        target_provider="antigravity", target_model="standard",
        aggregate_score=0.83, items_total=5, items_completed=5,
        by_axis=[("REFRAME", 0.90, 3), ("", 0.70, 2)],
    )))
    placeholder = _arr(render_eval_card(EvalCardData(
        target_provider="antigravity", target_model="standard",
        aggregate_score=0.83, items_total=5, items_completed=5,
        by_axis=[("REFRAME", 0.90, 3), (NON_LATIN_PLACEHOLDER, 0.70, 2)],
    )))
    assert _diff_px(rendered, void) > 200, (
        "eval share card painted an UNLABELLED bar for an all-non-Latin per-axis "
        "label — it stripped to '' and the bar floated with no label on the PUBLIC "
        "card. Axis labels must pass through fit_one_line with the placeholder."
    )
    assert _diff_px(rendered, placeholder) == 0, (
        "eval share card's all-non-Latin axis label did not render the placeholder "
        f"({_diff_px(rendered, placeholder)} px differ) — tofu / partial draw."
    )
