"""Guard (count cross-surface divergence): the HOME value-proof card and the
/stats routing chart BOTH render a council count, from DIFFERENT populations of
the same `council_outcomes/` ledger — so each MUST name its grain, or one ledger
reads as two contradicting "N councils" totals on the same page.

The shipped symptom (found 2026-06-20 by seeding ONE ledger and driving every
council-count surface): the home value-proof card painted "Across your <b>12</b>
councils …" while the /stats routing card painted "From your own <b>17</b>
councils …" — same launchpad page, same ledger, the SAME word "councils", two
different numbers. Verified at SOURCE (no false alarm):

  * the home card's count is `council_value_proof()["comparable"]` — REAL
    head-to-head contests where BOTH the chairman winner AND the user's default
    were recorded (walkovers and default-less runs excluded).
  * the /stats count is `compute_personal_routing_table()["councils_aggregated"]`
    — EVERY label-bearing council (walkovers included).
  * `status` already labels the SAME `comparable` subset "real contests" — so the
    home card was the lone surface calling a filtered subset the bare word
    "councils", letting it (a) read as the user's total and (b) collide with the
    larger /stats "councils" number.

This is NOT a math bug — each number is correct for the stat that rests on it
(`comparable` is the denominator the win-split percentages reconcile against;
`councils_aggregated` is the base the routing bars sharpen with). The DEFECT is
the copy: a filtered subset wearing the unqualified label of the whole. Same
class + same fix shape as the #278 wedge "by area:" grain-naming.

The fix names the grain on the home card ("head-to-head councils" + a tooltip
that the full history is larger), so the 12 can't be mistaken for the total or
collide with the /stats 17.

This guard seeds a DISCRIMINATING ledger where comparable (12) < councils_aggregated
(17), renders ONE launchpad DOM (home + /stats both present), and asserts:
  1. the home value-proof count IS the comparable subset (12),
  2. the /stats count IS the larger aggregated base (17) — they genuinely diverge,
  3. the home card's count is GRAIN-NAMED ("head-to-head"), so the bare word
     "councils" no longer wraps the filtered subset.

Mutation-proven to BITE: revert "head-to-head councils" → "councils" in
launchpad_template.py and assertion 3 reds — the home card then paints "Across
your 12 councils" while /stats paints "17 councils" on the same page, the exact
one-ledger-two-totals collision — while the count/visibility preconditions pass
first (so the bite is the grain LABEL, not a vacuous miss).

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

import trinity_local.personal_routing as pr

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# Discriminating fixture: comparable (12) < councils_aggregated (17). All records
# carry a routing_label (so councils_aggregated counts all 17); 5 are walkovers
# (substantive_members=1 → excluded from the value-proof `comparable`).
COMPARABLE = 12
AGGREGATED = 17
WALKOVERS = AGGREGATED - COMPARABLE  # 5


def _records():
    out = []

    def add(winner, default, substantive, task_type):
        out.append({
            "chairman_winner": winner,
            "winner_provider": winner,
            "primary_provider": default,
            "substantive_members": substantive,
            "task_type": task_type,
            "routing_label": {
                "task_type": task_type,
                "winner": winner,
                "provider_scores": {
                    winner: {"overall": 0.82},
                    default: {"overall": 0.61},
                },
            },
        })

    # 12 real head-to-head contests (both winner+default, substantive_members=2).
    # 7 differ from the default (clears changed_pct >= 25 and changed_count >= 3).
    for i in range(COMPARABLE):
        winner = "codex" if i < 7 else "claude"
        add(winner, "claude", 2, "code_refactor")

    # 5 walkovers — have a routing_label (so they land in councils_aggregated) but
    # only one substantive member, so they are NOT real contests / NOT comparable.
    for _ in range(WALKOVERS):
        add("claude", "claude", 1, "code_refactor")

    return out


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_home_value_proof_and_stats_council_counts_name_their_grain(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(), True))
    pr.invalidate_cache()

    # Source sanity — the two surfaces genuinely diverge on this seed, or the
    # browser assertion would chase a moving target.
    vp = pr.council_value_proof()
    assert vp.get("ready") and vp.get("comparable") == COMPARABLE, (
        f"seed must make the value-proof comparable == {COMPARABLE}: {vp}"
    )
    rt = pr.compute_personal_routing_table()
    assert rt.get("councils_aggregated") == AGGREGATED, (
        f"seed must make councils_aggregated == {AGGREGATED} (> comparable {COMPARABLE}) "
        f"so the two council counts diverge: {rt.get('councils_aggregated')}"
    )

    html = render_launchpad_html()  # home value-proof + /stats routing chart, one DOM
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1600}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stub'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                state = page.evaluate(
                    """() => {
                      const cv = document.querySelector('p.council-value');
                      const cvText = cv ? (cv.textContent || '').replace(/\\s+/g, ' ').trim() : null;
                      // The /stats routing-chart meta line: "From your own N councils …"
                      const metas = [...document.querySelectorAll('p.meta')]
                        .map(s => (s.textContent || '').replace(/\\s+/g, ' ').trim());
                      const statsMeta = metas.find(t => /From your own/.test(t) && /council/.test(t)) || null;
                      return {
                        cvPresent: !!cv,
                        cvVisible: cv ? cv.offsetParent !== null : false,
                        cvText,
                        statsMeta,
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # BITE preconditions — the card + meta must have painted, or the grain-label
    # assertion can't bite.
    assert state["cvPresent"], (
        "the #236 value-proof card never rendered on a ledger that clears every floor "
        "— the council-count grain-label assertion can't bite"
    )
    assert state["cvVisible"], "the value-proof card is in the DOM but not visible"
    assert state["cvText"], f"the value-proof card painted no text: {state}"
    assert state["statsMeta"], (
        f"the /stats routing-chart 'From your own N councils' meta never painted: {state}"
    )

    # ── The two surfaces genuinely render DIFFERENT council counts (12 vs 17) for
    #    the SAME ledger — the divergence that makes the grain label load-bearing. ─
    assert f"{COMPARABLE}" in state["cvText"], (
        f"the home value-proof card must show the comparable count {COMPARABLE}. Saw: {state['cvText']!r}"
    )
    assert f"{AGGREGATED} council" in state["statsMeta"], (
        f"the /stats routing chart must show the aggregated count {AGGREGATED} — a DIFFERENT "
        f"number than the home card's {COMPARABLE}. Saw: {state['statsMeta']!r}"
    )

    # ── ROOT: the home card's count is GRAIN-NAMED ("head-to-head"), so the bare
    #    word "councils" no longer wraps the filtered `comparable` subset. Without
    #    it, the home "Across your 12 councils" and the /stats "From your own 17
    #    councils" read as one ledger with two contradicting council totals. ──────
    assert "head-to-head" in state["cvText"].lower(), (
        "council-count cross-surface divergence: the home value-proof card must name "
        "its grain ('head-to-head councils') for the `comparable` subset. A bare "
        f"'Across your {COMPARABLE} councils' collides with the /stats 'From your own "
        f"{AGGREGATED} councils' — the same word, two numbers, one ledger, reading as a "
        f"self-contradiction. Saw: {state['cvText']!r}"
    )
    # The grain qualifier must sit ON the count, not somewhere unrelated in the card.
    assert f"{COMPARABLE}" in state["cvText"] and "head-to-head councils" in state["cvText"].lower(), (
        f"'head-to-head councils' must qualify the {COMPARABLE} count itself. Saw: {state['cvText']!r}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
