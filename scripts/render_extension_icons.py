"""Render Chrome extension icons at the four required sizes.

The browser-extension/manifest.json had no `icons` block, so Chrome
rendered the gray puzzle-piece placeholder in the toolbar and the
extensions list. This script generates the canonical ⠕ Trinity mark
(U+2815 — Braille pattern dots-135) at 16/32/48/128 px.

**Variant: "05 White tile, sage hairline, sage dots"** — picked
2026-05-23 over the prior "04 Filled badge" (sage disk + cream
dots). The white-tile ground makes the three dots read instantly
at 16 px (the actual toolbar size); the sage hairline keeps
brand color present so the icon doesn't disappear into Chrome's
light browser-chrome bar. Three sage dots drawn explicitly (not
through a font) for reliable rendering on Linux CI without
needing the Apple Braille font.

The three dots sit at the canonical ⠕ braille positions:
top-left (dot 1), bottom-left (dot 3), middle-right (dot 5).
Same arrangement Trinity carries on share artifacts and launchpad
eyebrow — visual continuity across surfaces, just rendered as
geometry instead of a glyph here so the toolbar icon is
self-contained.

Re-run if `design_system.COLORS` palette changes or the mark needs
updating. Output lands under `browser-extension/icons/` (4 PNGs)
plus a sibling `docs/favicon.png` rendered at 64 px for the site.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image, ImageDraw

from trinity_local.design_system import COLORS


SIZES = [16, 32, 48, 128]
OUT_DIR = REPO_ROOT / "browser-extension" / "icons"
FAVICON_PATH = REPO_ROOT / "docs" / "favicon.png"
FAVICON_SIZE = 64


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _scaled(size: int) -> tuple[int, int]:
    """Supersample dimensions so tiny icons stay crisp after downsize."""
    scale = 4 if size <= 48 else 2
    return size * scale, scale


def render_icon(canvas_size: int) -> Image.Image:
    """Render variant 05: white tile + sage hairline + 3 sage dots.

    Drawn geometrically (no font dependency). Hairline thickness and
    dot radius are sized as fractions of the canvas so the visual
    weight stays balanced across 16/32/48/128 px.
    """
    work_size, scale = _scaled(canvas_size)
    img = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # NOTE: var names "sage"/"cream" are legacy — these now read the live
    # Calm/Muted-Teal palette from COLORS. Re-run this script to regenerate the
    # committed PNGs in the teal palette if they predate v1.7.309.
    sage = _hex_to_rgb(COLORS["action_primary"])  # muted teal #3f777c — hairline + dots
    cream = _hex_to_rgb(COLORS["bg_base"])        # cool-mist #eaecef — tile ground

    # Rounded-square tile with sage hairline border. Corner radius is
    # ~14% of canvas (Material-icon proportion); stroke is ~3% of
    # canvas, clamped to >=2 px in working space so it survives the
    # LANCZOS downsize without disappearing at 16 px.
    inset = 1
    corner_radius = max(2, int(work_size * 0.14))
    stroke = max(2, int(work_size * 0.03))
    draw.rounded_rectangle(
        (inset, inset, work_size - inset, work_size - inset),
        radius=corner_radius,
        fill=cream,
        outline=sage,
        width=stroke,
    )

    # Three dots in the braille ⠕ pattern. Positions are normalized
    # to the 0..1 unit square inside the inset, then scaled. Dot
    # radius is ~9.5% of canvas — large enough to read as a dot at
    # 16 px without crowding the others.
    dot_r = int(work_size * 0.095)
    # (x_frac, y_frac) — column 1 top, column 2 middle, column 1 bottom
    positions = [
        (0.36, 0.28),  # top-left  (braille dot 1)
        (0.64, 0.50),  # middle-right (braille dot 5)
        (0.36, 0.72),  # bottom-left (braille dot 3)
    ]
    for fx, fy in positions:
        cx = int(work_size * fx)
        cy = int(work_size * fy)
        draw.ellipse(
            (cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r),
            fill=sage,
        )

    if scale != 1:
        img = img.resize((canvas_size, canvas_size), Image.LANCZOS)
    return img


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT_DIR / f"icon-{size}.png"
        img = render_icon(size)
        img.save(path, format="PNG")
        print(f"  → {path.relative_to(REPO_ROOT)}  ({size}×{size})")

    FAVICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    favicon = render_icon(FAVICON_SIZE)
    favicon.save(FAVICON_PATH, format="PNG")
    print(f"  → {FAVICON_PATH.relative_to(REPO_ROOT)}  ({FAVICON_SIZE}×{FAVICON_SIZE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
