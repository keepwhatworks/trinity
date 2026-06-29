"""Tests for me-card PNG export.

Per council_35b2ae198a65b349: the card is the F3 mitigation artifact.
These tests pin that the card renders deterministic-shape output regardless
of whether lens data exists yet (fresh install case) and that the empty
state still produces a valid PNG instead of crashing.
"""

from __future__ import annotations

import sys

import pytest


class TestCardData:
    def test_collect_returns_empty_when_no_lenses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me_card import collect_card_data
        data = collect_card_data()
        assert data.lens_pole_a is None
        assert data.lens_pole_b is None

    def test_collect_picks_lens_with_most_basins(self, tmp_path, monkeypatch):
        # When multiple lenses exist, the strongest = most cross-domain reach
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me.pair_mining import LensPair, save_lenses
        narrow = LensPair(
            pole_a="A", pole_b="B", failure_a="x", failure_b="y",
            basins_spanned=["b00", "b01"], verdict="accepted",
        )
        wide = LensPair(
            pole_a="WIDE_A", pole_b="WIDE_B", failure_a="x", failure_b="y",
            basins_spanned=["b00", "b01", "b02", "b03"], verdict="accepted",
        )
        save_lenses([narrow, wide], [])
        from trinity_local.me_card import collect_card_data
        data = collect_card_data()
        assert data.lens_pole_a == "WIDE_A"
        assert data.lens_pole_b == "WIDE_B"


