"""Layout guards for the eval-share card (1200×630 PNG, a PUBLIC benchmark
wedge). Found 2026-06-01 by rendering synthetic edge cases and EYEBALLING the
PNGs (share_card_visual_testing): with 5+ axes the per-axis rows overran the CTA
block + footer and clipped off the card (garbled), and long axis names ran under
the bar track. The canonical eval has 4 axes that just fit, but the card renders
"whatever the result has" and provider-imported evals can carry custom/extra axes.

Share-card PNGs have no pixel tests, so these pin the LAYOUT INVARIANTS at the
logic level instead: the row-cap math + that the shown rows fit above the CTA,
plus a render smoke that the edge cases don't crash.
"""
from __future__ import annotations

from trinity_local.eval_card import (
    CompareCardData,
    EvalCardData,
    render_compare_card,
    render_compare_matrix_card,
    render_eval_card,
    _axis_rows_to_show,
)
from trinity_local.share_card_base import CARD_HEIGHT, CARD_WIDTH, fit_one_line, load_font

# The card uses these in render_eval_card; mirror them for the invariant math.
_MARGIN = 60
_CTA_BLOCK_TOP = CARD_HEIGHT - _MARGIN - 90  # 480
_ROW_HEIGHT = 48
_ROW_CONTENT_H = 40
_CANONICAL_FIRST_ROW_Y = 276  # eyebrow+headline+identity+subhead (with identity line)


class TestAxisRowCap:
    def test_canonical_four_axes_all_shown(self):
        # The 4-axis eval (today's real case) must render ALL axes, no overflow.
        n_shown, overflow = _axis_rows_to_show(
            4, first_row_y=_CANONICAL_FIRST_ROW_Y, cta_block_top=_CTA_BLOCK_TOP,
            row_height=_ROW_HEIGHT, row_content_h=_ROW_CONTENT_H)
        assert (n_shown, overflow) == (4, 0)

    def test_five_or_more_axes_capped_with_overflow(self):
        for n in (5, 6, 8, 12):
            n_shown, overflow = _axis_rows_to_show(
                n, first_row_y=_CANONICAL_FIRST_ROW_Y, cta_block_top=_CTA_BLOCK_TOP,
                row_height=_ROW_HEIGHT, row_content_h=_ROW_CONTENT_H)
            assert n_shown < n, f"n={n}: should cap"
            assert overflow == n - n_shown
            assert overflow > 0
            assert n_shown >= 1  # always show at least one row

    def test_shown_rows_plus_note_fit_above_cta(self):
        # The load-bearing invariant: the last shown row AND the "+N more" note
        # must stay above the CTA block (else they overlap it — the bug).
        for n in (4, 5, 8, 20):
            n_shown, overflow = _axis_rows_to_show(
                n, first_row_y=_CANONICAL_FIRST_ROW_Y, cta_block_top=_CTA_BLOCK_TOP,
                row_height=_ROW_HEIGHT, row_content_h=_ROW_CONTENT_H)
            note_rows = 1 if overflow else 0
            # bottom of the last drawn element (last row's content, or the note)
            last_top = _CANONICAL_FIRST_ROW_Y + (n_shown + note_rows - 1) * _ROW_HEIGHT
            assert last_top + _ROW_CONTENT_H <= _CTA_BLOCK_TOP, (
                f"n={n}: last element bottom {last_top + _ROW_CONTENT_H} "
                f"overruns CTA at {_CTA_BLOCK_TOP}")

    def test_zero_axes(self):
        assert _axis_rows_to_show(0, first_row_y=276, cta_block_top=480) == (0, 0)


class TestAxisLabelTruncation:
    def test_long_axis_name_truncates_to_label_column(self):
        # The axis label shares its row with the bar (which starts at margin+240),
        # so it must truncate to the ~224px label column or it runs under the bar.
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (1200, 630)))
        font = load_font("bold", 20)
        label_max_w = (_MARGIN + 240) - _MARGIN - 16  # 224
        long = "COMPREHENSIVENESS_AND_DEPTH_AND_NUANCE_AND_MORE"
        out = fit_one_line(long, font, label_max_w, draw)
        assert out.endswith("…")
        assert draw.textbbox((0, 0), out, font=font)[2] <= label_max_w
        # a short name is untouched
        assert fit_one_line("REFRAME", font, label_max_w, draw) == "REFRAME"


class TestEvalCardRenderSmoke:
    """Edge cases must render valid PNG bytes, not raise."""

    def _data(self, by_axis):
        return EvalCardData(
            target_provider="claude", target_model="claude-opus-4-8",
            target_effort="high", aggregate_score=0.66,
            items_total=20, items_completed=20, by_axis=by_axis)

    def test_many_axes_renders(self):
        png = render_eval_card(self._data([(f"AXIS_{i}", 0.5, 5) for i in range(10)]))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature

    def test_long_axis_names_render(self):
        png = render_eval_card(self._data([
            ("COMPREHENSIVENESS_AND_DEPTH", 0.7, 5),
            ("INSTRUCTION_FOLLOWING_FIDELITY", 0.6, 5)]))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_empty_axes_renders_fallback(self):
        png = render_eval_card(EvalCardData(target_provider="claude", by_axis=[]))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def _compare_row(target, model, axes, n=5, agg=0.6):
    return {
        "target": target, "model": model, "aggregate_score": agg,
        "items_completed": 20, "judge": "claude",
        "by_axis": dict(axes), "by_axis_n": {a: n for a in axes},
    }


def _fn_body(start_fn: str, end_fn: str | None) -> str:
    """Source of one render_* function, scoped between its `def` and the next
    function's `def`, so a string match can't leak across function boundaries."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "src" / "trinity_local" / "eval_card.py").read_text()
    start = src.find(f"def {start_fn}(")
    end = src.find(f"def {end_fn}(", start) if end_fn else len(src)
    assert start != -1 and end > start
    return src[start:end]


class TestCompareMatrixLabelFit:
    """The matrix axis header must fit the COLUMN width with an honest ellipsis
    — a bare axis[:11] slice truncated a custom axis to a complete-looking
    fragment AND could overflow a narrow column at high axis counts (the
    v1.7.203 lesson: eval-import can carry custom/long axes)."""

    def test_short_axis_label_untouched_at_canonical_width(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (1200, 630)))
        font = load_font("regular", 11)
        canonical_col_w = (1200 - 60 - (60 + 130)) // 4  # 4-axis matrix column ≈ 237
        assert fit_one_line("COMPRESSION", font, canonical_col_w - 6, draw) == "COMPRESSION"

    def test_long_axis_label_ellipsizes_in_narrow_column(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (1200, 630)))
        font = load_font("regular", 11)
        narrow_col_w = (1200 - 60 - (60 + 130)) // 12  # 12-axis matrix column ≈ 79
        out = fit_one_line("LONG_AXIS_NAME_11", font, narrow_col_w - 6, draw)
        assert out.endswith("…")
        assert draw.textbbox((0, 0), out, font=font)[2] <= narrow_col_w - 6

    def test_matrix_renderer_uses_width_fit_not_bare_slice(self):
        """The fit-helper unit tests above don't prove the MATRIX RENDERER uses
        it — reverting to `axis[:11]` left them green (mutation gap). Pin the
        renderer's actual use: width-fit present in render_compare_matrix_card,
        the bare-slice gone. Scoped to the function body so a slice elsewhere
        can't mask a regression."""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "src" / "trinity_local" / "eval_card.py").read_text()
        start = src.find("def render_compare_matrix_card(")
        end = src.find("def render_compare_card(", start)
        assert start != -1 and end > start
        body = src[start:end]
        assert "_fit_one_line(axis, axis_label_font" in body, (
            "matrix axis header must width-fit the label (not a bare axis[:11])")
        # Match the CODE form `label = axis[:11]`, not the explanatory comment
        # that mentions the slice we're avoiding.
        assert "label = axis[:11]" not in body, (
            "the bare 11-char slice (no ellipsis, not width-aware) must be gone")


