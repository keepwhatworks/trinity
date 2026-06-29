"""CLASS-LEVEL guard: every provider BRAND label that sits in a FIXED-width / clamped
layout track on /stats must render on ONE LINE — the #275 brand flip must never wrap
or clip a brand inside a column tuned for the shorter raw dispatch slug.

Background — the founder symptom this closes. #275 (2026-06-06) flipped every council
surface from the raw dispatch slug (claude / codex / gemini, ~5-6 chars) to the MODEL
BRAND (Claude / GPT / Gemini), and added prefixed labels like "judge: Gemini" /
"runner-up: Gemini". A brand + prefix is WIDER than the slug a column was tuned for, so
any FIXED-width grid track or clamped element holding a provider label can now wrap to a
second line or clip — silent paint regressions on the exact "Proven on your rejections"
/ routing cards a journalist screenshots. This bit once as a one-off:

  - Iter 145 (df48522e): the cross-provider eval leaderboard's desktop ``.eval-lb-row``
    judge column was a fixed 70px tuned for the slug; "judge: Gemini" (~94px) WRAPPED to a
    2nd line, doubling every leaderboard row's height into a ragged block.

Rather than guard only that one column, this sweep is CLASS-LEVEL. On ONE populated
/stats render — seeded so the LONGEST real brand ("Gemini", from antigravity as BOTH a
provider AND a judge) lands in every brand-bearing track — it walks each width where the
brand columns are live and asserts EVERY brand-bearing cell that lives inside a
fixed/grid/table column renders on a single line (its laid-out height stays within one
line-box) and does not clip its own track (scrollWidth <= clientWidth). The cells swept:

  * ``.eval-lb-row`` judge cell (``judge: <BRAND>``) — the Iter-145 column.
  * ``.eval-lb-row`` provider cell (``<BRAND>``).
  * ``.bc-provider-row`` provider cell (``<BRAND>``, the desktop 140px column) — the
    #275 brand flip's never-geometry-guarded sibling (the existing bc-row test asserts
    the brand TEXT is present, not that it FITS the fixed column on one line).
  * the routing-table ``<th>`` brand column headers + the ``Best`` chip.

A FUTURE fixed-width column that renders a brand is therefore caught automatically: the
cell is selected by its brand CONTENT, not a hard-coded class, so a new brand-bearing
column inherits the single-line assertion the moment it carries "Gemini".

Mutation-proven: revert the desktop ``.eval-lb-row`` grid to the pre-Iter-145 fixed
``24px 80px 50px 1fr 70px 70px`` → the judge cell wraps to 2 lines at >=900px and this
reds with the exact "brand label WRAPPED inside a fixed track" symptom.

Slow + browser marked; skips without Playwright/chromium; runs in CI ``browser``.
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

# Widths where the brand-bearing FIXED columns are live. The eval-leaderboard judge
# column is hidden <=560px (.eval-lb-judge display:none) and the bc-provider/eval grids
# switch to minmax-bounded mobile tracks <=480px, so the FIXED-track invariant is tested
# at the desktop / mid widths where those columns hold their tuned fixed sizes.
WIDTHS = (1280, 1024, 900, 768)


def _seed(home: Path) -> None:
    """Seed routing/picks (via the synthetic-home seeder) + evals + conversations so
    EVERY brand-bearing fixed track renders with the LONGEST real brand ("Gemini").

    antigravity (→ "Gemini") is seeded as BOTH an eval target (provider cell) AND an
    eval judge ("judge: Gemini") AND a captured provider (bc-provider cell) — the three
    fixed tracks the #275 flip widened.
    """
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Routing cheat-sheet + picks + ELO come from real councils — reuse the PII-free
    # synthetic seeder (councils whose providers include antigravity → Gemini brand).
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "seed_synthetic_home.py")],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"seed_synthetic_home failed: {r.stderr[-400:]}"

    # Evals → cross-provider leaderboard (provider cell + judge cell). The judge is the
    # OTHER family so "judge: Gemini" / "judge: Claude" — the longest brand label —
    # actually renders on rows.
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_brandSET.json").write_text(
        json.dumps({"eval_id": "brandSET", "items": [{"id": f"i{i}"} for i in range(12)]}),
        encoding="utf-8",
    )

    def _run(provider: str, model: str, score: float, judge: str, idx: int) -> None:
        run = {
            "target_provider": provider, "target_model": model, "aggregate_score": score,
            "eval_id": "brandSET", "items_completed": 12, "items_total": 12, "items_failed": 0,
            "by_rejection_type": {
                "REFRAME": {"count": 4, "mean_score": score, "min_score": 0.5, "max_score": 0.95},
                "REDIRECT": {"count": 4, "mean_score": score - 0.02, "min_score": 0.5, "max_score": 0.95},
                "SHARPENING": {"count": 4, "mean_score": score - 0.04, "min_score": 0.5, "max_score": 0.95},
            },
            "items": [{"judge_provider": judge}],
            "completed_at": "20260619T120000", "started_at": "20260619T120000",
        }
        (results / f"eval_brandSET__model_{provider}__20260619T12000{idx}.json").write_text(
            json.dumps(run), encoding="utf-8",
        )

    # antigravity → "Gemini" in BOTH the provider cell (target) AND the judge cell.
    _run("antigravity", "gemini-3.1-pro", 0.842, "claude", 0)
    _run("claude", "claude-opus-4-8", 0.791, "antigravity", 1)
    _run("codex", "gpt-5.5", 0.733, "antigravity", 2)

    # Conversations → browser-capture rows (bc-provider cell). gemini canonical sidecar
    # is .stream.json; claude/chatgpt are .json.
    now = time.time()
    for prov, n, ext in (("claude", 5, ".json"), ("chatgpt", 4, ".json"), ("gemini", 3, ".stream.json")):
        d = home / "conversations" / prov
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            p = d / f"conv_{prov}_{i}{ext}"
            p.write_text("{}", encoding="utf-8")
            os.utime(p, (now, now))


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


# Collect every brand-bearing cell that sits in a FIXED-width column (a grid track or a
# table cell) and measure whether its brand label wraps / clips. A cell is "in a fixed
# track" if it's a direct child of a display:grid row OR a table th/td. The brand match
# (Claude/GPT/Gemini/MLX as a whole word) selects by CONTENT so a future column inherits
# the check. Returns laid-out line count (height / single-line-box) + scroll-vs-client.
_FIT_JS = r"""
() => {
  const BRAND = /(^|\s|:\s)(Claude|GPT|Gemini|MLX)(\s|$|\b)/;
  const fontLineH = (el) => {
    const s = getComputedStyle(el);
    return Math.round(parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.4);
  };
  const lineH = (el) => {
    const r = document.createRange(); r.selectNodeContents(el);
    return Math.round(r.getBoundingClientRect().height);
  };
  const cells = [];
  // (a) direct children of display:grid rows whose text is a brand label.
  document.querySelectorAll('.eval-lb-row, .bc-provider-row').forEach(row => {
    [...row.children].forEach(c => {
      const t = (c.textContent || '').trim();
      if (!BRAND.test(t)) return;
      // skip an empty/whitespace cell and the right-aligned spacer
      const lh = fontLineH(c);
      cells.push({
        where: (row.className || '').toString().split(' ')[0],
        text: t.slice(0, 40),
        lines: lh ? Math.round(lineH(c) / lh) : 1,
        sw: c.scrollWidth, cw: c.clientWidth,
      });
    });
  });
  // (b) routing-table brand column headers + the per-row Best chip.
  document.querySelectorAll('.routing-table thead th').forEach(th => {
    const t = (th.textContent || '').trim();
    if (!BRAND.test(t)) return;
    const lh = fontLineH(th);
    cells.push({ where: 'routing-th', text: t.slice(0, 40),
                 lines: lh ? Math.round(lineH(th) / lh) : 1, sw: th.scrollWidth, cw: th.clientWidth });
  });
  document.querySelectorAll('.routing-table tbody .suggestion-chip').forEach(chip => {
    const t = (chip.textContent || '').trim();
    if (!BRAND.test(t)) return;
    const lh = fontLineH(chip);
    cells.push({ where: 'routing-best', text: t.slice(0, 40),
                 lines: lh ? Math.round(lineH(chip) / lh) : 1, sw: chip.scrollWidth, cw: chip.clientWidth });
  });
  return cells;
}
"""


def test_brand_labels_in_fixed_tracks_stay_single_line_on_desktop():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed(home)
    page_path = _render_stats(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            saw_eval_judge = False
            saw_bc = False
            for w in WIDTHS:
                page = browser.new_page(viewport={"width": w, "height": 3000})
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(f"file://{page_path}", wait_until="load")
                page.wait_for_timeout(700)
                # Open any demoted <details> (browser-capture lives in one) so the row lays out.
                page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
                page.wait_for_timeout(200)
                assert not errs, f"JS errors rendering /stats at {w}px: {errs[:3]}"

                cells = page.evaluate(_FIT_JS)

                # Precondition: the sweep must actually FIND brand cells, else it's vacuous.
                assert cells, (
                    f"the brand-label sweep found NO brand-bearing fixed-track cells at {w}px "
                    "— the seed or the cell shape changed; the class guard went vacuous"
                )
                for c in cells:
                    if c["where"] == "eval-lb-row" and c["text"].startswith("judge:"):
                        saw_eval_judge = True
                    if c["where"] == "bc-provider-row":
                        saw_bc = True

                for c in cells:
                    # THE BITE: a brand label in a fixed/clamped track must stay on ONE line.
                    assert c["lines"] <= 1, (
                        f"the brand label {c['text']!r} WRAPPED to {c['lines']} lines inside the "
                        f"fixed {c['where']} track at {w}px — the post-#275 brand flip overflowed a "
                        "column tuned for the shorter raw dispatch slug, making the row tall/ragged "
                        "(the Iter-145 'judge column wrapped, tall ragged rows' CLASS)"
                    )
                    # And it must not clip its own track (truncation past the column edge).
                    assert c["sw"] <= c["cw"] + 1, (
                        f"the brand label {c['text']!r} CLIPS its fixed {c['where']} track at {w}px "
                        f"(scrollWidth {c['sw']} > clientWidth {c['cw']}) — the brand overran the "
                        "column tuned for the raw slug (post-#275 brand-label overflow)"
                    )
                page.close()

            # Both the Iter-145 judge column AND the never-geometry-guarded bc-provider
            # column must have been exercised — otherwise the consolidation is hollow.
            assert saw_eval_judge, (
                "the eval-leaderboard 'judge: <BRAND>' cell was never swept — the Iter-145 "
                "column the class generalizes from was not seeded/rendered"
            )
            assert saw_bc, (
                "the browser-capture brand cell was never swept — the #275 sibling this guard "
                "newly covers was not seeded/rendered"
            )
        finally:
            browser.close()
