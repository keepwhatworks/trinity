"""Real-browser guard: the cross-provider eval leaderboard keeps its score BAR
visible (and doesn't clip the judge column) at phone widths.

The /stats "Cross-provider leaderboard · YOUR corpus" rows are the visual wedge —
"which model wins YOUR rejections" — a horizontal score bar per provider (Claude
longest, Gemini shortest). The desktop row is a six-column grid
`24px 80px 50px 1fr 70px 70px`: rank · provider · n · BAR(1fr) · score · judge.
On a phone the fixed columns (~334px incl. gaps) eat the whole row, so the 1fr
BAR collapsed to **0px** at 375/393 while the trailing "judge: claude" column
clipped off the right edge ("ju / cla"). The leaderboard became a bare list of
numbers with a dead gap where the comparison bar should be — the wedge invisible
exactly on the most-shared (mobile) screenshot.

Fix: the row uses an `.eval-lb-row` class; below 560px the grid tightens its fixed
columns (minmax-bounded) so the BAR keeps width, and the secondary `.eval-lb-judge`
column is dropped (its info is already in the footer "Judges are rotated…" line).
The desktop grid is content-sized (auto text columns + a minmax(80px,1fr) bar) —
see `test_launchpad_eval_leaderboard_desktop_rows_browser.py` for the twin guard
on the DESKTOP wrap defect that fix superseded.

This guard seeds two scored providers (so the comparison leaderboard renders),
renders the REAL /stats, and at 375px asserts every comparison bar has a non-zero
rendered width AND no element clips past the viewport. Mutation-proven: revert the
`.eval-lb-row` mobile grid (the `@media (max-width: 560px)` block) → the bar
width is 0 → this reds with the exact symptom.

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


def _run(eval_id: str, provider: str, model: str, score: float, axes: dict) -> dict:
    return {
        "target_provider": provider,
        "target_model": model,
        "aggregate_score": score,
        "eval_id": eval_id,
        "items_completed": 12,
        "items_total": 12,
        "items_failed": 0,
        "by_rejection_type": {
            ax: {"count": c, "mean_score": m, "min_score": 0.5, "max_score": 0.95}
            for ax, (m, c) in axes.items()
        },
        # one item carries the judge so the row gets a "judge:" column on desktop
        "items": [{"judge_provider": "claude"}],
        "completed_at": "20260617T120000",
        "started_at": "20260617T120000",
    }


def _render_stats(home: Path, runs: list[dict]) -> Path:
    rd = home / "evals" / "results"
    rd.mkdir(parents=True, exist_ok=True)
    # an eval set file so the empty-state never fires
    (home / "evals" / "eval_personalA.json").write_text(
        json.dumps({"eval_id": "personalA", "items": [{"id": f"i{i}"} for i in range(12)]}),
        encoding="utf-8",
    )
    for i, run in enumerate(runs):
        name = f"eval_{run['eval_id']}__model_{run['target_provider']}__20260617T12000{i}.json"
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


def test_eval_leaderboard_bar_visible_at_phone_width():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    runs = [
        _run("personalA", "claude", "claude-opus-4-8", 0.812,
             {"REFRAME": (0.85, 5), "REDIRECT": (0.78, 4), "SHARPENING": (0.80, 3)}),
        _run("personalA", "codex", "gpt-5.5", 0.799,
             {"COMPRESSION": (0.82, 4), "SHARPENING": (0.77, 4), "REDIRECT": (0.79, 4)}),
    ]
    page_path = _render_stats(home, runs)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 375, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval leaderboard: {errs[:3]}"

            res = page.evaluate(
                """(vw) => {
                  const lb = [...document.querySelectorAll('.eyebrow')]
                    .find(e => /Cross-provider leaderboard/i.test(e.textContent || ''));
                  if (!lb) return {found: false};
                  const card = lb.closest('section');
                  const rows = [...card.querySelectorAll('.eval-lb-row')];
                  // the BAR is the relative-positioned span holding the absolute fill
                  const barWidths = rows.map(li => {
                    const bar = li.querySelector('span[style*="position: relative"]');
                    return bar ? Math.round(bar.getBoundingClientRect().width) : -1;
                  });
                  // any element in the leaderboard card clipping past the viewport?
                  let clip = null;
                  for (const el of card.querySelectorAll('*')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.right > vw + 1.5) {
                      clip = {right: Math.round(r.right), text: (el.textContent || '').slice(0, 30)};
                      break;
                    }
                  }
                  return {found: true, rowCount: rows.length, barWidths, clip,
                          rawLeak: /\\{\\{/.test(card.innerHTML)};
                }""",
                375,
            )

            assert res["found"], "the cross-provider leaderboard did not render (need >=2 scored providers)"
            assert res["rowCount"] >= 2, f"expected >=2 leaderboard rows, got {res['rowCount']}"
            assert not res["rawLeak"], "raw {{ }} leaked in the leaderboard card (petite-vue did not mount)"
            assert res["clip"] is None, (
                "an eval-leaderboard element clips past the 375px viewport — the trailing "
                f"'judge:' column overflows the row on a phone: {res['clip']}"
            )
            for w in res["barWidths"]:
                assert w > 8, (
                    "the cross-provider eval leaderboard score BAR collapsed to "
                    f"{w}px at 375px — the 'which model wins YOUR corpus' wedge is INVISIBLE "
                    "on a phone (the six-column grid's fixed columns ate the 1fr bar)"
                )
        finally:
            browser.close()
