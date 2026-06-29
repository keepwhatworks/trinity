"""Regression: the councils side-nav breakpoint contract (the #293 guard, rewritten
2026-06-16 for the chat-UI redesign).

The council rail is now a chat-UI side nav, not the old in-page-static rail:

  - BASE (mobile-first default): `.council-rail` is `position: fixed` and OFF-CANVAS
    (`transform: translateX(-100%)`), so a closed rail contributes nothing to the
    page's scrollWidth (the #291 overflow guard depends on this).
  - DESKTOP (`@media min-width: 1024px`): the rail slides in (`translateX(0)`) and
    `body > main` reserves `margin-left: var(--rail-w)` so the content re-centers in
    the remaining space (the claude.ai pattern). The hamburger collapses it.
  - MOBILE/NARROW (`@media max-width: 1023px`): the rail stays off-canvas; `body >
    main` reserves NOTHING (`margin-left: 0`); the hamburger opens it as a drawer
    (`body.rail-open .council-rail { translateX(0) }`) over a scrim.

The two breakpoints MUST meet exactly (rail reserves space at min-width K, drops the
reservation at max-width K-1) or there's an overlap/dead-zone band. This pins the
new drawer contract the same way the old version pinned the static-rail contract.
"""
from __future__ import annotations

import re


def _launchpad_html() -> str:
    from trinity_local.launchpad_template import render_launchpad_html

    return render_launchpad_html(page_data={})


def _rail_base_block(html: str) -> str:
    """The first `.council-rail { ... }` declaration block (the base rule)."""
    m = re.search(r"\.council-rail\s*\{([^}]*)\}", html)
    assert m, ".council-rail base rule not found"
    return m.group(1)


def _breakpoints(html: str) -> tuple[int, int]:
    """(desktop_reserve_min_width, mobile_drawer_max_width)."""
    desktop = re.search(
        r"@media \(min-width:\s*(\d+)px\)\s*\{[^@]*?body > main\s*\{\s*margin-left:\s*var\(--rail-w",
        html,
    )
    mobile = re.search(
        r"@media \(max-width:\s*(\d+)px\)\s*\{[^@]*?body > main\s*\{\s*margin-left:\s*0",
        html,
    )
    assert desktop, "desktop `body > main { margin-left: var(--rail-w) }` @media (min-width) rule not found"
    assert mobile, "mobile `body > main { margin-left: 0 }` @media (max-width) rule not found"
    return int(desktop.group(1)), int(mobile.group(1))


def test_rail_is_off_canvas_by_default():
    """The base rail rule is a fixed, off-canvas drawer — so a closed rail never
    contributes to horizontal scrollWidth at any width (the #291 contract)."""
    base = _rail_base_block(_launchpad_html())
    assert "position: fixed" in base, "the rail must be position: fixed"
    assert "translateX(-100%)" in base, (
        "the rail must be OFF-CANVAS by default (transform: translateX(-100%)); a "
        "rail visible-by-default at narrow widths re-introduces the side-panel bug"
    )
    # As a floating drawer the rail sits OVER the composer — its background must be
    # OPAQUE or the content bleeds through it (founder-caught 2026-06-16). The old
    # near-transparent rgba(...,0.045) tint must never come back.
    assert "background: var(--surface" in base, (
        "the rail background must be an OPAQUE surface token (it floats over content "
        "as a drawer); a translucent rgba tint lets the page bleed through it"
    )


def test_desktop_and_mobile_breakpoints_are_complementary():
    desktop_min, mobile_max = _breakpoints(_launchpad_html())
    assert mobile_max + 1 == desktop_min, (
        f"main reserves the rail width at >= {desktop_min}px but drops it at "
        f"<= {mobile_max}px — the reservation must hand off exactly at the rail "
        f"breakpoint (got a {mobile_max + 1}..{desktop_min - 1}px gap/overlap)"
    )


def test_desktop_shows_rail_mobile_opens_as_drawer():
    """Desktop slides the rail in (translateX(0)); narrow opens it as a drawer via
    body.rail-open. Both must be present or the nav is unreachable at some width."""
    html = _launchpad_html()
    assert re.search(
        r"@media \(min-width:\s*1024px\)\s*\{[^@]*?\.council-rail\s*\{\s*transform:\s*translateX\(0\)",
        html,
    ), "desktop (min-width: 1024px) must slide the rail in (translateX(0))"
    assert re.search(
        r"body\.rail-open\s+\.council-rail\s*\{\s*transform:\s*translateX\(0\)", html
    ), "the hamburger drawer (body.rail-open .council-rail { translateX(0) }) is missing"


def test_rail_width_token_defined():
    """The rail width is a single shared token (--rail-w) so main's reservation and
    the rail's own width can't drift apart (the old failure mode: 264 vs 264)."""
    html = _launchpad_html()
    assert re.search(r"--rail-w:\s*\d+px", html), "the --rail-w token must be defined in :root"
