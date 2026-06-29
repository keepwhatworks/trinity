"""Real-browser guard: the eval hero number carries the mixed-eval-set caveat when
the leaderboard rows span different sets — and refuses it when they agree.

The eval card's HERO ("claude · 0.80 · your strongest model") is, by the card's own
#303 comment, "the proof a journalist screenshots." But the headline is the top score
among each provider's most-recent run, and those runs can target DIFFERENT eval sets
(rebuild a set, re-score only some providers). When they do, the hero crowns whoever
got the highest NUMBER even though it isn't a head-to-head: claude 0.80 on a 5-item
set "beats" codex 0.74 on a different 10-item set, while on the one shared set codex
actually leads. The mixed_eval_sets flag was surfaced ONLY in the comparison block
below the fold; the hero — the screenshot — presented a clean "best model" claim.
That violates the confidence-honesty rule (a mixed-set claim must be demoted on EVERY
claim surface, not just the table). This adds the caveat to the hero.

Green-gate discipline: the caveat must be SHOWN when rows span sets AND REFUSED when
they don't (else it's noise that cries wolf on every legitimate single-set
leaderboard). Two synthetic homes drive both:
  * MIXED — claude on setA, codex on setB → hero shows "not a head-to-head win".
  * SAME  — claude + codex both on setA  → hero caveat ABSENT.

Mutation-proven: drop the `v-if="mixed_eval_sets"` hero caveat → the MIXED assertion
reds; render it unconditionally → the SAME (refused) assertion reds. Verified by hand
2026-06-09 (real corpus is mixed-set: claude 0.80/setA vs codex 0.74+antigravity
0.45/setB — the hero crowned claude across non-comparable sets).

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
_HERO_CAVEAT = "not a head-to-head win"


def _mkresult(rd: Path, provider: str, eval_id: str, score: float, ts: str) -> None:
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"eval_{eval_id}__model_{provider}__{ts}.json").write_text(json.dumps({
        "target_provider": provider, "target_model": provider + "-x",
        "aggregate_score": score, "eval_id": eval_id,
        "items_completed": 5, "items_total": 5, "items_failed": 0,
        "by_rejection_type": {}, "items": [], "completed_at": ts, "started_at": ts,
    }), encoding="utf-8")


def _render(home: Path, pairs: list[tuple[str, str, float]]) -> Path:
    rd = home / "evals" / "results"
    for i, (prov, eid, sc) in enumerate(pairs):
        _mkresult(rd, prov, eid, sc, f"20260609T1200{i:02d}")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", "portal-html"],
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists()
    return page


def _hero_caveat_visible(pairs) -> bool:
    """Render a fresh home with these results and return whether the hero mixed-set
    caveat is actually VISIBLE in the browser (not just present in the DOM)."""
    from playwright.sync_api import sync_playwright
    import tempfile
    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    page_path = _render(home, pairs)
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2200})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval card: {errs[:3]}"
            # Visible (not display:none) text match — petite-vue v-if removes the
            # node entirely, so query for an element CONTAINING the caveat text.
            visible = page.evaluate(
                """(needle) => {
                  for (const el of document.querySelectorAll('p, div')) {
                    if ((el.innerText || '').includes(needle) && el.offsetParent !== null) return true;
                  }
                  return false;
                }""",
                _HERO_CAVEAT,
            )
            return bool(visible)
        finally:
            browser.close()


def test_eval_hero_mixed_set_caveat_shown_and_refused():
    pytest.importorskip("playwright.sync_api")

    failures: list[str] = []
    # MIXED: different eval sets → hero caveat MUST show.
    if not _hero_caveat_visible([("claude", "setA", 0.80), ("codex", "setB", 0.74)]):
        failures.append("MIXED eval sets: the hero mixed-set caveat is NOT shown — the "
                        "hero crowns a 'best model' across non-comparable sets with no warning")
    # SAME: one eval set → hero caveat MUST be refused (green-gate).
    if _hero_caveat_visible([("claude", "setA", 0.80), ("codex", "setA", 0.74)]):
        failures.append("SAME eval set: the hero mixed-set caveat fired anyway — it cries "
                        "wolf on a legitimate single-set leaderboard")

    assert not failures, "eval hero mixed-set caveat regressed:\n  " + "\n  ".join(failures)
