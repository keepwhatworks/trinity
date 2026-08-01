"""CLASS-LEVEL guard: NO bar-bearing fixed-px grid row on /stats may collapse its
distribution/score BAR to ~0px OR clip the viewport at a phone width.

This is the durable generalization of the "fixed-px-grid columns eat the 1fr bar /
overflow the trailing column on a phone" defect that was fixed THREE times as one-offs:
  - Iter 32: the eval-leaderboard ``.eval-lb-row`` (24px 80px 50px 1fr 70px 70px)
  - Iter 37: the browser-capture ``.bc-provider-row`` (140px 60px 1fr 110px)
  - Iter 38: the eval per-axis ``.eval-axis-row`` (110px 50px 1fr 80px) — collapsed
    the score BAR to 0px at 320px (43px at 375px), the "which axis the model is
    strongest on" visual gone on a phone.

SCOPE, 2026-08-01: the two eval rows above are HISTORY, not current coverage. The
eval leaderboard UI was deleted on 2026-07-14 when the disagreement ledger replaced
the judge-dominated score card; neither class exists in src/ any longer. This file
was the only place still naming them, so it had been RED from that day until the
browser shard was next run — the shard is not in the default pytest selection.
``.bc-provider-row`` is the surviving seeded row and carries the coverage. The
class-level sweep below is unchanged and still catches a FUTURE fixed-px bar row
automatically, which is the point of the file; a `deadEval*` assertion now fires if
the eval rows ever return, so this docstring cannot quietly overstate its scope again.

Rather than add yet another per-row guard, this sweep is class-level: it seeds the
bar rows on ONE populated /stats render, then — at the narrowest supported
phone width (320px, where these fixed-column grids collapse hardest) — it finds
EVERY rendered BAR TRACK by its shape (a thin, height<=14px, position:relative|absolute
span carrying an absolutely-positioned background-filled child) regardless of which
grid row holds it, and asserts each track has a real rendered width. A FUTURE
fixed-px bar row is therefore caught automatically — no new test needed.

It also asserts no element clips past the viewport (the founder mobile-grid-clip
shape — a page that scrolls horizontally on a phone).

Mutation-proven (against ``.eval-axis-row``, before it was removed): dropping its
<=480px mobile grid back to a fixed ``110px 50px 1fr 80px`` at every width made all
four of its bars render at 0px at 320px and this file redded with the exact symptom.
The equivalent live mutation today is ``.bc-provider-row``'s <=480px rule at
launchpad_template.py:1162.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# 320px is the narrowest breakpoint the launchpad supports (it's in the ux-designer
# breakpoint list). The eval per-axis row's fixed columns (110+50+80 + gaps ≈ 264px)
# fully crush the 1fr bar to 0px AT 320 (they only squeeze it to 43px at 375), so the
# sweep must run here to bite the un-fixed eval-axis case.
PHONE_W = 320
# Floor: a bar narrower than this conveys no comparison. 8px matches the sibling
# per-row guards (Iter 32 / Iter 37).
BAR_FLOOR_PX = 8


def _seed_all_bar_rows(home: Path) -> None:
    """Seed the three bar-bearing /stats rows on one render:
      - eval per-axis bars + cross-provider leaderboard: two eval results, SAME
        eval_id, both with by_rejection_type axes → .eval-axis-row + .eval-lb-row.
      - browser-capture rows: captures + a sidebar listing more threads than on disk
        (missing_count>0 → the "N unsynced" pill) → .bc-provider-row.
    """
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)

    def write_result(target: str, model: str, agg: float, axes: dict[str, float]) -> None:
        by_axis = {
            ax: {
                "count": 12,
                "mean_score": m,
                "min_score": max(0.0, m - 0.1),
                "max_score": min(1.0, m + 0.1),
            }
            for ax, m in axes.items()
        }
        (results / f"eval_demoSET__model_{target}.json").write_text(
            json.dumps(
                {
                    "target_provider": target,
                    "target_model": model,
                    "aggregate_score": agg,
                    "items_completed": 48,
                    "items_total": 48,
                    "items_failed": 0,
                    "eval_id": "demoSET",
                    "by_rejection_type": by_axis,
                    "items": [{"judge_provider": "claude"}],
                    "completed_at": "2026-06-17T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    write_result(
        "antigravity",
        "gemini-3.1-pro",
        0.83,
        {"REFRAME": 0.91, "REDIRECT": 0.80, "SHARPENING": 0.74, "COMPRESSION": 0.66},
    )
    write_result(
        "codex",
        "gpt-5.5",
        0.77,
        {"REFRAME": 0.70, "REDIRECT": 0.82, "SHARPENING": 0.79, "COMPRESSION": 0.75},
    )
    (home / "evals" / "demoSET.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    conv = home / "conversations"
    (conv / "claude").mkdir(parents=True, exist_ok=True)
    (conv / "chatgpt").mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i in range(3):
        p = conv / "claude" / f"convclaude{i:02d}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (now, now))
    for i in range(2):
        p = conv / "chatgpt" / f"convgpt{i:02d}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (now, now))
    (conv / "claude" / "_sidebar.json").write_text(
        json.dumps(
            {
                "url": "https://claude.ai/api/organizations/abc123def4567890/x",
                "sidebar": {
                    "data": [{"uuid": f"convclaude{i:02d}"} for i in range(3)]
                    + [{"uuid": f"missingclaude{i:02d}"} for i in range(4)]
                },
            }
        ),
        encoding="utf-8",
    )
    (conv / "chatgpt" / "_sidebar.json").write_text(
        json.dumps({"sidebar": {"items": [{"id": f"convgpt{i:02d}"} for i in range(2)]}}),
        encoding="utf-8",
    )


def _render_stats(home: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists()
    return page


# Find every BAR TRACK by SHAPE (independent of its grid-row class, so a future
# fixed-px bar row is swept too) + report any element clipping the viewport.
_SWEEP_JS = r"""
(vw) => {
  const bars = [];
  let clip = null;
  document.querySelectorAll('*').forEach(el => {
    if (getComputedStyle(el).display !== 'grid') return;
    [...el.children].forEach(child => {
      const cs = getComputedStyle(child);
      if (cs.position !== 'relative' && cs.position !== 'absolute') return;
      const cr = child.getBoundingClientRect();
      if (!(cr.height > 0 && cr.height <= 14)) return;
      const fill = [...child.querySelectorAll('span,div')].find(f => {
        const fs = getComputedStyle(f);
        return fs.position === 'absolute'
          && fs.backgroundColor
          && fs.backgroundColor !== 'rgba(0, 0, 0, 0)';
      });
      if (!fill) return;
      bars.push({
        rowCls: (el.className || '').toString().slice(0, 48),
        barW: Math.round(cr.width),
      });
    });
  });
  document.querySelectorAll('body *').forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1.5 && getComputedStyle(e).visibility !== 'hidden' && !clip) {
      clip = {
        tag: e.tagName,
        cls: (e.className || '').toString().slice(0, 48),
        right: Math.round(r.right),
        text: (e.textContent || '').trim().slice(0, 30),
      };
    }
  });
  return {bars, clip};
}
"""


def test_no_stats_bar_grid_collapses_or_overflows_on_phone():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_all_bar_rows(home)
    page_path = _render_stats(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": PHONE_W, "height": 3200})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            # Demoted cards (e.g. browser-capture) live inside <details>; open them all
            # so every bar row is laid out and swept.
            page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
            page.wait_for_timeout(250)
            assert not errs, f"JS errors rendering /stats: {errs[:3]}"

            # Precondition: the bar rows must actually be present, else the sweep is
            # vacuous (a future seed regression would hide the real assertion).
            #
            # SCOPE NARROWED 2026-08-01. This guard used to require .eval-axis-row
            # and .eval-lb-row too. Both were DELETED from the product — the eval
            # leaderboard UI went with the judge-dominated score card on 2026-07-14,
            # when the disagreement ledger became the hero. Neither class exists
            # anywhere in src/ any more; the only file still naming them was this
            # test. So it had been RED since that removal, asserting on a surface
            # that no longer ships, and nobody saw it because `-m browser` is not in
            # the default shard. Removing a surface must remove its guard in the same
            # commit; this is the repair, not a relaxation — .bc-provider-row still
            # exists (with its <=480px mobile grid at launchpad_template.py:1160-1162)
            # and keeps the founder mobile-grid-clip coverage this file is FOR.
            present = page.evaluate(
                """() => ({
                  bcRow: document.querySelectorAll('.bc-provider-row').length,
                  deadEvalAxis: document.querySelectorAll('.eval-axis-row').length,
                  deadEvalLb: document.querySelectorAll('.eval-lb-row').length,
                })"""
            )
            assert present["bcRow"] >= 2, (
                f"the browser-capture rows did not render (got {present['bcRow']})"
            )
            # If the eval rows ever come BACK, this file must re-cover them rather
            # than silently sweeping a smaller surface than its docstring claims.
            assert present["deadEvalAxis"] == 0 and present["deadEvalLb"] == 0, (
                "the eval bar rows are rendering again (axis="
                f"{present['deadEvalAxis']}, lb={present['deadEvalLb']}). They were "
                "removed 2026-07-14 and this sweep was narrowed to match — restore "
                "them to the precondition and the docstring before shipping."
            )

            res = page.evaluate(_SWEEP_JS, PHONE_W)

            # The sweep must have FOUND bars (shape-detection sanity — a refactor that
            # renamed the bar markup must not silently make this vacuous).
            assert len(res["bars"]) >= 2, (
                "the bar-track sweep found fewer bars than seeded "
                f"({len(res['bars'])}) — the bar markup shape changed; the class-level "
                "guard would have gone vacuous (re-pin the bar-track detector)"
            )

            collapsed = [b for b in res["bars"] if b["barW"] <= BAR_FLOOR_PX]
            assert not collapsed, (
                f"a /stats distribution/score BAR collapsed to ~0px at {PHONE_W}px — the "
                "fixed-px grid columns ate the flexible bar column, so the visual is "
                f"INVISIBLE on a phone (the founder mobile-grid-clip CLASS): {collapsed}"
            )

            assert res["clip"] is None, (
                f"a /stats element clips past the {PHONE_W}px viewport — a fixed-px grid "
                "row's trailing column overflows, so the page scrolls horizontally on a "
                f"phone (the founder mobile-grid-clip shape): {res['clip']}"
            )
        finally:
            browser.close()
