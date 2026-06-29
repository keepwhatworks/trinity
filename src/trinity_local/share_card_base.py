"""Shared infrastructure for 1200×630 OG share cards.

Three card renderers (`me_card`, `eval_card`, `council_card`) used to
inline their own copies of the canvas dimensions, color palette, font
loader, wrap helper, and footer/CTA renderer. The doc-consistency tests
actively enforce that *"all three carry the same install CTA + same
landing URL"* — collapsing the shared contract here makes that
enforcement structural rather than fragile.

Each card module owns only its body (the unique data-dense middle) and
imports the canvas + footer from this base. Tufte direction: the body
is where every card gets data-dense — small multiples, inline labels,
no chartjunk — without re-rendering the brand contract surface.
"""
from __future__ import annotations

import re


# OG card shape — 1200×630 renders cleanly on Twitter / LinkedIn / Discord
# / Slack / iMessage previews. Pinned per surface-shape convention.
CARD_WIDTH = 1200
CARD_HEIGHT = 630

# Trinity palette — Calm/Muted-Teal (v1.7.310): cool mist BG + cool-charcoal ink +
# muted-teal accent. Matches the launchpad + keepwhatworks.com so a viewer sees one
# coherent product. No cream, no sage. All three cards share these.
COLOR_BG = (234, 236, 239)        # cool mist  (#eaecef)
COLOR_INK = (47, 54, 60)          # cool charcoal for headlines (#2f363c)
COLOR_MUTED = (91, 100, 107)      # muted ink for body (#5b646b)
COLOR_ACCENT = (79, 144, 149)     # muted teal for accents (#4f9095)
COLOR_SURFACE = (250, 251, 252)   # near-white card surface (#fafbfc)
COLOR_BORDER = (221, 225, 230)    # soft hairline (#dde1e6)

# Public landing URL + footer tagline for share artifacts. The domain
# constant lives in facts.py (#131) so doc surfaces and Python code
# both pull from one place; this module re-exports as LANDING_URL for
# back-compat with the existing share-card consumers.
from .facts import LANDING_DOMAIN

LANDING_URL = LANDING_DOMAIN
# Footer wordmark. The ⠕ (U+2815) braille logo char was dropped 2026-06-09
# (#276) because the vendored fonts tofu'd it; 2026-06-10 it's replaced by a
# DRAWN three-pip die mark (draw_die_mark, below) — pure vector, so no glyph,
# no font dependency, no tofu, ever. The tagline text stays glyph-free; the
# mark is painted just to its left. (The ⠕ stays in test_share_card_brand's
# _TOFU_GLYPHS — it must never come back as a character.)
FOOTER_TAGLINE = f"Trinity · {LANDING_DOMAIN}"


# Font path candidates — macOS first (the production path), then Linux
# (DejaVu/Liberation/FreeFont, covering Debian/Ubuntu/Fedora defaults), then
# Windows (C:/Windows/Fonts), then the SIZED Pillow default in load_font.
# Without the Linux+Windows rows a share card rendered off-Mac fell straight
# through to the unsized bitmap default and collapsed (see load_font).
_FONT_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ],
    "bold": [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    ],
    "serif": [
        "/System/Library/Fonts/Times.ttc",
        "/System/Library/Fonts/Charter.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/georgia.ttf",
    ],
    "mono": [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
    ],
}


# Vendored fonts (Calm/Muted-Teal, v1.7.310) — the cards now render in the brand
# face: Hanken Grotesk (everything; "serif" headlines are de-serifed to Hanken
# SemiBold) + JetBrains Mono (commands). Variable TTFs under data/fonts/; PIL sets
# the weight via set_variation_by_name. Falls back to the system candidates below.
_VENDORED = {
    "regular": ("HankenGrotesk.ttf", "Medium"),
    "bold":    ("HankenGrotesk.ttf", "SemiBold"),
    "serif":   ("HankenGrotesk.ttf", "SemiBold"),   # de-serif: the brand has no serif
    "mono":    ("JetBrainsMono.ttf", "Medium"),
}


def _load_vendored_font(kind: str, size: int):
    """Load the vendored brand font for `kind` at `size`, or None if unavailable."""
    spec = _VENDORED.get(kind)
    if not spec:
        return None
    name, variation = spec
    try:
        from importlib import resources
        from io import BytesIO
        from PIL import ImageFont

        data = resources.files("trinity_local").joinpath(f"data/fonts/{name}").read_bytes()
        font = ImageFont.truetype(BytesIO(data), size)
        try:
            font.set_variation_by_name(variation)
        except Exception:
            pass  # not a variable font / weight missing — default instance is fine
        return font
    except Exception:
        return None


