"""Browser guard: the /stats CROSS-PROVIDER BENCHMARK card prose must name the
provider trio with the MODEL BRAND (Claude / GPT / Gemini), never the raw
dispatch slugs (Codex / Antigravity).

Founder lineage — the #275 raw-slug-display directive ([[raw_slug_display_275_scope]]):
council + launchpad surfaces flipped Codex/Antigravity → GPT/Gemini, and the rule
codified in CLAUDE.md is "use MODEL NAMES in user-facing UI" (slugs stay only in
code / config / file paths / JSON keys / literal CLI commands the user pastes).

The cheat-sheet HEADERS and chart LEGEND were branded + guarded
(test_launchpad_provider_label_brand_browser.py). But the eval-empty-state
"Score the 3 providers on YOUR corpus" benchmark card carried a SEPARATE,
hand-written prose line that still read "Trinity scores Claude / Codex /
Antigravity against your own rejection patterns" — the dispatch slugs leaking
into user-facing marketing copy, sitting directly beneath the routing chips that
already render "GPT" / "Gemini". A reader sees two names for the same labs, and
"Antigravity" in particular is an internal slug no end user has ever seen branded.

Driven 2026-06-19 in the REAL side panel (/stats view) + reproduced on the
file:// /stats render: the benchmark card prose rendered the slugs. This guard
pins the RENDERED card text to the brand trio so a future copy edit that
re-introduces a slug ("Codex" / "Antigravity") in this card's prose reds.

NOTE the scope: the eval-run CLI chips ("… --target codex" / "… --target
antigravity") legitimately render the slug — they are literal commands the user
pastes, parsed by config.providers — so the assertion targets ONLY the benchmark
DESCRIPTION paragraph (the "scores … against your own rejection patterns" prose),
not the command chips.

Mutation-provable: revert the prose to "Claude / Codex / Antigravity" and the
brand-presence + no-slug assertions red. Slow + browser marked; skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# evalSummary present + no results → the cross-provider benchmark empty-state card
# renders (its prose line is unconditional within the card, above every sub-state).
_PAGE_DATA = {
    "evalSummary": {
        "has_results": False,
        "rejections_available": False,
        "eval_set_available": False,
    },
}


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _write_prod_layout(html: str, serve_root: Path) -> str:
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return "portal_pages/launchpad.html"


# Pull the benchmark card's DESCRIPTION paragraph by content (the line that names
# the trio), so the assertion follows the copy even if its DOM position shifts.
_PROBE = """() => {
  const card = document.querySelector('.eval-empty-state-card');
  if (!card) return { found: false };
  // The trio-naming prose sentence: the first <p> mentioning "Global benchmarks".
  const paras = [...card.querySelectorAll('p')].map(p => (p.textContent || '').trim());
  const prose = paras.find(t => /Global benchmarks/.test(t)) || '';
  return { found: true, prose };
}"""


def test_benchmark_card_prose_uses_model_brand_not_slug(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data=_PAGE_DATA, view="stats")
    rel = _write_prod_layout(html, tmp_path)
    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_selector(".eval-empty-state-card", timeout=10000)
                s = page.evaluate(_PROBE)

                assert s["found"], (
                    "the cross-provider benchmark (eval-empty-state) card did not render — "
                    "the evalSummary empty-state fixture is wrong"
                )
                prose = s["prose"]
                assert prose, (
                    "the benchmark card's trio-naming prose ('Global benchmarks …') did not "
                    "render — the surface under test is missing"
                )

                # The card must name the provider trio with the model BRAND.
                for brand in ("Claude", "GPT", "Gemini"):
                    assert brand in prose, (
                        f"the benchmark card prose must name the trio by model BRAND "
                        f"('{brand}' missing) — got: {prose!r}"
                    )

                # And must NOT leak the dispatch slugs into user-facing prose (the
                # #275 slug-in-copy regression — 'Antigravity' is the slug no user
                # has seen branded; 'Codex' here is the slug, not the CLI command).
                for slug in ("Antigravity", "Codex"):
                    assert slug not in prose, (
                        f"the benchmark card prose leaked the raw dispatch slug '{slug}' "
                        f"into user-facing copy (the #275 'scores Claude / Codex / "
                        f"Antigravity' regression) — got: {prose!r}"
                    )

                assert not errs, f"JS errors rendering the benchmark card: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
