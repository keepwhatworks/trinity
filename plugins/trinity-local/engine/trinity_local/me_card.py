"""me-card PNG export — turn /me lens output into a 1200×630 OG-shaped image.

Per council_35b2ae198a65b349: F3 (zero user screenshots in 14 days) fires by
default unless we ship a frictionless export-to-image artifact. The lens
text is the hero; the card is what gets posted to Twitter/LinkedIn.

Single function: `render_me_card(lens_data) -> bytes`. Caller owns where
the bytes land (CLI writes to disk; future launchpad button writes via
download). No HTTP, no headless browser — pure Pillow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .me.pair_mining import load_lenses, load_orderings
from .share_card_base import (
    CARD_WIDTH,
    CARD_HEIGHT,
    COLOR_INK,
    COLOR_MUTED,
    COLOR_ACCENT,
    draw_wordmark,
    load_font as _load_font,
    wrap_text as _wrap,
    fit_one_line as _fit_one_line,
    NON_LATIN_PLACEHOLDER as _NON_LATIN,
    blank_canvas,
    save_png,
)

# Card-specific accent — the sage tint behind the paired-tension block.
COLOR_LENS_BG = (37, 88, 71, 12)


def _text(value: Any) -> str | None:
    """Coerce a corpus/disk-derived lens field to a renderable string.

    Preserves None (so the render's `if data.lens_pole_a and ...` absence
    branch still fires for a genuinely-unbuilt lens) but turns any non-string
    value (an int/list from a corrupt me/lenses.json — LensPair is a plain
    dataclass that accepts whatever `LensPair(**row)` is handed) into a str so
    the PNG text-shapers, which iterate the field char-by-char, never raise
    `'int' object is not iterable` (the #258 corrupt-state crash class)."""
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


@dataclass
class CardLensData:
    """Trimmed-down view of /me lenses for card rendering. The card shows
    at most one lens (the strongest) + up to 3 orderings; full /me has more
    detail than fits on a 1200×630 image."""
    lens_pole_a: str | None = None
    lens_pole_b: str | None = None
    failure_a: str | None = None
    failure_b: str | None = None
    orderings: list[tuple[str, str]] = None  # [(pole_a, pole_b), ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lens_pole_a": self.lens_pole_a,
            "lens_pole_b": self.lens_pole_b,
            "failure_a": self.failure_a,
            "failure_b": self.failure_b,
            "orderings": self.orderings or [],
        }


def collect_card_data() -> CardLensData:
    """Read the latest lenses + orderings from disk, pick the strongest lens
    (most basins spanned) and the top 3 orderings. Returns empty fields when
    nothing has been built yet — callers should guard or surface an empty
    state."""
    lenses = load_lenses()
    orderings = load_orderings()

    # Strongest lens = most basins spanned (proxy for cross-domain reach).
    # Tie-break on the tension poles so the lens FEATURED on the share card is
    # deterministic: two lenses spanning the same basin count would otherwise
    # resolve to whichever load_lenses() returned first (file order). max
    # basins, then (pole_a, pole_b) ASC — the LensPair's content identity.
    # Coerce the tie-break keys to str: a hand-edited / corrupt me/lenses.json
    # row whose pole is a non-string (int/list — LensPair is a plain dataclass
    # with NO __post_init__ type check, so `LensPair(**row)` accepts it) would
    # otherwise raise `'<' not supported between 'int' and 'str'` in this sort.
    best = None
    if lenses:
        best = min(
            lenses,
            key=lambda p: (-len(p.basins_spanned or []), _text(p.pole_a), _text(p.pole_b)),
        )

    # Coerce every rendered text field to str at this read boundary (mirrors
    # council_card.collect_card_data_from_outcome's `str(...)` coercion). The
    # PNG text-shapers (strip_unrenderable / wrap_text / fit_one_line) iterate
    # the field char-by-char (`for ch in text`), so a non-string pole/failure
    # from a corrupt me/lenses.json crashed `trinity-local me-card` (and the
    # launchpad "Save as PNG card" dispatch) with `'int' object is not
    # iterable` instead of degrading gracefully (the #258 corrupt-state class).
    return CardLensData(
        lens_pole_a=_text(best.pole_a) if best else None,
        lens_pole_b=_text(best.pole_b) if best else None,
        failure_a=_text(best.failure_a) if best else None,
        failure_b=_text(best.failure_b) if best else None,
        orderings=[(_text(o.pole_a), _text(o.pole_b)) for o in orderings[:3]],
    )


