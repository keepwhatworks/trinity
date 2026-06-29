"""Browser guard for the launchpad "simple home + separate /stats page" split.

The founder wants the main launchpad dead-simple (only the cross-provider COUNCIL
action + the lens upsell), with all analytics/diagnostics moved to a dedicated
/stats page — modeled on how openrouter.ai/fusion's main page is one clear action.

The split is implemented as a CSS view-class, NOT a physical section cut:
`render_launchpad_html(..., view="home"|"stats")` stamps `lp-view-{view}` on the
shell, and two CSS rules hide the other set:
    .lp-view-home  .stats-card {{ display: none !important; }}
    .lp-view-stats .home-card  {{ display: none !important; }}
Every `<section>` stays in the HTML string (so the string-presence unit tests stay
green) — only VISIBILITY toggles. That visibility only resolves in a real browser
(computed style / layout), so a regression that mistags a card, drops the view
class, or removes a CSS rule passes every non-browser test and ships a broken
split. This pins it in headless Chromium.

Invariants (mutation-provable):
  HOME view —
    - the council dispatch textarea (`#council-prompt`, a `home-card`) is VISIBLE.
    - a `stats-card` (the unconditional memory-chips card, "four files…") is in
      the DOM but NOT visible.
    - the "View full stats & analytics →" home→/stats nav link is present.
  STATS view —
    - the council dispatch (`#council-prompt`, a `home-card`) is HIDDEN.
    - a `stats-card` (memory-chips) is VISIBLE.
    - the "← Back to the council" stats→home nav link is present.

Slow + browser marked; skips cleanly when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _build_page_data(tmp_home: Path, monkeypatch) -> dict:
    """Build a real launchpad page_data against an isolated TRINITY_HOME so the
    stats cards have content. A cold home is enough for the unconditional cards
    we target (the council dispatch + the memory-chips card both render with no
    v-if gate), so this stays fast and deterministic."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    from trinity_local.launchpad_data import build_page_data

    return build_page_data(live_review_path=None, recent_councils=[])


def _write_prod_layout(html: str, serve_root: Path, name: str) -> str:
    """Mirror prod: page at portal_pages/<name>, vendor at portal_pages/vendor/.
    Both the shared-CSS @font-face (`../portal_pages/vendor/…`) and the page's own
    JS (`./vendor/…`) only resolve from the portal_pages/ depth."""
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / name).write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return f"portal_pages/{name}"


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# Probe run after petite-vue mounts. `_visible` mirrors Playwright's is_hidden()
# heuristic (laid-out + not display:none/visibility:hidden) via getComputedStyle +
# offsetParent, evaluated in-page so we read the SAME element on both views.
_PROBE = """() => {
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const dispatch = document.querySelector('#council-prompt');
  // The memory-chips card is an unconditional stats-card (no v-if) — its <h2>
  // is "The four files that compose your cognitive memory".
  const statsCard = [...document.querySelectorAll('section.stats-card h2')]
    .map(h => h.closest('section.stats-card'))
    .find(s => /four files that compose/i.test(s.textContent));
  const body = document.body.textContent || '';
  return {
    dispatchInDom: !!dispatch,
    dispatchVisible: visible(dispatch),
    statsCardInDom: !!statsCard,
    statsCardVisible: visible(statsCard),
    homeNavLink: /View full stats & analytics/.test(body),
    backLink: /Back to the council/.test(body),
    leak: /\\{\\{|\\}\\}/.test(document.body.innerText || ''),
  };
}"""


def _probe_view(monkeypatch, tmp_path: Path, view: str) -> dict:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_template import render_launchpad_html

    page_data = _build_page_data(tmp_path / "home", monkeypatch)
    html = render_launchpad_html(page_data=page_data, view=view)
    serve_root = tmp_path / "serve"
    rel = _write_prod_layout(html, serve_root, f"{view}.html")
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1100, "height": 1400}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                # Never let a click reach the founder's live extension.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}",
                    wait_until="networkidle",
                    timeout=20000,
                )
                # Let petite-vue mount. Wait for ATTACHED (not visible): on the
                # home view every stats-card is display:none, so a default
                # visibility-wait would time out — the split working is exactly
                # why it's hidden. The memory-chips stats-card renders
                # unconditionally (no v-if), so it's attached on both views.
                page.wait_for_selector(
                    "section.stats-card", state="attached", timeout=10000
                )
                page.wait_for_timeout(300)
                state = page.evaluate(_PROBE)
                state["_errs"] = errs
            finally:
                browser.close()
    finally:
        httpd.shutdown()
    return state