class TestCompareCardRenderSmoke:
    """The compare cards already cap their dimensions (matrix rows at 4,
    leaderboard at 5, both + "+N more") — these pin that they render valid PNGs
    on stress inputs (many providers/axes, long names) rather than raise."""

    def test_matrix_many_axes_and_providers(self):
        axes = {f"AXIS_{i}": 0.5 for i in range(12)}
        rows = [_compare_row(p, m, axes) for p, m in [
            ("claude", "claude-opus-4-8"), ("codex", "gpt-5.5"),
            ("antigravity", "gemini-3.1-pro"), ("claude", "claude-sonnet-4-6"),
            ("codex", "gpt-5.5-mini"), ("antigravity", "gemini-3.1-flash")]]
        png = render_compare_matrix_card(CompareCardData(rows=rows))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_matrix_low_n_annotations(self):
        axes = {f"AX_{i}": 0.5 for i in range(8)}
        rows = [_compare_row("claude", "claude-opus-4-8", axes, n=2)]
        png = render_compare_matrix_card(CompareCardData(rows=rows))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_matrix_empty_fallback(self):
        png = render_compare_matrix_card(CompareCardData(rows=[]))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_leaderboard_many_providers_long_names(self):
        canon = {"COMPRESSION": 0.8, "REDIRECT": 0.6, "REFRAME": 0.5, "SHARPENING": 0.7}
        rows = [_compare_row(p, m, canon) for p, m in [
            ("claude", "claude-opus-4-8-with-extended-thinking"),
            ("codex", "gpt-5.5-high-effort"),
            ("antigravity", "gemini-3.1-pro-preview"),
            ("claude", "claude-sonnet-4-6"), ("codex", "gpt-5.5-mini"),
            ("antigravity", "gemini-3.1-flash")]]
        png = render_compare_card(CompareCardData(rows=rows))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_leaderboard_empty_fallback(self):
        png = render_compare_card(CompareCardData(rows=[]))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def _compare_drawn_strings(data: CompareCardData) -> list[str]:
    """Render the leaderboard PNG while spying on every ImageDraw.text() call,
    returning the exact strings painted. The compare card is a pure-Pillow PNG
    with no extractable text layer, so a DRAWN-TEXT invariant (vs a layout-math
    proxy) is the only way to pin what a recipient actually reads."""
    from PIL import ImageDraw

    drawn: list[str] = []
    orig_text = ImageDraw.ImageDraw.text

    def _spy(self, xy, text, *args, **kwargs):
        if isinstance(text, str):
            drawn.append(text)
        return orig_text(self, xy, text, *args, **kwargs)

    ImageDraw.ImageDraw.text = _spy
    try:
        render_compare_card(data)
    finally:
        ImageDraw.ImageDraw.text = orig_text
    return drawn


class TestCompareCardSelfJudgeDisclosure:
    """The cross-provider leaderboard PNG paints a "judge: <brand>" subline
    under each target. When the judge slug == the target slug (reachable via
    `eval-run --judge claude` on a claude target), the model graded its OWN
    family's output. The single `eval-run` terminal output ALREADY discloses
    this ("self-judge — same family as target") precisely because — in its own
    comment — it "can still look like a conflict of interest externally", and
    THIS card is the most external surface there is (a public OG image built to
    be posted to Twitter/LinkedIn). Before the fix the card dropped the
    disclosure: "Claude leads at 0.88 · judge: Claude" rendered as an unflagged
    self-graded win — the #35 green-while-degenerate shape on the most-public
    artifact, with the LESS-public single-run terminal disclosing what the
    MORE-public share card did not.

    Drawn-text invariant (read the painted glyphs, not a layout proxy)."""

    def test_self_judged_leader_row_discloses_self_on_public_card(self):
        # Row 1: claude target judged BY claude (self-judge). Row 2: codex
        # target judged BY claude (cross-judge — the normal case).
        rows = [
            {"target": "claude", "model": None, "aggregate_score": 0.881,
             "items_completed": 10, "judge": "claude"},
            {"target": "codex", "model": None, "aggregate_score": 0.742,
             "items_completed": 10, "judge": "claude"},
        ]
        drawn = _compare_drawn_strings(CompareCardData(rows=rows))
        judge_lines = [s for s in drawn if s.startswith("judge:")]
        # Precondition: both judge sublines were actually painted (the spy saw
        # them) — so a future refactor that stops drawing judge lines reds here,
        # not silently passes.
        assert len(judge_lines) == 2, (
            "leaderboard self-judge guard expected both per-row judge sublines "
            f"to be drawn, got {judge_lines!r}"
        )
        # The self-judged Claude row MUST carry the (self) flag.
        self_judged = [s for s in judge_lines if "(self)" in s]
        assert self_judged and "Claude" in self_judged[0], (
            "self-judge NOT disclosed on the public leaderboard card: the "
            "claude-judged-by-claude row painted "
            f"{judge_lines!r} — 'Claude leads · judge: Claude' reads as an "
            "unflagged self-graded win (the single eval-run terminal discloses "
            "'self-judge — same family as target' but this MORE-public share "
            "card did not)."
        )
        # The cross-judged row (codex judged by claude) MUST NOT be flagged
        # self — that would be a false positive smearing every row.
        cross = [s for s in judge_lines if "(self)" not in s]
        assert cross and cross[0] == "judge: Claude", (
            "cross-judge row must NOT carry (self); got "
            f"{judge_lines!r}"
        )


