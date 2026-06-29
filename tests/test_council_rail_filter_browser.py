"""Browser guard for the council-rail SEARCH FILTER behavior.

The rail is the single home for the full council list (305+ on the founder's
machine), so its client-side title filter is the load-bearing way to find a
council. `test_council_sidebar.py` guards this at the STRING layer — the search
box exists (`id="rail-filter"`), the `data-title` attrs are lowercased, the JS is
wired — but nothing types into the box and checks that rows actually narrow.

The regression modes string tests miss are real: the filter's
`querySelectorAll('.council-rail .rail-council')` selector can drift from the
emitted class (rows always show), `includes` can become `startsWith`, or the
`#rail-no-match` display logic can break (no "0 results" feedback). This exercises
the filter where it runs: a term narrows to exactly the matching rows, a
non-matching term shows the no-match message, and clearing restores everything.

Synthetic councils via a monkeypatched `_load_recent_councils` (known titles, no
PII, no disk seeding). Slow-marked (portal-html render + chromium); skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


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


_SYNTHETIC = [
    _council("alpha workflow design", 1),
    _council("beta workflow", 2),
    _council("gamma alpha review", 3),
]

# A LONG history (more than fills the rail's 100vh scroll viewport) so the
# scroll-the-filter-off-screen regression is reachable. Titles vary so they
# don't collapse; only the count matters for the sticky-header geometry guard.
_MANY = [
    _council(f"council number {n} about some topic area", n)
    for n in range(1, 41)
]


def test_rail_filter_narrows_shows_no_match_and_restores(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    # The rail is built from _load_recent_councils inside render_launchpad_html;
    # feed it known synthetic titles so the filter assertions are deterministic.
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
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            def apply(term: str) -> dict:
                page.evaluate(
                    "(t) => { const i = document.getElementById('rail-filter'); "
                    "i.value = t; i.dispatchEvent(new Event('input', {bubbles:true})); }",
                    term,
                )
                page.wait_for_timeout(150)
                return page.evaluate(
                    "() => { const rows = [...document.querySelectorAll('.council-rail .rail-council')]; "
                    "const shown = rows.filter(r => r.style.display !== 'none'); "
                    "const nm = document.getElementById('rail-no-match'); "
                    "const term = (document.getElementById('rail-filter').value||'').toLowerCase(); "
                    "return { total: rows.length, shown: shown.length, "
                    "allShownMatch: shown.every(r => (r.getAttribute('data-title')||'').includes(term)), "
                    "noMatchVisible: nm ? nm.style.display !== 'none' : null }; }"
                )

            base = apply("")
            assert base["total"] == 3, f"expected 3 synthetic rail rows, got {base['total']}"
            assert base["shown"] == 3 and base["noMatchVisible"] is False

            r = apply("alpha")  # matches "alpha workflow design" + "gamma alpha review"
            assert r["shown"] == 2 and r["allShownMatch"], r
            assert r["noMatchVisible"] is False

            r = apply("beta")  # matches only "beta workflow"
            assert r["shown"] == 1 and r["allShownMatch"], r

            r = apply("workflow")  # matches the two "workflow" titles
            assert r["shown"] == 2 and r["allShownMatch"], r

            r = apply("zzqq_nomatch")  # matches nothing → no-match message shows
            assert r["shown"] == 0, r
            assert r["noMatchVisible"] is True, "the rail-no-match message did not appear on 0 results"

            r = apply("")  # cleared → everything restored
            assert r["shown"] == 3 and r["noMatchVisible"] is False, r

            assert not errs, f"JS errors during rail filter: {errs[:3]}"
        finally:
            browser.close()


def test_rail_filter_stays_pinned_when_history_is_scrolled(tmp_path, monkeypatch):
    """The search box must stay reachable no matter how far the council list is
    scrolled. Founder symptom: with a long history (305+ councils on the founder's
    machine) the user scrolled the rail and the search box was simply GONE — it
    scrolled off the top and there was no way to filter without scrolling all the
    way back up. The fix pins the heading + search (.rail-header) sticky at the top
    of the scrolling rail. This guard scrolls a long rail to the bottom and asserts
    the filter is still on-screen (its top barely moves), and — as a non-vacuous
    control — that the long list actually overflowed (so the test is exercising a
    real scroll, not a list that fit)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import trinity_local.launchpad_page as lp

    monkeypatch.setattr(lp, "_load_recent_councils", lambda *a, **k: list(_MANY))
    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            # Panel-width (the worst case — narrow off-canvas drawer) AND a short
            # viewport so 40 councils certainly overflow the rail's 100vh.
            page = browser.new_context(viewport={"width": 375, "height": 700}).new_page()
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)
            # Open the off-canvas drawer (panel width) so the rail is interactable.
            page.evaluate(
                "() => { const b = document.querySelector('button.rail-toggle'); if (b) b.click(); }"
            )
            page.wait_for_timeout(300)

            geo = page.evaluate(
                "() => {"
                " const rail = document.querySelector('.council-rail');"
                " const filt = document.getElementById('rail-filter');"
                " const overflowed = rail.scrollHeight > rail.clientHeight + 8;"
                " const top0 = filt.getBoundingClientRect().top;"
                " rail.scrollTop = rail.scrollHeight;"  # scroll to the very bottom
                " return new Promise(res => requestAnimationFrame(() => {"
                "   const fr = filt.getBoundingClientRect();"
                "   res({ overflowed, top0,"
                "         topScrolled: fr.top,"
                "         onScreen: fr.bottom > 0 && fr.top < window.innerHeight,"
                "         scrolled: rail.scrollTop }); }));"
                "}"
            )

            # Non-vacuous control: the history actually overflowed and we really
            # scrolled — otherwise "still visible" proves nothing.
            assert geo["overflowed"], (
                "the synthetic 40-council rail did not overflow its scroll viewport — "
                f"the geometry guard would be vacuous ({geo!r})"
            )
            assert geo["scrolled"] > 200, (
                f"the rail never scrolled (scrollTop={geo['scrolled']}) — guard vacuous"
            )

            # THE BITE: after scrolling the long history to the bottom, the search
            # box is STILL on-screen and barely moved (it's pinned, not scrolled
            # away). On the un-fixed code the filter is a static child that scrolls
            # off the top to a large negative top → onScreen False.
            assert geo["onScreen"], (
                "the council-rail search box scrolled OFF-SCREEN when the history was "
                "scrolled to the bottom (founder: 'scrolled the councils list and the "
                "search box was just gone') — the .rail-header sticky pin regressed: "
                f"filter top moved {geo['top0']:.0f} -> {geo['topScrolled']:.0f}px"
            )
            assert abs(geo["topScrolled"] - geo["top0"]) <= 4, (
                "the council-rail search box did not stay PINNED while the history "
                f"scrolled (top {geo['top0']:.0f} -> {geo['topScrolled']:.0f}px, moved "
                f"{abs(geo['topScrolled'] - geo['top0']):.0f}px) — the .rail-header "
                "sticky pin regressed"
            )
        finally:
            browser.close()
