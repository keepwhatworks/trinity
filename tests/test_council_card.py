"""Council share-card rendering. The cards are 1200×630 PNGs shared PUBLICLY
(council-share), so a truncated claim must end with a clean ellipsis, never a
hard mid-sentence cut (a dangling ``word,`` reads as broken). Found by visually
inspecting rendered cards 2026-06-01 — no unit test checks pixels, so the
truncation path was unguarded.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from trinity_local.council_card import (
    CouncilCardData,
    COLOR_DISAGREE,
    _clip_lines,
    collect_card_data_from_outcome,
    render_council_card,
)
from trinity_local.share_card_base import COLOR_ACCENT, COLOR_BG, COLOR_MUTED, CARD_WIDTH


def _draw():
    img = Image.new("RGB", (10, 10))
    return ImageDraw.Draw(img), ImageFont.load_default()


def _teal_pixels_in_content_band(png_bytes: bytes, *, tol: int = 24) -> int:
    """Count COLOR_ACCENT (teal) pixels in the headline/section band
    (y 150..360). On a real (>=2 member) council card this band carries the
    teal "<winner> won." headline line + the teal "AGREED" section label.
    On a degenerate 1-member council there is no contest and no consensus, so
    BOTH must be suppressed → ~0 teal ink in the band. The eyebrow (top) and
    CTA/footer (bottom) teal sit OUTSIDE this band, so they don't pollute the
    count. This is the pixel-level invariant the #35 fix attests."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.load()
    assert px is not None
    w, h = img.size
    ar, ag, ab = COLOR_ACCENT
    n = 0
    for yy in range(150, min(360, h)):
        for xx in range(0, w):
            pixel = px[xx, yy]
            if not isinstance(pixel, tuple):
                continue
            r, g, b = pixel[0], pixel[1], pixel[2]
            if abs(r - ar) <= tol and abs(g - ag) <= tol and abs(b - ab) <= tol:
                n += 1
    return n


def test_clip_lines_passthrough_when_fits():
    draw, font = _draw()
    # Block shorter than the cap → returned verbatim, no ellipsis.
    assert _clip_lines(["a", "b", "c"], 5, font, 10_000, draw) == ["a", "b", "c"]
    assert _clip_lines(["only"], 1, font, 10_000, draw) == ["only"]


def test_clip_lines_ellipsizes_and_strips_dangling_separator():
    draw, font = _draw()
    # 3 lines into a 2-line cap → truncated. Last shown line ("beta,") must
    # lose its dangling comma and gain an ellipsis: "beta…", not "beta,".
    out = _clip_lines(["alpha", "beta,", "gamma"], 2, font, 10_000, draw)
    assert len(out) == 2
    assert out[-1] == "beta…", out
    assert "," not in out[-1], "dangling separator must be stripped before the ellipsis"
    # em-dash / semicolon / colon are stripped too.
    assert _clip_lines(["x", "y —", "z"], 2, font, 10_000, draw)[-1] == "y…"


def test_clip_lines_zero_cap_is_empty():
    draw, font = _draw()
    assert _clip_lines(["a", "b"], 0, font, 10_000, draw) == []


def test_render_council_card_long_disagreed_no_crash_and_is_png():
    # A council with a verbose disagreement (the chairman writes prose) must
    # still render to a valid PNG — the truncation path can't crash.
    data = CouncilCardData(
        members=["claude", "codex", "antigravity"],
        winner="claude",
        agreed_claims=["short one", "short two"],
        disagreed_claim=(
            "An extremely long disagreement statement that must wrap across "
            "several lines and stress the disagreed-section layout, testing "
            "whether the clip-with-ellipsis path engages cleanly at body_end."
        ),
        disagreed_why="A long why-matters tail that also wraps and pushes past the reserve.",
    )
    png = render_council_card(data)
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n", "must emit a PNG"
    assert len(png) > 1000


# ── #35 green-while-degenerate: a 1-member council is NOT a contest ─────────
# A council needs >=2 voices to have a winner or a consensus. When only one
# provider ran, the share card must NOT fabricate "<model> won." or an "AGREED"
# block — that's a confident verdict the data can't support, painted onto a
# PUBLICLY-shared PNG. Found 2026-06-17 by rendering the 1-member card and
# reading the pixels: it claimed "Trinity asked Claude. Claude won." + AGREED.


