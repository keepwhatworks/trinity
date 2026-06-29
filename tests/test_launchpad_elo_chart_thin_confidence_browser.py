"""Real-browser guard: the Local Elo chart self-demotes at low game counts.

THE BUG (UX sweep, /stats option-2: ELO chart at low n). The /stats "Local Elo"
bar chart plots each provider's rating (Claude / GPT / Gemini) from the user's own
council win/loss history. A provider needs only ``MIN_GAMES_FOR_ELO_CHART == 2``
games to appear — the floor a fresh-install user hits fast. Before this guard:

  * The bars were ABSOLUTE Elo on a ``y: {min: 1400}`` axis. A single 2-0 coin-flip
    lands Claude at ~1523 / GPT at ~1477 — a 46-point gap the 1400 floor stretches
    into a TOWERING Claude bar vs a stub, a confident screenshot-worthy "Claude
    crushes GPT" ranking off two coin flips.
  * NO per-bar game-count disclosure (a 2-game bar read as authoritative as a
    250-game one) and NO thin-data caveat — while the SIBLING cheat-sheet table
    one screen below already honestly demoted the same 2-council data ("Best: —",
    "n=2"). The Elo chart was the lone surface painting a confident ranking over a
    coin-flip — the #35 green-while-degenerate / confidence-honesty shape.

THE FIX (Trinity council council_21a5b74fb1df3fda, winner claude, codex chairman,
UNANIMOUS on the core — "do not hide at 2 games; disclose game counts; thin caveat
below 10; the 1400 axis floor exaggerates near-base differences — combine
disclosure with axis correction; render Elo as 1500-centered deltas, not magnified
absolute bars"). DEMOTE-not-HIDE, matching the eval-hero "Thin sample" floor of 10
and the council-value home card's 10-real-contest bar:
  * ``_elo_chart_data`` carries per-provider ``games``/``elos`` + a ``thin`` flag
    (weakest plotted provider < ``ELO_CHART_THIN_GAMES == 10``) and emits the bars
    as signed deltas from the 1500 base.
  * The template renders a "⚠️ Thin sample" caveat (gated on ``eloChart.thin``) +
    a per-bar "Claude · 1499 Elo · n=2" disclosure line, and the chart axis is
    CENTERED on 0 (delta-from-base) so a coin-flip is a small bar at the midline,
    not a tower floored at 1400. (A naive ``min:1500`` floor would CLIP a below-
    base provider — the council's lone disagreed_claim; the centered delta shows
    -61 honestly where the floor would hide it.)

GREEN-GATE DISCIPLINE: the caveat must be SHOWN when degenerate (n<10) AND REFUSED
when the data is real (n>=10), else it cries wolf on every legitimate chart.

Mutation-proven (see the per-test docstrings):
  * FAST: revert the ``thin`` computation in launchpad_data → the data test reds.
  * BROWSER (thin): seed 2 games/provider → caveat + n=2 + centered (negative-min)
    axis. Revert the template caveat / the delta transform → the asserts red.
  * BROWSER (rich): seed 15 games/provider → caveat REFUSED (no cry-wolf).

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# FAST data-layer guard (no browser) — the thin flag + delta transform.
# ---------------------------------------------------------------------------
def test_data_layer_two_game_chart_is_flagged_thin():
    from trinity_local.launchpad_data import (
        ELO_BASE_RATING,
        ELO_CHART_THIN_GAMES,
        _elo_chart_data,
    )

    snap = {"providers": {
        "claude": {"elo": 1523, "total_games": 2, "wins": 2},
        "codex": {"elo": 1477, "total_games": 2, "wins": 0},
    }}
    chart = _elo_chart_data(snap)
    assert chart["thin"] is True, (
        "a 2-game-each Elo chart is a coin-flip — `thin` MUST be set so the "
        "surface shows the 'Thin sample' caveat (the green-while-degenerate shape "
        "the cheat-sheet sibling already demotes with n=2)"
    )
    assert chart["minGames"] == 2
    assert chart["thinFloor"] == ELO_CHART_THIN_GAMES == 10
    assert chart["games"] == [2, 2]
    assert chart["elos"] == [1523, 1477]
    # Bars are signed deviation from the 1500 base, NOT absolute Elo floored at
    # 1400 — so a near-base flip is a small bar at the midline.
    assert chart["base"] == ELO_BASE_RATING == 1500
    assert chart["datasets"][0]["data"] == [1523 - 1500, 1477 - 1500] == [23, -23]


def test_data_layer_ten_game_chart_is_not_thin():
    """At/above the 10-game floor the chart is a real signal — `thin` MUST be
    False so the caveat doesn't cry wolf on every legitimate ranking."""
    from trinity_local.launchpad_data import _elo_chart_data

    snap = {"providers": {
        "claude": {"elo": 1561, "total_games": 15, "wins": 12},
        "codex": {"elo": 1439, "total_games": 15, "wins": 3},
    }}
    chart = _elo_chart_data(snap)
    assert chart["thin"] is False, (
        "a 15-game-each chart is real signal — `thin` must NOT be set or the "
        "thin-sample caveat cries wolf on a legitimate ranking"
    )
    assert chart["minGames"] == 15