def load_font(kind: str, size: int):
    """Best-effort font load: the vendored brand font (Hanken / JetBrains) first,
    then the system candidates, then a SIZED default.

    When NONE resolve (a minimal Linux container with no DejaVu/Liberation, or some
    Windows hosts), fall back to ``load_default(size)`` — the *sized* default.
    ``load_default()`` with no size returns a fixed ~10px bitmap that ignores `size`,
    collapsing every headline down to footer size and wrecking the layout. The sized
    default isn't pretty but keeps the layout intact. (Pillow 10.1+ added the size
    param; guard for older builds.)"""
    from PIL import ImageFont

    vendored = _load_vendored_font(kind, size)
    if vendored is not None:
        return vendored
    for path in _FONT_CANDIDATES.get(kind, []):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1 — no size param
        return ImageFont.load_default()


# Combining/joining codepoints that are meaningless once their base glyph is
# stripped — left dangling, they themselves tofu (variation selectors, the ZWJ,
# skin-tone modifiers). Dropped whenever they survive a strip pass so an emoji
# removal never leaves an orphaned modifier box behind.
_ORPHAN_JOINERS = (
    "‍",                              # ZWJ (emoji sequence joiner)
    "︎", "️",                    # variation selectors 15/16
    "\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff",  # skin tones
)


def _glyph_tofus(ch: str, font, _cache: dict = {}) -> bool:
    """True when ``font`` renders ``ch`` as the .notdef tofu box.

    Dependency-free + font-agnostic: compare the rendered pixel mask of ``ch``
    against the mask of a guaranteed-absent codepoint (U+FFFF noncharacter, which
    every normal font maps to .notdef). A real glyph and the box differ in their
    raster; an emoji / CJK / symbol the brand font lacks rasters IDENTICALLY to
    the box.

    Cached per (STABLE-font-identity, char). The key is (family, style, size) —
    NOT id(font): PIL FreeTypeFont objects are created fresh per render and GC'd,
    so id() is recycled, and an id-keyed cache returns a stale answer computed for
    a DIFFERENT font (the flaky cross-render bug). Two fonts with the same face +
    size give the same tofu verdict, so this key is both safe and shareable."""
    try:
        fid = (
            tuple(font.getname()) if hasattr(font, "getname") else None,
            getattr(font, "size", None),
        )
    except Exception:
        fid = (id(font),)  # last-resort; correctness over caching
    key = (fid, ch)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        ndkey = (fid, "￿")
        notdef = _cache.get(ndkey)
        if notdef is None:
            notdef = bytes(font.getmask("￿"))
            _cache[ndkey] = notdef
        result = bytes(font.getmask(ch)) == notdef
    except Exception:
        result = False  # never let detection crash a render
    _cache[key] = result
    return result


# Separators/punctuation that carry no meaning ON THEIR OWN once their word
# neighbours are stripped — a stripped "简洁 > עברית" leaves a bare ">", a stripped
# roster join leaves a dangling "·". Used to decide whether a strip ERASED a field
# down to nothing renderable (→ placeholder) vs left real words behind (→ keep).
_SEPARATOR_RESIDUE = re.compile(r"[\s·•>—–\-,.;:!?|/«»\"'`()\[\]{}…]+")

# The honest fallback drawn in place of an all-non-Latin field on a share card.
# The brand font (Hanken / JetBrains) has no CJK / Arabic / Hebrew / emoji glyphs,
# so an all-non-Latin lens pole / model name / claim strips to "" — a void on the
# public PNG. This note tells the viewer the field exists but lives in the app,
# instead of silently erasing the user's own non-Latin text. (Bundling a CJK/emoji
# font is the heavier alternative and a deliberate founder call.)
NON_LATIN_PLACEHOLDER = "non-Latin — view in app"


