"""council-share PNG export — render a council outcome as a 1200×630 OG card.

Companion to eval_card.py (eval results) and me_card.py (lens). All three
share the same visual language (cream BG, sage accent, serif headline,
mono CTA) so a viewer sees a coherent product, not three disconnected
artifacts.

Single function: ``render_council_card(card_data) -> bytes``. CLI writes
the bytes to disk; the recipient sees:

1. Headline — "[Winner] won" with the 3-model lineup
2. 1-2 agreed_claims (where models converged)
3. 1 disagreed_claim with its "why_matters" (where they fought + the stakes)
4. Install CTA → keepwhatworks.com

Privacy mode is the default. The user's verbatim prompt is NEVER inlined
on the card; the prompt may be present in the JSON outcome on disk but
never crosses to the share artifact. Members' full responses are also
omitted — only the chairman-extracted agreed_claims + disagreed_claims
land on the card.
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
    draw_wordmark,
    fit_one_line as _fit_one_line,
    strip_unrenderable as _strip_unrenderable,
    _break_long_word,
    NON_LATIN_PLACEHOLDER as _NON_LATIN,
    LANDING_URL as CTA_LANDING_URL,
    load_font as _load_font,
    blank_canvas,
    save_png,
)

# Card-specific accent — warm brown for the disagreement section that
# contrasts against the sage agreement accent.
COLOR_DISAGREE = (189, 106, 90)


@dataclass
class CouncilCardData:
    """Card-shaped projection of a CouncilOutcome. Members + claims
    are pre-flattened to plain strings so the renderer doesn't have to
    know about the CouncilRoutingLabel shape.
    """
    members: list[str] = field(default_factory=list)
    winner: str | None = None
    agreed_claims: list[str] = field(default_factory=list)
    disagreed_claim: str | None = None
    disagreed_why: str | None = None
    # Count of providers that were ATTEMPTED but failed and were excluded from
    # member_results (e.g. a rate-limited premium provider). The card lists only
    # the responders by brand, so without this a 2-of-3 council renders IDENTICALLY
    # to a deliberate 2-model one — the headline "Trinity asked Claude · GPT."
    # silently omits the 3rd Trinity actually asked. The live page + the persistent
    # ?council_id= review page already disclose this (the #238 honest-degradation
    # lineage); the publicly-shared share card is the unfixed sibling. Count-only
    # (no provider names) to stay clear of the #275 slug-vs-brand display call.
    failed_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": list(self.members),
            "winner": self.winner,
            "agreed_claims": list(self.agreed_claims),
            "disagreed_claim": self.disagreed_claim,
            "disagreed_why": self.disagreed_why,
            "failed_count": self.failed_count,
        }


def collect_card_data_from_outcome(outcome) -> CouncilCardData:
    """Build a CouncilCardData from a CouncilOutcome.

    Privacy-safe by construction: only fields from `routing_label`
    (chairman-extracted summary) cross to the card. The user's
    verbatim prompt + the members' full response text are NEVER read
    here, so they cannot leak through this path.
    """
    members = [m.provider for m in (outcome.member_results or [])]
    winner = outcome.winner_provider

    # The runner records casualties in metadata.failed_members but EXCLUDES them
    # from member_results, so the card's roster shows only the responders. Carry
    # the count through so the renderer can disclose the partial council honestly
    # (mirrors council_review's failed_disclosure_html). Shape-guarded against a
    # corrupt non-list metadata.failed_members.
    _meta = getattr(outcome, "metadata", None) or {}
    _failed = _meta.get("failed_members")
    failed_count = len(_failed) if isinstance(_failed, list) else 0

    label = outcome.routing_label
    agreed: list[str] = []
    disagreed_claim: str | None = None
    disagreed_why: str | None = None
    if label is not None:
        agreed = [str(c) for c in (label.agreed_claims or [])]
        if label.disagreed_claims:
            # Pick the FIRST disagreed_claim — the chairman emits them
            # in priority order. Each item is a dict {provider, claim,
            # why_matters}; we render `claim` + `why_matters`.
            d0 = label.disagreed_claims[0] or {}
            disagreed_claim = str(d0.get("claim") or "") or None
            disagreed_why = str(d0.get("why_matters") or "") or None

    return CouncilCardData(
        members=members,
        winner=winner,
        agreed_claims=agreed,
        disagreed_claim=disagreed_claim,
        disagreed_why=disagreed_why,
        failed_count=failed_count,
    )


def _provider_display(name: str | None) -> str:
    # Model trio (Claude / GPT / Gemini), not the harness trio — the share
    # card names the *models* that competed, so antigravity → "Gemini" (the
    # model a reader knows), consistent with codex → "GPT". Per #239
    # model-names-in-UI. Single-sourced via council_schema.provider_model_brand.
    # The launchpad/review web routing surfaces deliberately use the harness
    # trio; that's a separate surface, left as-is.
    if not name:
        return "?"
    from .council_schema import provider_model_brand

    return provider_model_brand(name) or name.capitalize()


def _provider_display_renderable(name: str | None, font) -> str | None:
    """The brand display name for `name`, or None when it strips to nothing the
    card font can draw (an all-non-Latin custom model name: provider_model_brand
    returns None, name.capitalize() keeps the CJK/Arabic, and the font tofus it).

    Returning None lets the ROSTER join drop the member entirely instead of
    leaving a dangling "·" separator, and lets the WINNER line fall back to a
    readable placeholder instead of painting "won." with no name on this
    PUBLICLY-shared PNG (the all-non-Latin sibling of the #275 brand-display
    class). The Latin/branded common case is unaffected."""
    disp = _provider_display(name)
    cleaned = _strip_unrenderable(disp, font)
    return cleaned or None


def _wrap(text: str, font, max_width: int, draw, *, placeholder: str = "") -> list[str]:
    """Greedy word-wrap that respects pixel width.

    Strips font-unrenderable glyphs first (share_card_base.strip_unrenderable) so
    a chairman's agreed/disagreed claim -- LLM text that routinely carries emoji
    the brand font can't render -- never wraps a tofu box onto this PUBLICLY-
    shared PNG. (No literal emoji in this docstring on purpose: it would trip the
    static-string tofu guard.) ``placeholder`` degrades an all-non-Latin claim to
    a readable note instead of an empty wrap (which left a naked "•" bullet / a
    dangling "—" on the public card)."""
    if not text:
        return []
    text = _strip_unrenderable(text, font, placeholder=placeholder)
    if not text:
        return []
    # Greedy word-wrap splits on whitespace, so a SINGLE separator-free token
    # wider than max_width (a chairman claim / why_matters carrying a file path
    # `src/trinity_local/...py`, a URL, a regex, a hash) became one line that ran
    # off the right margin to the very card edge on this PUBLICLY-shared PNG —
    # the long-unbreakable-token horizontal-overflow class the live + static
    # council pages already break (overflow-wrap:anywhere) but the share card
    # didn't. _break_long_word (shared) hard-breaks such a token at the character
    # level so no emitted line ever exceeds max_width.
    words: list[str] = []
    for raw in text.split():
        words.extend(_break_long_word(raw, font, max_width, draw))
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _clip_lines(lines: list[str], max_lines: int, font, max_width: int, draw) -> list[str]:
    """Return at most ``max_lines`` lines; when the block is longer, end the
    last shown line with an ellipsis instead of a hard mid-sentence cut. These
    cards are shared publicly — a clean ``word…`` reads as "there's more",
    whereas a dangling ``word,`` reads as broken/unfinished. Strips a trailing
    separator (``, ; : — –`` ) before the ellipsis and trims words so the
    ``…`` still fits within ``max_width``."""
    if max_lines <= 0:
        return []
    if len(lines) <= max_lines:
        return lines
    shown = list(lines[:max_lines])
    last = shown[-1].rstrip().rstrip(",;:—–-").rstrip()
    ell = "…"
    while last and draw.textbbox((0, 0), last + ell, font=font)[2] > max_width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    shown[-1] = (last + ell) if last else ell
    return shown


CTA_HEADLINE = "Run your own council:"
# CTA_LANDING_URL / FOOTER_TAGLINE imported from share_card_base.


def render_council_card(data: CouncilCardData) -> bytes:
    """Render the 1200×630 PNG. Returns bytes; caller writes to disk."""
    img, draw = blank_canvas()
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img, "RGBA")

    eyebrow = _load_font("bold", 22)
    headline = _load_font("serif", 48)
    section_label = _load_font("bold", 18)
    claim_body = _load_font("regular", 20)
    disclosure_font = _load_font("regular", 18)
    cta_label = _load_font("bold", 20)
    cta_url = _load_font("mono", 22)
    footer = _load_font("regular", 18)

    margin = 60
    y = margin

    # A council needs at least TWO DISTINCT voices to have a contest. When only
    # one member ran (a single provider was enabled), OR every member is the
    # SAME provider (e.g. members=["claude","claude","claude"] — all brand to
    # one "Claude", and the chairman's winner/runner_up/scores all key on that
    # single slug), there is no one to "win" against and no one to "agree" with.
    # The competition framing ("Claude · Claude · Claude. Claude won." + the
    # AGREED consensus block) is then degenerate and OVERCLAIMS a contest
    # between identical voices on this PUBLICLY-shared card (#35
    # green-while-degenerate). Gate on the count of DISTINCT provider slugs, not
    # the raw member count — the same-provider list-roster was the unfixed
    # sibling of the 1-responder solo branch (the live page already collapses
    # this, since its members map is keyed by provider). Render the honest
    # single-model framing instead.
    distinct_responders = len({m for m in (data.members or [])})
    # ALL-FAILED (0 responders): a hand-edited / legacy / imported outcome can
    # carry member_results=[] (the runner itself raises before persisting, but
    # the share card renders whatever lands on disk — the #258 hand-editable-
    # state class). With ZERO responders the solo copy "Only ONE model answered"
    # is a flat LIE that also CONTRADICTS the disclosure line one row up
    # ("…over the 0 that answered"). The live page already distinguishes this
    # (council_review "Every provider attempted but failed to respond — there's
    # no synthesis to show"); the share card was the unfixed sibling that folded
    # n=0 into the n=1 solo branch. Treat it as its own honest state.
    all_failed = distinct_responders == 0 and data.failed_count > 0
    solo = (not all_failed) and distinct_responders <= 1

    # ── Eyebrow ───────────────────────────────────────────────────
    draw.text((margin, y), "TRINITY · YOUR COUNCIL",
              font=eyebrow, fill=COLOR_ACCENT)
    y += 46

    # ── Headline ──────────────────────────────────────────────────
    # Every headline line is fit to the body width with _fit_one_line — a long
    # provider brand (an unbranded local-model slug title-cases to its raw name,
    # e.g. "Some-Long-Local-Model-Slug") would otherwise run the 48px serif line
    # straight off the right edge of this PUBLICLY-shared PNG, clipped at the card
    # boundary. The eval card already fits its headline provider name for the same
    # reason (a long local-model slug); the council card headline was the unfixed
    # sibling of that #283 class.
    head_width = CARD_WIDTH - 2 * margin
    # Build the roster from members whose brand name the card font can actually
    # draw — an all-non-Latin custom model name strips to nothing, and joining it
    # raw left a dangling "·" ("· Claude · GPT.") on the public PNG. Drop the
    # un-renderable member from the join instead. The winner gets a readable
    # placeholder rather than a bare "won." with no name.
    roster = [d for d in (_provider_display_renderable(m, headline) for m in data.members[:3]) if d]
    winner_disp = _provider_display_renderable(data.winner, headline) if data.winner else None
    if all_failed:
        # Zero responders. State it plainly; the disclosure + body below carry
        # the honest detail. NO winner/roster framing (there was no contest, no
        # answer to crown), no "one model answered" overclaim.
        draw.text((margin, y), "No model responded.",
                  font=headline, fill=COLOR_INK)
        y += 70
    elif solo and data.members:
        # Single-model: state what actually happened, no fabricated contest.
        member_text = roster[0] if roster else _NON_LATIN
        draw.text((margin, y),
                  _fit_one_line(f"Trinity asked {member_text}.", headline, head_width, draw),
                  font=headline, fill=COLOR_INK)
        y += 60
        draw.text((margin, y), "One model — no council.",
                  font=headline, fill=COLOR_MUTED)
        y += 70
    elif data.winner and data.members:
        members_text = " · ".join(roster) if roster else _NON_LATIN
        headline_text = f"Trinity asked {members_text}."
        # Two-line headline — first line = roster, second line = winner.
        draw.text((margin, y),
                  _fit_one_line(headline_text, headline, head_width, draw),
                  font=headline, fill=COLOR_INK)
        y += 60
        winner_text = f"{winner_disp or _NON_LATIN} won."
        draw.text((margin, y),
                  _fit_one_line(winner_text, headline, head_width, draw),
                  font=headline, fill=COLOR_ACCENT)
        y += 70
    elif data.members:
        # No winner recorded — still show the roster.
        members_text = " · ".join(roster) if roster else _NON_LATIN
        draw.text((margin, y),
                  _fit_one_line(f"Trinity asked {members_text}.", headline, head_width, draw),
                  font=headline, fill=COLOR_INK)
        y += 70
    else:
        # Empty-state fallback.
        draw.text((margin, y), "Trinity council",
                  font=headline, fill=COLOR_INK)
        y += 70

    # ── Honest partial-council disclosure ─────────────────────────
    # A provider was ATTEMPTED but failed and was excluded from the roster (e.g.
    # a rate-limited premium model). Without this, the headline "Trinity asked
    # Claude · GPT." silently omits the 3rd Trinity actually asked, so a 2-of-3
    # council reads as a deliberate 2-model one on this PUBLICLY-shared PNG. The
    # live page + the persistent ?council_id= review page already disclose it
    # (the #238 honest-degradation lineage); the share card is the unfixed
    # sibling. Count-only (no provider names) to stay clear of the #275
    # slug-vs-brand display call, same as the other two surfaces.
    if data.failed_count > 0:
        _resp = max(len(data.members), 0)
        _plural = "" if data.failed_count == 1 else "s"
        if all_failed:
            # ZERO responders — "over the 0 that answered" is nonsensical (it
            # implies a synthesis exists over nobody). State the total failure.
            disclosure_text = (
                "The model attempted but failed to respond — no synthesis."
                if data.failed_count == 1 else
                f"All {data.failed_count} models attempted but failed to "
                f"respond — no synthesis."
            )
        else:
            disclosure_text = (
                f"{data.failed_count} model{_plural} didn't respond — this is over "
                f"the {_resp} that answered."
            )
        # The caution mark is a VECTOR triangle-with-bang, NOT the U+26A0 warning
        # glyph: the card font has no glyph for it and renders a tofu box on the
        # PNG (the eval card hit the exact same bug — see eval_card
        # _draw_mixed_set_warning). Reuse that shape so the two cards' caution
        # marks match.
        th = 16
        apex_x, top = margin + 8, y + 1
        bot = top + th
        draw.polygon(
            [(apex_x, top), (apex_x - 8, bot), (apex_x + 8, bot)],
            outline=COLOR_DISAGREE, width=2,
        )
        draw.line([(apex_x, top + 5), (apex_x, bot - 5)], fill=COLOR_DISAGREE, width=2)
        draw.ellipse([apex_x - 1, bot - 4, apex_x + 1, bot - 2], fill=COLOR_DISAGREE)
        # The CAUTION MARK stays warm-brown (COLOR_DISAGREE, the disagreement hue) so
        # it reads as a caution, but the DISCLOSURE SENTENCE is regular-weight 18px
        # BODY text — COLOR_DISAGREE on the cool-mist BG is only 3.30:1, below the
        # 4.5:1 WCAG AA body floor, so the sentence painted as the least-readable text
        # on this PUBLICLY-shared PNG. Paint the text in COLOR_MUTED (5.1:1, passes
        # AA), exactly the eval card's _draw_mixed_set_warning convention (icon in the
        # caution hue, sentence in COLOR_MUTED). The council card was the un-fixed
        # sibling that put BOTH the icon and the sentence in the sub-AA caution hue.
        draw.text((apex_x + 16, y), disclosure_text,
                  font=disclosure_font, fill=COLOR_MUTED)
        y += 34

    # ── Body: agreed_claims + disagreed_claim ─────────────────────
    body_width = CARD_WIDTH - 2 * margin
    body_end = CARD_HEIGHT - margin - 100  # leave room for CTA + footer

    if all_failed:
        # ZERO responders — no winner, no claims, no "one model answered". The
        # disclosure line above states the total failure; the body explains what
        # to do. This is the share-card twin of the live page's "Every provider
        # attempted but failed to respond — there's no synthesis to show".
        all_failed_body = (
            "Every model failed to respond, so there's no answer to share. "
            "Check the providers are signed in and try again."
        )
        for line in _wrap(all_failed_body, claim_body, body_width, draw):
            draw.text((margin, y), line, font=claim_body, fill=COLOR_INK)
            y += 28
    elif solo and data.members:
        # No contest, no consensus to report. Say so honestly + point at the
        # real value (asking MORE than one model). Beats a fabricated
        # "X won." over a blank card. When a second provider WAS enabled but
        # FAILED (failed_count > 0), the disclosure line above already states
        # that — so don't tell the user to "Enable a second provider" (they did;
        # it failed), which would contradict the ⚠ note one line up.
        # Gated on data.members: with ZERO members AND no recorded failures
        # (a truly empty/corrupt no-data outcome) "Only one model answered" is
        # itself false — fall through to the generic empty card instead.
        solo_body = (
            "Only one model answered, so there's no winner and nothing to "
            "agree on yet."
            if data.failed_count > 0 else
            "Only one model answered, so there's no winner and nothing to "
            "agree on yet. Enable a second provider to run a real council."
        )
        for line in _wrap(solo_body, claim_body, body_width, draw):
            draw.text((margin, y), line, font=claim_body, fill=COLOR_INK)
            y += 28
    elif data.members and not data.agreed_claims and not data.disagreed_claim:
        # Winner + a real roster, but the chairman recorded NO structured
        # claims (empty agreed_claims AND disagreed_claims — a schema-valid,
        # reachable outcome: "the members reached no recorded consensus" is
        # semantically distinct from solo, see council_schema.to_dict). Without
        # this branch the 1200×630 card painted ~290px of pure void below
        # "<winner> won." — a half-empty, broken-looking PNG on the public
        # share surface. Render an honest fallback body (mirrors the solo
        # fallback) so a no-claims council still ships a complete card. Gated on
        # data.members: with ZERO members the "a winner emerged" framing is false
        # (no roster to emerge from) — handled by all_failed above or left to the
        # generic empty card.
        no_claims_body = (
            "The models weighed in and a winner emerged, but the chairman "
            "logged no shared claims — open the full council to read each "
            "answer."
        )
        for line in _wrap(no_claims_body, claim_body, body_width, draw):
            draw.text((margin, y), line, font=claim_body, fill=COLOR_INK)
            y += 28

    if data.agreed_claims and not solo:
        draw.text((margin, y), "AGREED",
                  font=section_label, fill=COLOR_ACCENT)
        y += 24
        # Up to 2 agreed claims, each wrapped to body_width. Wrap the claim WITH
        # the non-Latin placeholder, THEN prepend the bullet — an all-non-Latin
        # claim used to strip to "" and leave a naked "• " bullet on the public
        # card; now it degrades to "• non-Latin — view in app".
        for claim in data.agreed_claims[:2]:
            if y > body_end - 60:
                break
            lines = _wrap(claim, claim_body, body_width, draw, placeholder=_NON_LATIN)
            if lines:
                lines[0] = f"• {lines[0]}"
            for line in _clip_lines(lines, 2, claim_body, body_width, draw):  # cap each claim at 2 visual lines
                draw.text((margin, y), line, font=claim_body, fill=COLOR_INK)
                y += 28
            y += 6
        y += 8

    if data.disagreed_claim and not solo and y < body_end - 60:
        draw.text((margin, y), "DISAGREED — WHY IT MATTERS",
                  font=section_label, fill=COLOR_DISAGREE)
        y += 24
        # Render claim + why_matters as one wrapped block. Strip each part FIRST
        # so an emoji-only claim joined to a non-Latin why doesn't collapse to a
        # dangling " — " on the public card; join only the parts that survive, and
        # fall back to the placeholder when neither does.
        claim_part = _strip_unrenderable(data.disagreed_claim or "", claim_body)
        why_part = _strip_unrenderable(data.disagreed_why or "", claim_body) if data.disagreed_why else ""
        parts = [p for p in (claim_part, why_part) if p]
        composite = " — ".join(parts) if parts else _NON_LATIN
        lines = _wrap(composite, claim_body, body_width, draw, placeholder=_NON_LATIN)
        # Lines that fit: cap at 3 AND respect body_end (CTA/footer reserve).
        fit_count = 0
        for i in range(min(len(lines), 3)):
            if y + i * 28 > body_end - 28:
                break
            fit_count += 1
        for line in _clip_lines(lines, fit_count, claim_body, body_width, draw):
            draw.text((margin, y), line, font=claim_body, fill=COLOR_INK)
            y += 28

    # ── CTA block ─────────────────────────────────────────────────
    cta_block_top = CARD_HEIGHT - margin - 90
    draw.text((margin, cta_block_top), CTA_HEADLINE,
              font=cta_label, fill=COLOR_ACCENT)
    draw.text((margin, cta_block_top + 28), CTA_LANDING_URL,
              font=cta_url, fill=COLOR_INK)

    # ── Footer wordmark (die + tagline) ───────────────────────────
    draw_wordmark(draw, font=footer, margin=margin)

    return save_png(img)