def render_me_card(data: CardLensData) -> bytes:
    """Render a 1200×630 PNG. Returns the bytes; caller writes to disk or
    pipes to stdout. Empty lens data still produces a card (fallback CTA
    "Run trinity-local lens" to generate yours)."""
    img, draw = blank_canvas()
    # Re-init with RGBA mode to enable alpha-tinted sage block fill
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img, "RGBA")

    eyebrow = _load_font("bold", 22)
    headline = _load_font("serif", 56)
    body = _load_font("regular", 32)
    fail_label = _load_font("bold", 20)
    ordering = _load_font("regular", 24)
    footer = _load_font("regular", 18)

    margin = 60
    y = margin

    # Eyebrow: "TRINITY · YOUR TASTE, DISTILLED"
    draw.text((margin, y), "TRINITY · YOUR TASTE, DISTILLED",
              font=eyebrow, fill=COLOR_ACCENT)
    y += 50

    if data.lens_pole_a and data.lens_pole_b:
        # Stacked-poles layout: two poles separated by a sage-tinted
        # horizontal divider with a small "vs." label. Avoids the
        # unicode-arrow tofu issue (Helvetica doesn't have ↔), gives the
        # tension visual weight, and stays font-independent (the divider
        # is a drawn shape, not a glyph).
        # Use slightly smaller headline so 2 poles + labels + orderings
        # fit comfortably on a 630px tall card.
        pole_font = _load_font("serif", 44)
        pole_width = CARD_WIDTH - 2 * margin
        # An all-non-Latin pole (a CJK / Arabic / Hebrew lens) strips to "" and
        # left the ENTIRE tension block empty — just a floating "vs." divider over a
        # void on this PUBLICLY-shared PNG. Degrade to a readable note instead of
        # silently erasing the user's own taste text. (Bundling a CJK/emoji font is
        # a founder call; this is the light honest fallback.)
        lines_a = _wrap(data.lens_pole_a, pole_font, pole_width, draw, placeholder=_NON_LATIN)
        lines_b = _wrap(data.lens_pole_b, pole_font, pole_width, draw, placeholder=_NON_LATIN)
        # Greedy word-wrap (_wrap) can't split a SINGLE unbreakable token wider than
        # the pole column — a hyphenated compound pole ("domain-driven-design-with-…")
        # or a no-space string is emitted as one line that then runs the 44px serif
        # straight off the right edge of this PUBLICLY-shared PNG. Fit each wrapped
        # line so an over-long token is character-truncated + ellipsized within the
        # column (same #283 headline-overflow class the council/eval cards fix).
        lines_a = [_fit_one_line(ln, pole_font, pole_width, draw) for ln in lines_a]
        lines_b = [_fit_one_line(ln, pole_font, pole_width, draw) for ln in lines_b]
        line_h = 56
        divider_h = 40
        block_top = y - 12
        block_height = (len(lines_a) + len(lines_b)) * line_h + divider_h + 24
        draw.rounded_rectangle(
            [margin - 16, block_top, CARD_WIDTH - margin + 16, block_top + block_height],
            radius=12,
            fill=(37, 88, 71, 18),
        )
        # Pole A on top
        for line in lines_a:
            draw.text((margin, y), line, font=pole_font, fill=COLOR_INK)
            y += line_h
        # Divider — horizontal sage line + centered "vs." label
        y += 8
        divider_y = y + 8
        line_left = margin + 60
        line_right = CARD_WIDTH - margin - 60
        # Two short rules with "vs." between them
        rule_color = (37, 88, 71, 80)
        label_text = "vs."
        label_font = _load_font("regular", 18)
        bbox = draw.textbbox((0, 0), label_text, font=label_font)
        lw = bbox[2] - bbox[0]
        center_x = (line_left + line_right) // 2
        draw.line(
            [(line_left, divider_y), (center_x - lw // 2 - 12, divider_y)],
            fill=rule_color, width=2,
        )
        draw.line(
            [(center_x + lw // 2 + 12, divider_y), (line_right, divider_y)],
            fill=rule_color, width=2,
        )
        draw.text((center_x - lw // 2, divider_y - 12), label_text,
                  font=label_font, fill=COLOR_ACCENT)
        y += divider_h
        # Pole B below
        for line in lines_b:
            draw.text((margin, y), line, font=pole_font, fill=COLOR_INK)
            y += line_h
        y += 24

        # Failure modes — only render if there's room. With two-pole stacked
        # headline, vertical space is tighter on long-pole lenses; clip
        # gracefully rather than overlapping the footer.
        #
        # The orderings block ("ALWAYS render when present", P96) sits in the
        # band below the failures, above the footer. On a TALL lens (BOTH poles
        # wrap to 2 lines — the COMMON real-lens case, not a contrived one),
        # two failure rows push `y` past ~542, so the orderings-fit guard below
        # silently dropped the WHOLE "ALSO PREFERRED" section despite
        # orderings_count: 3 — the exact P96 symptom the 1-line-pole guard was
        # meant to kill but never bit on a multi-line-pole lens. Fix the class
        # at the priority level: poles > orderings (P96 mandatory) > failure_a
        # > failure_b. Each failure row renders only if it leaves room for the
        # reserved orderings band, and B never renders without A (showing
        # "PURE-B FAILS AS" alone — A silently dropped — read as a broken card).
        # Without orderings the failures keep their original, roomier ceilings.
        FAIL_ROW_H = 68  # label 24 + body row 44
        ORDERINGS_BAND_H = 62  # label 30 + one row 32 (the minimum P96 promises)
        # The lowest y a failure block may START and still leave the orderings
        # band above the footer-collision break (CARD_HEIGHT - margin - 38).
        orderings_floor = (CARD_HEIGHT - margin - 38) - ORDERINGS_BAND_H if data.orderings else CARD_HEIGHT
        fail_a_ceiling = min(CARD_HEIGHT - 200, orderings_floor - FAIL_ROW_H) if data.orderings else CARD_HEIGHT - 200
        fail_b_ceiling = min(CARD_HEIGHT - 150, orderings_floor - FAIL_ROW_H) if data.orderings else CARD_HEIGHT - 150
        rendered_fail_a = False
        if data.failure_a and y < fail_a_ceiling:
            draw.text((margin, y), "PURE-A FAILS AS",
                      font=fail_label, fill=COLOR_MUTED)
            y += 24
            draw.text((margin, y), _fit_one_line(data.failure_a, body, CARD_WIDTH - 2 * margin, draw, placeholder=_NON_LATIN),
                      font=body, fill=COLOR_INK)
            y += 44
            rendered_fail_a = True
        # B is the lower-priority of the pair: never show it when A was dropped.
        if data.failure_b and (rendered_fail_a or not data.failure_a) and y < fail_b_ceiling:
            draw.text((margin, y), "PURE-B FAILS AS",
                      font=fail_label, fill=COLOR_MUTED)
            y += 24
            draw.text((margin, y), _fit_one_line(data.failure_b, body, CARD_WIDTH - 2 * margin, draw, placeholder=_NON_LATIN),
                      font=body, fill=COLOR_INK)
            y += 44
    elif data.orderings:
        # Orderings-only state — a REAL Stage-3 output: no pair cleared all
        # three tension tests (so there's no featured lens), but some pairs
        # were preserved as directional orderings. The launchpad taste card +
        # "Copy as text" button BOTH render this state (launchpad_data.py
        # ~2622 "A real Stage-3 output is orderings-only … the taste card STILL
        # renders the Orderings block"). The me-card was the asymmetric sibling
        # that fell into the empty-state branch below and painted the
        # "Run trinity-local lens" BUILD CTA — which means "nothing built yet" —
        # directly ABOVE the unconditional "ALSO PREFERRED" orderings block that
        # only exists AFTER a build. The result was a self-contradicting PUBLIC
        # PNG: it told the recipient the lens didn't exist while showing its
        # distilled orderings. Lead with what the card IS (their preference
        # orderings), not a build CTA it contradicts. The "ALSO PREFERRED"
        # block at the bottom carries the actual rows.
        draw.text((margin, y), "What you reach for first",
                  font=headline, fill=COLOR_INK)
        y += 80
        draw.text((margin, y),
                  "The directional preferences Trinity surfaced in how you decide.",
                  font=body, fill=COLOR_MUTED)
        y += 60
    else:
        # Empty state — invite the user to build their own /me. Reached only
        # when there's NO featured lens AND NO orderings (a genuinely unbuilt
        # lens), so the build CTA is honest here.
        draw.text((margin, y), "Run trinity-local lens",
                  font=headline, fill=COLOR_INK)
        y += 80
        draw.text((margin, y),
                  "to surface the tensions in how you think.",
                  font=body, fill=COLOR_MUTED)
        y += 60

    # Footer wordmark, bottom-right corner. The tagline ALREADY carries the
    # landing domain ("⠕ Trinity · keepwhatworks.com" — single-sourced via
    # share_card_base.FOOTER_TAGLINE), so a recipient who sees this me-card on
    # Twitter has the URL to follow. We used to append LANDING_URL again here,
    # which rendered the domain twice on one line ("…keepwhatworks.com   ·
    # keepwhatworks.com") — found 2026-05-31 by eyeballing the generated PNG.
    # The other cards show it twice ON PURPOSE but in distinct roles (Install
    # CTA block vs wordmark, via draw_footer); the me-card has only the
    # wordmark line, so once is correct.
    draw_wordmark(draw, font=footer, margin=margin)

    # Bottom-left orderings preview. 100-persona audit P96 fix: prior
    # guard was `if data.orderings and y < CARD_HEIGHT - 200` (i.e. y < 430)
    # — on any real lens with 2 paired tensions + failure modes, y easily
    # crossed 430, so the orderings region (~460–542) silently dropped
    # despite JSON reporting orderings_count: 3. Left ~40% empty whitespace
    # below "hallucinated confidence".
    #
    # New rule: ALWAYS render orderings when present. Anchor to fixed
    # bottom region (CARD_HEIGHT - margin - 110 for label); if the upper
    # lens-render pushed y past the orderings label position, slide the
    # orderings block DOWN past y so it doesn't overlap. Footer stays at
    # absolute bottom; orderings live in the gap between lens-content and
    # footer.
    if data.orderings:
        # Default position: anchored bottom-left, with room for 2 rows + footer.
        orderings_label_y = CARD_HEIGHT - margin - 110
        # If lens content already runs past the orderings region, slide the
        # block DOWN to clear it (no upper cap — an earlier `min(y+20,
        # CARD_HEIGHT-margin-80)` cap forced the orderings UP into the failure
        # rows on tall lenses, overlapping them into an unreadable mess; found
        # 2026-06-01 by eyeballing a long-pole render). The per-row footer-
        # collision break below drops orderings that no longer fit — dropping
        # cleanly beats overlapping.
        if y > orderings_label_y - 20:
            orderings_label_y = y + 20
        # Only render the section when at least one ordering row fits below the
        # label — otherwise a tall lens leaves a lone "ALSO PREFERRED" with
        # nothing under it.
        if orderings_label_y + 30 <= CARD_HEIGHT - margin - 38:
            draw.text((margin, orderings_label_y),
                      "ALSO PREFERRED",
                      font=fail_label, fill=COLOR_MUTED)
            oy = orderings_label_y + 30
            # Render up to 3 (was 2) — orderings_count up to 3 in real data.
            for pa, pb in data.orderings[:3]:
                if oy > CARD_HEIGHT - margin - 38:
                    break  # would collide with footer line
                draw.text((margin, oy), _fit_one_line(f"{pa} > {pb}", ordering, CARD_WIDTH - 2 * margin, draw, placeholder=_NON_LATIN),
                          font=ordering, fill=COLOR_INK)
                oy += 32

    return save_png(img)