class TestCompareCardVerticalCtaCollision:
    """RENDERED-PNG guard for the #283 VERTICAL-overflow class on the compare
    leaderboard. render_compare_card used a HARD max_rows=5 with no awareness of
    how far the OPTIONAL mixed-eval-set warning (+24px) had already pushed the
    body down. With 6 rows (forcing the "+N more" note) AND mixed_eval_sets, the
    note's baseline (y≈464, glyph bottom ≈482) ran straight into the CTA headline
    "Run this benchmark against your own taste:" drawn at y=480 — two text lines
    colliding on a PUBLICLY-shared PNG. The aggregate card already fixed this with
    _axis_rows_to_show; the compare leaderboard was the unfixed sibling. The
    matrix card render_compare_matrix_card is checked too (same class).

    Iter-89 only checked HORIZONTAL (right-margin) headline/pole overflow; this is
    the unchecked VERTICAL half — measured in real pixels, not reasoned about."""

    # The CTA "Run this benchmark…" headline is drawn at y=480 in ACCENT teal.
    # The clear band that the BODY must never paint into: the 14px strip just
    # above the CTA headline. Any non-bg, non-accent (INK/MUTED body) ink there =
    # the "+N more" note (or a leaderboard row) overrunning the CTA.
    _GUARD_BAND_TOP = 466
    _GUARD_BAND_BOT = 480  # CTA headline top
    _LEFT_COL_MAX_X = 700  # left of the right-aligned footer wordmark

    def _body_ink_in_guard_band(self, png_bytes: bytes) -> int:
        import io

        from PIL import Image

        from trinity_local.share_card_base import COLOR_ACCENT, COLOR_BG

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        px = img.load()
        assert px is not None
        bg, accent = COLOR_BG, COLOR_ACCENT
        body = 0
        for y in range(self._GUARD_BAND_TOP, self._GUARD_BAND_BOT):
            for x in range(0, self._LEFT_COL_MAX_X):
                pixel = px[x, y]
                if not isinstance(pixel, tuple):
                    continue
                if (abs(pixel[0] - bg[0]) + abs(pixel[1] - bg[1])
                        + abs(pixel[2] - bg[2])) <= 40:
                    continue  # background
                # ACCENT teal is the CTA's own ink and is allowed; only INK/MUTED
                # body text (the overrunning note/row) is the bug.
                if (abs(pixel[0] - accent[0]) + abs(pixel[1] - accent[1])
                        + abs(pixel[2] - accent[2])) < 60:
                    continue
                body += 1
        return body

    def _six_rows_mixed(self) -> CompareCardData:
        # The exact failing shape: 6 distinct-scored rows (forces "+N more") with
        # a per-row judge sub-line + mixed_eval_sets (the +24px warning). A real
        # `eval-show --compare` across 6 targets spanning >1 eval set hits this.
        def row(t, m, agg):
            return {"target": t, "model": m, "aggregate_score": agg,
                    "judge": "claude"}
        rows = [
            row("claude", None, 0.792), row("codex", None, 0.741),
            row("antigravity", None, 0.703),
            row("ollama", "qwen3.6:35b-a3b-instruct-2507", 0.655),
            row("ollama", "deepseek-r1-distill-qwen-32b", 0.601),
            row("ollama", "llama-3.3-70b-instruct", 0.552),
        ]
        return CompareCardData(rows=rows, mixed_eval_sets=True)

    def test_compare_leaderboard_note_does_not_collide_with_cta(self):
        png = render_compare_card(self._six_rows_mixed())
        body = self._body_ink_in_guard_band(png)
        assert body == 0, (
            "compare-share leaderboard: the '+N more' note / a leaderboard row "
            f"painted {body} body pixels into the 14px strip just above the CTA "
            "headline (y=480) — two text lines colliding on a PUBLICLY-shared "
            "PNG. The mixed-eval-set warning pushed the body down past a hard "
            "max_rows=5; the row cap must be y-aware (reserve the '+N more' slot "
            "above the CTA, like the aggregate card's _axis_rows_to_show). "
            "The #283 VERTICAL-overflow class."
        )

    def test_compare_matrix_rows_do_not_collide_with_cta(self):
        # Sibling check (same class): the per-axis matrix with 6 providers × many
        # axes + mixed_eval_sets. Its 4-row cap + bounded chip area should keep the
        # "+N more" row off the CTA; pin it so a future row-height/cap change can't
        # regress it silently.
        axes = {a: 0.6 for a in (
            "REFRAMING", "REDIRECTION", "SHARPENING", "COMPRESSION",
            "COMPREHENSIVENESS", "GROUNDING", "FACTUALITY", "CONCISENESS")}
        rows = [
            {"target": t, "model": m, "by_axis": dict(axes),
             "by_axis_n": {a: 8 for a in axes}}
            for t, m in [
                ("claude", None), ("codex", None), ("antigravity", None),
                ("ollama", "qwen3.6:35b-a3b-instruct-2507"),
                ("ollama", "deepseek-r1-distill-qwen-32b"),
                ("ollama", "llama-3.3-70b-instruct")]
        ]
        png = render_compare_matrix_card(
            CompareCardData(rows=rows, mixed_eval_sets=True))
        body = self._body_ink_in_guard_band(png)
        assert body == 0, (
            "compare-share matrix: a matrix row / '+N more' note painted "
            f"{body} body pixels into the strip just above the CTA headline "
            "(y=480) on a PUBLICLY-shared PNG. The #283 vertical-overflow class."
        )


class TestProviderLabelFit:
    """v1.7.212: a long provider label — a local-model slug like
    `qwen3.6:35b-a3b-coding-nvfp4`, which has NO brand so it renders raw via
    _provider_display_name's capitalize() fallback — overran its fixed label
    column straight into the matrix/leaderboard bars + scores, unreadable. Found
    2026-06-01 by rendering the matrix with the founder's local-rig default slug
    and EYEBALLING it (share_card_visual_testing). The axis labels already
    width-fit; the provider labels didn't. Realistic: `eval-run --target
    <local-ollama-slug>` produces exactly this row."""

    _LONG = "qwen3.6:35b-a3b-coding-nvfp4-an-absurdly-long-local-model-slug"

    def test_matrix_long_label_does_not_bleed_into_bars(self):
        """Strongest proof: render the matrix with a short vs an absurdly-long
        provider name and assert the BAR REGION (x >= 200) is byte-identical. A
        label that overruns its 130px column paints ink there and diverges. It's
        a RELATIVE diff (long-vs-short), so it's robust to per-platform font
        metrics — no absolute pixel assertion."""
        import io

        from PIL import Image

        def render(target):
            # TWO distinct providers so the matrix is NOT solo — the solo branch
            # draws the (fitted) provider name in the HEADLINE, which is a real
            # text element that legitimately differs by name length and would
            # confound a bar-region diff. The per-row TARGET-COLUMN label (what
            # this test guards) is exercised by the first row; the fixed-name
            # second row holds the layout. mixed_eval_sets still suppresses the
            # per-axis LEADER CHIPS so the provider name never lands up top.
            rows = [
                _compare_row(target, None, {"REFRAME": 0.7, "REDIRECT": 0.6}),
                _compare_row("codex", "gpt-5.5", {"REFRAME": 0.5, "REDIRECT": 0.4}),
            ]
            png = render_compare_matrix_card(
                CompareCardData(rows=rows, mixed_eval_sets=True))
            return Image.open(io.BytesIO(png)).convert("RGB")

        short = render("claude")
        long_ = render(self._LONG)
        # Bars start at axes_area_x = margin(60) + target_col_width(130) = 190;
        # x >= 200 is strictly bars/scores, never the provider label.
        box = (200, 0, CARD_WIDTH, CARD_HEIGHT)
        assert short.crop(box).tobytes() == long_.crop(box).tobytes(), (
            "long provider label bled past its 130px column into the matrix bars")

    def test_matrix_renderer_fits_provider_label(self):
        # `_fit_one_line(` + `target_col_width` both already appear (the axis-
        # header fit), so their presence DOESN'T prove the PROVIDER label is fit
        # (mutation gap — the pixel test catches it, this must too). Pin the
        # absence of the bare unfitted assignment instead.
        body = _fn_body("render_compare_matrix_card", "render_compare_card")
        assert 'target_name = _provider_display_name(row["target"]' not in body, (
            "matrix provider label must be wrapped in _fit_one_line, not drawn raw")
        assert "target_col_width - 8" in body, (
            "the provider-label fit must size to the target column")

    def test_compare_card_fits_both_row_label_and_headline(self):
        # The leaderboard fits the ROW target label AND the HEADLINE leader name
        # (the local model could win) — two distinct fit sites.
        body = _fn_body("render_compare_card", None)
        assert body.count("_fit_one_line(") >= 2, (
            "compare card must fit BOTH the row target label and the headline leader name")

    def test_eval_card_fits_provider_headline(self):
        body = _fn_body("render_eval_card", "render_compare_matrix_card")
        assert "_fit_one_line(" in body and "provider_name" in body and "suffix_w" in body, (
            "aggregate-card headline must fit the provider name (leave room for the score suffix)")

    def test_all_three_cards_render_with_long_provider_name(self):
        rows = [_compare_row(self._LONG, "qwen3.6 35b a3b", {"REFRAME": 0.6, "REDIRECT": 0.5})]
        assert render_compare_matrix_card(CompareCardData(rows=rows))[:8] == b"\x89PNG\r\n\x1a\n"
        assert render_compare_card(CompareCardData(rows=rows))[:8] == b"\x89PNG\r\n\x1a\n"
        agg = EvalCardData(
            target_provider=self._LONG, target_model="qwen3.6 35b a3b",
            aggregate_score=0.58, items_completed=20,
            by_axis=[("REFRAME", 0.6, 5), ("REDIRECT", 0.5, 5)],
        )
        assert render_eval_card(agg)[:8] == b"\x89PNG\r\n\x1a\n"


