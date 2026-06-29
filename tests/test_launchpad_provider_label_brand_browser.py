"""Browser guard: the launchpad routing cheat-sheet COLUMN HEADERS and the
personal-preference CHART LEGEND must render the MODEL BRAND (Claude / GPT /
Gemini), never the raw uppercase dispatch slugs (CODEX / ANTIGRAVITY).

These are the two surfaces of the #275 self-caught MISS (commit 26d669d2): the
`formatProviderLabel` unification (a3f9cfac) branded the winner CELLS but left
the column headers + chart legend on a DIFFERENT path, `{{ provider.toUpperCase()
}}`, which renders CLAUDE / CODEX / ANTIGRAVITY. 26d669d2 swapped both to
`formatProviderLabel(provider)` — but touched ONLY launchpad_template.py (4
lines) and added NO regression test; it was "verified in real Chrome" by hand.

So a refactor that reverts either header/legend binding back to
`provider.toUpperCase()` (or adds a new provider column with the old pattern)
silently re-leaks CODEX / ANTIGRAVITY with ZERO test failures — the exact
[[raw_slug_display_275_scope]] trap: a raw-slug audit (and any string-presence
test) must match the RENDERED uppercase form, which only a JS engine produces.

This pins it by rendering the launchpad with a fixture whose routing data +
benchmark list both contain `codex` + `antigravity`, then asserting the rendered
DOM labels are branded. Mutation-provable: revert either binding to
`provider.toUpperCase()` and a header/legend reads "CODEX"/"ANTIGRAVITY" → reds.

The founder's local Ollama slugs (Gemma4local / Qwen) legitimately render raw
(no brand exists) — so the assertion targets ONLY the two providers that HAVE a
brand-vs-slug split (codex→GPT, antigravity→Gemini), not a blanket slug scan.

Slow + browser marked; skips when Playwright/chromium are absent. Found
2026-06-07 dogfooding the real launchpad (rawSlugLabels: [] confirmed live);
this codifies that invariant.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Routing + benchmark data carrying BOTH brand-split providers (codex,
# antigravity) plus claude. globalBenchmarks left unset → the bar chart skips
# (no console noise); the legend <strong>s render independently under
# councils_aggregated.
_PAGE_DATA = {
    "benchmarkProviders": ["claude", "codex", "antigravity"],
    "providerModels": {"claude": "Opus 4.8", "codex": "gpt-5.5", "antigravity": "Gemini 3.1 Pro"},
    "personalRoutingTable": {
        "councils_aggregated": 12,
        "by_task_type": {
            # Each provider cell renders `scores[provider].overall.toFixed(1)` + `.n`,
            # so both fields must be present or the table errors (fixture-completeness,
            # not the surface under test).
            "code_review": {
                "claude": {"overall": 7.4, "n": 9, "wins": 4},
                "codex": {"overall": 8.1, "n": 9, "wins": 3},
                "antigravity": {"overall": 6.8, "n": 9, "wins": 2},
            },
        },
        "cold_start": {"code_review": {"n_personal": 9, "alpha": 0.8, "personalization_pct": 80}},
        "best_per_task_type": {"code_review": "codex"},
        "wins_per_task_type": {"code_review": {"wins": 3, "total": 9}},
    },
}

# The two raw uppercase slug labels the #275 miss rendered. formatProviderLabel
# maps codex→GPT, antigravity→Gemini, so neither may appear as a header/legend.
_FORBIDDEN = {"CODEX", "ANTIGRAVITY"}

_PROBE = """() => {
  const headerTexts = [...document.querySelectorAll('table.routing-table thead th')]
    .map(th => (th.textContent || '').trim());
  // Legend <strong>s are bare provider labels; filter out non-provider <strong>
  // (e.g. the "Best" emphasis in the intro copy) by matching the brand set.
  const brandRe = /^(Claude|GPT|Gemini|Codex|Antigravity)$/i;
  const legendTexts = [...document.querySelectorAll('strong')]
    .map(s => (s.textContent || '').trim())
    .filter(t => brandRe.test(t));
  return { headerTexts, legendTexts };
}"""


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


def test_cheatsheet_headers_and_chart_legend_render_branded(tmp_path):
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
                page.on(
                    "console",
                    lambda m: errs.append(f"console.error: {m.text[:160]}") if m.type == "error" else None,
                )
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_selector("table.routing-table thead th", timeout=10000)
                s = page.evaluate(_PROBE)

                headers = {t.upper() for t in s["headerTexts"]}
                legend = {t.upper() for t in s["legendTexts"]}

                # The cheat-sheet renders a provider column per routing provider.
                assert s["headerTexts"], "no cheat-sheet headers rendered — the routing table didn't mount"
                assert "GPT" in headers, f"codex column must render as GPT, not a slug: headers={s['headerTexts']}"
                assert "GEMINI" in headers, f"antigravity column must render as Gemini: headers={s['headerTexts']}"
                assert not (_FORBIDDEN & headers), (
                    f"cheat-sheet headers leaked a raw uppercase slug (the #275 miss "
                    f"regressed): {_FORBIDDEN & headers} in {s['headerTexts']}"
                )

                # The chart legend renders one branded label per benchmark provider.
                assert "GPT" in legend, f"chart legend must brand codex as GPT: legend={s['legendTexts']}"
                assert "GEMINI" in legend, f"chart legend must brand antigravity as Gemini: legend={s['legendTexts']}"
                assert not (_FORBIDDEN & legend), (
                    f"chart legend leaked a raw uppercase slug: {_FORBIDDEN & legend} in {s['legendTexts']}"
                )

                assert not errs, f"JS errors rendering the branded launchpad: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


# --- Cross-language brand parity on the picks cheat-sheet -------------------
# The cortex cheat-sheet paints each pick's winner via the JS formatProviderLabel
# → normalizeProviderSlug map. That map CLAIMED to "mirror the Python
# council_schema._LEGACY_PROVIDER_ALIASES boundary" but only carried a 3-key
# subset {gemini,chatgpt,claude_ai}, so a picks.json winner of "gpt" / "google"
# / "bard" / "anthropic" painted "Gpt" / "Google" / "Bard" / "Anthropic" on this
# cheat-sheet while Python provider_model_brand (and the memory viewer's picks
# reader, which canonicalizes "gpt") both branded them "GPT" / "Gemini" / "Gemini"
# / "Claude". Same picks.json winner, two readers, two answers — the founder's
# cross-language-divergence shape. This guard drives the REAL cheat-sheet and
# asserts the painted brand equals Python provider_model_brand for every legacy
# alias slug. Mutation-provable: shrink the JS normalizeProviderSlug map back to
# {gemini,chatgpt,claude_ai} and "gpt" repaints "Gpt" → reds here while Python
# still says "GPT".

# Legacy-alias winners that bypass the canonical {claude,codex,antigravity}
# trio — exactly the slugs the short JS subset mishandled. Margins are above the
# 0.15 routing floor so the rows render undimmed (not the surface under test).
_LEGACY_ALIAS_WINNERS = ["gpt", "google", "bard", "anthropic"]

_CHEATSHEET_CORTEX_PAGE_DATA = {
    "cortexRules": {
        "winner_margin_floor": 0.15,
        "total_basins": len(_LEGACY_ALIAS_WINNERS),
        "rules": [
            {
                "basin_id": f"b0{i}",
                "winner": slug,
                "margin": 0.40 - i * 0.03,
                "count": 9 - i,
                "n_episodes": 9 - i,
                "evidence": [],
            }
            for i, slug in enumerate(_LEGACY_ALIAS_WINNERS)
        ],
    },
}

_CHEATSHEET_PROBE = """() => {
  return [...document.querySelectorAll('.cortex-cheat-sheet tbody tr .suggestion-chip')]
    .map(el => (el.textContent || '').trim());
}"""


def test_cheatsheet_picks_winner_brand_matches_python(tmp_path):
    """The picks cheat-sheet's JS formatProviderLabel must brand a legacy-alias
    winner identically to Python council_schema.provider_model_brand — no
    cross-language divergence (the founder's same-value-two-ways shape)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.council_schema import provider_model_brand

    # The Python truth the JS must match, in margin-desc render order.
    expected = [provider_model_brand(slug) for slug in _LEGACY_ALIAS_WINNERS]
    # Source sanity: these slugs genuinely fold to the brand trio in Python (so
    # a JS subset that leaves them title-cased is a REAL divergence, not noise).
    assert expected == ["GPT", "Gemini", "Gemini", "Claude"], (
        f"Python brand baseline shifted unexpectedly: {expected}"
    )

    html = render_launchpad_html(page_data=_CHEATSHEET_CORTEX_PAGE_DATA, view="stats")
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
                page.on(
                    "console",
                    lambda m: errs.append(f"console.error: {m.text[:160]}") if m.type == "error" else None,
                )
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_selector(".cortex-cheat-sheet tbody tr .suggestion-chip", timeout=10000)
                painted = page.evaluate(_CHEATSHEET_PROBE)

                # Precondition: every seeded pick row mounted (bite is the brand,
                # not a vacuous empty list).
                assert len(painted) == len(_LEGACY_ALIAS_WINNERS), (
                    f"cheat-sheet rows didn't all mount: painted={painted} "
                    f"expected {len(_LEGACY_ALIAS_WINNERS)} rows"
                )
                # The founder symptom: the JS painted "Gpt"/"Google"/"Bard"/
                # "Anthropic" while Python brands them GPT/Gemini/Gemini/Claude.
                assert painted == expected, (
                    "CROSS-LANGUAGE BRAND DIVERGENCE on the picks cheat-sheet: "
                    f"JS formatProviderLabel painted {painted} but Python "
                    f"provider_model_brand says {expected} for winners "
                    f"{_LEGACY_ALIAS_WINNERS}. The launchpad normalizeProviderSlug "
                    "map dropped legacy aliases the Python _LEGACY_PROVIDER_ALIASES "
                    "(and the memory viewer's canonProviderSlug) fold."
                )
                assert not errs, f"JS errors rendering the cheat-sheet: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
