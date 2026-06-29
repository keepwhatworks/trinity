"""Browser guard: the COLD (empty-home) LAUNCHPAD HOME must make ZERO claims about
data that doesn't exist yet — the brand-new installer's first screen cannot fabricate
a "your taste / your councils" stat.

Coverage gap this fills: the existing cold-start home guard
(test_launchpad_cold_start_browser) pins that the page renders, mounts, doesn't leak
templates, and shows a council CTA — but it never asserts the INVERSE: that the
data-claim cards (the #212 cold-start aha "🪞 ONE surprising true tension" and the
#236 council-value-proof "⚖ Across your N councils, the synthesized winner differed
from your default X% — that's how often a single-tab habit would have shipped the
worse answer") are ABSENT on a genuinely empty home. The complementary tests prove the
PRESENT case: test_launchpad_cold_open_render_browser seeds a populated `coldOpen` and
asserts the card RENDERS; test_council_value_proof_value_floor gates the value-claim at
n>=10. NOTHING pins that a brand-new install (no transcripts, no councils, no lens)
shows neither claim.

This is the "(d) overclaim" first-run failure mode: a data-builder regression that
emits a placeholder `coldOpen` string or a zero-filled `councilValue` dict on an empty
home would paint a fabricated "🪞 your tension" / "⚖ across your 0 councils … single-tab
habit would have shipped the worse answer" claim to a user who has run nothing — a
self-defeating, dishonest first screen — while every existing test stays green (the
present-case tests seed data; the cold-start test only checks for leaks/CTA, not for
the ABSENCE of an unearned claim).

Renders the REAL cold-start home (empty TRINITY_HOME, autoscan off) over http and pins,
at 1280 + 393:
  • the petite-vue shell mounts and the council/Ask first-run CTA is present (so the
    assert isn't vacuous on a blank page),
  • the `.hero-proof` data-claim section is ABSENT from the DOM (both its `.cold-open`
    and `.council-value` children gone),
  • none of the overclaim text ("🪞", "across your … councils", "single-tab",
    "differed from your default") reaches the visible UI.

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


@pytest.mark.parametrize("width", [1280, 393])
def test_cold_start_home_makes_no_unearned_data_claim(tmp_path, monkeypatch, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # A genuinely empty home — the brand-new-install state. autoscan off so the
    # render can't kick a background lens build mid-test.
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    html = render_launchpad_html()  # live builder → build_page_data() on the empty home
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
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
                    viewport={"width": width, "height": 1600}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.on(
                    "console",
                    lambda m: errs.append("console.error: " + m.text[:200])
                    if m.type == "error" and "favicon" not in m.text.lower()
                    else None,
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_timeout(1000)

                assert not errs, f"cold-start home threw JS errors: {errs[:4]}"

                # Precondition (non-vacuous): the shell mounted and the first-run
                # council CTA is present — so an absent hero-proof card means the
                # card genuinely self-hid, not that the page failed to render.
                state = page.evaluate(
                    """() => {
                      const body = document.body.innerText || '';
                      return {
                        shell: !!document.querySelector('.launchpad-shell'),
                        bodyLen: body.length,
                        hasCouncilCta: /council|ask/i.test(body),
                        heroProofInDom: !!document.querySelector('.hero-proof'),
                        coldOpenInDom: !!document.querySelector('.cold-open'),
                        councilValueInDom: !!document.querySelector('.council-value'),
                        hasMirror: body.includes('\\u{1FA9E}'),
                        hasAcrossYour: /across your/i.test(body)
                                       && /councils/i.test(body),
                        hasSingleTab: /single-tab/i.test(body),
                        hasDifferedDefault: /differed from your default/i.test(body),
                      };
                    }"""
                )

                assert state["shell"], (
                    "the .launchpad-shell didn't mount on a cold home — precondition "
                    "for the no-overclaim assert failed (broken render)"
                )
                assert state["bodyLen"] > 200, (
                    f"cold home rendered near-blank ({state['bodyLen']} chars) — "
                    "precondition for the no-overclaim assert failed"
                )
                assert state["hasCouncilCta"], (
                    "cold home shows no council/Ask first-run CTA — precondition for "
                    "the no-overclaim assert failed (nothing to render)"
                )

                # THE INVARIANT: a brand-new install must make NO unearned data claim.
                assert not state["heroProofInDom"], (
                    "the .hero-proof data-claim card RENDERED on a genuinely empty "
                    "home — a brand-new installer (no transcripts, no councils) is "
                    "seeing a fabricated 'your taste / your councils' claim "
                    "(coldOpen/councilValue must be null on a cold home → the card "
                    "self-hides). This is the '(d) overclaim' first-run failure mode."
                )
                assert not state["coldOpenInDom"], (
                    "the #212 cold-start aha card (.cold-open '🪞 ONE surprising true "
                    "tension') rendered on an empty home with no lens signal — an "
                    "unearned claim about taste the user hasn't built"
                )
                assert not state["councilValueInDom"], (
                    "the #236 council-value-proof card (.council-value) rendered on an "
                    "empty home with zero councils — a fabricated 'across your N "
                    "councils' claim"
                )
                assert not state["hasMirror"], (
                    "the cold home leaked the '🪞' aha marker with no lens signal"
                )
                assert not (
                    state["hasAcrossYour"]
                    or state["hasSingleTab"]
                    or state["hasDifferedDefault"]
                ), (
                    "the cold home shows council-value-proof overclaim text ('across "
                    "your N councils' / 'single-tab habit' / 'differed from your "
                    "default') with zero councils run — a self-defeating, dishonest "
                    "first screen"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