# ── #283 long-UNBREAKABLE-token horizontal overflow on the eval cards ──────────
# The council card's chairman-claim wrap was fixed at the shared
# share_card_base.wrap_text root (_break_long_word) at Iter 344. The eval card
# does NOT use wrap_text — every field is single-line — but it had THREE dynamic
# fields painted RAW (no width fit) that overflowed on a long SEPARATOR-FREE
# token a real eval can carry:
#   1. render_eval_card identity line  ("<target_model> · <effort>") — target_model
#      is user-controlled (local-model slug / agy settings / imported result JSON).
#   2. render_compare_matrix_card per-axis LEADER CHIP — a custom axis name from an
#      eval-import; one over-wide chip ran off the edge (the wrap-to-next-line check
#      only resets x, it can't shrink a chip already wider than the whole card).
#   3. render_compare_card judge attribution ("judge: <slug>") — judge slug raw from
#      a hand-edited / imported result.
# Each was driven (a 150-char no-space token) and read at the PIXEL level: the
# rightmost ink reached x=1199/1195 (the card EDGE), past the 1140 right margin —
# clipped mid-token on a PUBLICLY-shared PNG. Fixed by routing each through the
# shared _fit_one_line (the single-line sibling of _break_long_word, which
# hard-truncates a no-space token char-by-char). These guards bite the RENDERED
# PNG, not a source string.

_MARGIN_283 = 60
_RIGHT_MARGIN_283 = CARD_WIDTH - _MARGIN_283  # 1140
# A token with NO whitespace and NO hyphens — nothing the greedy wrapper or an
# ellipsis-on-space fitter could break on; only a char-by-char hard truncation
# keeps it inside the margin.
_LONG_NOSEP = "Z" * 150


def _rightmost_ink_283(png_bytes, y0, y1, *, tol=12):
    """Largest x with non-background ink in the band [y0, y1). Uses the
    COLOR_BG-tolerance test (NOT a brightness threshold — the card BG is the
    near-white #eaecef mist, which a >240=ink test false-flags as ink)."""
    import io
    from PIL import Image
    from trinity_local.share_card_base import COLOR_BG

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.load()
    w, h = img.size
    br, bg, bb = COLOR_BG
    max_x = -1
    for yy in range(y0, min(y1, h)):
        for xx in range(w - 1, -1, -1):
            r, g, b = px[xx, yy][:3]
            if not (abs(r - br) <= tol and abs(g - bg) <= tol and abs(b - bb) <= tol):
                if xx > max_x:
                    max_x = xx
                break
    return max_x


def _card_worst_ink_283(png_bytes):
    """The rightmost ink anywhere on the card body (skip the very bottom footer
    band, which carries the right-aligned wordmark by design). Used as a
    whole-card overflow probe for the chip path, whose y is layout-dependent."""
    worst = -1
    for y0 in range(50, 540, 5):
        rx = _rightmost_ink_283(png_bytes, y0, y0 + 5)
        if rx > worst:
            worst = rx
    return worst