def test_data_layer_weakest_link_drives_thin():
    """The chart is only as trustworthy as its LEAST-played bar: one well-played
    provider does not redeem a 2-game one plotted beside it."""
    from trinity_local.launchpad_data import _elo_chart_data

    snap = {"providers": {
        "claude": {"elo": 1561, "total_games": 40, "wins": 28},
        "codex": {"elo": 1490, "total_games": 2, "wins": 0},  # the weak link
    }}
    chart = _elo_chart_data(snap)
    assert chart["thin"] is True, (
        "the weakest plotted provider has 2 games — thin must flag on the MIN, "
        "not the max (else a 2-game bar rides a 40-game one's authority)"
    )
    assert chart["minGames"] == 2


# ---------------------------------------------------------------------------
# Shared seeder: write real council outcomes so the full pipeline
# (telemetry.build_elo_snapshot → launchpad_data → render) produces the chart.
# ---------------------------------------------------------------------------
def _seed(home: Path, games: int) -> None:
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local import personal_routing

    os.environ["TRINITY_HOME"] = str(home)
    (home / "council_outcomes").mkdir(parents=True, exist_ok=True)
    for i in range(games):
        # claude wins ~80% so it leads but both labs have exactly `games` games.
        winner = "claude" if i % 5 != 0 else "codex"
        scores = {"claude": 8.2, "codex": 8.1}
        members = [
            CouncilMemberResult(
                provider=p,
                model="claude-opus-4-8" if p == "claude" else "gpt-5.5",
                output_text="x" * 250,
            )
            for p in scores
        ]
        label = CouncilRoutingLabel(
            winner=winner,
            task_type="debug",
            provider_scores={p: {"overall": v} for p, v in scores.items()},
        )
        save_council_outcome(CouncilOutcome(
            council_run_id=f"c{i:03d}", bundle_id=f"b{i:03d}", task_cluster_id="cl",
            primary_provider="claude", winner_provider=winner,
            created_at="2026-06-17T00:00:00", member_results=members,
            synthesis_output="Chairman: " + "y" * 200, routing_label=label,
            metadata={"task_type": "debug"},
        ))
    personal_routing.invalidate_cache()


def _render_stats(home: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists()
    return page


def _drive(games: int) -> dict:
    """Render a fresh /stats with `games` councils, open the Elo <details>, and
    return what the user sees: caveat shown? n disclosed? axis centered?"""
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed(home, games)
    page_path = _render_stats(home)
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 393, "height": 2800})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(1100)
            assert not errs, f"JS errors rendering /stats: {errs[:3]}"
            # Open the Elo details (the chart + caveat live inside it).
            opened = page.evaluate(
                """() => {
                  const det = [...document.querySelectorAll('details')].find(d => {
                    const s = d.querySelector('summary');
                    return s && (s.innerText || '').includes('Local Elo');
                  });
                  if (!det) return false;
                  det.open = true;
                  return true;
                }"""
            )
            assert opened, "Local Elo <details> not found (precondition — non-vacuous)"
            page.wait_for_timeout(900)
            res = page.evaluate(
                """() => {
                  const det = [...document.querySelectorAll('details')].find(d => {
                    const s = d.querySelector('summary');
                    return s && (s.innerText || '').includes('Local Elo');
                  });
                  const text = det.innerText || '';
                  // Chart instance: read the axis range + the plotted deltas.
                  const c = document.getElementById('provider-elo-chart');
                  let yMin = null, yMax = null, values = null, rendered = false;
                  if (c) {
                    rendered = c.offsetParent !== null;
                    const inst = (window.Chart && window.Chart.getChart)
                      ? window.Chart.getChart(c) : null;
                    if (inst) {
                      yMin = inst.options.scales.y.min;
                      yMax = inst.options.scales.y.max;
                      values = inst.data.datasets[0].data;
                    }
                  }
                  const docOverflow =
                    document.documentElement.scrollWidth -
                    document.documentElement.clientWidth;
                  return {
                    thinShown: text.includes('Thin sample'),
                    nDisclosed: /n=\\d/.test(text),
                    text,
                    yMin, yMax, values, rendered, docOverflow,
                  };
                }"""
            )
            res["errs"] = errs
            return res
        finally:
            browser.close()


