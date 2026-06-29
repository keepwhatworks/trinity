"""Real-browser guard: the cross-provider eval leaderboard paints its rows in
SCORE-DESCENDING order (best on top) with the rank label `i+1` pairing the right
score — the RENDERED-SURFACE half of the data-correctness value-binding track.

The data layer is already guarded: `test_launchpad_eval_leaderboard_canon.py
::test_leaderboard_sorted_descending_by_score_not_recency` asserts that
`launchpad_data._eval_summary()`'s `comparison` list comes out score-descending
(with a recency-inverted seed so a mtime sort would mis-rank it). But that test
calls the Python builder directly — it never renders the page. The template paints
the leaderboard with `v-for="(row, i) in pageData.evalSummary.comparison"` and a
literal `{{ i + 1 }}.` rank label (launchpad_template.py ~L2241-2244): a binding
regression at the SURFACE — wrapping the loop array in a `.slice().reverse()`, a
stray re-sort in a computed, or rebinding the `v-for` to an un-sorted sibling
array — would paint the WEAKEST model on top with a "1." rank next to it while the
data-layer canon test stays GREEN. That is the surface-binding sibling of
green-while-degenerate (the #293 / Iter-219 family): a value (here the ORDER + the
rank↔score pairing) correct at the primitive but unverified at the binding that
PAINTS it.

This guard seeds three scored providers via the REAL builder with the discriminating
property INSERTION-ORDER != SORTED-ORDER: the weakest (Gemini 0.401) is written
FIRST and the strongest (Claude 0.913) LAST. It renders the REAL /stats via
`portal-html` and at 1024px reads the painted `.eval-lb-row` cells, asserting:
  1. (BITE precondition A) the leaderboard paints with >=3 rows and no raw `{{`.
  2. (BITE precondition B) the seed is genuinely discriminating — the painted
     top-row score is NOT the first-inserted (weakest) one, so an order assert
     can't pass vacuously on an already-ascending paint.
  3. the painted scores are strictly DESCENDING down the rows (best on top).
  4. the rank label on each row equals its 1-based position (`row i paints "i+1."`)
     AND the strongest score carries rank "1." — so the rank↔score pairing is
     arithmetically correct, not just monotonic text.

Mutation-proven against the SAME source the guard renders from
(render_stats_html -> launchpad_template.py): wrap the `v-for` array in
`...comparison.slice().reverse()` (a surface-only re-order the data-layer canon
test cannot see) -> the painted rows flip to ASCENDING, rank "1." lands on the
0.401 row -> this reds with the "weakest model painted on top" symptom. Restore ->
green.

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
        },
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


# The discriminating seed: WEAKEST first, STRONGEST last. A correct render must
# INVERT this insertion order to score-descending. A naive insertion-order paint
# (or a `.reverse()` binding regression) would put the 0.401 row on top.
_WEAKEST = 0.401
_MIDDLE = 0.622
_STRONGEST = 0.913


def test_eval_leaderboard_rows_paint_score_descending():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    runs = [
        _run("personalA", "antigravity", "gemini-3.1-pro", _WEAKEST, "claude"),
        _run("personalA", "codex", "gpt-5.5", _MIDDLE, "claude"),
        _run("personalA", "claude", "claude-opus-4-8", _STRONGEST, "antigravity"),
    ]
    page_path = _render_stats(home, runs)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 2400})
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
                  const out = rows.map(li => {
                    const cells = [...li.children];
                    const rank = (cells[0].textContent || '').trim();   // "1." / "2." / ...
                    const name = (cells[1].textContent || '').trim();   // brand
                    const txt = li.innerText || '';
                    const m = txt.match(/0\\.\\d{2,3}/);                  // painted score
                    return {rank, name, score: m ? parseFloat(m[0]) : null};
                  });
                  return {found: true, rows: out, rawLeak: /\\{\\{/.test(card.innerHTML)};
                }""",
            )

            # BITE precondition A: the leaderboard actually painted, fully mounted.
            assert res["found"], (
                "the cross-provider leaderboard did not render (need >=2 scored providers)"
            )
            assert not res["rawLeak"], (
                "raw {{ }} leaked in the leaderboard card (petite-vue did not mount) — "
                "an order assert below would be vacuous on an unmounted template"
            )
            rows = res["rows"]
            assert len(rows) >= 3, f"expected >=3 leaderboard rows, got {len(rows)}: {rows}"
            scores = [r["score"] for r in rows]
            assert all(s is not None for s in scores), (
                f"a leaderboard row painted no score: {rows}"
            )

            # BITE precondition B: the SEED is genuinely discriminating — checked on
            # the fixture constants (RENDER-INDEPENDENT), not the painted DOM. The
            # runs are inserted weakest-first, so insertion order != score-descending;
            # the render must ACTIVELY sort for the order assert below to be non-vacuous.
            # Deliberately NOT keyed on scores[0]: a reverse-binding regression makes
            # scores[0] == _WEAKEST, and we want THAT to red at the ORDER assert with
            # the clear "weakest model on top" symptom — not here with a misleading
            # "seed not discriminating" message a dev could "fix" by reordering the
            # seed (which would mask the very regression this guards).
            _seed_insertion_order = [_WEAKEST, _MIDDLE, _STRONGEST]
            assert _seed_insertion_order != sorted(_seed_insertion_order, reverse=True), (
                "the seed's insertion order is already score-descending — the fixture "
                "no longer discriminates an insertion-order/no-op paint from a real "
                "sort. Keep the weakest run FIRST so the render must actively re-sort."
            )

            # THE BITE (order): scores must be strictly DESCENDING down the rows —
            # best model on top. A `.reverse()` / insertion-order binding regression
            # paints the weakest 0.401 on top and reds here.
            for a, b in zip(scores, scores[1:]):
                assert a > b, (
                    f"the eval leaderboard rows are NOT score-descending: painted "
                    f"{scores} top-to-bottom. The strongest model on YOUR rejections "
                    "must rank FIRST — a binding regression (a .reverse() / re-sort / "
                    "rebind of the v-for array) painted a WEAKER model above a STRONGER "
                    "one on the 'proven on your rejections · YOUR corpus' card "
                    "(founder symptom: 'weakest model on top of the leaderboard')"
                )

            # THE BITE (rank<->score pairing): rank label = 1-based row position, and
            # the strongest score carries rank "1." — the i+1 label must pair the
            # right score, not just be monotonic text.
            for idx, r in enumerate(rows):
                assert r["rank"] == f"{idx + 1}.", (
                    f"leaderboard row #{idx + 1} painted rank label {r['rank']!r}, "
                    f"expected {idx + 1}!r — the `{{ i + 1 }}.` rank binding drifted "
                    "from the row position"
                )
            assert abs(rows[0]["score"] - _STRONGEST) < 1e-6, (
                f"rank '1.' must carry the STRONGEST score {_STRONGEST}, but row 1 "
                f"painted {rows[0]['score']} — the rank label and the score sort "
                "disagree on the leaderboard winner"
            )
        finally:
            browser.close()
