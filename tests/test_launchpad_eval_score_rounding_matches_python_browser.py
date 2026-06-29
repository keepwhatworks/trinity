"""Real-browser guard: the launchpad eval scores round IDENTICALLY to the Python
eval-share PNG / CLI eval-run — never with JavaScript's different rounding.

The cross-language bug this guard bites (the "Python computes, the JS re-formats,
they disagree" vein): the launchpad eval card formatted scores with JS
`Number.toFixed(N)` (round-HALF-UP), while every Python eval surface — the public
share card (`eval_card.py` `f"{:.2f}"`) and `eval-run`/`eval-show`
(`f"{:.3f}"`) — uses Python's `format` (round-half-to-EVEN / banker's). They
DIVERGE on a `.5`-at-the-cut boundary:

  * aggregate / per-axis mean of exactly 0.625 (5 of 8 items):
      Python `f"{0.625:.2f}"`  -> "0.62"   (banker's)
      JS     `(0.625).toFixed(2)` -> "0.63"   (half-up)
  * leaderboard row of exactly 0.5625 (9 of 16 items), shown at 3dp:
      Python `f"{0.5625:.3f}"` -> "0.562"
      JS     `(0.5625).toFixed(3)` -> "0.563"

So the SAME run read "0.63" on the launchpad hero but "0.62" on the founder's
shareable benchmark PNG — the app and the public artifact a journalist screenshots
disagreed about the model's score. Root-cause fix: format ONCE in Python
(`launchpad_data._fmt_score` -> `aggregate_score_str` / `mean_str` /
`aggregate_score_str` on the leaderboard rows) and have the template render the
string verbatim instead of re-rounding the raw float.

This guard seeds two runs whose scores land EXACTLY on those boundaries, drives the
real launchpad, reads the PAINTED hero / axis / leaderboard text, and asserts each
equals the Python `:.Nf` rendering (== the share-card value). It is mutation-proven
to fail on the old `.toFixed()` template: re-introduce `aggregate_score.toFixed(2)`
in the hero and this reds with the painted "0.63" vs the share-card "0.62".

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# The discriminating boundary scores: each lands on a .5-at-the-cut where Python's
# round-half-to-even and JS's round-half-up DISAGREE.
_AGG_2DP = 5 / 8        # == 0.625 exactly. py :.2f -> "0.62", js toFixed(2) -> "0.63".
_LB_3DP = 9 / 16        # == 0.5625 exactly. py :.3f -> "0.562", js toFixed(3) -> "0.563".


def _write(rd: Path, idx: int, payload: dict) -> None:
    rd.mkdir(parents=True, exist_ok=True)
    name = (
        f"eval_{payload['eval_id']}__model_{payload['target_provider']}"
        f"__20260619T1200{idx:02d}.json"
    )
    (rd / name).write_text(json.dumps(payload), encoding="utf-8")


def _render(home: Path, payloads: list[dict]) -> Path:
    rd = home / "evals" / "results"
    for i, pl in enumerate(payloads):
        _write(rd, i, pl)
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


def test_eval_scores_round_like_python_not_javascript():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Two providers, ONE eval set (so the leaderboard + per-axis chips render and
    # don't trip the mixed-set suppression). The winner (Claude) is exactly 0.625;
    # its REFRAME axis mean is exactly 0.625 too; the runner-up (Gemini) is exactly
    # 0.5625 — the 3dp leaderboard boundary.
    winner = {
        "target_provider": "claude", "target_model": "claude-opus-4-8",
        "aggregate_score": _AGG_2DP, "eval_id": "setRND",
        "items_completed": 16, "items_total": 16, "items_failed": 0,
        "by_rejection_type": {
            # REFRAME mean exactly 0.625 — the per-axis boundary.
            "REFRAME": {"count": 8, "mean_score": _AGG_2DP, "min_score": 0.0, "max_score": 1.0},
            "REDIRECT": {"count": 8, "mean_score": 0.75, "min_score": 0.5, "max_score": 1.0},
        },
        "items": [], "completed_at": "20260619T120100", "started_at": "20260619T120100",
        "n_scored": 16, "self_judge": False,
    }
    runner = {
        "target_provider": "antigravity", "target_model": "gemini-3.1-pro",
        "aggregate_score": _LB_3DP, "eval_id": "setRND",
        "items_completed": 16, "items_total": 16, "items_failed": 0,
        "by_rejection_type": {
            "REFRAME": {"count": 8, "mean_score": 0.5, "min_score": 0.0, "max_score": 1.0},
            "REDIRECT": {"count": 8, "mean_score": 0.625, "min_score": 0.0, "max_score": 1.0},
        },
        "items": [], "completed_at": "20260619T120000", "started_at": "20260619T120000",
        "n_scored": 16, "self_judge": False,
    }

    # The Python (share-card / CLI) renderings — the values that MUST win on the
    # launchpad too. Computed the same way eval_card.py / commands/eval.py do.
    py_hero = f"{_AGG_2DP:.2f}"        # "0.62"
    py_axis = f"{_AGG_2DP:.2f}"        # "0.62"
    py_lb_runner = f"{_LB_3DP:.3f}"    # "0.562"
    # Source-sanity (the BITE precondition): a naive JS re-round WOULD differ —
    # otherwise this fixture proves nothing. Confirm the boundary really diverges.
    assert py_hero == "0.62", py_hero
    assert py_lb_runner == "0.562", py_lb_runner

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    page_path = _render(home, [winner, runner])

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2800})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval card: {errs[:3]}"
            painted = page.evaluate(
                """() => {
                  const card = document.querySelector('.eval-summary-card');
                  if (!card) return {found: false};
                  // Hero score: the h2's tabular-nums score span.
                  const h2 = card.querySelector('h2');
                  const heroMatch = (h2 ? (h2.innerText || '') : '').match(/0\\.\\d{2,3}/);
                  // Per-axis rows: name -> painted score text (the right-aligned span).
                  const axisRows = {};
                  card.querySelectorAll('.eval-axis-row').forEach(row => {
                    const spans = row.querySelectorAll('span');
                    if (spans.length) {
                      const name = (spans[0].innerText || '').trim();
                      const last = spans[spans.length - 1];
                      axisRows[name] = (last.innerText || '').trim();
                    }
                  });
                  // Leaderboard rows: brand -> painted score text (3dp).
                  const lbRows = {};
                  card.querySelectorAll('.eval-lb-row').forEach(row => {
                    const txt = row.innerText || '';
                    const brand = (txt.match(/(Claude|Gemini|GPT)/) || [])[0] || '';
                    const score = (txt.match(/0\\.\\d{3}/) || [])[0] || '';
                    if (brand) lbRows[brand] = score;
                  });
                  return {found: true, hero: heroMatch ? heroMatch[0] : null,
                          axisRows, lbRows};
                }"""
            )
        finally:
            browser.close()

    assert painted.get("found"), "eval-summary-card never rendered"

    failures: list[str] = []
    # 1. Hero aggregate (2dp): the founder symptom — 0.625 painting "0.63" in the
    #    app while the share PNG shows "0.62".
    hero = painted.get("hero")
    if hero != py_hero:
        failures.append(
            f"HERO score: launchpad painted '{hero}' but the share card / CLI render "
            f"'{py_hero}' for the SAME 0.625 run (JS round-half-up vs Python banker's) "
            f"— the app and the public benchmark PNG disagree on the score"
        )
    # 2. Per-axis mean (2dp): REFRAME mean 0.625 must read "0.62", not "0.63".
    axis_reframe = painted.get("axisRows", {}).get("REFRAME")
    if axis_reframe != py_axis:
        failures.append(
            f"REFRAME axis mean: launchpad painted '{axis_reframe}' but Python renders "
            f"'{py_axis}' for mean 0.625 — per-axis bar re-rounds in JS"
        )
    # 3. Leaderboard row (3dp): Gemini's 0.5625 must read "0.562", not "0.563".
    lb_gemini = painted.get("lbRows", {}).get("Gemini")
    if lb_gemini != py_lb_runner:
        failures.append(
            f"LEADERBOARD Gemini row: launchpad painted '{lb_gemini}' but Python renders "
            f"'{py_lb_runner}' for 0.5625 — leaderboard row re-rounds in JS at 3dp"
        )

    assert not failures, (
        "launchpad eval scores diverge from the Python share-card / CLI rounding:\n  "
        + "\n  ".join(failures)
        + f"\n  (painted={painted})"
    )