def test_single_member_council_card_suppresses_won_and_agreed_framing():
    """The DEGENERATE case: only one model answered. The card must drop the
    teal "<winner> won." headline AND the teal "AGREED" consensus block — both
    are multi-voice claims a council of one can't make. Asserted at the PIXEL
    level (teal ink in the content band), so it bites the rendered output, not
    a string in source."""
    solo = CouncilCardData(
        members=["claude"],
        winner="claude",
        agreed_claims=[
            "Use a connection pool to bound database concurrency",
            "Add an index on the user_id column",
        ],
        disagreed_claim=None,
    )
    png = render_council_card(solo)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
    teal = _teal_pixels_in_content_band(png)
    assert teal < 50, (
        "REGRESSION: a 1-member council card painted the competition framing — "
        f"{teal} teal pixels in the headline/AGREED band means the '<model> won.' "
        "line or the 'AGREED' consensus label rendered. One model can't win a "
        "council or agree with itself; the card must NOT overclaim a verdict on a "
        "publicly-shared PNG (#35 green-while-degenerate)."
    )


def test_multi_member_council_card_keeps_won_and_agreed_framing():
    """The COMPLEMENT (so the fix isn't over-broad and didn't kill the real
    card): a genuine 2-member council DOES have a winner + consensus, so the
    teal 'won.'/'AGREED' framing MUST still render. 2-member is the 79% common
    case — suppressing it here would gut the product's flagship share artifact."""
    two = CouncilCardData(
        members=["claude", "codex"],
        winner="codex",
        agreed_claims=["Cache the embedding model load across calls"],
        disagreed_claim="Whether to vendor the dependency",
        disagreed_why="vendoring trades update friction for offline reproducibility",
    )
    teal = _teal_pixels_in_content_band(render_council_card(two))
    assert teal > 500, (
        "REGRESSION: a real 2-member council card LOST its teal 'won.'/'AGREED' "
        f"framing ({teal} teal pixels in the content band) — the solo-suppression "
        "fix over-reached and gutted the multi-member card."
    )


def test_all_same_provider_council_card_suppresses_contest_framing():
    """The DEGENERATE same-provider case (Iter 111): every member is the SAME
    provider — members=["claude","claude","claude"]. There are 3 responders but
    only ONE distinct voice, so the chairman's winner/runner_up/agreed all key
    on that single slug and the card would paint "Trinity asked Claude · Claude
    · Claude. Claude won." + an "AGREED" block — a fabricated contest between
    identical voices on a PUBLICLY-shared PNG (the same #35 overclaim the
    1-responder solo branch suppresses; the same-provider roster was its unfixed
    sibling). The solo gate must count DISTINCT provider slugs, not raw members,
    so this collapses to the honest "One model — no council." framing → ~0 teal
    ink in the headline/AGREED band. Asserted at the PIXEL level so it bites the
    rendered PNG, not a string in source."""
    same = CouncilCardData(
        members=["claude", "claude", "claude"],
        winner="claude",
        agreed_claims=[
            "Use a hash map for O(1) lookups",
            "Validate input at the boundary",
        ],
        disagreed_claim="Whether to cache the result",
        disagreed_why="Cache invalidation adds complexity",
    )
    png = render_council_card(same)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
    teal = _teal_pixels_in_content_band(png)
    assert teal < 50, (
        "REGRESSION: an all-same-provider council card painted the competition "
        f"framing — {teal} teal pixels in the headline/AGREED band means "
        "'Claude · Claude · Claude. Claude won.' + 'AGREED' rendered. Three "
        "identical voices are not a contest (the winner is its own runner-up); "
        "the card must collapse to 'One model — no council.' and NOT overclaim a "
        "verdict on a publicly-shared PNG (#35 green-while-degenerate, Iter 111)."
    )


