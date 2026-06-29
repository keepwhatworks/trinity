"""eval-share PNG export — render an eval run result as a 1200×630 OG card.

Matches the visual language of `me_card.py` (same palette, fonts, margin
system, footer convention). The hero is the aggregate score against the
user's lens; per-axis bars show where the target model wins and loses on
the user's specific rejection signal.

Single function: ``render_eval_card(card_data) -> bytes``. CLI writes the
bytes to disk; future launchpad share button would download them.

The card is the artifact the user's pitch produces — *"I ran my evals on
Antigravity; here's where it landed; here's how you can do it too."* The
recipient gets:

1. The headline ("Claude scored 0.661 on YOUR kind of question")
2. Per-axis breakdown (REFRAME / COMPRESSION / REDIRECT / SHARPENING)
3. A clear install CTA below the chart
4. A github.com URL footer for the repo-public surface
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .share_card_base import (
    CARD_WIDTH,
    CARD_HEIGHT,
    COLOR_INK,
    COLOR_MUTED,
    COLOR_ACCENT,
    LANDING_URL as CTA_LANDING_URL,
    draw_wordmark,
    load_font as _load_font,
    fit_one_line as _fit_one_line,
    strip_unrenderable as _strip_unrenderable,
    NON_LATIN_PLACEHOLDER as _NON_LATIN,
    blank_canvas,
    save_png,
)

# Card-specific accents — solid sage for the score bar, transparent
# sage for the empty track behind it.
COLOR_BAR_FILL = (79, 144, 149)
COLOR_BAR_TRACK = (37, 88, 71, 36)
# Amber caution — matches the launchpad's mixed-eval-set warning (#c4791f).
COLOR_WARN = (189, 150, 88)


def _draw_mixed_set_warning(draw, margin: int, y: int, warn_font) -> None:
    """Draw the 'rows span multiple eval sets' caution line.

    The caution mark is a VECTOR triangle-with-bang, NOT the U+26A0 warning
    glyph: the card font (serif/Helvetica via PIL) has no glyph for it and
    renders it as a tofu box on the PNG (same class of bug as the U+2194
    tension arrow, which me_card replaced with the text "vs."). Both eval-card
    render paths (aggregate leaderboard + per-axis matrix) call this, so the
    icon can't drift between them. test_share_card_brand guards the regression.
    """
    th = 16  # icon height
    apex_x, top = margin + 9, y + 1
    bot = top + th
    draw.polygon(
        [(apex_x, top), (apex_x - 9, bot), (apex_x + 9, bot)],
        outline=COLOR_WARN, width=2,
    )
    draw.line([(apex_x, top + 5), (apex_x, bot - 5)], fill=COLOR_WARN, width=2)
    draw.ellipse([apex_x - 1, bot - 4, apex_x + 1, bot - 2], fill=COLOR_WARN)
    draw.text(
        (apex_x + 18, y),
        "rows span multiple eval sets — pass --eval-id to scope",
        font=warn_font, fill=COLOR_MUTED,
    )

# The MIN_AXIS_LEADER_N threshold the launchpad + CLI share (one source of truth
# in evals.composition_floor; commits dd83aa0, 0c20656). Below this floor: leader
# chips suppress, and BOTH
# share-card render paths (the single-target aggregate bars AND the
# per-axis matrix) render the low-n bar with reduced opacity and a
# "(n=N)" annotation. Don't let a 0.12-fill bar from n=1 read with the
# same visual weight as a 0.93-fill bar from n=20 — least of all on the
# single-target card, the most public artifact (the headline + install
# CTA), where a 1-prompt "GPT scored 0.50" bar painted as fully
# authoritative was exactly the confidence-honesty regression these
# constants exist to prevent.
# Single source of truth for the per-provider per-axis leader floor — imported,
# not re-hardcoded, so the share PNG can't drift from the launchpad/CLI gates
# (see evals.composition_floor.MIN_AXIS_LEADER_N).
from .evals.composition_floor import MIN_AXIS_LEADER_N as MIN_AXIS_SAMPLES
from .utils import finite_float_or_none
# Low-n visual treatment, SHARED by both render paths so the matrix and
# the single-target card can't drift in how they de-emphasize a thin
# sample (they used to: the matrix had this, the single-target card had
# a fully-opaque bar + bare score on the same n=1 data).
LOW_N_BAR_FILL = (37, 88, 71, 100)  # COLOR_BAR_FILL hue at ~40% alpha
LOW_N_INK = (60, 60, 60, 120)  # muted ink for low-n score text


def _distinct_target_count(rows: list[dict]) -> int:
    """How many DISTINCT providers a comparison card actually has.

    A "leads"/"leader"/"different models" verdict needs at least two
    contestants — a leaderboard of one cannot rank, and a per-axis
    "leader" with no opponent is the council-card solo-overclaim shape
    (#35 green-while-degenerate) on a PUBLICLY-shared OG card. The CLI
    dedups rows per provider upstream (_collect_leaderboard_rows.by_target),
    but the card defends itself: count distinct NORMALIZED slugs so two
    runs of the same provider (a `gemini` capture + an `antigravity` CLI
    run) never read as two contestants. Empty/None targets don't count.

    Thin re-export of the shared evals.composition_floor.distinct_target_count
    so the PNG card, the CLI text/JSON, and the launchpad chips all agree on
    "is this a contest?" — the contender gate can't drift between surfaces.
    """
    from .evals.composition_floor import distinct_target_count

    return distinct_target_count(rows)


@dataclass
class EvalCardData:
    """Card-shaped view of an eval run result. The card shows the
    aggregate + up to 4 per-axis bars + the install CTA."""
    target_provider: str
    target_model: str | None = None
    # #239 — thinking level, the third leg of the identity triple (slug +
    # model + effort). Rendered next to the model id so the card honestly
    # attributes which CONFIGURATION scored, not just the model family.
    target_effort: str | None = None
    aggregate_score: float | None = None
    items_total: int = 0
    items_completed: int = 0
    by_axis: list[tuple[str, float, int]] = field(default_factory=list)
    # (axis_name, mean_score, item_count) — sorted display order is
    # alphabetical (REFRAME / COMPRESSION / REDIRECT / SHARPENING) for
    # stability across runs.
    # #246: scorer sets this when the judge degraded (e.g. TF-IDF fallback) and
    # ALSO nulls aggregate_score, "so every surface" can suppress. The card
    # checks BOTH — it shouldn't rely on the aggregate=None side-effect alone, or
    # a result written without that nulling would headline a degraded score on a
    # public share card.
    scoring_degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_provider": self.target_provider,
            "target_model": self.target_model,
            "target_effort": self.target_effort,
            "aggregate_score": self.aggregate_score,
            "items_total": self.items_total,
            "items_completed": self.items_completed,
            "by_axis": list(self.by_axis),
            "scoring_degraded": self.scoring_degraded,
        }


@dataclass
class CompareCardData:
    """Cross-provider leaderboard view. Each row is the most-recent eval
    run for one target_provider against the user's rejection signal.
    The card surfaces the ranked list (top 5 if more), the leader's
    margin over the runner-up, and the mixed-eval-set warning when
    rows aren't directly comparable.
    """
    rows: list[dict]  # [{target, model, aggregate_score, items_completed, judge, ...}]
    eval_id: str | None = None
    mixed_eval_sets: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": list(self.rows),
            "eval_id": self.eval_id,
            "mixed_eval_sets": self.mixed_eval_sets,
        }


def collect_card_data_from_result(result) -> EvalCardData:
    """Build EvalCardData from a RunResult (the dataclass loaded from
    ~/.trinity/evals/results/*.json by evals.runner.load_run_result).

    Pure-data transformation — no disk I/O, no scoring. The caller passes
    the loaded result; this just shapes it for the card renderer.
    """
    by_axis: list[tuple[str, float, int]] = []
    if result.by_rejection_type:
        for axis_name in sorted(result.by_rejection_type.keys()):
            stats = result.by_rejection_type[axis_name]
            # Shape-guard the per-axis numerics (#304) on the PUBLIC share-card
            # path: load_run_result coerces by_rejection_type to a dict but passes
            # each axis `stats` through raw, so a hand-edited / half-migrated eval
            # result can carry a non-dict `stats`, a non-numeric `mean_score`
            # ("abc" → `float(...)` ValueError), a missing `count` (KeyError), or a
            # NaN/Inf — any of which crashed `collect_card_data_from_result` →
            # `eval-share` / the MCP eval-share PNG (a journalist-screenshottable
            # surface). Skip an axis with no finite mean; coerce count to a finite
            # int. `finite_float_or_none` is the shared coercer used across every
            # numeric render-path reader.
            if not isinstance(stats, dict):
                continue
            mean = finite_float_or_none(stats.get("mean_score"))
            if mean is None:
                continue
            count = finite_float_or_none(stats.get("count"))
            by_axis.append((axis_name, mean, int(count) if count is not None else 0))

    return EvalCardData(
        target_provider=result.target_provider,
        # Coerce a wrong-type model to a None-preserving str at the read boundary
        # (#258/#304 corrupt-state vein, the iter-455 me-card sibling): `target_model`
        # flows RAW from a hand-edited eval result JSON through `load_run_result`
        # (raw.get("target_model")) into the single-target card's identity line,
        # where `_strip_unrenderable(data.target_model or "", sub)` iterates it —
        # a non-str truthy model (an int / list) → `TypeError: 'int' object is not
        # iterable`, crashing `eval-share` / the MCP eval-share PNG. None passes
        # through (the `if data.target_model` gate suppresses the identity line);
        # only a wrong-TYPE value is stringified.
        target_model=(
            result.target_model
            if result.target_model is None or isinstance(result.target_model, str)
            else str(result.target_model)
        ),
        target_effort=getattr(result, "target_effort", None),
        # Coerce a non-numeric / NaN / Inf aggregate to None at the source (#304):
        # the empty-state gate (`aggregate_score is None`) only caught None, so a
        # NaN aggregate (a corrupt/partial result — json.loads accepts bare `NaN`)
        # slipped past it and `f"{nan:.2f}"` painted "Claude scored nan" on the
        # PUBLIC, journalist-screenshottable share card. Normalizing here means
        # EVERY consumer (the gate AND the headline) sees None and the card falls
        # back to the honest "run eval-run" empty state.
        aggregate_score=finite_float_or_none(result.aggregate_score),
        items_total=result.items_total,
        items_completed=result.items_completed,
        by_axis=by_axis,
        scoring_degraded=bool(getattr(result, "scoring_degraded", False)),
    )


def _provider_display_name(provider: str, model: str | None) -> str:
    """Friendly display name for the headline.

    `provider` is the slug Trinity uses internally (claude / codex /
    antigravity); `model` is the specific version (claude-opus-4-7,
    gpt-5-5, gemini-3-1-pro-preview). The headline uses the friendly
    MODEL-brand capitalization; the subhead carries the exact model id.

    The brand is the *model* trio (Claude / GPT / Gemini), not the harness
    trio (Claude / Codex / Antigravity). The headline brand must agree
    with the model line right below it — a card that says "Antigravity
    scored 0.50" over a "Gemini 3.1 Pro" subhead contradicts itself, and
    "Gemini" is what a reader recognizes. Per the #239 model-names-in-UI
    convention. Single-sourced via council_schema.provider_model_brand so
    this card, the council card, and the launchpad eval bar can't drift.
    (The launchpad/review web ROUTING surfaces deliberately use the harness
    trio — that's a different surface, left as-is.)
    """
    from .council_schema import provider_model_brand

    # Coerce a wrong-type provider at the display boundary (#258/#304 corrupt-
    # state vein, the iter-455 me-card sibling): `target_provider` flows RAW from
    # a hand-edited / half-migrated eval result JSON through `load_run_result`
    # (raw.get("target_provider", "")) and `_collect_leaderboard_rows`
    # (normalize_provider_slug passes non-str through) — a non-str slug (an int /
    # list / dict) hits `provider_model_brand` (returns "" for non-str) then the
    # `.capitalize()` fallback → `AttributeError: 'int' object has no attribute
    # 'capitalize'`, crashing `eval-share` / `eval-share --compare` / the MCP
    # eval-share PNG (a journalist-screenshottable surface) on EVERY card path
    # (single-target + leaderboard + matrix all route the provider name through
    # this one helper). str() here is the single shared chokepoint; None
    # capitalizes to "None" but that's an honest visible fallback, not a crash.
    if not isinstance(provider, str):
        provider = str(provider)
    return provider_model_brand(provider) or provider.capitalize()


# Public landing URL — single source of truth here. Points at the
# GitHub Pages site (matches docs/CNAME), which has the full curl|sh
# install one-liner on its hero page. Doing it this way (Pages URL on
# the card, install one-liner on the Pages landing) keeps the PNG
# legible at 1200×630 — the full raw.github URL is too long for the
# card without wrapping.
#
# When the Pages URL moves, sweep this string AND update the same
# reference in launch.md / docs/CNAME.
CTA_HEADLINE = "Run this eval against your own taste:"
# CTA_LANDING_URL / FOOTER_TAGLINE imported from share_card_base.


def _axis_rows_to_show(
    n_axes: int, *, first_row_y: int, cta_block_top: int,
    row_height: int = 48, row_content_h: int = 40,
) -> tuple[int, int]:
    """How many per-axis rows fit ABOVE the CTA block; returns (n_shown, overflow).

    The canonical eval has 4 axes (which just fit), but the card renders
    "whatever the result has" and a provider-imported eval can carry custom
    /extra axes. Before this cap, 5+ rows overran the CTA + footer and clipped
    off the 630px card — garbled on a PUBLIC share card. When there's overflow
    one row slot is reserved for the "+N more axes" note so nothing collides.
    """
    if n_axes <= 0:
        return 0, 0
    fit_rows = max(1, (cta_block_top - row_content_h - first_row_y) // row_height + 1)
    if n_axes > fit_rows:
        n_shown = max(1, fit_rows - 1)  # reserve the last slot for the note
        return n_shown, n_axes - n_shown
    return n_axes, 0


def render_eval_card(data: EvalCardData) -> bytes:
    """Render the 1200×630 PNG. Returns bytes; caller writes to disk.

    Empty / missing-score states are handled by falling through to a
    minimal "run trinity-local eval-run to produce yours" message so the
    card always renders something coherent.
    """
    img, draw = blank_canvas()
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img, "RGBA")

    eyebrow = _load_font("bold", 22)
    headline = _load_font("serif", 56)
    sub = _load_font("regular", 26)
    axis_label = _load_font("bold", 20)
    axis_hint = _load_font("regular", 14)  # one-liner per axis below the label
    axis_score = _load_font("mono", 22)
    cta_label = _load_font("bold", 20)
    cta_cmd = _load_font("mono", 22)
    footer = _load_font("regular", 18)

    margin = 60
    y = margin

    # Eyebrow: "TRINITY · YOUR PERSONAL BENCHMARK"
    draw.text((margin, y), "TRINITY · YOUR PERSONAL BENCHMARK",
              font=eyebrow, fill=COLOR_ACCENT)
    y += 50

    # Empty-state fallback — no aggregate (or a degraded judge) means no
    # card-worthy data. Checking `scoring_degraded` directly (not just the
    # aggregate=None side-effect the scorer also sets) keeps a degraded score
    # off a public share card even if some path forgets to null the aggregate.
    if data.aggregate_score is None or data.scoring_degraded or not data.by_axis:
        draw.text((margin, y), "Run trinity-local eval-run",
                  font=headline, fill=COLOR_INK)
        y += 80
        draw.text((margin, y),
                  "to score any model against your kind of question.",
                  font=sub, fill=COLOR_MUTED)
    else:
        # Headline: "Claude scored 0.661"
        provider_name = _provider_display_name(data.target_provider, data.target_model)
        score_str = f"{data.aggregate_score:.2f}"  # 2 dp is the tweet-shape
        # Fit the provider name so the headline can't run off the right edge on a
        # long unbranded local-model slug — keep room for the " scored 0.74"
        # suffix (the #239 identity line below still carries the exact model id).
        suffix = f" scored {score_str}"
        suffix_w = int(draw.textbbox((0, 0), suffix, font=headline)[2])
        provider_name = _fit_one_line(
            provider_name, headline, CARD_WIDTH - 2 * margin - suffix_w, draw,
        )
        draw.text((margin, y),
                  f"{provider_name}{suffix}",
                  font=headline, fill=COLOR_INK)
        y += 76

        # #239 identity line: the exact model + thinking level that scored, so
        # the card attributes the CONFIGURATION (the same model at a different
        # effort is a different contestant), not just the provider family. Strip
        # the model id INDIVIDUALLY before joining — an all-non-Latin model name
        # stripped to "" left a dangling "· high" separator on the public card.
        # Join only the parts that survive the font.
        model_part = _strip_unrenderable(data.target_model or "", sub) if data.target_model else ""
        effort_part = data.target_effort or ""
        identity = " · ".join(b for b in (model_part, effort_part) if b)
        # Fit the joined identity to a single line within the right margin (#283
        # long-token class): target_model is user-controlled (a local-model slug
        # from `config.model` / agy settings.json, or a raw value in a hand-edited
        # / eval-imported result JSON) with no length cap, so a long SEPARATOR-FREE
        # model name (e.g. an Ollama tag with no hyphens) painted the 26px line
        # straight off the card's right edge — clipped mid-token on this PUBLIC PNG.
        # _fit_one_line hard-truncates a no-space token char-by-char (the single-
        # line sibling of share_card_base._break_long_word), so the line can never
        # run off the edge; a normal hyphenated model name fits untouched.
        identity = _fit_one_line(identity, sub, CARD_WIDTH - 2 * margin, draw)
        if identity:
            draw.text((margin, y), identity,
                      font=sub, fill=COLOR_MUTED)
            y += 40

        # Subhead: "on YOUR kind of question · 20 prompts, 4 axes"
        # The prompt count must AGREE with the bars the card is showing. A
        # persisted result can carry items_completed=0 while by_rejection_type
        # is fully populated — load_run_result defaults the field to 0 when it's
        # absent (an imported result, a self_preference synthetic run, an
        # older-schema JSON), so a real `eval-share` on such a result rendered a
        # self-contradicting "scored 0.83 · 0 prompts, 4 axes" PUBLIC card over
        # four populated bars. Floor the displayed count on the per-axis evidence
        # the card is ALREADY drawing — the bars sum to the genuinely-scored
        # items, so they're the honest count of record. (Same green-gate
        # discipline as the eval hero: a headline must not contradict the data
        # underneath it.)
        axis_count = len(data.by_axis)
        axis_item_sum = sum(c for _, _, c in data.by_axis)
        prompt_count = max(data.items_completed, axis_item_sum)
        # Pluralize against the COUNT — a 1-item / single-axis run renders this
        # PUBLIC card, and "1 prompts, 1 axes" is ungrammatical (the same n=1
        # plural-literal class fixed on the /stats captions; "axis" is the
        # singular of "axes", already used by the "+N more axis/axes" note below).
        prompt_word = "prompt" if prompt_count == 1 else "prompts"
        axis_word = "axis" if axis_count == 1 else "axes"
        subhead = (
            f"on YOUR kind of question · "
            f"{prompt_count} {prompt_word}, {axis_count} {axis_word}"
        )
        draw.text((margin, y), subhead, font=sub, fill=COLOR_MUTED)
        y += 50

        # Per-axis bars — left-anchored label, right-anchored score.
        # Bar track + fill use the sage palette; bar width scales with
        # the mean_score (0..1). 4 axes is the canonical case but we
        # render whatever the result has.
        bar_track_x = margin + 240
        bar_track_width = CARD_WIDTH - bar_track_x - margin - 80
        bar_height = 14
        row_height = 48  # bumped from 36 to make room for the axis-hint line
        label_max_w = bar_track_x - margin - 16  # label column before the bar

        # Cap rows to what fits ABOVE the CTA block. The canonical eval has 4
        # axes (which just fit), but the loop renders "whatever the result has"
        # — a provider-imported eval (eval-import) can carry custom/extra axes,
        # and 5+ rows overran the CTA + footer and clipped off the 630px card
        # (garbled, unreadable — and this is a PUBLIC share card). cta_block_top
        # mirrors the value recomputed for the CTA draw below; keep in sync.
        cta_block_top = CARD_HEIGHT - margin - 90
        n_shown, overflow = _axis_rows_to_show(
            len(data.by_axis), first_row_y=y, cta_block_top=cta_block_top,
            row_height=row_height,
        )
        shown = data.by_axis[:n_shown]

        # Lazy import — keeps eval_card.py importable without the runtime
        # scorer module on render-only paths.
        from .evals.scorer import AXIS_ONELINER

        for axis_name, mean_score, axis_n in shown:
            # A bar built on n < MIN_AXIS_SAMPLES is noise — render it with the
            # same low-n honesty treatment as the matrix card (reduced-alpha
            # fill + "(n=N)" annotation + muted ink) so a 1-prompt score doesn't
            # paint as authoritative as a 20-prompt one on this PUBLIC card.
            low_n = axis_n < MIN_AXIS_SAMPLES
            # Label (left-anchored, truncated to the label column so a long
            # axis name can't run under the bar) + small hint line below
            draw.text((margin, y), _fit_one_line(axis_name, axis_label, label_max_w, draw, placeholder=_NON_LATIN),
                      font=axis_label, fill=COLOR_MUTED)
            # Hint sits BELOW the bar in whitespace (won't overlap it) and only
            # ever comes from the fixed AXIS_ONELINER dict (custom axes get ""),
            # so it's bounded — no truncation needed, unlike the same-row label.
            hint = AXIS_ONELINER.get(axis_name, "")
            if hint:
                draw.text((margin, y + 22), hint, font=axis_hint, fill=COLOR_MUTED)

            # Bar track
            track_y_top = y + 4
            track_y_bot = track_y_top + bar_height
            draw.rounded_rectangle(
                [bar_track_x, track_y_top,
                 bar_track_x + bar_track_width, track_y_bot],
                radius=bar_height // 2,
                fill=COLOR_BAR_TRACK,
            )

            # Bar fill — width clamped to [0, 1] of the track
            fill_pct = max(0.0, min(1.0, mean_score))
            fill_width = int(bar_track_width * fill_pct)
            if fill_width > bar_height:
                draw.rounded_rectangle(
                    [bar_track_x, track_y_top,
                     bar_track_x + fill_width, track_y_bot],
                    radius=bar_height // 2,
                    fill=LOW_N_BAR_FILL if low_n else COLOR_BAR_FILL,
                )

            # Score in the gutter. A low-n score carries its sample size, but
            # the gutter is only ~64px wide — an inline "0.50 (n=1)" runs off
            # the card's right edge — so the "(n=N)" tag sits in small muted
            # text BELOW the score, right-aligned to the same column.
            score_x = bar_track_x + bar_track_width + 16
            draw.text(
                (score_x, y - 2),
                f"{mean_score:.2f}",
                font=axis_score,
                fill=LOW_N_INK if low_n else COLOR_INK,
            )
            if low_n:
                draw.text(
                    (score_x, y + 20),
                    f"n={axis_n}",
                    font=axis_hint,
                    fill=LOW_N_INK,
                )

            y += row_height

        if overflow:
            draw.text(
                (margin, y),
                f"+ {overflow} more {'axis' if overflow == 1 else 'axes'} — "
                "see trinity-local eval-show",
                font=axis_hint, fill=COLOR_MUTED,
            )
            y += row_height

        y += 8

    # ── CTA block, anchored above the footer ──────────────────────
    #
    # "Run this eval against your own taste:" on top, the GH Pages
    # landing URL below it. Two lines, sitting in the gap between the
    # bars and the bottom-right footer. The Pages page hosts the full
    # install one-liner so we don't have to fit it on the card.
    cta_block_top = CARD_HEIGHT - margin - 90
    draw.text((margin, cta_block_top), CTA_HEADLINE,
              font=cta_label, fill=COLOR_ACCENT)
    draw.text((margin, cta_block_top + 28), CTA_LANDING_URL,
              font=cta_cmd, fill=COLOR_INK)

    # ── Footer tagline, bottom-right corner ───────────────────────
    draw_wordmark(draw, font=footer, margin=margin)

    return save_png(img)


def render_compare_matrix_card(data: CompareCardData) -> bytes:
    """Per-axis × provider matrix card. The wedge artifact for the
    'best at this kind of question' claim — each provider gets a row
    with one short bar per axis, and the leader chip surfaces per axis.

    Same 1200×630 canvas as the aggregate card. Different shape: the
    aggregate card has bars-per-row sized by aggregate; this card has
    bars-per-axis sized by per-axis mean. When the per-axis spread
    between providers is large (live data: COMPRESSION codex 0.77 vs
    antigravity 0.08, a 0.7-spread), the matrix bars make it visible
    in a way the aggregate flattens.
    """
    img, _ = blank_canvas()
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img, "RGBA")

    eyebrow = _load_font("bold", 22)
    headline = _load_font("serif", 38)
    sub = _load_font("regular", 18)
    leader_chip_font = _load_font("bold", 16)
    target_font = _load_font("bold", 20)
    axis_label_font = _load_font("regular", 11)
    score_font = _load_font("mono", 14)
    warn_font = _load_font("regular", 14)
    cta_label = _load_font("bold", 20)
    cta_cmd = _load_font("mono", 22)
    footer = _load_font("regular", 18)

    margin = 60
    y = margin

    # A per-axis LEADERBOARD + "different models for different questions" wedge
    # needs at least TWO distinct providers — a per-axis "leader" with no
    # opponent is the council-card solo-overclaim shape (#35) on a PUBLICLY-
    # shared OG card. Demote-not-hide: a single-provider matrix keeps its per-
    # axis bars (each score is meaningful per se) but drops the leaderboard
    # eyebrow, the comparison headline, and the per-axis leader chips.
    solo = _distinct_target_count(data.rows) <= 1

    draw.text((margin, y),
              "TRINITY · YOUR PER-AXIS BENCHMARK" if solo
              else "TRINITY · PER-AXIS LEADERBOARD",
              font=eyebrow, fill=COLOR_ACCENT)
    y += 46

    # Collect axis set + per-axis leaders (sorted for stable order)
    axes_seen: set[str] = set()
    for row in data.rows:
        axes_seen.update((row.get("by_axis") or {}).keys())
    axes_ordered = sorted(axes_seen)

    if not data.rows or not axes_ordered:
        draw.text((margin, y), "Per-axis matrix needs ≥1 provider",
                  font=headline, fill=COLOR_INK)
        draw.text((margin, y + 60),
                  "with by_rejection_type breakdown. Re-run `trinity-local eval-run`.",
                  font=sub, fill=COLOR_MUTED)
    else:
        # Headline: the wedge. With ≥2 providers it's the cross-provider claim
        # ("Different models for different questions."); with ONE it would
        # overclaim a comparison, so name the single provider's per-axis profile.
        if solo:
            solo_name = _provider_display_name(
                data.rows[0]["target"], data.rows[0].get("model"))
            solo_name = _fit_one_line(
                solo_name, headline, CARD_WIDTH - 2 * margin
                - int(draw.textbbox((0, 0), " on YOUR kind of question",
                                    font=headline)[2]), draw)
            draw.text((margin, y),
                      f"{solo_name} on YOUR kind of question",
                      font=headline, fill=COLOR_INK)
        else:
            draw.text((margin, y), "Different models for different questions.",
                      font=headline, fill=COLOR_INK)
        y += 50

        if data.mixed_eval_sets:
            _draw_mixed_set_warning(draw, margin, y, warn_font)
            y += 22

        # Per-axis leader chips above the matrix. The tweet-line of
        # the card: "COMPRESSION → codex (0.77)  REFRAME → claude (0.81) ..."
        #
        # SUPPRESSED in three cases:
        # 1. mixed_eval_sets — leader synthesis across mismatched sets
        # 2. solo (one distinct provider) — a per-axis "leader" with no
        #    opponent is the council-card solo-overclaim shape (#35) on a
        #    public card. The bars still render; only the leader CLAIM drops.
        # 3. Any contender's by_axis_n[axis] < MIN_AXIS_SAMPLES (3) —
        #    sample too small to claim a winner. Live trigger:
        #    COMPRESSION on user's set had n=2 per provider; calling
        #    "codex wins COMPRESSION 0.77" based on 2 prompts is noise.
        # Matrix bars stay (each per-row score is meaningful per se).
        # MIN_AXIS_SAMPLES is module-level (declared at top).
        if not data.mixed_eval_sets and not solo:
            chip_x = margin
            chip_y = y
            for axis in axes_ordered:
                scored = [
                    (r["target"], r["by_axis"][axis], (r.get("by_axis_n") or {}).get(axis, 0))
                    for r in data.rows
                    if axis in (r.get("by_axis") or {})
                ]
                if not scored or any(n < MIN_AXIS_SAMPLES for _, _, n in scored):
                    continue
                # TIE DEMOTION (#35): when the top-two axis scorers round equal at
                # the 2dp the chip shows, "COMPRESSION: Claude 0.75" names a leader
                # of a TIED axis (the slug tie-break below picks a deterministic
                # but ARBITRARY name) — a false per-axis winner on the PUBLIC share
                # PNG. Suppress the chip for that axis; the matrix bars below still
                # show each provider's own per-axis score. Same gate as the CLI +
                # launchpad chip paths.
                from .evals.composition_floor import scores_tied, TIE_DP_AXIS
                _top2 = sorted((s for _, s, _ in scored), reverse=True)[:2]
                if len(_top2) >= 2 and scores_tied(_top2[0], _top2[1], dp=TIE_DP_AXIS):
                    continue
                # Slug tie-break so the per-axis leader NAME painted on the share
                # PNG is deterministic on an axis-score tie (mirrors the launchpad
                # wedge chip + eval-show/eval-share leader canon). max score,
                # lexically-smallest slug.
                leader_target, leader_score, _ = min(scored, key=lambda kv: (-kv[1], kv[0]))
                leader_name = _provider_display_name(leader_target, None)
                # No `→` — the bundled fonts lack the glyph and render
                # missing-glyph boxes in the chip. ASCII separator is safer.
                # Fit the AXIS name to the chip line width (#283 long-token class):
                # a custom axis from an eval-import is user/provider-controlled with
                # no length cap, so a long SEPARATOR-FREE axis name made one chip
                # WIDER than the whole card — the wrap-to-next-line check only resets
                # x, it can't shrink an over-wide chip, so it still painted off the
                # right edge on this PUBLIC PNG. Cap the axis to the card body width
                # (minus room for ": <leader> 0.00") so the chip always fits.
                _chip_tail = f": {leader_name} {leader_score:.2f}"
                _tail_w = int(draw.textbbox((0, 0), _chip_tail, font=leader_chip_font)[2])
                axis_label_fit = _fit_one_line(
                    axis, leader_chip_font,
                    CARD_WIDTH - 2 * margin - 16 - _tail_w, draw,
                )
                chip_text = f"{axis_label_fit}{_chip_tail}"
                bbox = draw.textbbox((0, 0), chip_text, font=leader_chip_font)
                chip_w = bbox[2] - bbox[0] + 16
                chip_h = bbox[3] - bbox[1] + 8
                if chip_x + chip_w > CARD_WIDTH - margin:
                    chip_x = margin
                    chip_y += chip_h + 8
                draw.rounded_rectangle(
                    [chip_x, chip_y, chip_x + chip_w, chip_y + chip_h],
                    radius=4,
                    fill=(45, 138, 62, 18),
                )
                draw.text((chip_x + 8, chip_y + 4), chip_text,
                          font=leader_chip_font, fill=COLOR_ACCENT)
                chip_x += chip_w + 6
            y = chip_y + 40
        else:
            # No chips → leave a smaller gap above the matrix, matching
            # the visual rhythm of the agreed-sets variant.
            y += 18

        # Matrix: target-name column + N axis-bar columns. Card width
        # gives ~280px for target column + remainder split N ways.
        target_col_width = 130
        axes_area_x = margin + target_col_width
        axes_area_w = CARD_WIDTH - margin - axes_area_x
        axis_col_w = axes_area_w // len(axes_ordered)
        bar_height = 8
        row_height = 50

        # Axis label header row. Fit to the COLUMN width with an honest
        # ellipsis (not a bare `axis[:11]` slice): a custom axis from an
        # eval-import (e.g. "COMPREHENSIVENESS") would otherwise truncate to a
        # complete-looking "COMPREHENSI" with no "…", and at high axis counts
        # the narrowed column would clip the label into its neighbour.
        for i, axis in enumerate(axes_ordered):
            col_center = axes_area_x + axis_col_w * i + axis_col_w // 2
            label = _fit_one_line(axis, axis_label_font, max(24, axis_col_w - 6), draw)
            bbox = draw.textbbox((0, 0), label, font=axis_label_font)
            lw = bbox[2] - bbox[0]
            draw.text((col_center - lw // 2, y), label,
                      font=axis_label_font, fill=COLOR_MUTED)
        y += 18

        # Rows: one per provider
        max_rows = 4
        rows_to_render = data.rows[:max_rows]
        for row in rows_to_render:
            # Target name — fit to the label column. A long local-model slug
            # (e.g. "qwen3.6:35b-a3b-coding-nvfp4", which has no brand so it
            # renders raw via _provider_display_name's capitalize() fallback)
            # otherwise overruns the 130px column straight into the first axis
            # bar + its score, making the matrix unreadable. The axis HEADER
            # labels already fit this way; the provider labels didn't.
            target_name = _fit_one_line(
                _provider_display_name(row["target"], row.get("model")),
                target_font, target_col_width - 8, draw,
            )
            draw.text((margin, y + 4), target_name,
                      font=target_font, fill=COLOR_INK)
            # Per-axis bars + scores. Low-n cells (count < MIN_AXIS_SAMPLES)
            # render with alpha-blended fill — same honesty pattern as
            # the launchpad axis-bar opacity (commit 0c20656). A bar
            # filled to 12% based on n=1 should not look as authoritative
            # as a bar filled to 93% based on n=20.
            row_axes = row.get("by_axis") or {}
            row_axes_n = row.get("by_axis_n") or {}
            bar_pad = 10  # horizontal padding inside each axis column
            # LOW_N_BAR_FILL / LOW_N_INK are module-level — shared with the
            # single-target card so the two paths de-emphasize a thin sample
            # identically (they can't drift).
            for i, axis in enumerate(axes_ordered):
                col_x = axes_area_x + axis_col_w * i + bar_pad
                col_bar_w = axis_col_w - bar_pad * 2
                track_top = y + 8
                track_bot = track_top + bar_height
                draw.rounded_rectangle(
                    [col_x, track_top, col_x + col_bar_w, track_bot],
                    radius=bar_height // 2,
                    fill=COLOR_BAR_TRACK,
                )
                if axis in row_axes:
                    val = row_axes[axis]
                    axis_n = row_axes_n.get(axis, 0)
                    low_n = axis_n < MIN_AXIS_SAMPLES
                    fill_pct = max(0.0, min(1.0, val))
                    fill_w = int(col_bar_w * fill_pct)
                    if fill_w > bar_height:
                        draw.rounded_rectangle(
                            [col_x, track_top, col_x + fill_w, track_bot],
                            radius=bar_height // 2,
                            fill=LOW_N_BAR_FILL if low_n else COLOR_BAR_FILL,
                        )
                    # Score below the bar (small mono, center-aligned in column)
                    # Low-n cells annotate with " (n=N)" so the user sees
                    # the sample size, not just a number.
                    score_text = f"{val:.2f}" + (f" (n={axis_n})" if low_n else "")
                    bbox = draw.textbbox((0, 0), score_text, font=score_font)
                    sw = bbox[2] - bbox[0]
                    draw.text(
                        (col_x + (col_bar_w - sw) // 2, track_bot + 4),
                        score_text,
                        font=score_font,
                        fill=LOW_N_INK if low_n else COLOR_INK,
                    )
                else:
                    # Missing-axis cell — small dash, center-aligned
                    bbox = draw.textbbox((0, 0), "—", font=score_font)
                    sw = bbox[2] - bbox[0]
                    draw.text(
                        (col_x + (col_bar_w - sw) // 2, track_bot + 4),
                        "—",
                        font=score_font,
                        fill=COLOR_MUTED,
                    )
            y += row_height

        if len(data.rows) > max_rows:
            draw.text(
                (margin, y + 4),
                f"+ {len(data.rows) - max_rows} more — see `eval-show --compare --by-axis`",
                font=axis_label_font, fill=COLOR_MUTED,
            )

    # CTA + footer (same as render_compare_card)
    cta_block_top = CARD_HEIGHT - margin - 90
    draw.text((margin, cta_block_top),
              "Run this benchmark against your own taste:",
              font=cta_label, fill=COLOR_ACCENT)
    draw.text((margin, cta_block_top + 28), CTA_LANDING_URL,
              font=cta_cmd, fill=COLOR_INK)

    draw_wordmark(draw, font=footer, margin=margin)

    return save_png(img)


def render_compare_card(data: CompareCardData) -> bytes:
    """Render the cross-provider leaderboard as a 1200×630 PNG.

    Each row = one target_provider's most-recent eval run against the
    user's rejection signal. The card's wedge is the COMPARISON —
    "I scored Claude, Codex, and Gemini on my taste; Claude won."

    Empty-state fallback mirrors render_eval_card so the file always
    contains something coherent; callers exit nonzero before reaching
    here when rows is empty, so this branch is defensive only.
    """
    img, _ = blank_canvas()
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img, "RGBA")

    eyebrow = _load_font("bold", 22)
    headline = _load_font("serif", 48)
    sub = _load_font("regular", 22)
    rank_font = _load_font("mono", 22)
    target_font = _load_font("bold", 24)
    score_font = _load_font("mono", 26)
    judge_font = _load_font("regular", 14)
    warn_font = _load_font("regular", 16)
    cta_label = _load_font("bold", 20)
    cta_cmd = _load_font("mono", 22)
    footer = _load_font("regular", 18)

    margin = 60
    y = margin

    # A "leads"/leaderboard verdict needs at least TWO distinct providers to
    # have a ranking. With one scored model the card is a single-provider
    # benchmark, NOT a cross-provider leaderboard — "X leads at 0.79" overclaims
    # a head-to-head with no opponent (the council-card solo shape, #35
    # green-while-degenerate, on a PUBLICLY-shared OG card). Demote-not-hide:
    # honest "scored" framing + a non-leaderboard eyebrow + a prompt to add a
    # second provider. The bar still renders (the score itself is meaningful).
    solo = _distinct_target_count(data.rows) <= 1

    eyebrow_text = ("TRINITY · YOUR PERSONAL BENCHMARK" if solo
                    else "TRINITY · CROSS-PROVIDER LEADERBOARD")
    draw.text((margin, y), eyebrow_text,
              font=eyebrow, fill=COLOR_ACCENT)
    y += 50

    if not data.rows:
        draw.text((margin, y), "Run trinity-local eval-run",
                  font=headline, fill=COLOR_INK)
        draw.text((margin, y + 70),
                  "against ≥2 providers to populate this card.",
                  font=sub, fill=COLOR_MUTED)
    else:
        # Headline: name the leader. Soften from "Claude leads at 0.79"
        # to "Claude scored 0.79" when mixed_eval_sets is True — "leads"
        # implies a fair head-to-head against the runner-up; "scored"
        # is just each-provider's-own-number, which IS meaningful
        # regardless of set agreement. The SOLO case uses the same "scored"
        # verb for the same reason: one model has no one to lead.
        leader = data.rows[0]
        leader_name = _provider_display_name(leader["target"], leader.get("model"))
        leader_agg = leader.get("aggregate_score")
        # TIE DEMOTION (#35 green-while-degenerate): when the top two providers
        # round equal at the displayed leaderboard precision, "X leads at 0.75"
        # over a "+0.000 ahead of Y" subhead names a winner of a contest that
        # ENDED TIED — a false leader on a PUBLIC, journalist-screenshottable OG
        # card. Demote to honest "scored" framing (same verb solo/mixed already
        # use, for the same "no fair head-to-head" reason). top_two_tied folds the
        # web-era slug + None-handling; mirrors the routing cheat-sheet "tied" shape.
        from .evals.composition_floor import top_two_tied
        agg_tie = top_two_tied(data.rows)
        if leader_agg is not None:
            suffix = (f" scored {leader_agg:.2f}" if (data.mixed_eval_sets or solo or agg_tie)
                      else f" leads at {leader_agg:.2f}")
        else:
            suffix = (" — your benchmark" if solo else " ranked first")
        # Fit the leader name so a long unbranded local-model slug (if the local
        # model wins) can't push the headline off the card edge — keep room for
        # the scored/leads suffix.
        suffix_w = int(draw.textbbox((0, 0), suffix, font=headline)[2])
        leader_name = _fit_one_line(
            leader_name, headline, CARD_WIDTH - 2 * margin - suffix_w, draw,
        )
        headline_text = f"{leader_name}{suffix}"
        draw.text((margin, y), headline_text, font=headline, fill=COLOR_INK)
        y += 64

        # Subhead: skip the ±margin against runner-up when mixed — that's
        # exactly the head-to-head subtraction the warning forbids. SOLO gets
        # the honest "no ranking yet" line + the actionable next step (score a
        # second provider) instead of a fabricated margin.
        if solo:
            margin_text = "on YOUR kind of question — score a 2nd provider to rank them"
        elif agg_tie and len(data.rows) >= 2 and not data.mixed_eval_sets:
            # TIE: print the honest "tied with Y" instead of a fabricated
            # "+0.000 ahead of Y" margin — a +0.000 lead IS the tie.
            runner = data.rows[1]
            margin_text = (
                "on YOUR kind of question · tied with "
                f"{_provider_display_name(runner['target'], runner.get('model'))}"
            )
        elif len(data.rows) >= 2 and not data.mixed_eval_sets:
            runner = data.rows[1]
            runner_agg = runner.get("aggregate_score")
            if leader_agg is not None and runner_agg is not None:
                margin_text = (
                    f"on YOUR kind of question · "
                    f"{leader_agg - runner_agg:+.3f} ahead of "
                    f"{_provider_display_name(runner['target'], runner.get('model'))}"
                )
            else:
                margin_text = "on YOUR kind of question"
        else:
            margin_text = "on YOUR kind of question"
        draw.text((margin, y), margin_text, font=sub, fill=COLOR_MUTED)
        y += 42

        if data.mixed_eval_sets:
            _draw_mixed_set_warning(draw, margin, y, warn_font)
            y += 24

        # Leaderboard rows: rank · target · bar · score · (judge)
        # Cap rows to what fits ABOVE the CTA block, y-aware — NOT a hard
        # max_rows=5. A bare count cap ignored how far the OPTIONAL mixed-eval-set
        # warning (+24px) had already pushed the body down: 5 rows + the warning +
        # the "+N more" note ran the note's baseline (y≈464, glyph bottom ≈482)
        # straight into the CTA headline at y=480 — two text lines colliding on a
        # PUBLICLY-shared PNG (the #283 vertical-overflow class). The aggregate
        # card already solved this with _axis_rows_to_show; the compare leaderboard
        # was the unfixed sibling. Reuse the same helper so the cap reserves a slot
        # for the "+N more" note above the CTA and can never overlap it.
        bar_x = margin + 290
        bar_width = CARD_WIDTH - bar_x - margin - 120  # leave room for score column
        bar_height = 16
        row_height = 44
        cta_block_top = CARD_HEIGHT - margin - 90
        n_shown, _overflow = _axis_rows_to_show(
            len(data.rows), first_row_y=y, cta_block_top=cta_block_top,
            row_height=row_height, row_content_h=row_height,
        )
        # Keep the established visual ceiling (the card reads as a top-5
        # leaderboard) but never EXCEED what fits above the CTA.
        max_rows = min(5, n_shown)
        rows_to_render = data.rows[:max_rows]

        for i, row in enumerate(rows_to_render, 1):
            # Rank
            draw.text((margin, y + 8), f"{i}.", font=rank_font, fill=COLOR_MUTED)
            # Target name (display-friendly), fit to the label column so a long
            # local-model slug (no brand → raw) doesn't overrun into the bar that
            # starts at bar_x.
            target_name = _fit_one_line(
                _provider_display_name(row["target"], row.get("model")),
                target_font, bar_x - (margin + 36) - 12, draw,
            )
            draw.text((margin + 36, y + 4), target_name,
                      font=target_font, fill=COLOR_INK)

            # Judge attribution under the target name — small + muted. Fit to the
            # card body width (#283 long-token class): `judge` is a provider slug
            # from a result's items (normally claude/codex) but is raw in a hand-
            # edited / eval-imported result, so a long SEPARATOR-FREE judge slug
            # painted the line off the card's right edge on this PUBLIC PNG. A
            # normal short slug fits untouched.
            judge = row.get("judge")
            if judge:
                # SELF-JUDGE DISCLOSURE: when the judge slug == the target slug,
                # the model graded its OWN family's output (reachable via
                # `eval-run --judge claude` on a claude target). The single
                # eval-run terminal ALREADY discloses this ("self-judge — same
                # family as target") because, in its own words, it "can still
                # look like a conflict of interest externally" — and THIS card
                # is the most external surface there is (a public OG image built
                # to be posted to Twitter/LinkedIn). Dropping the disclosure here
                # rendered "Claude leads at 0.88 · judge: Claude" as an
                # unflagged self-graded win — the #35 green-while-degenerate
                # shape on the most-public artifact. Use the SAME self-judge
                # definition the scorer uses (normalize_provider_slug equality).
                from .council_schema import normalize_provider_slug
                self_judged = (
                    normalize_provider_slug(judge)
                    == normalize_provider_slug(row.get("target"))
                )
                judge_disp = _provider_display_name(judge, None)
                judge_text = (
                    f"judge: {judge_disp} (self)" if self_judged
                    else f"judge: {judge_disp}"
                )
                judge_line = _fit_one_line(
                    judge_text,
                    judge_font, CARD_WIDTH - (margin + 36) - margin, draw,
                )
                draw.text((margin + 36, y + 28), judge_line,
                          font=judge_font, fill=COLOR_MUTED)

            # Bar
            agg = row.get("aggregate_score")
            track_top = y + 12
            track_bot = track_top + bar_height
            draw.rounded_rectangle(
                [bar_x, track_top, bar_x + bar_width, track_bot],
                radius=bar_height // 2,
                fill=COLOR_BAR_TRACK,
            )
            if agg is not None:
                fill_pct = max(0.0, min(1.0, agg))
                fill_width = int(bar_width * fill_pct)
                if fill_width > bar_height:
                    draw.rounded_rectangle(
                        [bar_x, track_top, bar_x + fill_width, track_bot],
                        radius=bar_height // 2,
                        fill=COLOR_BAR_FILL,
                    )

            # Score (right-anchored)
            score_str = f"{agg:.3f}" if agg is not None else "—"
            draw.text(
                (bar_x + bar_width + 18, y + 6),
                score_str,
                font=score_font,
                fill=COLOR_INK,
            )

            y += row_height

        # If we truncated, surface that the leaderboard has more.
        if len(data.rows) > max_rows:
            draw.text(
                (margin, y + 4),
                f"+ {len(data.rows) - max_rows} more — see `eval-show --compare`",
                font=judge_font, fill=COLOR_MUTED,
            )

    # CTA + footer (same convention as render_eval_card)
    cta_block_top = CARD_HEIGHT - margin - 90
    draw.text((margin, cta_block_top),
              "Run this benchmark against your own taste:",
              font=cta_label, fill=COLOR_ACCENT)
    draw.text((margin, cta_block_top + 28), CTA_LANDING_URL,
              font=cta_cmd, fill=COLOR_INK)

    draw_wordmark(draw, font=footer, margin=margin)

    return save_png(img)
