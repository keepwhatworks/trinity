"""The in-content council spinner must HONOR `prefers-reduced-motion: reduce`.

Found 2026-06-19 driving the launchpad under an emulated OS "reduce motion"
setting (the WCAG 2.3.3 vestibular-disorder accommodation). The side-panel SHELL
spinner (`#loading .spinner` in sidepanel.html) already had a reduced-motion guard
that stops it — but the IN-CONTENT `.spinner` rendered by the launchpad's
launch-status row + the council review/live-council pages spins
`animation: trinity-spin 0.8s linear infinite` for the ENTIRE multi-minute council
run (the longest-lived motion on the page) with NO reduced-motion override. The
only prior reduced-motion rules in the SHARED CSS were `scroll-behavior: auto` and
one `.council-rail` transition — neither touched the spinner — so a user who set
"reduce motion" watched the shell spinner stop while the in-page council spinner
kept whirling for minutes.

The fix lives in `design_system.py` SHARED_CSS (the `@media
(prefers-reduced-motion: reduce)` block neutralizes every keyframe animation +
collapses decorative transitions), so it covers the launchpad, council review, and
review pages in one rule — the whole spinner class, not just this instance.

This guard drives the file:// launchpad (the SHARED-CSS surface the side panel
renders too) under BOTH motion preferences, injects the real `.spinner-row >
.spinner` markup the council uses, and asserts:
  * with `reduced_motion="reduce"`: the spinner's computed transform is STABLE
    across a sampling window (it is frozen to a static busy ring), AND
    animation-iteration-count is no longer `infinite` — the bite.
  * with `no-preference`: the SAME spinner's transform CHANGES across the window
    (it really rotates) — proving the test isn't vacuously asserting "no motion"
    on a non-animating element.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_launchpad(tmp_path) -> Path:
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(tmp_path)
    import trinity_local.launchpad_page as lp  # noqa: E402
    import trinity_local.vendor as vendor  # noqa: E402

    pages = lp.write_portal_html().parent
    try:
        vendor.publish_vendor_files(pages)
    except Exception:
        pass
    return pages / "launchpad.html"


# JS: drop a real council spinner into the page, then report whether its computed
# transform moved across a brief window + its animation-iteration-count.
_INJECT = """
() => {
  const row = document.createElement('div');
  row.className = 'spinner-row';
  const sp = document.createElement('span');
  sp.className = 'spinner';
  sp.id = '__probe_spinner';
  row.appendChild(sp);
  document.body.appendChild(row);
  const cs = getComputedStyle(sp);
  return { animIter: cs.animationIterationCount, animName: cs.animationName };
}
"""


def _spinner_state(page) -> dict:
    meta = page.evaluate(_INJECT)
    t1 = page.evaluate(
        "() => getComputedStyle(document.getElementById('__probe_spinner')).transform"
    )
    page.wait_for_timeout(300)
    t2 = page.evaluate(
        "() => getComputedStyle(document.getElementById('__probe_spinner')).transform"
    )
    meta["moved"] = t1 != t2
    meta["t1"] = t1
    meta["t2"] = t2
    return meta


def test_in_content_spinner_freezes_under_reduced_motion(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    page_path = _render_launchpad(tmp_path)
    url = f"file://{page_path}"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            # NON-VACUOUS control: without the preference the SAME spinner rotates.
            ctx_n = browser.new_context(
                viewport={"width": 900, "height": 900}, reduced_motion="no-preference"
            )
            page_n = ctx_n.new_page()
            page_n.goto(url, wait_until="load")
            page_n.wait_for_timeout(800)
            normal = _spinner_state(page_n)
            ctx_n.close()
            assert normal["animName"] == "trinity-spin", (
                "control spinner is not the council `.spinner` (trinity-spin) — the "
                f"test would be vacuous: got animation-name {normal['animName']!r}"
            )
            assert normal["moved"], (
                "the spinner did NOT rotate even WITHOUT reduced-motion — the probe "
                "isn't a live animation, so a frozen reduced-motion spinner proves "
                f"nothing. transform stayed {normal['t1']!r}"
            )

            # THE BITE: with reduced-motion the spinner must be frozen.
            ctx_r = browser.new_context(
                viewport={"width": 900, "height": 900}, reduced_motion="reduce"
            )
            page_r = ctx_r.new_page()
            page_r.goto(url, wait_until="load")
            page_r.wait_for_timeout(800)
            reduced = _spinner_state(page_r)
            ctx_r.close()

            assert not reduced["moved"], (
                "the in-content council `.spinner` KEPT ROTATING under "
                "prefers-reduced-motion:reduce — the shell spinner stopped but the "
                "multi-minute in-page council spinner ignored the user's OS "
                f"'reduce motion' setting (WCAG 2.3.3). transform moved "
                f"{reduced['t1']!r} -> {reduced['t2']!r}"
            )
            assert reduced["animIter"] != "infinite", (
                "the spinner still declares animation-iteration-count:infinite under "
                "reduced-motion — the reduced-motion override in SHARED_CSS did not "
                f"reach it: got {reduced['animIter']!r}"
            )
        finally:
            browser.close()
