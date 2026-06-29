"""Browser guard for the PRIMARY icon-only nav controls' touch-target size.

Found 2026-06-18 (Iter 96, the a11y / interaction-mechanics dimension) measuring
every clickable control's rendered box in the REAL Chrome side panel @ 393px: the
two PRIMARY icon-only navigation affordances were both under the WCAG 2.5.5 /
2.5.8 44×44 minimum touch target —

  - the rail-toggle (☰) — the ONLY way to open the council-history drawer on
    mobile / the side panel — rendered 40×40 (4px short on both axes);
  - the settings gear (⚙) — the ONLY way to open the settings modal — rendered
    30×40 (14px short in width, 4px short in height).

A thumb on a phone / the narrow panel has to hit these two glyphs to navigate at
all, so an undersized box is a real, repeated mis-tap on the two most-used mobile
controls. (The fix bumps the rail-toggle to 44×44 in SHARED_CSS and the gear
button to min 44×44 via min-width/min-height + inline-flex centering. Secondary
inline text links/chips — `stats`, the basin chips, the rebuild chip — are NOT
inflated; that's a whole-UI call.)

Both fixes live in the SHARED launchpad template / design-system CSS, so the
file:// surface here exercises the exact same rendered box the side panel uses.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# WCAG 2.5.5 Target Size (Enhanced) / 2.5.8 minimum — a thumb-hit primary control.
MIN_TAP = 44


def _council(title: str, n: int) -> dict:
    return {
        "council_id": f"c{n}",
        "chain_root_id": f"bundle_{n}",
        "review_page_path": f"/x/review_pages/council_{n}.html",
        "title": title,
        "winner_provider": "claude",
        "created_at": f"2026-06-0{n}T00:00:00+00:00",
        "task_type": "design",
        "segment_count": 1,
    }


_SYNTHETIC = [_council("alpha workflow", 1), _council("beta workflow", 2)]


def test_primary_icon_only_nav_controls_meet_44px_tap_target(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import trinity_local.launchpad_page as lp

    monkeypatch.setattr(lp, "_load_recent_councils", lambda *a, **k: list(_SYNTHETIC))
    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            # 393px = the phone / Chrome-side-panel width where a thumb taps these.
            page.set_viewport_size({"width": 393, "height": 852})
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            # The two PRIMARY icon-only nav controls. Each is the SOLE affordance
            # for its action (open the drawer / open settings), so each must clear
            # 44×44 for a thumb. Measured WITH padding via getBoundingClientRect.
            boxes = page.evaluate(
                "() => {"
                "  const grab = (sel) => { const e = document.querySelector(sel);"
                "    if (!e) return null; const r = e.getBoundingClientRect();"
                "    return {w: Math.round(r.width), h: Math.round(r.height)}; };"
                "  return {"
                "    railToggle: grab('.rail-toggle'),"
                "    gear: grab('button[aria-label=\"Open settings\"]'),"
                "  };"
                "}"
            )

            rt = boxes["railToggle"]
            assert rt, "the rail-toggle (☰ council-drawer hamburger) was not rendered"
            assert rt["w"] >= MIN_TAP and rt["h"] >= MIN_TAP, (
                "the rail-toggle (the ONLY way to open the council-history drawer "
                "on mobile / the side panel) is a sub-44px tap target at 393px — "
                f"rendered {rt['w']}x{rt['h']} (needs >= {MIN_TAP}x{MIN_TAP}); a thumb "
                "mis-taps the primary nav control (Iter-96 a11y defect)"
            )

            gear = boxes["gear"]
            assert gear, "the settings gear (⚙, aria-label='Open settings') was not rendered"
            assert gear["w"] >= MIN_TAP and gear["h"] >= MIN_TAP, (
                "the settings gear (the ONLY way to open the settings modal) is a "
                f"sub-44px tap target at 393px — rendered {gear['w']}x{gear['h']} "
                f"(needs >= {MIN_TAP}x{MIN_TAP}); a thumb mis-taps the primary settings "
                "control (Iter-96 a11y defect)"
            )
        finally:
            browser.close()
