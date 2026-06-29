"""Browser guard for the settings-modal × CLOSE button's touch-target size.

Found 2026-06-21 (Iter 197, the tap-target / interaction-mechanics dimension)
driving the OPEN settings modal at 393px: the × close button — the modal's
PRIMARY dismiss affordance on a touch surface (the side panel + phones, where
backdrop-click is far less discoverable than the visible ×) — rendered a bare
26×30px glyph. No min-width/min-height, no ::before hit-area extender. It was
the ONE icon-action the 44px-hit-area sweep missed, because it's a bare
inline-styled <button> (not a `.icon-action`, not a `.button`, not a
`.copy-badge`).

This is the ASYMMETRIC SIBLING of the settings gear: the gear that OPENS the
modal is 44×44 (test_launchpad_nav_tap_targets_browser), but the × that CLOSES
it was 26×30 — a thumb on a phone / the narrow panel mis-taps the dismiss
control. The founder's recorded directive: "every action button clears 44px on
touch widths."

The close button lives in the SHARED launchpad template, so the file:// surface
here exercises the exact same rendered box the real side panel uses (the box
size is pure CSS — not opaque-origin-dependent; the existing
test_sidepanel_settings_modal_close_browser already proves the × stays IN-VIEW
and clickable in the real panel, which is the orthogonal property).

Mutation-proven: reverting the fix (drop min-width/min-height:44px) renders the
× at 26×30 and this test goes RED with the founder symptom named.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# WCAG 2.5.5 Target Size (Enhanced) / 2.5.8 minimum — a thumb-hit action control.
MIN_TAP = 44


def test_settings_modal_close_x_meets_44px_tap_target(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import trinity_local.launchpad_page as lp

    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            # 393px = the phone / Chrome-side-panel width where a thumb taps this.
            page.set_viewport_size({"width": 393, "height": 852})
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            # Open the settings modal (the × only exists once the modal mounts).
            page.click("button[aria-label='Open settings']")
            page.wait_for_timeout(300)

            box = page.evaluate(
                "() => {"
                "  const e = document.querySelector("
                "    \"[aria-label='Close settings']\");"
                "  if (!e) return null;"
                "  const r = e.getBoundingClientRect();"
                "  return {w: Math.round(r.width), h: Math.round(r.height)};"
                "}"
            )
            assert box, (
                "the settings-modal × close button (aria-label='Close settings') "
                "did not render after opening the modal"
            )
            assert box["w"] >= MIN_TAP and box["h"] >= MIN_TAP, (
                "the settings-modal × CLOSE button (the modal's primary dismiss "
                "affordance on touch — the side panel + phones) is a sub-44px tap "
                f"target at 393px — rendered {box['w']}x{box['h']} (needs >= "
                f"{MIN_TAP}x{MIN_TAP}); the gear that OPENS this modal is already "
                "44x44, so the dismiss control was the asymmetric sub-floor sibling "
                "(Iter-197 tap-target defect)"
            )
        finally:
            browser.close()