def test_home_view_hides_stats_shows_council(tmp_path, monkeypatch):
    """view=home: the council dispatch is visible; a stats-card is in the DOM
    (the string survives, so unit tests stay green) but NOT visible."""
    s = _probe_view(monkeypatch, tmp_path, "home")
    assert not s["_errs"], f"JS errors rendering the home launchpad: {s['_errs'][:4]}"
    assert s["dispatchInDom"], "council dispatch textarea missing from the home DOM"
    assert s["dispatchVisible"], "the council dispatch must be VISIBLE on the simple home page"
    assert s["statsCardInDom"], (
        "a stats-card must still be PRESENT in the home HTML string (the split is "
        "CSS visibility, not a section cut — string-presence unit tests rely on it)"
    )
    assert not s["statsCardVisible"], (
        "an analytics stats-card must be HIDDEN on the simple home page "
        "(the .lp-view-home .stats-card rule is broken)"
    )
    assert s["homeNavLink"], "the home → /stats nav link ('View full stats & analytics') is missing"
    assert not s["leak"], "petite-vue template leak ({{ }}) on the home launchpad"


def test_stats_view_hides_council_shows_stats(tmp_path, monkeypatch):
    """view=stats: the council dispatch (a home-card) is hidden; a stats-card is
    visible; the back-to-council link is present."""
    s = _probe_view(monkeypatch, tmp_path, "stats")
    assert not s["_errs"], f"JS errors rendering the stats page: {s['_errs'][:4]}"
    assert s["dispatchInDom"], "council dispatch textarea missing from the stats DOM"
    assert not s["dispatchVisible"], (
        "the council dispatch (a home-card) must be HIDDEN on /stats "
        "(the .lp-view-stats .home-card rule is broken)"
    )
    assert s["statsCardInDom"], "a stats-card must be present in the stats DOM"
    assert s["statsCardVisible"], (
        "an analytics stats-card must be VISIBLE on /stats — the whole point of "
        "the page"
    )
    assert s["backLink"], "the stats → home '← Back to the council' link is missing"
    assert not s["leak"], "petite-vue template leak ({{ }}) on the stats page"


# Side-panel (<=560px, e.g. the Chrome extension side panel) action-first layout.
# The narrow `@media (max-width: 560px)` block flips the page so the user lands on
# the ACTION: the councils rail drops BELOW the launchpad, the hero compacts, and
# the council dispatch is pulled directly under the hero (the proof/evidence cards
# follow it). All three are pure CSS layout (flex order / body-flex), so they only
# resolve in a real browser at a real width — a regression that drops the
# breakpoint or the order rules passes every non-browser test and re-buries the
# action under the rail (the exact bug this fixes).
_GEO_PROBE = """() => {
  const top = (sel) => {
    const e = document.querySelector(sel);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) ? r.top : null;
  };
  const railEl = document.querySelector('.council-rail');
  return {
    hero: top('section.hero-shell'),
    dispatch: top('#council-prompt'),
    proof: top('section.hero-proof'),
    // The rail is now an off-canvas drawer (transform: translateX(-100%)); when
    // closed its right edge sits at/left of x=0, so it contributes nothing to the
    // fold. (Old contract: rail stacked BELOW main — replaced by the drawer.)
    railRight: railEl ? railEl.getBoundingClientRect().right : null,
  };
}"""


def test_sidepanel_action_first_layout(tmp_path, monkeypatch):
    """At side-panel width (393px) the council dispatch must sit ABOVE both the
    councils rail (history dropped below the launchpad) and the proof/evidence
    cards (action-first), with the compact hero still leading."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_template import render_launchpad_html

    page_data = _build_page_data(tmp_path / "home", monkeypatch)
    # Inject the two proof signals so the hero-proof card renders (it self-hides
    # on a cold home) — needed to assert dispatch-above-proof ordering.
    page_data["coldOpen"] = "Your taste in three words: concrete, action."
    page_data["councilValue"] = {
        "councils": 42, "changedPct": 50,
        "wins": [{"label": "Claude", "pct": 60}, {"label": "GPT", "pct": 40}],
        "wedge": [],
    }
    html = render_launchpad_html(page_data=page_data, view="home")
    serve_root = tmp_path / "serve"
    rel = _write_prod_layout(html, serve_root, "home.html")
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 393, "height": 880}
                ).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_selector("#council-prompt", state="attached", timeout=10000)
                page.wait_for_timeout(300)
                g = page.evaluate(_GEO_PROBE)
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert g["hero"] is not None, "hero not laid out at side-panel width"
    assert g["dispatch"] is not None, "council dispatch not laid out at side-panel width"
    assert g["railRight"] is not None, "councils rail missing from the DOM"
    assert g["proof"] is not None, "proof card should render when coldOpen/councilValue are set"
    assert g["hero"] < g["dispatch"], "the compact hero must lead, above the dispatch"
    assert g["railRight"] <= 1, (
        "the councils rail must be OFF-CANVAS (drawer closed) at side-panel width — "
        f"its right edge is at x={g['railRight']}, not <=0; a visible rail re-buries "
        "the action like the original side-panel bug"
    )
    assert g["dispatch"] < g["proof"], (
        "the council dispatch must sit ABOVE the proof/evidence cards "
        "(the action-first order rule is broken)"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