def test_elo_chart_thin_at_two_games_demotes_and_discloses():
    """The brief's exact case: 2 games per provider. The chart must DEMOTE — show
    the 'Thin sample' caveat + per-bar n=N + a CENTERED axis (the bars are deltas
    from 1500, so the axis min is NEGATIVE, never the old 1400 floor that
    magnified a coin-flip into a tower).

    Mutation-proof: revert the template caveat `v-if="eloChart && eloChart.thin"`
    or the `_elo_chart_data` thin flag → `thinShown` False → reds. Revert the
    delta transform (data back to absolute Elo, axis back to min:1400) →
    `yMin >= 1400` → the centered-axis assert reds.
    """
    pytest.importorskip("playwright.sync_api")
    res = _drive(2)
    assert res["rendered"], "the Elo chart canvas must still render (DEMOTE, not HIDE)"
    assert res["thinShown"], (
        "2 games per provider is a coin-flip — the 'Thin sample' caveat MUST show. "
        "Without it the Local Elo chart paints a confident 'Claude crushes GPT' "
        "ranking off two coin flips while the cheat-sheet sibling shows 'n=2'."
    )
    assert res["nDisclosed"], (
        "the per-bar game-count (n=2) MUST be disclosed — a 2-game bar can't read "
        "as authoritative as a 250-game one"
    )
    # Bars are 1500-centered deltas → the axis spans below zero. The old bug used
    # a fixed y:{min:1400} floor (which magnified the near-base coin-flip); a
    # centered axis has a NEGATIVE min.
    assert res["yMin"] is not None and res["yMin"] < 0, (
        f"the Elo axis must be CENTERED on the 1500 base (negative min), not "
        f"floored at 1400 — got yMin={res['yMin']}. The 1400 floor stretched a "
        f"46-point coin-flip gap into a towering Claude bar vs a GPT stub."
    )
    # And the plotted values are small deltas near zero, not absolute ratings.
    assert res["values"] and all(abs(v) < 100 for v in res["values"]), (
        f"the bars must be deltas from 1500 (small near-zero values for a flip), "
        f"not absolute Elo (~1500) — got {res['values']}"
    )
    assert res["docOverflow"] <= 1, f"horizontal overflow @393: {res['docOverflow']}"
    assert not res["errs"], f"JS errors: {res['errs'][:3]}"


def test_elo_chart_rich_at_fifteen_games_refuses_caveat():
    """Green-gate: at 15 games/provider the chart is REAL signal — the 'Thin
    sample' caveat must be REFUSED (else it cries wolf on every legitimate
    ranking). The below-base provider's bar still renders (delta goes negative,
    NOT clipped — the council's lone disagreed_claim about a naive 1500 floor).

    Mutation-proof: force `thin` always-True (or render the caveat unconditionally)
    → `thinShown` True here → this REFUSED assert reds.
    """
    pytest.importorskip("playwright.sync_api")
    res = _drive(15)
    assert res["rendered"], "the Elo chart must render for the rich case too"
    assert not res["thinShown"], (
        "15 games per provider is a real signal — the 'Thin sample' caveat must "
        "be REFUSED, or it cries wolf on every legitimate ranking"
    )
    assert res["nDisclosed"], "n disclosure stays on for the rich case (always useful)"
    # The below-base provider (GPT, ~1439) must render its bar going DOWN from the
    # midline — a negative delta — not clipped to zero by a 1500 floor.
    assert res["values"] and any(v < 0 for v in res["values"]), (
        f"a below-base provider must render a negative-delta bar (not clipped) — "
        f"got {res['values']}"
    )
    assert res["docOverflow"] <= 1, f"horizontal overflow @393: {res['docOverflow']}"
    assert not res["errs"], f"JS errors: {res['errs'][:3]}"