class TestRenderShape:
    def test_render_with_real_lens_produces_valid_png(self):
        from trinity_local.me_card import CardLensData, render_me_card
        data = CardLensData(
            lens_pole_a="leading proxy signal",
            lens_pole_b="official lagging metric",
            failure_a="paranoid pattern-matching",
            failure_b="consensus follower",
            orderings=[("a", "b"), ("c", "d")],
        )
        png = render_me_card(data)
        # PNG signature
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        # 1200×630 produces roughly tens-of-KB at this complexity; sanity check
        assert 5_000 < len(png) < 200_000

    def test_render_empty_state_still_produces_valid_png(self):
        # Fresh install — no lenses yet. Card should still render with
        # the "Run trinity-local lens-build" CTA, NOT crash.
        from trinity_local.me_card import CardLensData, render_me_card
        data = CardLensData()  # all fields None / empty
        png = render_me_card(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 1000

    def test_render_handles_long_pole_text(self):
        # Wrap logic must not crash on 200-char poles
        from trinity_local.me_card import CardLensData, render_me_card
        long_pole = "x " * 50  # 100 words
        data = CardLensData(
            lens_pole_a=long_pole,
            lens_pole_b="b",
            failure_a="x", failure_b="y",
        )
        png = render_me_card(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unbreakable_token_pole_does_not_overflow_right_edge(self):
        """#283-class headline/text overflow: greedy word-wrap can't split a SINGLE
        token wider than the pole column, so a hyphenated compound pole
        ("domain-driven-design-with-…") or a no-space string was emitted as one line
        that ran the 44px serif straight off the right edge of this PUBLICLY-shared
        PNG. Each wrapped pole line must now be _fit_one_line-truncated within the
        column. Asserted at the PIXEL level: NO text ink past the decorative sage
        block's right edge (the block bleeds 16px past the margin BY DESIGN on both
        sides — only ink past THAT is a genuine text overflow). Found 2026-06-18 by
        rendering a hyphenated-compound pole and reading the rightmost ink (x=1197,
        well past the 1156 block edge / off the 1200 card)."""
        import io
        from PIL import Image
        from trinity_local.me_card import CardLensData, render_me_card
        from trinity_local.share_card_base import COLOR_BG, CARD_WIDTH

        margin = 60
        block_right = CARD_WIDTH - margin + 16  # 1156: the decorative block's edge
        data = CardLensData(
            lens_pole_a="domain-driven-design-with-bounded-contexts-and-aggregate-roots-everywhere",
            lens_pole_b="pragmatism-over-ceremony-in-every-single-engineering-decision-always",
            failure_a="over-engineering", failure_b="rework churn",
            orderings=[("shipping", "planning")],
        )
        png = render_me_card(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
        img = Image.open(io.BytesIO(png)).convert("RGB")
        px = img.load()
        w, h = img.size
        br, bg, bb = COLOR_BG
        tol = 12
        max_text_x = -1
        for yy in range(h):
            for xx in range(w - 1, block_right, -1):
                r, g, b = px[xx, yy][:3]
                if not (abs(r - br) <= tol and abs(g - bg) <= tol and abs(b - bb) <= tol):
                    if xx > max_text_x:
                        max_text_x = xx
                    break
        assert max_text_x <= block_right, (
            "REGRESSION: the me-card pole text ran off the right edge on an "
            f"unbreakable/hyphenated token — text ink at x={max_text_x} sits past the "
            f"decorative block edge ({block_right}px) / off the {CARD_WIDTH}px card. "
            "Greedy word-wrap can't split a single over-long token; each wrapped pole "
            "line must be _fit_one_line-truncated within the column so it can't clip "
            "on this PUBLICLY-shared PNG (the #283 overflow class)."
        )

    def test_footer_renders_landing_domain_exactly_once(self, monkeypatch):
        """Live 2026-05-31 (eyeballing the generated PNG): the me-card footer
        appended LANDING_URL to a tagline that ALREADY contained the domain,
        so it rendered 'keepwhatworks.com   ·   keepwhatworks.com' on one
        line. The me-card has only the wordmark line (no separate Install-CTA
        block like council/eval cards), so the domain must appear exactly
        once. Capture every draw.text() call and count."""
        from PIL import ImageDraw
        from trinity_local.me_card import CardLensData, render_me_card
        from trinity_local.share_card_base import LANDING_DOMAIN

        drawn: list[str] = []
        orig = ImageDraw.ImageDraw.text

        def capture(self, xy, text="", *a, **k):
            drawn.append(str(text))
            return orig(self, xy, text, *a, **k)

        monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
        render_me_card(CardLensData(
            lens_pole_a="self-generated frame",
            lens_pole_b="received expert frame",
            failure_a="echo chamber",
            failure_b="anchoring",
            orderings=[("concrete", "abstract")],
        ))
        rendered = "\n".join(drawn)
        n = rendered.count(LANDING_DOMAIN)
        assert n == 1, (
            f"landing domain rendered {n}× (expected 1). The footer must not "
            f"append the URL to a tagline that already contains the domain."
        )

    @pytest.mark.skipif(
        sys.platform == "linux",
        reason="Pixel-region assertion depends on macOS font metrics. PIL falls back to bitmap default on Linux without the macOS fonts; orderings region offsets shift. Tested on darwin dev gate; Linux CI skips.",
    )
    def test_orderings_always_render_when_present(self):
        """100-persona audit P96: orderings silently dropped when the
        lens-render `y` cursor crossed 430. New rule: ALWAYS render
        orderings when present; slide down past lens content if needed.
        Pixel-level check: the orderings region must contain non-bg
        pixels when orderings are present, even on cards where the
        upper lens content is dense."""
        from io import BytesIO
        from PIL import Image
        from trinity_local.me_card import CardLensData, render_me_card

        # Dense lens that previously pushed y past the orderings guard.
        data = CardLensData(
            lens_pole_a="leading proxy signal under uncertainty pressure",
            lens_pole_b="official lagging metric ratified by consensus",
            failure_a="paranoid pattern-matching from undersampled traces",
            failure_b="consensus follower trailing the actual shift by quarters",
            orderings=[("speed", "polish"), ("real test", "theoretical"), ("load-bearing", "menu")],
        )
        png = render_me_card(data)
        img = Image.open(BytesIO(png)).convert("RGB")

        # The orderings ("ALSO PREFERRED" + rows) sit in the lower-left of the card.
        # Scan the whole band (not a single hardcoded y) so the probe survives
        # font-metric layout shifts — a single-y sample lands in the gap between the
        # label and the rows when the line heights change (e.g. the v1.7.310 Hanken
        # migration moved the label to y~470 and rows to y~500+). The invariant is
        # "the orderings region is NOT empty", not "content is at exactly y=480".
        # Background is the cool-mist BG (234,236,239); any pixel far from it = drawn.
        bg = (234, 236, 239)
        non_bg = 0
        for y in range(440, 580, 3):
            for x in range(80, 400, 4):
                px = img.getpixel((x, y))
                if abs(px[0] - bg[0]) + abs(px[1] - bg[1]) + abs(px[2] - bg[2]) > 40:
                    non_bg += 1
        assert non_bg > 0, (
            "Orderings region (y~440–580) is empty — orderings silently dropped "
            "again per persona audit P96"
        )

    def test_orderings_survive_a_tall_two_line_pole_lens(self):
        """TALL-LENS orderings drop (found 2026-06-19 by rendering a lens whose
        BOTH poles wrap to two 44px-serif lines — the COMMON real-lens case).
        The P96 guarantee is 'ALWAYS render orderings when present', but the
        1-line-pole guard never bit a multi-line-pole lens: two failure rows
        pushed the `y` cursor past ~542, so the orderings-fit guard silently
        dropped the WHOLE 'ALSO PREFERRED' section despite orderings_count: 3 —
        the exact symptom P96 claimed to have killed. Root-cause fix is at the
        PRIORITY level (poles > orderings > failure_a > failure_b): the failure
        rows reserve the orderings band rather than consuming it.

        Font-metric-independent: `_wrap` is monkeypatched to force two lines per
        pole, so the tall path is exercised deterministically on Linux CI too
        (no macOS fonts needed). Asserts via draw.text capture (the y the card
        hands Pillow) — independent of which font actually rasterizes — that
        'ALSO PREFERRED' + >=1 ordering row are drawn AND land above the
        footer-collision break, and that a 'PURE-B FAILS AS' is never shown when
        'PURE-A FAILS AS' was dropped (a B-without-A card reads as broken)."""
        import trinity_local.me_card as mc
        from trinity_local.me_card import CardLensData, render_me_card
        from trinity_local.share_card_base import CARD_HEIGHT
        from PIL import ImageDraw

        # Force every pole to wrap to exactly two lines — reproduces the tall
        # layout regardless of platform font metrics.
        real_wrap = mc._wrap
        # `**kw` absorbs the keyword-only `placeholder=` the me-card now passes to
        # _wrap for all-non-Latin lens degradation (Iter 246) — the real signature.
        monkeypatch_wrap = lambda text, font, width, draw, **kw: ["line one of " + text[:20], "line two of " + text[:20]]
        self_drawn: list[tuple[int, str]] = []
        orig_text = ImageDraw.ImageDraw.text

        def capture(self, xy, text="", *a, **k):
            self_drawn.append((round(xy[1]), str(text)))
            return orig_text(self, xy, text, *a, **k)

        try:
            mc._wrap = monkeypatch_wrap
            ImageDraw.ImageDraw.text = capture
            png = render_me_card(CardLensData(
                lens_pole_a="optimize for the long-run compounding leverage of a durable system",
                lens_pole_b="capture the immediate measurable win that proves value to the room today",
                failure_a="analysis paralysis that never ships a single thing to a real user",
                failure_b="short-term theater that mortgages the foundation for a demo applause",
                orderings=[("load-bearing", "decorative"), ("real test", "theoretical"), ("ship", "polish")],
            ))
        finally:
            mc._wrap = real_wrap
            ImageDraw.ImageDraw.text = orig_text

        assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"

        label_drawn = any("ALSO PREFERRED" in t for _, t in self_drawn)
        ordering_rows = [(y, t) for y, t in self_drawn if " > " in t]
        footer_break = CARD_HEIGHT - 60 - 38  # the per-row footer-collision floor

        assert label_drawn and ordering_rows, (
            "REGRESSION: a TALL me-card lens (both poles wrap to two lines) "
            "silently dropped the entire 'ALSO PREFERRED' orderings section "
            "despite orderings being present — the P96 'ALWAYS render orderings' "
            "promise breaks on the COMMON multi-line-pole lens because the two "
            "failure rows consume the orderings band. Failures must yield to "
            "orderings (poles > orderings > failure_a > failure_b), not the "
            "reverse."
        )
        # The orderings the card DID draw must sit above the footer line, not
        # painted off the bottom / over the wordmark.
        assert all(y <= footer_break for y, _ in ordering_rows), (
            f"orderings rows drawn past the footer-collision break ({footer_break}px): "
            f"{[y for y, _ in ordering_rows]} — they would overlap the wordmark."
        )
        # Never show 'PURE-B FAILS AS' when 'PURE-A FAILS AS' was dropped.
        fail_a = any("PURE-A FAILS" in t for _, t in self_drawn)
        fail_b = any("PURE-B FAILS" in t for _, t in self_drawn)
        assert not (fail_b and not fail_a), (
            "me-card showed 'PURE-B FAILS AS' with 'PURE-A FAILS AS' dropped — "
            "a B-without-A failure block reads as a broken/half-rendered card."
        )

    def test_orderings_only_state_does_not_paint_the_build_cta(self):
        """Found 2026-06-21 by rendering the orderings-only me-card and READING
        THE PIXELS: the card painted the empty-state BUILD CTA
        ('Run trinity-local lens' / 'to surface the tensions in how you think.')
        directly ABOVE the unconditional 'ALSO PREFERRED' orderings block — a
        self-contradicting PUBLIC PNG that told the recipient the lens didn't
        exist while showing its distilled orderings.

        Orderings-only is a REAL Stage-3 output (some pairs preserved as
        directional orderings, none cleared all three tension tests so there's
        no featured lens) — the launchpad taste card + 'Copy as text' button
        already render it (launchpad_data.py ~2622). The me-card was the
        asymmetric sibling that fell into the empty branch.

        Asserted via draw.text capture (font-metric-independent, runs on Linux
        CI too): on the orderings-only state the BUILD-CTA strings must NOT be
        drawn, while the orderings rows + a non-build headline MUST be."""
        from trinity_local.me_card import CardLensData, render_me_card
        from PIL import ImageDraw

        drawn: list[str] = []
        orig_text = ImageDraw.ImageDraw.text

        def capture(self, xy, text="", *a, **k):
            drawn.append(str(text))
            return orig_text(self, xy, text, *a, **k)

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
            png = render_me_card(CardLensData(
                lens_pole_a=None, lens_pole_b=None,
                failure_a=None, failure_b=None,
                orderings=[("shipping", "planning"), ("concrete", "abstract")],
            ))
        finally:
            monkeypatch.undo()

        assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
        rendered = "\n".join(drawn)

        # PRECONDITION (so the bite is the contradiction, not a vacuous pass):
        # the orderings block genuinely rendered on this card.
        assert "ALSO PREFERRED" in rendered and any(" > " in t for t in drawn), (
            "precondition failed: orderings block did not render at all"
        )

        # The contradiction: the BUILD CTA must NOT be on a card that shows
        # distilled orderings (those only exist AFTER a build).
        assert "Run trinity-local lens" not in rendered, (
            "REGRESSION: the orderings-only me-card painted the empty-state "
            "BUILD CTA 'Run trinity-local lens' directly above its own "
            "'ALSO PREFERRED' orderings — a self-contradicting public PNG that "
            "denies the lens exists while showing its distilled orderings. The "
            "build CTA must fire ONLY when there are NO orderings either."
        )
        assert "to surface the tensions in how you think." not in rendered, (
            "REGRESSION: the orderings-only me-card painted the empty-state "
            "build-CTA subhead while showing distilled orderings."
        )
        # And it must lead with what the card IS, not a build invitation.
        assert "What you reach for first" in rendered, (
            "the orderings-only me-card must lead with an orderings headline "
            "('What you reach for first'), not the build CTA."
        )


class TestWordWrap:
    def test_short_text_returns_single_line(self):
        # Internal helper: greedy word-wrap. Validate boundary behavior so
        # any future font change doesn't silently break the layout.
        from trinity_local.me_card import _wrap, _load_font
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(img)
        font = _load_font("regular", 20)
        out = _wrap("hello", font, 1000, draw)
        assert out == ["hello"]

    def test_handles_empty_string(self):
        from trinity_local.me_card import _wrap, _load_font
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(img)
        font = _load_font("regular", 20)
        assert _wrap("", font, 1000, draw) == []


class TestOverflowHandling:
    """The PURE-A/B failure rows + ALSO-PREFERRED ordering rows are single-line.
    Before v1.7.193 they were drawn un-wrapped and overflowed the right card
    edge, and a fixed-position-cap forced the orderings to overlap the failures
    on long-pole lenses — a broken-looking flagship "your taste" share card.
    Found 2026-06-01 by eyeballing a long-content render.
    """

    def _draw(self):
        from PIL import Image, ImageDraw
        from trinity_local.me_card import _load_font
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img), _load_font("regular", 32)

    def test_fit_one_line_passthrough_when_fits(self):
        from trinity_local.me_card import _fit_one_line
        draw, font = self._draw()
        assert _fit_one_line("short", font, 10_000, draw) == "short"
        assert _fit_one_line("", font, 100, draw) == ""

    def test_fit_one_line_truncates_within_width_with_ellipsis(self):
        from trinity_local.me_card import _fit_one_line
        draw, font = self._draw()
        long_text = "accepting a green check while the underlying data is silently degenerate"
        out = _fit_one_line(long_text, font, 400, draw)
        assert out.endswith("…"), out
        # Must actually FIT — the whole point is to stop the right-edge overflow.
        assert draw.textbbox((0, 0), out, font=font)[2] <= 400

    def test_render_long_failures_and_orderings_no_crash(self):
        from trinity_local.me_card import CardLensData, render_me_card
        data = CardLensData(
            lens_pole_a="deeply verified ground-truth that traces the full mechanism end to end",
            lens_pole_b="a confidently stated verdict that asserts a conclusion without the chain",
            failure_a="accepting a green check while the underlying data is silently degenerate or unrepresentative",
            failure_b="over-engineering a structural redesign when a small targeted in-place patch would suffice",
            orderings=[("a long preferred pole that would itself overflow the row width", "the other pole")],
        )
        png = render_me_card(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert 5_000 < len(png) < 200_000


class TestMeCardCliVerb:
    """Cover the `me-card` CLI verb itself — its register()/handle_me_card
    binding — not just the renderer module. Without this, the CLI-coverage
    guard (test_gstack_patterns) attributed the verb only to an incidental
    `"me-card"` string in a doc-consistency test; that's a fragile signal that
    vanishes the moment the doc test changes."""

    def test_me_card_verb_registers_and_binds_handler(self):
        import argparse

        from trinity_local.commands import me_card

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        me_card.register(sub)
        assert "me-card" in sub.choices
        ns = parser.parse_args(["me-card"])
        assert ns.handler is me_card.handle_me_card