def _body_band_ink(png_bytes: bytes, *, y0: int = 240, y1: int = 460, tol: int = 30) -> int:
    """Count NON-BACKGROUND pixels in the card's BODY band (y 240..460) — the
    region between the headline ('<winner> won.') and the CTA block. On a card
    that carries claims this band holds the AGREED/DISAGREED text; on the solo
    card it holds the 'Only one model answered…' fallback. The defect this
    guards: a multi-member council with a winner but EMPTY agreed/disagreed
    claims (a schema-valid, reachable outcome — see council_schema.to_dict)
    painted this whole band BLANK, shipping a half-empty 1200×630 PNG on the
    public share surface."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.load()
    assert px is not None
    w, h = img.size
    br, bg, bb = COLOR_BG
    n = 0
    for yy in range(y0, min(y1, h)):
        for xx in range(0, w):
            pixel = px[xx, yy]
            if not isinstance(pixel, tuple):
                continue
            r, g, b = pixel[0], pixel[1], pixel[2]
            if abs(r - br) > tol or abs(g - bg) > tol or abs(b - bb) > tol:
                n += 1
    return n


def test_council_card_with_winner_but_no_claims_does_not_paint_a_blank_body():
    """A REAL, schema-valid council outcome: a multi-member council that produced
    a winner but where the chairman recorded NO structured claims (empty
    agreed_claims AND disagreed_claims — council_schema.to_dict explicitly emits
    both even when empty, calling 'the members reached no recorded consensus' a
    state distinct from solo). Before the fix the card rendered '<winner> won.'
    and then ~290px of PURE VOID down to the CTA — a half-empty, broken-looking
    PNG on the PUBLIC share surface (no-claims share-card void). The fix paints
    an honest fallback body line ('… the chairman logged no shared claims — open
    the full council …'). Asserted at the PIXEL level on the body band so it
    bites the rendered PNG, not a string in source."""
    no_claims = CouncilCardData(
        members=["claude", "codex", "antigravity"],
        winner="claude",
        agreed_claims=[],
        disagreed_claim=None,
        disagreed_why=None,
    )
    png = render_council_card(no_claims)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
    ink = _body_band_ink(png)
    assert ink > 800, (
        "REGRESSION: a multi-member council card with a winner but NO recorded "
        f"claims painted a BLANK body band ({ink} non-bg pixels in y 240..460) — "
        "the no-claims share-card void is back: '<winner> won.' over ~290px of "
        "nothing on a publicly-shared 1200×630 PNG. The card must render the "
        "honest no-claims fallback body so the artifact reads as complete."
    )


def test_all_failed_council_card_does_not_claim_one_model_answered():
    """DEGENERATE all-failed council (Iter 269): a hand-edited / legacy / imported
    outcome with member_results=[] but failed_count>0 — ZERO models answered (the
    runner raises before persisting, but the share card renders whatever lands on
    disk; #258 hand-editable-state class). Before the fix the card painted the SOLO
    body 'Only one model answered, so there's no winner …' — a flat LIE (one model
    answered when zero did) that ALSO contradicted the disclosure line one row up
    ('… over the 0 that answered'). The live page already distinguishes this; the
    share card was the unfixed sibling that folded n=0 into the n=1 solo branch.

    Asserted on the DRAWN TEXT (the ImageDraw.text monkeypatch — the same 'read the
    pixels' technique the brand test uses), not a source-string presence check.
    Mutation-proven RED on the un-fixed source: with `all_failed` removed, the
    n=0 case re-enters the solo branch and 'Only one model answered' re-appears."""
    all_failed = CouncilCardData(
        members=[],            # ZERO responders
        winner="claude",       # a stale winner the chairman emitted pre-failure
        agreed_claims=[],
        disagreed_claim=None,
        failed_count=3,        # all three providers attempted and failed
    )
    drawn = _drawn_text(lambda: render_council_card(all_failed))
    # Bite-precondition (A): the card actually painted (the eyebrow is always drawn).
    assert "TRINITY · YOUR COUNCIL" in drawn, "card did not render — test is vacuous"
    # Bite-precondition (B): the fixture is genuinely the all-failed shape — no
    # responders AND a recorded failure count (checked render-independently).
    assert all_failed.members == [] and all_failed.failed_count == 3
    # The LIE: "Only one model answered" must NOT appear when zero answered.
    assert "Only one model answered" not in drawn, (
        "FOUNDER SYMPTOM (Iter 269): the all-failed council card (0 responders, "
        "3 failed) painted the SOLO body 'Only one model answered, so there's no "
        "winner …' on a PUBLICLY-shared PNG — a flat lie (zero models answered) "
        "that also contradicts the disclosure line 'over the 0 that answered' one "
        "row up. The n=0 all-failed state must NOT reuse the n=1 solo copy."
    )
    # The nonsensical disclosure must NOT appear ("over the 0 that answered").
    assert "over the 0 that answered" not in drawn, (
        "the partial-council disclosure 'this is over the 0 that answered' is "
        "nonsensical for a 0-responder council — it implies a synthesis exists "
        "over nobody."
    )
    # And the honest all-failed copy MUST be present (the card still ships complete).
    assert "No model responded." in drawn and "no synthesis" in drawn.lower(), (
        "the all-failed card must state the total failure honestly ('No model "
        "responded.' + a 'no synthesis' note), mirroring the live page's "
        "'Every provider attempted but failed to respond — there's no synthesis "
        "to show.'"
    )


def test_genuine_solo_council_card_still_claims_one_model_answered():
    """POSITIVE CONTROL for Iter 269 (so the all-failed fix isn't over-broad): a
    GENUINE solo council (exactly ONE responder, no failures) MUST still paint the
    honest solo body 'Only one model answered …'. One real model DID answer here —
    suppressing the solo framing would break the legitimate 1-provider case."""
    solo = CouncilCardData(
        members=["claude"],    # ONE genuine responder
        winner="claude",
        agreed_claims=[],
        disagreed_claim=None,
        failed_count=0,        # nothing failed — a real single-provider council
    )
    drawn = _drawn_text(lambda: render_council_card(solo))
    assert "TRINITY · YOUR COUNCIL" in drawn, "card did not render — test is vacuous"
    assert solo.members == ["claude"] and solo.failed_count == 0
    assert "Only one model answered" in drawn, (
        "REGRESSION: the genuine 1-responder solo council LOST its honest solo "
        "body — the Iter 269 all-failed fix over-reached and gutted the real "
        "single-provider case."
    )
    assert "No model responded." not in drawn, (
        "the genuine solo card must NOT paint the all-failed copy — one model "
        "DID answer."
    )


# ── #238 honest-degradation: a PARTIAL council (a member attempted but failed) ──
# The runner records casualties in metadata.failed_members but EXCLUDES them from
# member_results, so the card lists only the responders by brand. Without a
# disclosure, a 2-of-3 council renders IDENTICALLY to a deliberate 2-model one —
# the headline "Trinity asked Claude · GPT." silently omits the 3rd Trinity
# actually asked. The live page + the persistent ?council_id= review page already
# disclose this; the publicly-shared share card was the unfixed sibling. The
# disclosure mark is a warm-brown (COLOR_DISAGREE) caution line rendered in a band
# just below the headline (y 240..270) — well above the "DISAGREED" label (y 375+),
# so the band isolates the disclosure ink. Asserted at the PIXEL level, so it bites
# the rendered PNG, not a string in source.


def _band_pixels(png_bytes: bytes, target, *, tol: int = 30, y0: int = 240, y1: int = 270) -> int:
    """Count pixels matching ``target`` colour in the partial-council DISCLOSURE
    band (y 240..270) — the row where the '⚠ N model didn't respond …' caution
    line renders. The 'DISAGREED — WHY IT MATTERS' label sits far lower (y 375+),
    so it can't pollute this band."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.load()
    assert px is not None
    w, h = img.size
    ar, ag, ab = target
    n = 0
    for yy in range(y0, min(y1, h)):
        for xx in range(0, w):
            pixel = px[xx, yy]
            if not isinstance(pixel, tuple):
                continue
            r, g, b = pixel[0], pixel[1], pixel[2]
            if abs(r - ar) <= tol and abs(g - ag) <= tol and abs(b - ab) <= tol:
                n += 1
    return n


def _disclosure_brown_pixels(png_bytes: bytes, *, tol: int = 30) -> int:
    """COLOR_DISAGREE (warm-brown) pixels in the disclosure band — after the
    contrast fix this is ONLY the small triangle caution icon; the disclosure
    SENTENCE itself paints in COLOR_MUTED (see _disclosure_text_pixels)."""
    return _band_pixels(png_bytes, COLOR_DISAGREE, tol=tol)


def _disclosure_text_pixels(png_bytes: bytes, *, tol: int = 30) -> int:
    """COLOR_MUTED pixels in the disclosure band — the '⚠ N model didn't respond …'
    SENTENCE, repainted from the sub-AA warm-brown (3.3:1) to COLOR_MUTED (5.1:1)
    so the public share card's disclosure clears the WCAG AA body-text floor."""
    return _band_pixels(png_bytes, COLOR_MUTED, tol=tol)


def test_partial_council_card_discloses_failed_member():
    """A 2-of-3 council (a 3rd provider attempted but failed) MUST paint the
    honest '⚠ N model didn't respond — this is over the M that answered.'
    disclosure below the headline. Asserted at the PIXEL level (brown ink in the
    disclosure band)."""
    partial = CouncilCardData(
        members=["claude", "codex"],
        winner="codex",
        agreed_claims=["Use a bounded connection pool", "Add an index on user_id"],
        disagreed_claim="Whether to vendor the dependency",
        disagreed_why="vendoring trades update friction for offline reproducibility",
        failed_count=1,
    )
    png = render_council_card(partial)
    icon = _disclosure_brown_pixels(png)
    text = _disclosure_text_pixels(png)
    assert icon > 20 and text > 400, (
        "REGRESSION: a 2-of-3 council card (a provider attempted but FAILED) did "
        f"NOT paint the honest 'N model didn't respond' disclosure (icon={icon} "
        f"warm-brown px, text={text} muted px in the disclosure band). The headline "
        "'Trinity asked Claude · GPT.' silently omits the 3rd Trinity actually asked "
        "— the share card overclaims a clean 2-model contest on a PUBLICLY-shared "
        "PNG (#238 honest-degradation)."
    )


def test_clean_council_card_has_no_failed_member_disclosure():
    """NEGATIVE CONTROL (so the disclosure isn't always-on): a council where every
    member responded (failed_count=0) MUST NOT paint the disclosure band — there
    was no casualty to disclose. Identical card minus the failure."""
    clean = CouncilCardData(
        members=["claude", "codex"],
        winner="codex",
        agreed_claims=["Use a bounded connection pool", "Add an index on user_id"],
        disagreed_claim="Whether to vendor the dependency",
        disagreed_why="vendoring trades update friction for offline reproducibility",
        failed_count=0,
    )
    png = render_council_card(clean)
    icon = _disclosure_brown_pixels(png)
    text = _disclosure_text_pixels(png)
    assert icon < 20 and text < 20, (
        "REGRESSION: a CLEAN council card (no failed members) painted the "
        f"failed-member disclosure band (icon={icon} brown px, text={text} muted "
        "px) — the ⚠ note must fire ONLY on a real casualty, not always-on."
    )


def _wcag_ratio(fg, bg) -> float:
    """WCAG 2.x contrast ratio between two opaque sRGB colours."""
    def _lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def _lum(rgb) -> float:
        r, g, b = rgb
        return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)

    la, lb = _lum(fg), _lum(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_partial_council_disclosure_text_clears_wcag_aa_body_contrast():
    """The '⚠ N model didn't respond — this is over the M that answered.' line is
    regular-weight 18px (13.5pt) BODY text — NOT a large/bold section label — so it
    needs the 4.5:1 WCAG AA body floor on this PUBLICLY-shared 1200×630 PNG. It used
    to paint in COLOR_DISAGREE (warm-brown), only 3.30:1 over the cool-mist BG, the
    LEAST-readable text on the card. The fix repaints the SENTENCE in COLOR_MUTED
    (5.1:1) while the small triangle caution icon stays warm-brown.

    Read from the real rendered pixels: find the strongest (darkest) text-glyph
    pixel in the disclosure band and measure its contrast against the card BG. A
    string/const check would MISS a future revert that swaps the fill back — this
    bites on the PAINTED colour."""
    partial = CouncilCardData(
        members=["claude", "codex"],
        winner="codex",
        agreed_claims=["Use a bounded connection pool", "Add an index on user_id"],
        disagreed_claim="Whether to vendor the dependency",
        disagreed_why="vendoring trades update friction for offline reproducibility",
        failed_count=1,
    )
    img = Image.open(io.BytesIO(render_council_card(partial))).convert("RGB")
    px = img.load()
    assert px is not None
    w, _h = img.size

    # PRECONDITION (non-vacuous): the disclosure SENTENCE actually painted. The
    # text starts ~x=84 (apex_x + 16, past the triangle icon at margin 60) — so
    # restrict the sample to x>=120 to exclude the warm-brown icon entirely and
    # measure ONLY the sentence glyphs.
    text_band = []
    for yy in range(240, 270):
        for xx in range(120, w):
            r, g, b = px[xx, yy]
            # Any pixel meaningfully darker than the cool-mist BG (~235) is text.
            if (r + g + b) < (210 * 3):
                text_band.append((r, g, b))
    assert len(text_band) > 300, (
        "PRECONDITION FAILED: the failed-member disclosure SENTENCE did not paint "
        f"in the band ({len(text_band)} text pixels) — the contrast assertion would "
        "be vacuous. Check the partial-council fixture / disclosure layout."
    )

    # The glyph CORE = the darkest pixels (least BG-blended). Take the darkest 5%
    # and average them to get the rendered ink colour, robust to anti-aliasing.
    text_band.sort(key=lambda p: p[0] + p[1] + p[2])
    core = text_band[: max(1, len(text_band) // 20)]
    avg = tuple(round(sum(c[i] for c in core) / len(core)) for i in range(3))

    ratio = _wcag_ratio(avg, COLOR_BG)
    assert ratio >= 4.5, (
        "REGRESSION: the council-share card's failed-member disclosure sentence "
        f"('N model didn't respond …') paints at {ratio:.2f}:1 (rendered ink "
        f"~{avg}) over the card BG — BELOW the 4.5:1 WCAG AA body floor. A public "
        "share card's honest-degradation disclosure became the least-readable text "
        "on the PNG (the warm-brown COLOR_DISAGREE at 3.30:1). Paint the SENTENCE "
        "in COLOR_MUTED (5.1:1), like the eval card's _draw_mixed_set_warning."
    )


def test_collect_card_data_carries_failed_count_from_outcome():
    """The data path: collect_card_data_from_outcome must read the runner's
    metadata.failed_members into failed_count, so the renderer can disclose it.
    A non-list (corrupt) metadata coerces to 0, not a crash."""
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    outcome = CouncilOutcome(
        council_run_id="council_x",
        bundle_id="b",
        task_cluster_id="t",
        primary_provider="claude",
        winner_provider="codex",
        member_results=[
            CouncilMemberResult(provider="claude", model="m", session_id=None, output_text="a"),
            CouncilMemberResult(provider="codex", model="m", session_id=None, output_text="b"),
        ],
        routing_label=CouncilRoutingLabel(task_type="x", winner="codex"),
        metadata={"failed_members": ["antigravity"]},
    )
    assert collect_card_data_from_outcome(outcome).failed_count == 1
    # No metadata / no failures → 0.
    outcome.metadata = {}
    assert collect_card_data_from_outcome(outcome).failed_count == 0
    # Corrupt non-list failed_members → coerced to 0 (no crash).
    outcome.metadata = {"failed_members": "antigravity"}
    assert collect_card_data_from_outcome(outcome).failed_count == 0


# ── #283-class headline overflow: a long provider BRAND must not run off the card ──
# The eval card already fits its headline provider name with _fit_one_line so a long
# unbranded local-model slug ("Some-Long-Local-Model-Slug" — an unknown slug title-
# cases to its raw name) can't run the headline off the right edge. The council card
# headline (the roster line "Trinity asked X · Y · Z." AND the "<winner> won." line)
# was the UNFIXED sibling of that class: a long brand painted the 48px serif headline
# straight off the right margin, clipped at the card boundary on this PUBLICLY-shared
# PNG. Found 2026-06-18 by rendering a long-winner card and reading the rightmost ink:
# the roster line reached x=1199 (the card edge) on a 1140 right margin.

MARGIN = 60  # the card's body margin (council_card.render_council_card)
RIGHT_MARGIN = CARD_WIDTH - MARGIN  # 1140


def _rightmost_ink_x_in_band(png_bytes: bytes, y0: int, y1: int, *, tol: int = 12) -> int:
    """The largest x with non-background ink anywhere in the band [y0, y1). Used to
    detect headline text that overruns the right margin (ink past RIGHT_MARGIN)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.load()
    assert px is not None
    w, h = img.size
    br, bg, bb = COLOR_BG
    max_x = -1
    for yy in range(y0, min(y1, h)):
        for xx in range(w - 1, -1, -1):
            r, g, b = px[xx, yy][:3]
            if not (abs(r - br) <= tol and abs(g - bg) <= tol and abs(b - bb) <= tol):
                if xx > max_x:
                    max_x = xx
                break  # only need the rightmost ink per row
    return max_x


def test_council_card_long_winner_brand_headline_does_not_overflow_right_margin():
    """A long provider BRAND (an unbranded/local-model slug title-cases to its raw
    long name) must NOT run the headline off the right edge of the 1200×630 card.
    Both headline lines — the roster ('Trinity asked …') and the '<winner> won.'
    line — must be ellipsis-fit within the right margin. Asserted at the PIXEL level
    (rightmost ink in the headline band y 96..236), so it bites the rendered PNG,
    not a string in source."""
    long_slug = "some-extremely-long-local-model-provider-slug-name"
    data = CouncilCardData(
        members=["claude", "codex", long_slug],
        winner=long_slug,  # title-cases to a long unbranded name
        agreed_claims=["short claim"],
        disagreed_claim="x",
        disagreed_why="y",
    )
    png = render_council_card(data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
    rightmost = _rightmost_ink_x_in_band(png, 96, 236)
    # A clipped overflow paints ink all the way to (or past) the card edge (1200);
    # a properly fit line stops at/under the 1140 right margin. Allow a 2px AA fringe.
    assert 0 < rightmost <= RIGHT_MARGIN + 2, (
        "REGRESSION: the council share-card headline ran off the right margin on a "
        f"long provider brand — rightmost headline ink x={rightmost} exceeds the "
        f"{RIGHT_MARGIN}px right margin (card edge is {CARD_WIDTH}). A long unbranded "
        "local-model slug title-cases to a long name and the 48px serif headline "
        "(roster line or '<winner> won.') is painted off the edge, clipped on this "
        "PUBLICLY-shared PNG. Fit each headline line with _fit_one_line (the #283 "
        "class the eval card already fixed; the council card was the unfixed sibling)."
    )


def test_council_card_normal_brands_headline_unchanged_by_fit():
    """COMPLEMENT (so the fit isn't over-broad): a normal 3-known-brand council
    ('Trinity asked Claude · GPT · Gemini.') is already well within the margin, so
    the fit must NOT touch it — the roster line still renders in full (no spurious
    ellipsis truncating a short, fitting headline)."""
    data = CouncilCardData(
        members=["claude", "codex", "antigravity"],
        winner="claude",
        agreed_claims=["short"],
        disagreed_claim="x",
        disagreed_why="y",
    )
    rightmost = _rightmost_ink_x_in_band(render_council_card(data), 96, 236)
    # The full "Trinity asked Claude · GPT · Gemini." headline reaches ~833px — well
    # under the margin and far enough right that an over-aggressive truncation would
    # measurably pull it left. A floor of 700 catches a fit that wrongly clipped it.
    assert 700 < rightmost <= RIGHT_MARGIN + 2, (
        "REGRESSION: the headline-fit truncated a NORMAL short-brand council roster "
        f"('Trinity asked Claude · GPT · Gemini.') — rightmost ink x={rightmost} is "
        "below the expected ~833px, meaning the fit clipped a headline that already "
        "fit. The fit must only engage on genuine overflow."
    )


# ── long-unbreakable-token CLAIMS-BODY overflow: a separator-free token in a
# chairman claim / why_matters must not run the body text off the card edge ──
# The headline-overflow class above fits the headline lines with _fit_one_line.
# The CLAIMS body (AGREED bullets + the DISAGREED "claim — why_matters" block) is
# word-wrapped with _wrap, which split ONLY on whitespace — so a single
# separator-free token wider than the body width (a chairman claim carrying a long
# URL / file path `src/trinity_local/...py` / regex / base64 hash, all routine in
# synthesis prose) became one line that ran off the right margin to the very card
# edge (x=1199 on a 1140 margin), clipped on this PUBLICLY-shared PNG. _clip_lines
# masked it ONLY when the over-wide line happened to be the ellipsized last shown
# line; a DISAGREED block whose over-wide token lands on a NON-last shown line was
# unmasked. Found 2026-06-22 by rendering a disagreed claim with a 150-char
# unbreakable URL and reading the rightmost body ink. Fixed at source: _wrap (and
# the shared wrap_text) hard-break an over-wide token char-by-char via
# _break_long_word, the PNG analog of overflow-wrap:anywhere the council pages use.


def test_council_card_long_unbreakable_token_in_claim_does_not_overflow_body_margin():
    """A DISAGREED claim carrying a long SEPARATOR-FREE token (a 150-char URL — the
    kind a chairman writes inline) must NOT paint body text off the right edge of
    the 1200×630 card. The token is placed so it is NOT the ellipsized last shown
    line (where _clip_lines would have incidentally trimmed it), isolating the
    _wrap word-break. Asserted at the PIXEL level (rightmost ink in the claims-body
    band y 240..470), so it bites the rendered PNG, not a string in source."""
    overwide = "https://example.com/" + ("a" * 130)  # ~1600px, well past the ~1080px body
    data = CouncilCardData(
        members=["claude", "codex", "antigravity"],
        winner="claude",
        agreed_claims=[],  # push the disagreed block up; over-wide token = a non-last line
        disagreed_claim="They split on " + overwide + " and more",
        disagreed_why="tail",
    )
    png = render_council_card(data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "must still emit a valid PNG"
    rightmost = _rightmost_ink_x_in_band(png, 240, 470)
    # A clipped overflow paints body ink to/past the card edge (1200); a hard-broken
    # token wraps and stays at/under the 1140 right margin. Allow a 2px AA fringe.
    assert 0 < rightmost <= RIGHT_MARGIN + 2, (
        "REGRESSION: a long unbreakable token in a chairman claim ran the council "
        f"share-card body text off the right margin — rightmost claims-body ink "
        f"x={rightmost} exceeds the {RIGHT_MARGIN}px right margin (card edge is "
        f"{CARD_WIDTH}). A separator-free token (a URL / file path / hash, routine "
        "in synthesis prose) word-wrapped to a single over-wide line and was painted "
        "off the edge, clipped on this PUBLICLY-shared PNG. Hard-break the token in "
        "_wrap / wrap_text via _break_long_word (the PNG analog of overflow-wrap:"
        "anywhere the live + static council pages already apply)."
    )


def test_council_card_normal_claim_prose_unchanged_by_token_break():
    """COMPLEMENT (so the token-break isn't over-broad): a normal multi-word claim
    that already fits is wrapped intact — the break engages only on a genuinely
    over-wide token, never on ordinary spaced prose. The body ink must still reach
    a healthy measure (the claim renders in full), not collapse to a sliver."""
    data = CouncilCardData(
        members=["claude", "codex", "antigravity"],
        winner="claude",
        agreed_claims=[],
        disagreed_claim="The two models disagreed about which approach to take here",
        disagreed_why="because the trade-off is real and the stakes are not trivial",
    )
    rightmost = _rightmost_ink_x_in_band(render_council_card(data), 240, 470)
    # Ordinary prose wraps to a comfortable measure well within the margin; a floor
    # of 400 catches a break that wrongly shredded fitting text into a narrow column.
    assert 400 < rightmost <= RIGHT_MARGIN + 2, (
        "REGRESSION: the token-break shredded a NORMAL spaced claim — rightmost body "
        f"ink x={rightmost} fell below the expected healthy measure, meaning the "
        "hard-break engaged on ordinary prose. It must fire only on an over-wide "
        "separator-free token, never on text that already wraps on whitespace."
    )


def _drawn_text(png_render_fn) -> str:
    """Render via ``png_render_fn`` while capturing every string handed to
    ``ImageDraw.text``, joined newline-separated. Lets a value-binding assert
    re-extract the ACTUAL drawn glyphs (the brand the card paints), not just a
    pixel-region heuristic. Mirrors the capture technique in test_me_card.py."""
    from PIL import ImageDraw

    drawn: list[str] = []
    orig = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *a, **k):
        drawn.append(str(text))
        return orig(self, xy, text, *a, **k)

    ImageDraw.ImageDraw.text = capture
    try:
        png_render_fn()
    finally:
        ImageDraw.ImageDraw.text = orig
    return "\n".join(drawn)


def test_council_card_winner_and_roster_render_model_brand_not_slug():
    """VALUE-BINDING on the PUBLICLY-shared council PNG: the winner headline
    ('<winner> won.') AND the roster line ('Trinity asked …') must paint the
    MODEL BRAND (antigravity→Gemini, codex→GPT), never the raw dispatch slug.

    Discriminating fixture: winner='antigravity' — the slug and its brand are
    DIFFERENT strings, so a render that drops `_provider_display` (drawing
    `f"{data.winner} won."` or a raw-slug roster) paints 'antigravity won.' /
    'Trinity asked claude · codex · antigravity.' on a card a user posts to
    Twitter. Asserted on the DRAWN TEXT, not a teal-pixel count — the existing
    council-card suite (solo-suppression, partial-disclosure, overflow-fit) is
    entirely pixel-region / rightmost-x based and stays GREEN on a slug leak
    (mutation-proven 2026-06-21): drawing the raw slug keeps the teal pixels,
    the disclosure band, and the right-margin fit all intact, so this binding
    was unguarded. The #275 slug-vs-brand canon already flipped the launchpad/
    live-council web surfaces to the brand; the share PNG winner+roster line was
    the unguarded sibling. (The launchpad brand is DOM-guarded by
    test_launchpad_provider_label_brand_browser; the PNG had no equivalent.)"""
    from trinity_local.council_schema import provider_model_brand

    # Precondition, checked render-independently on the fixture constants: the
    # slug and its brand are genuinely DIFFERENT, so this fixture discriminates a
    # slug leak from correct brand rendering. (If brand == slug the test couldn't
    # tell them apart and would pass vacuously.)
    assert provider_model_brand("antigravity") == "Gemini" != "antigravity"
    assert provider_model_brand("codex") == "GPT" != "codex"

    data = CouncilCardData(
        members=["claude", "codex", "antigravity"],
        winner="antigravity",  # → must render "Gemini won."
        agreed_claims=["Cache the embedding model load across calls"],
        disagreed_claim="Whether to vendor the dependency",
        disagreed_why="vendoring trades update friction for offline reproducibility",
    )
    text = _drawn_text(lambda: render_council_card(data))

    # The winner brand IS drawn, the raw slug is NOT — anywhere on the card.
    assert "Gemini won." in text, (
        "REGRESSION: the council share card did NOT paint the winner's MODEL "
        "BRAND ('Gemini won.') for winner='antigravity'. Drawn text:\n" + text
    )
    assert "Trinity asked Claude · GPT · Gemini." in text, (
        "REGRESSION: the council share card roster line did NOT render the "
        "MODEL BRANDS ('Trinity asked Claude · GPT · Gemini.'). Drawn text:\n"
        + text
    )
    # No raw dispatch slug leaks onto this PUBLIC PNG (case-insensitive — the
    # winner line draws the slug verbatim under the leak, lowercase).
    lowered = text.lower()
    assert "antigravity" not in lowered and "codex" not in lowered, (
        "REGRESSION: a raw dispatch slug ('antigravity'/'codex') leaked onto the "
        "PUBLICLY-shared council PNG instead of the model brand (Gemini/GPT) — "
        "the #275 slug-vs-brand canon broke on the winner/roster headline. "
        "Drawn text:\n" + text
    )
