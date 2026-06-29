"""Guard: the HOME council value-proof card must paint the CORRECT derived
numbers, not merely render.

The flagship #236 value-proof card — the journalist-screenshot surface — reads:
  "⚖ Across your N councils, the synthesized winner differed from your default
   X% of the time …  wins: Claude A% · GPT B% · Gemini C%  by area: code→Claude"

Every existing guard on this card checks PRESENCE or ABSENCE:
  * test_launchpad_cold_start_no_overclaim_browser — the card is ABSENT on a cold home,
  * test_council_value_proof_value_floor           — the card SELF-HIDES at 0% / thin n,
  * test_council_value_proof.py                     — the DATA layer math (changed_pct == 75).
NONE asserts that when the card DOES render on real data, the painted numbers are the
RIGHT ones. A template mis-binding — the `councils` count painted into the `%` slot, the
win-split bound to `w.count` instead of `w.pct`, or the wedge leader swapped — would
render a card that LOOKS right (correct copy, correct shape) while showing WRONG numbers,
and every test above stays green. This is the "a displayed value that contradicts the data
behind it" class — the broader sibling of green-while-degenerate (#35).

Seed (monkeypatched `_scan_outcomes`, the technique the value-floor test uses — the
on-disk path recomputes substantive_members and drops records without a deserializable
routing_label, so hand-written files read as an EMPTY ledger):
  default = claude every council.  winners: claude ×7, codex ×4, antigravity ×1.
  → 12 comparable real contests; changed (winner != claude) = 4 + 1 = 5.
  → changedPct = round(100·5/12) = 42      (DISTINCT from councils=12, so a councils↔pct
                                             swap is detectable)
  → win split out of n=12: claude 58% (count 7) · codex 33% (count 4) · antigravity 8%
    (count 1)  — pct and count differ per provider, so a pct↔count swap is detectable.
  → brand mapping: codex→GPT, antigravity→Gemini.

Mutation-proven to BITE: in launchpad_template.py swap the headline bindings
`councilValue.changedPct` ↔ `councilValue.councils` (the card then paints "Across your 42
councils … differed 12% of the time") OR bind the win-split to `w.count` instead of
`w.pct` ("Claude 7%") → the corresponding exact-value assertion reds with the founder
symptom. The card-VISIBLE + non-empty preconditions pass first, so the bite is the painted
number, not a vacuous absent-card pass.

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


def _records():
    """A KNOWN real-contest ledger: claude is always the default; the chairman
    winner is claude ×7, codex ×4, antigravity ×1 (12 comparable contests). Each
    carries the chairman task_type `code_review` so the per-kind wedge resolves to
    code→Claude (claude wins that family the most). Every record is a real contest
    (substantive_members == 2)."""
    out = []
    winners = ["claude"] * 7 + ["codex"] * 4 + ["antigravity"] * 1
    for win in winners:
        out.append({
            "chairman_winner": win,
            "winner_provider": win,
            "primary_provider": "claude",
            "substantive_members": 2,
            "task_type": "code_review",
            "routing_label": {"task_type": "code_review", "winner": win},
        })
    return out


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_value_proof_card_paints_correct_derived_numbers(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # Control exactly what the rendered card sees — the launchpad builder calls
    # council_value_proof() → _scan_outcomes() in-process.
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(), True))

    # Sanity: the data layer must agree with the arithmetic this test pins, or the
    # browser assertions would chase a moving target (a value-proof refactor that
    # changes the denominator would fail HERE, loudly, not as a silent DOM drift).
    vp = pr.council_value_proof()
    assert vp["ready"] is True, f"the known 42%/5-flip ledger must clear the value floor: {vp}"
    assert vp["comparable"] == 12 and vp["changed_pct"] == 42, (
        f"value-proof data-layer drifted from the pinned fixture math: {vp}"
    )

    html = render_launchpad_html()  # live builder reads the patched ledger
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
                page = browser.new_context(viewport={"width": 720, "height": 1400}).new_page()
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
                      if (!cv) return { present: false };
                      const metas = [...cv.querySelectorAll('span.meta')]
                        .map(s => (s.textContent || '').replace(/\\s+/g, ' ').trim());
                      return {
                        present: true,
                        visible: cv.offsetParent !== null,
                        full: (cv.textContent || '').replace(/\\s+/g, ' ').trim(),
                        winsMeta: metas.find(t => /^wins:/i.test(t)) || null,
                        kindMeta: metas.find(t => /^by area:/i.test(t)) || null,
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # BITE preconditions — the card must actually have painted, or the value
    # assertions below would pass vacuously on a missing card.
    assert state["present"], (
        "the #236 council-value-proof card never rendered on a 42%-changed/5-flip "
        "ledger that clears the value floor — the value-correctness assertions can't bite"
    )
    assert state["visible"], "the council-value card is in the DOM but not visible"
    full = state["full"]
    assert len(full) > 80, f"the council-value card painted suspiciously little text: {full!r}"

    # ── HEADLINE: councils count and changedPct are DISTINCT (12 vs 42), so a
    #    binding swap repaints the wrong number into each slot. ────────────────
    assert "Across your 12 head-to-head councils" in full, (
        "the value-proof headline must paint the COMPARABLE council count (12) under its "
        "GRAIN-NAMED label ('head-to-head councils', not bare 'councils' — which would "
        "collide with the /stats 'councils_aggregated' total). seed had 12 comparable "
        f"contests. A councils↔changedPct swap shows '42 head-to-head councils'. Saw: {full!r}"
    )
    assert "differed from your default 42% of the time" in full, (
        "the value-proof headline must paint changedPct=42% (round(100·5/12)) — the "
        "fraction of councils where the chairman picked a non-default model. A "
        f"councils↔changedPct swap shows 'differed … 12%'; a wrong denominator shows a "
        f"different %. Saw: {full!r}"
    )

    # ── WIN SPLIT: per-provider PCT (not count), correct brand, correct order
    #    (desc by count). pct≠count per provider, so a pct↔count swap is caught. ─
    wins = state["winsMeta"]
    assert wins is not None, f"the value-proof win-split meta line never painted: {state}"
    assert wins == "wins: Claude 58% · GPT 33% · Gemini 8%", (
        "the win-split must paint each provider's WIN PERCENTAGE (claude 7/12=58%, "
        "codex 4/12=33%, antigravity 1/12=8%) under its BRAND name (codex→GPT, "
        "antigravity→Gemini), sorted desc by win count. A pct↔count binding swap paints "
        "'Claude 7% · GPT 4% · Gemini 1%'; a brand-map miss leaks 'codex'/'antigravity'; "
        f"a sort flip leads with Gemini. Saw: {wins!r}"
    )

    # ── PER-AREA WEDGE: claude won the most code_review councils → code→Claude.
    #    The label is "by area:" (NOT "by kind:") so the COARSE family rollup
    #    doesn't collide with the /stats cheat-sheet's "per kind of question"
    #    grain — see test_launchpad_value_wedge_grain_label_browser for the #278
    #    grain-divergence guard. ─────────────────────────────────────────────────
    kind = state["kindMeta"]
    assert kind is not None, f"the per-area wedge meta line never painted: {state}"
    assert kind == "by area: code→Claude", (
        "the per-area wedge must name the family leader from the data — claude won the "
        "most code_review councils, so it reads 'code→Claude'. A leader swap or unbranded "
        f"slug ('code→claude'), or a regressed 'by kind:' label, reds here. Saw: {kind!r}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
