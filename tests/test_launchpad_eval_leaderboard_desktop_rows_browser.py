"""Real-browser guard: the cross-provider eval leaderboard rows stay COMPACT
(single-line) on the desktop / mid-width /stats view.

Found 2026-06-19 by driving the eval card at 1024 / 900 / 768px on a populated
multi-provider state and READING the pixels. The desktop `.eval-lb-row` grid used
fixed text columns `24px 80px 50px 1fr 70px 70px` (rank · provider · n · BAR ·
score · judge) tuned for raw provider SLUGS (`claude`/`codex`/`gemini`, ~50px).
The #275 brand flip then made the judge cell render "judge: <BRAND>" — and
"judge: Gemini" measures ~94px, so the fixed 70px column **wrapped it onto a
second line**. Every leaderboard row ballooned to ~47px tall (double height): a
ragged, hard-to-scan block with the central score-bar wedge floating in a sea of
whitespace — on the EXACT "Proven on your rejections · YOUR corpus" card a
journalist screenshots. The fix content-sizes the desktop text columns
(`24px minmax(56px,auto) auto minmax(80px,1fr) auto auto`) so the brand + judge
fit on ONE line while the bar keeps a minmax(80px,1fr) floor.

This guard seeds THREE scored providers via the REAL builder (so the leaderboard
renders with the real `provider_model_brand` labels AND a judge column), renders
the REAL /stats via `portal-html`, and at 1024px asserts every leaderboard row is
single-line — the judge cell renders on ONE line and the row height is within
~1.5x the single-line baseline (the rank cell). Mutation-proven: revert the
desktop `.eval-lb-row` grid to the old `24px 80px 50px 1fr 70px 70px` → the judge
cell wraps to 2 lines and the row doubles in height → this reds with the exact
"tall ragged rows" symptom.

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


def _run(eval_id: str, provider: str, model: str, score: float, judge: str) -> dict:
    return {
        "target_provider": provider,
        "target_model": model,
        "aggregate_score": score,
        "eval_id": eval_id,
        "items_completed": 12,
        "items_total": 12,
        "items_failed": 0,
        "by_rejection_type": {
            "REFRAME": {"count": 4, "mean_score": score, "min_score": 0.5, "max_score": 0.95},
            "REDIRECT": {"count": 4, "mean_score": score - 0.02, "min_score": 0.5, "max_score": 0.95},
            "SHARPENING": {"count": 4, "mean_score": score - 0.04, "min_score": 0.5, "max_score": 0.95},
        },
        # the judge slug drives the trailing "judge: <BRAND>" desktop column —
        # the cell that wrapped under the old fixed 70px width.
        "items": [{"judge_provider": judge}],
        "completed_at": "20260619T120000",
        "started_at": "20260619T120000",
    }


def _render_stats(home: Path, runs: list[dict]) -> Path:
    rd = home / "evals" / "results"
    rd.mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_personalA.json").write_text(
        json.dumps({"eval_id": "personalA", "items": [{"id": f"i{i}"} for i in range(12)]}),
        encoding="utf-8",
    )
    for i, run in enumerate(runs):
        name = f"eval_{run['eval_id']}__model_{run['target_provider']}__20260619T12000{i}.json"
        (rd / name).write_text(json.dumps(run), encoding="utf-8")
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


def test_eval_leaderboard_rows_single_line_on_desktop():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    runs = [
        # judges are the OTHER family so "judge: Gemini" / "judge: Claude" render —
        # the longest real judge label is the wrap trigger.
        _run("personalA", "claude", "claude-opus-4-8", 0.842, "antigravity"),
        _run("personalA", "antigravity", "gemini-3.1-pro", 0.791, "claude"),
        _run("personalA", "codex", "gpt-5.5", 0.733, "claude"),
    ]
    page_path = _render_stats(home, runs)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            # 1024px: the eval card sits in the full-width /stats grid; well above
            # the 560px breakpoint so the desktop grid (with the judge column) is live.
            page = browser.new_page(viewport={"width": 1024, "height": 2200})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval leaderboard: {errs[:3]}"

            res = page.evaluate(
                """() => {
                  const lb = [...document.querySelectorAll('.eyebrow')]
                    .find(e => /Cross-provider leaderboard/i.test(e.textContent || ''));
                  if (!lb) return {found: false};
                  const card = lb.closest('section');
                  const rows = [...card.querySelectorAll('.eval-lb-row')];
                  const lineHeight = (el) => {
                    const r = document.createRange();
                    r.selectNodeContents(el);
                    return Math.round(r.getBoundingClientRect().height);
                  };
                  const out = rows.map(li => {
                    const cells = [...li.children];
                    const rankH = lineHeight(cells[0]);       // single-line baseline
                    const judge = li.querySelector('.eval-lb-judge');
                    const judgeVisible = judge && getComputedStyle(judge).display !== 'none';
                    const judgeH = judgeVisible ? lineHeight(judge) : 0;
                    return {
                      rowH: Math.round(li.getBoundingClientRect().height),
                      rankH,
                      judgeText: judge ? (judge.textContent || '').trim() : '',
                      judgeVisible,
                      // a single line ≈ rankH; >=1.8x means the judge cell wrapped
                      judgeLines: judgeVisible && rankH ? Math.round(judgeH / rankH) : 0,
                    };
                  });
                  return {found: true, rowCount: rows.length, rows: out,
                          rawLeak: /\\{\\{/.test(card.innerHTML)};
                }""",
            )

            assert res["found"], "the cross-provider leaderboard did not render (need >=2 scored providers)"
            assert res["rowCount"] >= 3, f"expected >=3 leaderboard rows, got {res['rowCount']}"
            assert not res["rawLeak"], "raw {{ }} leaked in the leaderboard card (petite-vue did not mount)"

            for r in res["rows"]:
                assert r["judgeVisible"], (
                    "the desktop leaderboard dropped its 'judge:' column at 1024px — "
                    "it should only hide below 560px"
                )
                # THE BITE: the judge cell must render on ONE line.
                assert r["judgeLines"] <= 1, (
                    f"the eval leaderboard judge cell {r['judgeText']!r} WRAPPED to "
                    f"{r['judgeLines']} lines at 1024px — the post-#275 'judge: <BRAND>' "
                    "label overflowed the fixed-width judge column, making the row TALL "
                    "and RAGGED on the 'proven on your rejections' card a journalist "
                    "screenshots (founder symptom: 'judge column wrapped, tall ragged rows')"
                )
                # And the row stays compact — within ~1.5x the single-line baseline.
                assert r["rowH"] <= r["rankH"] * 1.6 + 8, (
                    f"an eval leaderboard row is {r['rowH']}px tall vs a {r['rankH']}px "
                    "single-line baseline — a leaderboard cell wrapped and the row doubled "
                    "in height (tall ragged rows on /stats)"
                )
        finally:
            browser.close()