def strip_unrenderable(text: str, font, *, placeholder: str = "") -> str:
    """Drop every codepoint ``font`` would render as a tofu box, collapsing the
    whitespace the removal leaves behind.

    The static-string tofu guard (test_share_card_brand) only covers glyphs the
    CODE draws literally. But the card body is DYNAMIC: chairman agreed/disagreed
    claims, lens poles/failures, model names -- all LLM- or corpus-derived, and
    emoji in that text is common. The brand font (Hanken Grotesk / JetBrains
    Mono) has no emoji/CJK glyphs, so an unsanitized claim rendered a row of
    boxes on a PUBLICLY-shared PNG. Strip them at the shared text-shaping
    boundary so every card is glyph-clean. (Glyph examples kept out of this
    docstring on purpose -- a literal emoji here would trip the static-string
    tofu guard, which scans string tokens; see that test's emoji-free message.)

    ``placeholder`` — honest graceful degradation for a field that is ENTIRELY
    non-Latin (an all-CJK / all-Arabic / all-Hebrew lens pole, a model whose name
    is wholly non-Latin, an emoji-only claim). Stripping such a field to "" left a
    VOID on the public PNG: a "PURE-A FAILS AS" label with no text under it, a
    winner line reading "won." with no name, a naked AGREED bullet, an unlabelled
    eval bar — broken AND dishonest (the council card claimed a winner but named
    nobody). When the strip removes all word content (only separators/punctuation
    survive, or nothing), the field is replaced with ``placeholder`` (e.g.
    "non-Latin — view in app") so the card degrades to a READABLE note instead of
    silent erasure. Default "" preserves the legacy erase-to-empty behaviour for
    callers that want it; bundling a CJK/emoji font is the heavier alternative and
    is a deliberate founder call, so this is the light honest fallback."""
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        if ch in ("\n", "\t", " ") or ch.isspace():
            out.append(ch)
            continue
        if ch in _ORPHAN_JOINERS:
            continue  # only meaningful attached to a base glyph we may have dropped
        if _glyph_tofus(ch, font):
            continue
        out.append(ch)
    cleaned = "".join(out)
    if cleaned == text:
        return text
    # Collapse runs of spaces the removal opened up (an emoji between two words
    # leaves a double space) without touching newlines.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # Trim a space left dangling before/after punctuation or at the ends.
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    cleaned = cleaned.strip()
    # Honest degradation: the input HAD non-whitespace content but the strip left
    # no renderable word behind (all-non-Latin field, or only separators survive).
    # Substitute the placeholder rather than paint a void onto the public PNG.
    if placeholder and text.strip() and not _SEPARATOR_RESIDUE.sub("", cleaned):
        return placeholder
    return cleaned


def wrap_text(text: str, font, max_width: int, draw, *, placeholder: str = "") -> list[str]:
    """Greedy word-wrap respecting the font's measured pixel width.
    Walks word-by-word using draw.textbbox; returns lines.

    Strips font-unrenderable glyphs first (see strip_unrenderable) so dynamic
    card text (chairman claims, lens poles) never wraps a tofu box onto the PNG.
    ``placeholder`` degrades an all-non-Latin field to a readable note instead of
    an empty wrap (which painted a void/dangling label on the public PNG)."""
    text = strip_unrenderable(text, font, placeholder=placeholder)
    raw_words = text.split()
    if not raw_words:
        return []
    # Greedy word-wrap splits on whitespace, so a SINGLE separator-free token
    # wider than max_width (a chairman claim / lens pole carrying a file path,
    # a URL, a regex, a hash) became one line that ran off the card's right edge
    # on a PUBLICLY-shared PNG — the long-unbreakable-token horizontal-overflow
    # class. _break_long_word hard-breaks such a token character by character so
    # no emitted line ever exceeds max_width.
    words: list[str] = []
    for w in raw_words:
        words.extend(_break_long_word(w, font, max_width, draw))
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _break_long_word(word: str, font, max_width: int, draw) -> list[str]:
    """Hard-break a single whitespace-free ``word`` into chunks each no wider
    than ``max_width``. A token that already fits (every normal word) is returned
    unchanged; only an over-wide separator-free token (a path / URL / hash) is
    split, so the greedy wrapper never emits a line that runs off the card."""
    if draw.textbbox((0, 0), word, font=font)[2] <= max_width:
        return [word]
    chunks: list[str] = []
    current = ""
    for ch in word:
        candidate = current + ch
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            chunks.append(current)
            current = ch
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def fit_one_line(text: str, font, max_width: int, draw, *, placeholder: str = "") -> str:
    """Truncate ``text`` to a single line that fits ``max_width``, ending with
    an ellipsis when clipped (dangling ``, ; : — –`` stripped). Used by share
    cards for single-line fields (a me-card failure/ordering, an eval-card axis
    label) where overflow would run off the card edge or under an adjacent
    element. Shared so every card truncates identically.

    Strips font-unrenderable glyphs first (see strip_unrenderable) so a single-
    line dynamic field (a model name, a lens failure, an ordering pair) never
    draws a tofu box onto the PNG. ``placeholder`` degrades an all-non-Latin field
    to a readable note instead of erasing it to "" (which left a dangling label /
    naked separator on the public PNG)."""
    if not text:
        return ""
    text = strip_unrenderable(text, font, placeholder=placeholder)
    if not text:
        return ""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    ell = "…"
    while text and draw.textbbox((0, 0), text + ell, font=font)[2] > max_width:
        text = text.rsplit(" ", 1)[0] if " " in text else text[:-1]
    return (text.rstrip(",;:—–- ") + ell) if text else ell


