"""Browser guard: the COLD (empty-home) /stats VIEW — the analytics/diagnostics
face a brand-new user reaches via "View full stats & analytics →" before building
anything — must render every "what Trinity will learn" card as an HONEST teaching
empty-state, never promising data it can't show on a fresh install.

Coverage gap this fills: `test_launchpad_cold_start_browser` renders the cold launchpad
HOME view (the council/Ask painkiller). But the cards that are MOST prone to the
"promise data it can't show" cold-start trap — routing ("which model wins"), the
cheat-sheet, the cross-provider benchmark, and the four cognitive-memory file chips
(core/lens/topics/vocabulary) — all live on the /stats VIEW, and NO cold-home
end-to-end browser test renders them from a genuinely empty home via the real
`render_launchpad_html(view="stats")` → `build_page_data()` path. Every stats test
on disk seeds a POPULATED home (eval sets, picks, captures). So a new card — or a
data-shape change that leaks a degraded value, dead-ends, or pre-renders a fake
"from your own N councils" count on a fresh install — would slip past on the
make-or-break first-run /stats surface.

Renders the REAL cold /stats (empty TRINITY_HOME, autoscan off) over http and pins:
  • no uncaught JS / console errors,
  • the petite-vue shell mounted (not a blank pre-mount page),
  • no `{{ }}` template leak, no `undefined` / `NaN` / `[object Object]` in the
    visible text (degraded-data leaking to the UI),
  • the routing card shows its HONEST empty-state ("after a few councils"), NOT a
    pre-rendered "from your own 0 councils" data promise,
  • the cold first-run CTAs that teach the next step are present (`trinity-local lens`
    + `consolidate`) — so a new user has an obvious build path,
  • the four cognitive-memory file chips render (their click → honest "Not built yet"
    empty-state is separately guarded by the memory-viewer cold-start tests).

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_cold_start_stats_renders_honest_and_error_free(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # A genuinely empty home — the brand-new-install state. autoscan off so the
    # render can't kick a background lens build mid-test.
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # The REAL /stats view from a cold home, via the live builder.
    html = render_launchpad_html(view="stats")
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.on(
                    "console",
                    lambda m: errs.append("console.error: " + m.text[:200])
                    if m.type == "error"
                    and "favicon" not in m.text.lower()
                    and "woff" not in m.text.lower()
                    else None,
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1200)

                assert not errs, f"cold /stats threw JS errors: {errs[:4]}"

                # the petite-vue shell mounted — not a broken/blank pre-mount page
                assert page.query_selector(".launchpad-shell") is not None, (
                    "the .launchpad-shell didn't mount on a cold /stats — the first-run "
                    "analytics render is broken"
                )

                body = page.evaluate("document.body.innerText")
                assert len(body) > 200, (
                    f"cold /stats rendered a near-blank page ({len(body)} chars) — "
                    "a broken empty-home render, not a useful first-run state"
                )
                low = body.lower()

                # degraded-data / template leaks must NOT reach the visible UI.
                leaks = [
                    tok for tok in ("{{", "}}", "undefined", "nan", "[object object]")
                    if tok in low
                ]
                assert not leaks, (
                    f"cold /stats leaked {leaks} into the visible text — a template "
                    "directive or degraded-data value reached the first-run UI"
                )

                # the routing card must show its HONEST empty-state, NOT a pre-rendered
                # data promise. "from your own 0 councils" is the exact orphan-value
                # shape (a card promising data it can't have on a fresh install).
                assert "from your own 0 councils" not in low, (
                    "cold /stats promised 'from your own 0 councils' — a card rendered "
                    "a populated-data line it has no data to back on a fresh install"
                )
                assert "after a few councils" in low, (
                    "cold /stats lost its honest routing empty-state ('after a few "
                    "councils') — the first-run routing card no longer teaches the "
                    "prerequisite before promising a result"
                )

                # cold first-run CTAs that teach the build path must be present, so a
                # brand-new user has an obvious next step (not a dead-end).
                assert "trinity-local lens" in low, (
                    "cold /stats shows no 'trinity-local lens' CTA — a new user has no "
                    "obvious lens-build next step"
                )
                assert "consolidate" in low, (
                    "cold /stats shows no 'consolidate' teaching CTA — the routing "
                    "empty-state no longer names the verb that builds the picks"
                )

                # the four cognitive-memory file chips render (their click → honest
                # 'Not built yet' empty-state is guarded by the memory-viewer cold tests).
                chips = page.eval_on_selector_all(
                    ".memory-chip code", "els => els.map(e => e.innerText.trim())"
                )
                for fname in ("core.md", "lens.md", "topics.json", "vocabulary.md"):
                    assert fname in chips, (
                        f"cold /stats dropped the {fname} cognitive-memory chip — the "
                        f"'four files that compose your cognitive memory' card is "
                        f"incomplete on a fresh install (chips: {chips})"
                    )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_cold_stats_shows_exactly_one_routing_empty_state_card(tmp_path, monkeypatch):
    """COLD /stats must NOT stack TWO back-to-back 'Routing'-eyebrow cards.

    Founder symptom (found driving cold /stats, Iter 188): on a fresh install the
    /stats view rendered TWO consecutive cards under the same `Routing` eyebrow —
    the CHART card "Which model wins for which kind of question" (degraded to a bare
    "Run a few councils via `trinity-local council --task ...`" paragraph) AND the
    dedicated empty-state card "Run a few councils to learn which model works best
    for you" (same command, same "bars sharpen with every council" line). Same
    eyebrow, same CTA, stacked — a REDUNDANT pair. The chart card is now gated on the
    routing data EXISTING (`v-if personalRoutingTable && councils_aggregated`) so the
    dedicated empty-state card OWNS the cold state and the two never co-render.

    BITES the regression two ways: (1) exactly ONE visible Routing-eyebrow card in the
    cold state, and (2) the surviving one is the dedicated empty-state (its "learn
    which model works best for you" headline), NOT the chart card degrading to a
    duplicate paragraph. Drop the chart-card gate and BOTH cards render → count 2 → RED.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    html = render_launchpad_html(view="stats")
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 900, "height": 1600}
                ).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1200)

                # Collect every VISIBLE stats card whose eyebrow reads "Routing".
                routing = page.evaluate(
                    """() => Array.from(
                        document.querySelectorAll('section.card, article.card'))
                      .filter(c => c.offsetParent !== null
                                   && c.getBoundingClientRect().height > 0)
                      .map(c => {
                        const eb = c.querySelector('.eyebrow');
                        const h = c.querySelector('h1,h2,h3');
                        return { eyebrow: eb ? eb.innerText.trim() : '',
                                 head: h ? h.innerText.trim() : '' };
                      })
                      .filter(c => c.eyebrow.toLowerCase() === 'routing')"""
                )

                # PRECONDITION (non-vacuous): on a cold home at least one Routing card
                # must exist — so the bite is the DUPLICATE, not a missing/seed artifact.
                assert len(routing) >= 1, (
                    "cold /stats rendered NO 'Routing'-eyebrow card — the routing "
                    "empty-state vanished entirely (seed/render regression, not the "
                    "duplicate this guards)"
                )

                # THE BITE: exactly ONE Routing card on a cold install.
                assert len(routing) == 1, (
                    "cold /stats stacked %d 'Routing'-eyebrow cards back-to-back — the "
                    "REDUNDANT pair regressed: the chart card 'Which model wins for "
                    "which kind of question' degraded to a duplicate 'run a few "
                    "councils' paragraph beside the dedicated empty-state card. "
                    "Cards: %r" % (len(routing), routing)
                )

                # The survivor must be the DEDICATED empty-state (the one with the
                # copy-command CTA), not the chart card degrading to a bare paragraph.
                head = routing[0]["head"].lower()
                assert "learn which model works best for you" in head, (
                    "the surviving cold Routing card is the chart card "
                    "('%s'), not the dedicated empty-state — the chart card should be "
                    "hidden until there's routing data to plot" % routing[0]["head"]
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