class TestEvalCardLongTokenHorizontalOverflow:
    """#283 long-unbreakable-token overflow on the eval share cards (the eval
    sibling of the Iter-344 council-card fix). Pixel-level: rightmost ink must
    stay within the 1140 right margin even on a 150-char separator-free token."""

    def test_identity_line_long_model_stays_within_right_margin(self):
        # target_model with no spaces/hyphens (a plausible Ollama tag); the #239
        # identity line ("<model> · high") painted it RAW and ran to x=1199.
        data = EvalCardData(
            target_provider="ollama",
            target_model="qwen3p6" + _LONG_NOSEP + "end",
            target_effort="high",
            aggregate_score=0.74, items_total=20, items_completed=20,
            by_axis=[("REFRAME", 0.81, 10), ("COMPRESSION", 0.66, 10)],
        )
        png = render_eval_card(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        # The identity line sits just under the headline; band y ~184..232 (26px sub).
        rightmost = _rightmost_ink_283(png, 184, 232)
        assert 0 < rightmost <= _RIGHT_MARGIN_283 + 2, (
            "REGRESSION: the eval-share identity line ran off the right margin on a "
            f"long separator-free target_model — rightmost ink x={rightmost} exceeds "
            f"the {_RIGHT_MARGIN_283}px right margin (card edge {CARD_WIDTH}). A "
            "user-controlled local-model slug with no hyphens painted '<model> · high' "
            "off the edge, clipped mid-token on this PUBLIC PNG. Fit the identity line "
            "with _fit_one_line (the #283 long-token class; council card fixed Iter 344)."
        )

    def test_matrix_leader_chip_long_axis_stays_within_right_margin(self):
        # A custom axis name (eval-import) with no separators made ONE leader chip
        # wider than the whole card; the wrap-to-next-line reset can't shrink it.
        rows = [
            _compare_row("claude", "claude-opus", {_LONG_NOSEP: 0.81}, n=10),
            _compare_row("codex", "gpt-5-5", {_LONG_NOSEP: 0.55}, n=10),
        ]
        png = render_compare_matrix_card(CompareCardData(rows=rows))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        worst = _card_worst_ink_283(png)
        assert 0 < worst <= _RIGHT_MARGIN_283 + 2, (
            "REGRESSION: a per-axis leader CHIP ran off the right margin on a long "
            f"separator-free custom axis name — rightmost card ink x={worst} exceeds "
            f"the {_RIGHT_MARGIN_283}px right margin. An eval-import axis with no "
            "separators made one chip wider than the whole card (the wrap-to-next-line "
            "check only resets x). Fit the axis name into the chip width with _fit_one_line."
        )

    def test_compare_judge_long_slug_stays_within_right_margin(self):
        # A long separator-free judge slug (raw in a hand-edited / imported result)
        # painted the "judge: <slug>" attribution line off the edge.
        # The judge attribution uses the small 14px judge font, so the slug must be
        # long to overflow it (the field is raw-user-influenceable via a hand-edited
        # / imported result; a real `judge_provider` is short, so this is the
        # defensive far edge). 220 no-sep chars runs to the card edge unfixed.
        rows = [
            {"target": "claude", "model": "claude-opus", "aggregate_score": 0.8,
             "items_completed": 10, "judge": "Z" * 220},
            {"target": "codex", "model": "gpt-5-5", "aggregate_score": 0.6,
             "items_completed": 10, "judge": "claude"},
        ]
        png = render_compare_card(CompareCardData(rows=rows))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        # The judge line sits under the first row's target name (y ~+28); the first
        # row begins ~y=210 in the leaderboard. Probe the whole body to be robust.
        worst = _card_worst_ink_283(png)
        assert 0 < worst <= _RIGHT_MARGIN_283 + 2, (
            "REGRESSION: the compare-card 'judge: <slug>' attribution ran off the "
            f"right margin on a long separator-free judge slug — rightmost card ink "
            f"x={worst} exceeds the {_RIGHT_MARGIN_283}px right margin. Fit the judge "
            "line with _fit_one_line."
        )

    def test_normal_text_unchanged_no_spurious_truncation(self):
        # COMPLEMENT (so the fits aren't over-broad and the break only fires on a
        # genuinely over-wide token): a normal hyphenated model, normal axes, and a
        # normal judge slug all stay within the margin AND keep their ink (the fit
        # must not erase or spuriously truncate a field that already fits).
        data = EvalCardData(
            target_provider="antigravity", target_model="gemini-3-1-pro-preview",
            target_effort="high", aggregate_score=0.74, items_total=20, items_completed=20,
            by_axis=[("REFRAME", 0.81, 10), ("COMPRESSION", 0.66, 10)],
        )
        png = render_eval_card(data)
        identity_ink = _rightmost_ink_283(png, 184, 232)
        # The full "gemini-3-1-pro-preview · high" is well under the margin — present
        # (ink > a bare separator's width) and not pushed to the edge.
        assert 100 < identity_ink <= _RIGHT_MARGIN_283, (
            "the fit over-truncated or shifted a NORMAL identity line "
            f"(rightmost ink x={identity_ink}); it should render in full within margin")

        rows = [
            _compare_row("claude", "claude-opus", {"REFRAME": 0.81, "COMPRESSION": 0.55}, n=10),
            _compare_row("codex", "gpt-5-5", {"REFRAME": 0.6, "COMPRESSION": 0.77}, n=10),
        ]
        assert _card_worst_ink_283(
            render_compare_matrix_card(CompareCardData(rows=rows))) <= _RIGHT_MARGIN_283 + 2
        crows = [
            {"target": "claude", "model": "claude-opus", "aggregate_score": 0.8,
             "items_completed": 10, "judge": "codex"},
            {"target": "codex", "model": "gpt-5-5", "aggregate_score": 0.6,
             "items_completed": 10, "judge": "claude"},
        ]
        assert _card_worst_ink_283(
            render_compare_card(CompareCardData(rows=crows))) <= _RIGHT_MARGIN_283 + 2


class TestLaunchAssetEvalCardsPalette:
    """The 3 committed reference eval cards in docs/launch_assets/ are the
    canonical 'personal benchmark' artifact referenced by CONTRIBUTING.md +
    design-system/README.md. They were rendered ONCE and committed, so they
    silently went stale when the palette flipped cream -> Calm/Muted-Teal
    (v1.7.310 in share_card_base) — the renderer was teal but the committed PNGs
    stayed cream for weeks (caught 2026-06-06). This pins their background to the
    current COLOR_BG so a future palette change forces a re-render (the regen
    recipe is in the commit that added this guard)."""

    def test_committed_eval_cards_use_current_palette(self):
        from pathlib import Path
        from PIL import Image
        from trinity_local.share_card_base import COLOR_BG

        repo = Path(__file__).resolve().parents[1]
        cards = [
            "eval_card_claude.png",
            "eval_card_codex.png",
            "eval_card_antigravity.png",
        ]
        for name in cards:
            p = repo / "docs" / "launch_assets" / name
            assert p.exists(), f"reference eval card missing: {p}"
            img = Image.open(p).convert("RGB")
            corner = img.getpixel((5, 5))
            assert corner == COLOR_BG, (
                f"{name} background {corner} != current palette {COLOR_BG} — the "
                f"committed reference card is stale (palette drifted). Re-render: "
                f"load_run_result(<source>) -> collect_card_data_from_result -> "
                f"render_eval_card -> write_bytes."
            )


def _drawn_strings(data: EvalCardData) -> list[str]:
    """Render an eval card while spying on every ImageDraw.text() call, and
    return the exact strings the renderer painted onto the PNG. The eval card
    is a pure-Pillow PNG with no extractable text layer, so the only way to
    pin a DRAWN-TEXT invariant (vs. a layout-math proxy) is to record the
    strings as they're drawn."""
    from PIL import ImageDraw

    drawn: list[str] = []
    orig_text = ImageDraw.ImageDraw.text

    def _spy(self, xy, text, *args, **kwargs):
        if isinstance(text, str):
            drawn.append(text)
        return orig_text(self, xy, text, *args, **kwargs)

    ImageDraw.ImageDraw.text = _spy
    try:
        render_eval_card(data)
    finally:
        ImageDraw.ImageDraw.text = orig_text
    return drawn


class TestEvalCardPromptCountAgreesWithBars:
    """The subhead prompt count ("· N prompts, M axes") is a PUBLIC claim that
    sits directly above the per-axis bars. A persisted eval result can carry
    items_completed=0 while by_rejection_type is fully populated —
    load_run_result defaults the field to 0 when it's absent (an imported
    result, a self_preference synthetic run, or an older-schema JSON), so a
    real `eval-share` on such a result rendered a self-contradicting
    'Gemini scored 0.83 · 0 prompts, 4 axes' card OVER four populated bars
    (found 2026-06-17 by rendering the load-from-disk path + READING the PNG).
    The count must be floored on the per-axis evidence the card is already
    drawing (the bars sum to the genuinely-scored items)."""

    def _subhead(self, drawn: list[str]) -> str:
        # Match on the stable prefix the count-subhead always carries — NOT on
        # "prompts,"/"axes", which the n=1 case correctly singularizes to
        # "1 prompt, 1 axis" (see test_single_prompt_single_axis_is_singular).
        hits = [s for s in drawn
                if "on YOUR kind of question · " in s
                and ("prompt" in s and ("axis" in s or "axes" in s))]
        assert hits, f"no subhead drawn; strings were: {drawn}"
        return hits[0]

    def test_zero_items_completed_with_populated_axes_does_not_claim_zero(self):
        # The exact disk-load default shape: items_completed absent -> 0, but
        # by_axis sums to 19 (7+5+4+3).
        data = EvalCardData(
            target_provider="antigravity",
            target_model="gemini-3.1-pro",
            aggregate_score=0.833,
            items_total=19,
            items_completed=0,
            by_axis=[("REFRAME", 0.91, 7), ("COMPRESSION", 0.78, 5),
                     ("REDIRECT", 0.84, 4), ("SHARPENING", 0.80, 3)],
        )
        subhead = self._subhead(_drawn_strings(data))
        assert "0 prompts" not in subhead, (
            "eval-share PUBLIC card claimed '0 prompts' while showing four "
            "populated per-axis bars (sum 19) and a confident 0.833 score — a "
            f"self-contradicting benchmark. subhead drawn: {subhead!r}"
        )
        assert "19 prompts" in subhead, (
            "the prompt count must be floored on the per-axis evidence the card "
            f"is already drawing (7+5+4+3=19). subhead drawn: {subhead!r}"
        )

    def test_real_items_completed_is_preserved(self):
        # When the field IS present and >= the axis sum, it must win (the count
        # of dispatched items can legitimately exceed the judged per-axis sum).
        data = EvalCardData(
            target_provider="claude",
            target_model="claude-opus-4-8",
            aggregate_score=0.74,
            items_total=25,
            items_completed=22,
            by_axis=[("REFRAME", 0.8, 5), ("COMPRESSION", 0.7, 4)],
        )
        subhead = self._subhead(_drawn_strings(data))
        assert "22 prompts" in subhead, (
            "a real items_completed (22) that exceeds the axis sum (9) must be "
            f"preserved, not clobbered by the floor. subhead drawn: {subhead!r}"
        )

    def test_single_prompt_single_axis_is_singular(self):
        # n=1 plural-literal class (the /stats-caption bug, Iter 101): a 1-item,
        # single-axis eval (e.g. `run_eval` limit=1, or a corpus with only one
        # rejection type) renders this PUBLIC share card — "1 prompts, 1 axes" is
        # ungrammatical. The count is the REAL DRAWN string on the PNG, captured
        # via the ImageDraw.text spy (not a source check). "axis" is the singular
        # of "axes".
        data = EvalCardData(
            target_provider="claude",
            target_model="opus-4-8",
            aggregate_score=0.74,
            items_total=1,
            items_completed=1,
            by_axis=[("REFRAME", 0.74, 1)],
        )
        subhead = self._subhead(_drawn_strings(data))
        assert "1 prompt, 1 axis" in subhead, (
            "the eval-share PUBLIC card must read '1 prompt, 1 axis' at n=1, not "
            f"the ungrammatical hardcoded plural. subhead drawn: {subhead!r}"
        )
        assert "1 prompts" not in subhead and "1 axes" not in subhead, (
            "eval-share PUBLIC card rendered ungrammatical '1 prompts'/'1 axes' "
            f"at n=1 (the hardcoded plural-literal regressed). subhead: {subhead!r}"
        )

    def test_plural_preserved_above_one(self):
        # The singular fix must NOT over-reach: n>=2 must still read plural.
        data = EvalCardData(
            target_provider="claude",
            target_model="opus-4-8",
            aggregate_score=0.74,
            items_total=3,
            items_completed=3,
            by_axis=[("REFRAME", 0.7, 1), ("REDIRECT", 0.7, 1),
                     ("COMPRESSION", 0.8, 1)],
        )
        subhead = self._subhead(_drawn_strings(data))
        assert "3 prompts, 3 axes" in subhead, (
            "n>=2 eval-share card lost its correct plural — the singular fix "
            f"over-reached. subhead drawn: {subhead!r}"
        )


def _drawn_strings_for(render_fn, data) -> list[str]:
    """Spy on every ImageDraw.text() call for an ARBITRARY card renderer and
    return the exact strings painted onto the PNG. Generalizes _drawn_strings
    (which only wrapped render_eval_card) to the compare leaderboard + matrix
    renderers. A pure-Pillow PNG has no extractable text layer, so a drawn-text
    invariant is the strongest available pixel-level guard (the comparison verb
    that lands on the public card is exactly what these assert)."""
    from PIL import ImageDraw

    drawn: list[str] = []
    orig_text = ImageDraw.ImageDraw.text

    def _spy(self, xy, text, *args, **kwargs):
        if isinstance(text, str):
            drawn.append(text)
        return orig_text(self, xy, text, *args, **kwargs)

    ImageDraw.ImageDraw.text = _spy
    try:
        render_fn(data)
    finally:
        ImageDraw.ImageDraw.text = orig_text
    return drawn


class TestCompareCardSoloOverclaim:
    """The cross-provider leaderboard + per-axis matrix cards are PUBLIC OG
    artifacts (eval-share --compare → Twitter/LinkedIn). A user who has only
    scored ONE provider — the common early state, since you score one model
    before you think to score the others — yields exactly ONE leaderboard row
    (_collect_leaderboard_rows dedups per provider). With one contestant the
    leaderboard headline rendered 'Claude leads at 0.79' under a 'CROSS-PROVIDER
    LEADERBOARD' eyebrow, and the matrix painted per-axis 'leader' chips
    ('REFRAME: Claude 0.81') under 'Different models for different questions.' —
    a head-to-head verdict with no opponent (found 2026-06-17 by rendering the
    single-row card + READING the pixels). That is the council-card solo-
    overclaim shape (#35 green-while-degenerate): a ranking claim the data can't
    support. The comparison framing must gate on >=2 DISTINCT scored providers;
    a solo card demotes to an honest single-provider benchmark (bars kept)."""

    _SOLO = [_compare_row("claude", "claude-opus-4-8",
                          {"REFRAME": 0.81, "COMPRESSION": 0.74})]
    # Distinct aggregates (0.81 vs 0.72) so the leader has a REAL lead — the
    # default _compare_row agg=0.6 would tie BOTH rows, and the tie-demotion
    # gate (#35) correctly drops "leads at" on a tie, which is not what this
    # "real lead keeps the ranking verbs" test means to assert. The per-axis
    # scores still split (claude wins REFRAME, codex wins COMPRESSION) so the
    # per-axis leader chips render.
    _TWO = [_compare_row("claude", "claude-opus-4-8",
                         {"REFRAME": 0.81, "COMPRESSION": 0.74}, agg=0.81),
            _compare_row("codex", "gpt-5.5",
                         {"REFRAME": 0.60, "COMPRESSION": 0.90}, agg=0.72)]

    def test_solo_leaderboard_does_not_claim_leads(self):
        drawn = _drawn_strings_for(render_compare_card,
                                   CompareCardData(rows=self._SOLO))
        joined = " || ".join(drawn)
        assert not any("leads at" in s for s in drawn), (
            "single-provider eval-share --compare PUBLIC card claimed "
            "'<model> leads at 0.79' with NO opponent — a head-to-head verdict "
            f"a leaderboard of one can't support (#35). strings drawn: {joined!r}"
        )
        assert not any("LEADERBOARD" in s for s in drawn), (
            "single-provider card kept the 'CROSS-PROVIDER LEADERBOARD' eyebrow "
            f"with one contestant — it is not a cross-provider ranking. {joined!r}"
        )
        # Demote-not-hide: the honest self-score verb + the actionable next step.
        # _compare_row hardcodes aggregate_score=0.60, so the headline self-number.
        assert any("scored 0.60" in s for s in drawn), (
            f"solo card must keep the honest 'scored' self-number. {joined!r}")
        assert any("score a 2nd provider" in s for s in drawn), (
            f"solo card must tell the user how to unlock the ranking. {joined!r}")

    def test_solo_matrix_does_not_claim_per_axis_leaders(self):
        drawn = _drawn_strings_for(render_compare_matrix_card,
                                   CompareCardData(rows=self._SOLO))
        joined = " || ".join(drawn)
        # Per-axis leader chips read "<AXIS>: <Model> <score>". With one model
        # there is no leader to crown.
        assert not any(("REFRAME: " in s or "COMPRESSION: " in s) for s in drawn), (
            "single-provider per-axis matrix painted a 'leader' chip "
            f"(e.g. 'REFRAME: Claude 0.81') with NO opponent (#35). {joined!r}")
        assert not any("Different models" in s for s in drawn), (
            "single-provider matrix kept the 'Different models for different "
            f"questions.' comparison wedge over ONE model. {joined!r}")
        # Bars stay: a single per-axis score is meaningful per se — the card is
        # demoted to an honest one-provider per-axis profile, not blanked.
        assert any("0.81" in s for s in drawn) and any("0.74" in s for s in drawn), (
            f"solo matrix must keep the per-axis bar scores. {joined!r}")

    def test_two_distinct_providers_keep_leads_and_chips(self):
        # The fix must be scoped — the >=2-provider path (the whole point of the
        # card) must STILL paint the ranking verbs, or the guard is over-broad.
        lb = _drawn_strings_for(render_compare_card, CompareCardData(rows=self._TWO))
        assert any("leads at" in s for s in lb), (
            "two-provider leaderboard lost its 'leads at' ranking — the solo gate "
            f"over-reached and gutted the card's whole purpose. drawn: {lb!r}")
        mx = _drawn_strings_for(render_compare_matrix_card,
                                CompareCardData(rows=self._TWO))
        assert any("Different models" in s for s in mx), (
            f"two-provider matrix lost the comparison wedge headline. drawn: {mx!r}")
        assert any(("REFRAME: " in s or "COMPRESSION: " in s) for s in mx), (
            f"two-provider matrix lost the per-axis leader chips. drawn: {mx!r}")

    def test_same_provider_under_two_slugs_reads_as_solo(self):
        # Two rows but ONE actual model (a `gemini` capture + an `antigravity`
        # CLI run fold to the same provider) must NOT unlock the ranking — the
        # card counts DISTINCT normalized slugs, not raw row count.
        rows = [_compare_row("gemini", "gemini-3.1-pro", {"REFRAME": 0.80}),
                _compare_row("antigravity", "gemini-3.1-pro", {"REFRAME": 0.70})]
        drawn = _drawn_strings_for(render_compare_card, CompareCardData(rows=rows))
        assert not any("leads at" in s for s in drawn), (
            "same provider under two slugs (gemini + antigravity) read as two "
            f"contestants and claimed a ranking. drawn: {drawn!r}")
        assert any("scored" in s for s in drawn), (
            f"folded-duplicate card must show the honest single-model score. {drawn!r}")


class TestSingleTargetCardLowNHonesty:
    """The SINGLE-TARGET eval-share card (`render_eval_card`) must de-emphasize a
    per-axis bar built on n < MIN_AXIS_SAMPLES — the SAME low-n honesty treatment
    the per-axis MATRIX card has always had (reduced-alpha fill + '(n=N)'
    annotation). Found 2026-06-19 by rendering `eval-run --limit 1` -> `eval-share`
    and READING the PNG: a 1-prompt 'GPT scored 0.50' COMPRESSION bar painted as a
    fully-opaque, authoritative teal bar with a bare '0.50' — indistinguishable
    from a 20-prompt bar. That is the exact confidence-honesty regression the
    rest of the eval stack (leaderboard suppress, matrix alpha+n-tag, /stats
    captions) refuses to ship; the single-target card — the MOST public artifact
    (headline + install CTA) — was the one path that skipped it.

    Two independent signals, both MUTATION-PROVEN against the un-fixed code (a
    fully-opaque bar + bare score):
      1. The DRAWN '(n=N)' tag (text-spy) — bites if the annotation is dropped.
      2. A real PIXEL-luminance read — the low-n bar composites LIGHTER than a
         full-n bar at the SAME score, so it bites if the alpha-fill is reverted
         to the solid COLOR_BAR_FILL (the two bars would then be identical).
    """

    def _low_n_data(self):
        # Two axes, IDENTICAL score (0.6) so any color difference between the two
        # bars can only come from the low-n alpha treatment, not the fill width.
        return EvalCardData(
            target_provider="claude", target_model="Claude Opus 4.8",
            target_effort="high", aggregate_score=0.6, items_completed=9,
            by_axis=[("REFRAME", 0.6, 8), ("COMPRESSION", 0.6, 1)],
        )

    def test_low_n_axis_draws_sample_size_tag(self):
        drawn = _drawn_strings(self._low_n_data())
        assert any(s == "n=1" for s in drawn), (
            "the single-target eval-share card drew a 1-prompt COMPRESSION bar "
            "with NO sample-size tag — a 0.50-on-n=1 score painted as a confident "
            "public benchmark (the matrix card's low-n '(n=N)' honesty never "
            f"reached this path). drawn strings: {drawn!r}"
        )
        # Scope guard: a full-n axis must NOT carry the tag (no over-reach).
        assert not any(s == "n=8" for s in drawn), (
            "the n>=MIN_AXIS_SAMPLES bar wrongly carried a sample-size tag — the "
            f"low-n treatment over-reached onto a healthy sample. drawn: {drawn!r}"
        )

    def test_low_n_bar_fill_is_visibly_lighter_than_full_n_bar(self):
        import io
        from PIL import Image
        from trinity_local.eval_card import MIN_AXIS_SAMPLES

        assert MIN_AXIS_SAMPLES == 3  # the floor this card honors

        png = render_eval_card(self._low_n_data())
        img = Image.open(io.BytesIO(png)).convert("RGB")

        # Scan a column just inside the bar fill (bar_track_x=300; both bars are
        # filled to 0.6 so x=320 is inside both) for the two fill-row bands.
        x = 320
        bands: list[tuple[int, tuple[int, int, int]]] = []
        in_band = False
        for yy in range(250, _CTA_BLOCK_TOP):
            r, g, b = img.getpixel((x, yy))
            is_fill = g > r + 8 and b > r and g > 100 and r < 200
            if is_fill and not in_band:
                bands.append((yy, (r, g, b)))
            in_band = is_fill
        assert len(bands) >= 2, (
            f"expected two bar-fill bands (full-n + low-n), found {len(bands)}: "
            f"{bands!r} — the low-n bar may have vanished or both collapsed."
        )
        full_n_px = bands[0][1]   # REFRAME (n=8) — top row, solid fill
        low_n_px = bands[1][1]    # COMPRESSION (n=1) — second row, alpha fill
        full_lum = sum(full_n_px)
        low_lum = sum(low_n_px)
        assert low_lum > full_lum + 60, (
            "the n=1 bar composited to the SAME (or darker) color as the n=8 bar "
            f"at an identical 0.6 score — full-n {full_n_px} (lum {full_lum}) vs "
            f"low-n {low_n_px} (lum {low_lum}). The low-n alpha-fill is missing: a "
            "1-prompt score paints as authoritatively as a 20-prompt one on the "
            "PUBLIC eval-share card."
        )


class TestEvalCardDrawsModelBrandNotSlug:
    """The eval-share cards (1200×630 PNGs posted to HN/Twitter/LinkedIn) draw a
    PROVIDER identity in EVERY render path — the aggregate headline ("Gemini
    scored 0.74"), the leaderboard headline + runner-up ("Gemini leads at 0.79 ·
    +0.170 ahead of GPT"), and the per-axis matrix leader chips + row labels
    ("REFRAME: Gemini 0.81"). Each must paint the MODEL BRAND
    (antigravity→Gemini, codex→GPT), never the raw dispatch slug.

    The existing eval guards prove this only INDIRECTLY: test_share_card_brand
    asserts the HELPER `_provider_display_name(slug, None) == brand` (so the
    brand-fold function is correct), and the layout/pixel-region tests
    (TestProviderLabelFit, TestCompareCardVerticalCtaCollision) assert WHERE ink
    lands, not WHICH glyphs. None RENDER a card with a discriminating slug≠brand
    fixture and assert the brand is drawn while the slug is absent — so a render
    path that drew `data.target_provider` / `row["target"]` RAW (bypassing
    _provider_display_name) would leak the lowercase slug onto the public PNG
    while every existing test stayed green. This is the exact council_card gap
    closed in test_council_card.TestCouncilCardDrawsModelBrandNotSlug; eval_card
    is the unguarded sibling that draws a provider in three more places.

    Discriminating fixtures: antigravity (→ Gemini) and codex (→ GPT) — the slug
    and its brand are DIFFERENT strings, so a raw-slug leak is detectable. (claude
    is useless here: its slug capitalizes to its own brand "Claude".)
    """

    def test_aggregate_headline_draws_brand_not_slug(self):
        # Precondition: the chosen slug's brand differs from the slug itself, so
        # "brand drawn + slug absent" is a real discriminator (not vacuous).
        from trinity_local.council_schema import provider_model_brand
        assert provider_model_brand("antigravity") == "Gemini" != "antigravity"

        data = EvalCardData(
            target_provider="antigravity", target_model="gemini-3.1-pro",
            target_effort="high", aggregate_score=0.74, items_total=20,
            items_completed=20,
            by_axis=[("REFRAME", 0.8, 6), ("COMPRESSION", 0.7, 5)],
        )
        drawn = _drawn_strings(data)
        joined = "\n".join(drawn)
        assert any("Gemini scored 0.74" in s for s in drawn), (
            "eval-share aggregate card drew the wrong headline for "
            "target='antigravity' — expected the MODEL BRAND 'Gemini scored 0.74'. "
            f"Drawn:\n{joined}"
        )
        assert "antigravity" not in joined.lower(), (
            "REGRESSION: the raw dispatch slug 'antigravity' leaked onto the "
            "PUBLICLY-shared eval PNG instead of the model brand 'Gemini' — the "
            "aggregate headline must route through _provider_display_name, not "
            f"draw data.target_provider raw. Drawn:\n{joined}"
        )

    def test_leaderboard_headline_and_runner_draw_brand_not_slug(self):
        from trinity_local.council_schema import provider_model_brand
        assert provider_model_brand("antigravity") == "Gemini" != "antigravity"
        assert provider_model_brand("codex") == "GPT" != "codex"

        rows = [
            {"target": "antigravity", "model": "gemini-3.1-pro",
             "aggregate_score": 0.79, "items_completed": 20, "judge": "claude",
             "by_axis": {"REFRAME": 0.8}},
            {"target": "codex", "model": "gpt-5.5",
             "aggregate_score": 0.62, "items_completed": 20, "judge": "claude",
             "by_axis": {"REFRAME": 0.6}},
        ]
        drawn = _drawn_strings_for(render_compare_card, CompareCardData(rows=rows))
        joined = "\n".join(drawn)
        # Leader headline (antigravity→Gemini) and the "+N ahead of <runner>"
        # subhead (codex→GPT) both carry a brand.
        assert any("Gemini leads at 0.79" in s for s in drawn), (
            "eval-share --compare leaderboard drew the wrong leader headline for "
            f"target='antigravity' — expected 'Gemini leads at 0.79'. Drawn:\n{joined}"
        )
        assert any("ahead of GPT" in s for s in drawn), (
            "the leaderboard runner-up subhead drew the wrong brand for "
            f"target='codex' — expected '… ahead of GPT'. Drawn:\n{joined}"
        )
        assert "antigravity" not in joined.lower() and "codex" not in joined.lower(), (
            "REGRESSION: a raw dispatch slug ('antigravity'/'codex') leaked onto "
            "the PUBLICLY-shared cross-provider leaderboard PNG instead of the "
            "model brand (Gemini/GPT) — the headline, the runner-up subhead, and "
            "each row label must route through _provider_display_name, not draw "
            f"row['target'] raw. Drawn:\n{joined}"
        )

    def test_matrix_leader_chips_and_rows_draw_brand_not_slug(self):
        from trinity_local.council_schema import provider_model_brand
        assert provider_model_brand("antigravity") == "Gemini" != "antigravity"
        assert provider_model_brand("codex") == "GPT" != "codex"

        rows = [
            {"target": "antigravity", "model": "gemini-3.1-pro",
             "by_axis": {"REFRAME": 0.81, "COMPRESSION": 0.50},
             "by_axis_n": {"REFRAME": 6, "COMPRESSION": 6}},
            {"target": "codex", "model": "gpt-5.5",
             "by_axis": {"REFRAME": 0.40, "COMPRESSION": 0.90},
             "by_axis_n": {"REFRAME": 6, "COMPRESSION": 6}},
        ]
        drawn = _drawn_strings_for(render_compare_matrix_card,
                                   CompareCardData(rows=rows))
        joined = "\n".join(drawn)
        # Per-axis leader chips name the winning model per axis.
        assert any("REFRAME: Gemini" in s for s in drawn), (
            "eval-share --compare --by-axis matrix drew the wrong per-axis leader "
            "chip for target='antigravity' — expected 'REFRAME: Gemini 0.81'. "
            f"Drawn:\n{joined}"
        )
        assert any("COMPRESSION: GPT" in s for s in drawn), (
            "the matrix per-axis leader chip drew the wrong brand for "
            f"target='codex' — expected 'COMPRESSION: GPT 0.90'. Drawn:\n{joined}"
        )
        assert "antigravity" not in joined.lower() and "codex" not in joined.lower(), (
            "REGRESSION: a raw dispatch slug ('antigravity'/'codex') leaked onto "
            "the PUBLICLY-shared per-axis matrix PNG instead of the model brand "
            "(Gemini/GPT) — the leader chips AND the per-provider row labels must "
            "route through _provider_display_name, not draw row['target'] raw. "
            f"Drawn:\n{joined}"
        )