def blank_canvas():
    """1200×630 cream canvas + ImageDraw handle. Every card starts here."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), COLOR_BG)
    return img, ImageDraw.Draw(img)


def draw_die_mark(draw, x: int, y: int, size: int, *, body=COLOR_ACCENT, pip=COLOR_BG) -> None:
    """Draw the three-pip die brand mark with its top-left at (x, y). Pips sit in
    the braille dots-1-3-5 arrangement (top-left, middle-right, bottom-left) — the
    keepwhatworks.com logo + the extension toolbar icon. Pure vector — no font —
    so it draws the ARRANGEMENT and can never tofu the way the old braille logo
    glyph did. Teal body + cool-mist pips, matching the favicon.

    Rendered 4× and downscaled (LANCZOS) so the small footer mark has crisp,
    anti-aliased pips instead of chunky integer-pixel blobs."""
    from PIL import Image, ImageDraw

    # Pip centers in the braille dots-1-3-5 arrangement (mirrors the favicon +
    # render_extension_icons.py fractions): top-left, middle-right, bottom-left.
    pip_xy = ((0.36, 0.28), (0.64, 0.50), (0.36, 0.72))

    ss = 4
    s = size * ss
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.rounded_rectangle([0, 0, s - 1, s - 1], radius=round(s * 0.22), fill=body)
    pip_r = max(2, round(s * 0.092))
    for fx, fy in pip_xy:
        cx, cy = s * fx, s * fy
        td.ellipse([cx - pip_r, cy - pip_r, cx + pip_r, cy + pip_r], fill=pip)
    tile = tile.resize((size, size), Image.Resampling.LANCZOS)

    target = getattr(draw, "_image", None)
    if target is not None:
        target.paste(tile, (round(x), round(y)), tile)
        return
    # Fallback (no accessible backing image): draw directly, no supersample.
    draw.rounded_rectangle([x, y, x + size, y + size], radius=max(2, round(size * 0.22)), fill=body)
    fpr = max(1, round(size * 0.10))
    for fx, fy in pip_xy:
        cx, cy = x + size * fx, y + size * fy
        draw.ellipse([cx - fpr, cy - fpr, cx + fpr, cy + fpr], fill=pip)


def draw_wordmark(draw, *, font=None, margin: int = 60) -> None:
    """The bottom-right 'Trinity · domain' wordmark with the three-pip die mark
    painted to its left — the single footer brand mark every share card shares.
    The die is drawn vector (no glyph), so it can never tofu like the old mark.
    Pass the card's footer `font` so the size matches its layout."""
    if font is None:
        font = load_font("regular", 18)
    bbox = draw.textbbox((0, 0), FOOTER_TAGLINE, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    text_x = CARD_WIDTH - margin - width
    text_y = CARD_HEIGHT - margin - 18
    die = max(13, round(height * 0.95))
    die_y = text_y + (height - die) // 2  # vertically center the die on the text
    draw_die_mark(draw, text_x - die - 9, die_y, die)
    draw.text((text_x, text_y), FOOTER_TAGLINE, font=font, fill=COLOR_MUTED)


def draw_footer(draw, *, cta_block_top: int, margin: int = 60) -> None:
    """Render the shared install CTA + landing URL + footer tagline.

    Layout (anchored to cta_block_top):
        cta_block_top:        "Install"
        cta_block_top + 28:   LANDING_URL  (sage)
        bottom-32:            FOOTER_TAGLINE  (right-aligned, muted)

    Each card's body owns where `cta_block_top` lands; everything below
    that is shared brand contract.
    """
    cta_label = load_font("regular", 14)
    cta_url = load_font("bold", 22)

    draw.text((margin, cta_block_top), "Install", font=cta_label, fill=COLOR_MUTED)
    draw.text((margin, cta_block_top + 28), LANDING_URL,
              font=cta_url, fill=COLOR_ACCENT)

    draw_wordmark(draw, margin=margin)


def save_png(img) -> bytes:
    """Serialize the canvas to PNG bytes. Caller owns disk write."""
    import io
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
