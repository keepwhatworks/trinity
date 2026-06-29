"""Guard (degenerate-ranking honesty): the /stats "Local Elo" chart is a
RELATIVE ranking — its whole meaning is "which model rates above which". With a
single entrant plotted there is NOTHING to rank against, so a lone bar floating
above the 1500 base is not a "thin ranking", it's NOT A RANKING.

The shipped symptom (found 2026-06-22 by seeding a 2-council ledger whose default
played two DIFFERENT challengers and driving the real /stats ELO section): the
chart painted ONE bar — "Claude · 1524 Elo · n=2" — rising +24 above the 1500
base, captioned with the thin-sample caveat ("a single coin-flip moves the bars"
… "the gaps sharpen", language that assumes multiple bars / gaps that don't
exist). The two providers Claude actually beat (codex + antigravity, both at
1488) were SILENTLY dropped below the 2-game floor, so the user reads "Claude
rates 1524" as a settled standalone verdict with the opponents hidden.

This IS normal-operation reachable (verified at SOURCE — no false alarm,
no corrupt/imported file needed): `build_elo_snapshot` increments `total_games`
per participant per ≥2-member council, so a default that played two distinct
challengers across two councils clears `MIN_GAMES_FOR_ELO_CHART=2` ALONE while
each partner stays at 1 game and drops below the floor. `_elo_chart_data` then
plots exactly one bar.

Same class as the council-card solo-overclaim (#35 / a model "wins a contest"
with no contest), the eval per-axis-leader contest gate ("a leader needs a
CONTEST … the data layer must not EMIT a leader at 1 contender"), and the
routing cheat-sheet's ghost-table suppression. The fix mirrors them: the data
layer marks the snapshot `degenerate` at <2 plotted entrants (so every consumer
inherits the gate), and /stats suppresses the meaningless one-bar chart + its
thin caveat + per-bar caption, painting an honest single-entrant explanation
instead.

This guard seeds the DISCRIMINATING degenerate ledger (claude clears the floor
at 2 games; codex + antigravity each stay at 1), drives the real /stats ELO
<details>, and asserts:
  1. the per-bar caption + thin caveat (the misleading lone-bar copy) are GONE,
  2. the <canvas> for the one-bar chart is NOT rendered,
  3. the honest single-entrant message IS painted and names the lone model.

Mutation-proven to BITE: revert the `degenerate` gate in `_elo_chart_data`
(force `degenerate=False`) and the canvas + the "Claude · 1524 Elo · n=2" caption
re-appear with no single-entrant message — the exact lone-bar overclaim — while
the bite preconditions (the ELO details exists + the data layer genuinely sees a
single entrant) pass first, so the bite is the degenerate gate, not a vacuous
miss.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _seed_degenerate_ledger() -> None:
    """One provider (claude) clears MIN_GAMES_FOR_ELO_CHART=2; each challenger
    stays at 1 game (below the floor) — the single-entrant ELO chart."""
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    pairs = [("claude", "codex"), ("claude", "antigravity")]
    for i, (a, b) in enumerate(pairs):
        members = [
            CouncilMemberResult(provider=a, model=f"{a}-m", output_text=f"ans {a} " * 15),
            CouncilMemberResult(provider=b, model=f"{b}-m", output_text=f"ans {b} " * 15),
        ]
        label = CouncilRoutingLabel(
            winner=a, runner_up=b, confidence="high", task_type="coding",
            provider_scores={a: {"overall": 0.82}, b: {"overall": 0.61}},
            agreed_claims=[f"pt{i}"], disagreed_claims=[],
        )
        save_council_outcome(CouncilOutcome(
            council_run_id=f"c{i:02d}", bundle_id=f"c{i:02d}",
            task_cluster_id="cl", primary_provider=a, primary_model=f"{a}-m",
            winner_provider=a, winner_model=f"{a}-m", agreement_score=0.7,
            metadata={"task_text": f"q{i}"}, member_results=members,
            synthesis_prompt="x", synthesis_output=f"syn{i}",
            routing_label=label, created_at=f"2026-06-0{i + 1}T00:00:00+00:00",
        ))


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_single_entrant_elo_shows_no_ranking_only_an_honest_message(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    _seed_degenerate_ledger()

    # ── Source sanity (RENDER-INDEPENDENT) — the snapshot genuinely sees ONE
    #    plotted entrant, so the degenerate gate is the thing under test (no
    #    false alarm, no moving target). ──────────────────────────────────────
    from trinity_local.launchpad_data import _elo_chart_data
    from trinity_local.telemetry import build_elo_snapshot

    chart = _elo_chart_data(build_elo_snapshot())
    # The discriminating fact is the ENTRANT COUNT (render-independent) — NOT the
    # `degenerate` flag, which is the thing under test (a guard that pre-asserted
    # the flag would short-circuit a flag-mutation before the rendered bite).
    assert chart["labels"] == ["Claude"], (
        f"seed must make a SINGLE plotted entrant (claude at 2 games; codex + "
        f"antigravity at 1, below the floor). Saw labels: {chart['labels']!r}"
    )

    from trinity_local.launchpad_page import render_stats_html
    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(render_stats_html(), encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 2200}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stub'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # Open the Elo <details> so its body paints.
                page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                page.wait_for_timeout(500)
                state = page.evaluate(
                    """() => {
                      const details = [...document.querySelectorAll('details')]
                        .find(d => /Local Elo/.test(d.textContent));
                      const metas = [...document.querySelectorAll('p.meta')];
                      const visible = (el) => el && el.offsetParent !== null;
                      const degMsg = metas.find(p => /has enough councils/.test(p.textContent));
                      const thin = metas.find(p => /Thin sample/.test(p.textContent));
                      const perBar = metas.find(p => /Elo · n=/.test(p.textContent));
                      return {
                        eloDetailsPresent: !!details,
                        canvasPresent: !!document.getElementById('provider-elo-chart'),
                        degMsgText: (degMsg && visible(degMsg)) ? degMsg.textContent.replace(/\\s+/g, ' ').trim() : null,
                        thinVisible: visible(thin),
                        perBarVisible: visible(perBar),
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # ── BITE precondition (A): the ELO details surface actually painted, or the
    #    suppression assertions can't bite. ─────────────────────────────────────
    assert state["eloDetailsPresent"], (
        "the /stats 'Local Elo' <details> never rendered — the single-entrant "
        "suppression guard can't bite on a missing surface"
    )

    # ── ROOT 1: the meaningless one-bar chart canvas is NOT rendered. ──────────
    assert not state["canvasPresent"], (
        "single-entrant ELO overclaim: the one-bar <canvas#provider-elo-chart> "
        "rendered for a SINGLE plotted model — an ELO 'ranking' with nothing to "
        "rank against. The lone bar floats above the 1500 base while the models it "
        "beat are hidden below the games floor."
    )

    # ── ROOT 2: the misleading lone-bar copy (thin caveat + per-bar 'X Elo · n=N')
    #    is suppressed — it assumes multiple bars / gaps that don't exist. ───────
    assert not state["thinVisible"], (
        "single-entrant ELO overclaim: the 'Thin sample … a single coin-flip moves "
        "the bars … the gaps sharpen' caveat painted over a ONE-bar chart, where "
        "there are no gaps and nothing to coin-flip against"
    )
    assert not state["perBarVisible"], (
        "single-entrant ELO overclaim: the per-bar 'Claude · 1524 Elo · n=2' caption "
        "painted a standalone Elo rating for the lone entrant, reading as a settled "
        "verdict"
    )

    # ── ROOT 3: an HONEST single-entrant message IS painted and names the model. ─
    assert state["degMsgText"], (
        "single-entrant ELO overclaim: with one plotted model the chart must paint "
        "the honest single-entrant explanation (not a one-bar 'ranking'). It never "
        "rendered"
    )
    assert "Claude" in state["degMsgText"], (
        f"the single-entrant message must NAME the lone model. Saw: {state['degMsgText']!r}"
    )
    assert "ranking" in state["degMsgText"].lower(), (
        f"the single-entrant message must say there's no ranking yet. Saw: {state['degMsgText']!r}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
