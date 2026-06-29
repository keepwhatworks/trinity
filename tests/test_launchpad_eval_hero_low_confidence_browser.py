"""Real-browser guard: the eval HERO number self-demotes at low sample size and on
single-axis dominance — and REFUSES both caveats when the run is big + balanced.

The eval card's HERO ("Gemini · 0.83 · the one signal no model vendor can copy")
is, by the card's own #303 comment, "the proof a journalist screenshots." Before
this guard a 2-item smoke run rendered an IDENTICAL confident "0.83 /1.00" hero —
no thin-sample caveat, no axis-imbalance caveat. A journalist (or the user)
screenshots "this model wins YOUR benchmark" off 2 items on a single REFRAME axis.
That violates the confidence-honesty rule (a low-n / skewed claim must be demoted on
EVERY claim surface, not just the per-axis bars). composition_floor.py was built to
caveat exactly this but was wired into NOTHING — the launchpad hero never saw it.

Two INDEPENDENT failure modes, each its own visible caveat
(Trinity-council-decided 2026-06-17, council_4416b36956074e21, winner claude,
unanimous — "low sample size and axis dominance are separate failure modes"):
  1. items_completed < 10 → "Thin sample — only N items scored" (the value-proof
     headline's 10-item bar, NOT the smaller per-axis MIN_AXIS_SAMPLES=3, because
     the hero is a PUBLIC claim).
  2. one rejection axis > 60% of scored items → "One axis dominates — REFRAME is
     X% … mostly a REFRAME score, not a balanced YOUR-kind-of-question score".

Green-gate discipline: the caveats must be SHOWN when degenerate AND REFUSED when
the run is big + balanced (else they cry wolf on every legitimate benchmark). Three
synthetic homes drive both directions:
  * THIN    — a 2-item single-axis run → "Thin sample" SHOWN, score still rendered.
  * BALANCED— a 12-item 4-axis-even run → BOTH caveats ABSENT.
  * DOMINANT— a 20-item run, 90% REFRAME → "One axis dominates" SHOWN, "Thin
              sample" ABSENT (the two gates are independent).

Mutation-proven: revert `hero_low_confidence` in launchpad_data → the THIN
assertion reds; revert the `hero_dominant_axis` computation → the DOMINANT assertion
reds; render either caveat unconditionally → the BALANCED (refused) assertion reds.

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
_THIN = "Thin sample"
_AXIS = "One axis dominates"


def _write(rd: Path, idx: int, payload: dict) -> None:
    rd.mkdir(parents=True, exist_ok=True)
    name = f"eval_{payload['eval_id']}__model_{payload['target_provider']}__20260609T1200{idx:02d}.json"
    (rd / name).write_text(json.dumps(payload), encoding="utf-8")


def _render(home: Path, payloads: list[dict]) -> Path:
    rd = home / "evals" / "results"
    for i, pl in enumerate(payloads):
        _write(rd, i, pl)
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


def _flags(payloads: list[dict]) -> dict:
    """Render a fresh home and return which hero caveats are VISIBLE + score shown."""
    from playwright.sync_api import sync_playwright
    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    page_path = _render(home, payloads)
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval card: {errs[:3]}"
            res = page.evaluate(
                """() => {
                  const vis = (needle) => {
                    for (const el of document.querySelectorAll('p, div')) {
                      if ((el.innerText || '').includes(needle) && el.offsetParent !== null) return true;
                    }
                    return false;
                  };
                  const card = document.querySelector('.eval-summary-card');
                  // The hero h2 holds the aggregate score — confirm it still renders
                  // (the caveat DEMOTES, it must not HIDE the number).
                  const h2 = card ? card.querySelector('h2') : null;
                  const hasScore = !!h2 && /\\d\\.\\d{2}/.test(h2.innerText || '');
                  // Contrast of an honesty-caveat banner's TEXT over its OWN composited
                  // background (the amber tint sits BEHIND the text). The caveat fires
                  // exactly when the eval is degenerate — the moment it must be READABLE.
                  const parseRGB = (s) => (s.match(/[\\d.]+/g) || []).map(Number);
                  const lin = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
                  const lum = ([r,g,b]) => 0.2126*lin(r)+0.7152*lin(g)+0.0722*lin(b);
                  const over = (top, bot) => { // composite top(rgba) over bot(rgb)
                    const [r,g,b,a=1] = top;
                    return [r,g,b].map((c,i) => Math.round(a*c + (1-a)*bot[i]));
                  };
                  const bgChain = (el) => { // walk up compositing every non-transparent layer
                    let layers = [];
                    let node = el;
                    while (node && node !== document.documentElement) {
                      const c = parseRGB(getComputedStyle(node).backgroundColor);
                      if (c.length >= 3 && (c[3] === undefined || c[3] > 0)) layers.push(c.length === 3 ? [...c,1] : c);
                      node = node.parentElement;
                    }
                    layers.push([255,255,255,1]); // page base
                    let acc = layers.pop();
                    while (layers.length) acc = over(layers.pop(), acc);
                    return acc.slice(0,3);
                  };
                  const ratio = (fg, bg) => {
                    const L1 = lum(fg), L2 = lum(bg);
                    const hi = Math.max(L1,L2), lo = Math.min(L1,L2);
                    return (hi+0.05)/(lo+0.05);
                  };
                  const bannerContrast = (needle) => {
                    for (const el of document.querySelectorAll('p.meta')) {
                      if ((el.innerText || '').includes(needle) && el.offsetParent !== null) {
                        const fg = parseRGB(getComputedStyle(el).color).slice(0,3);
                        return Math.round(ratio(fg, bgChain(el)) * 100) / 100;
                      }
                    }
                    return null;
                  };
                  return {thin: vis('Thin sample'), axis: vis('One axis dominates'), score: hasScore,
                          thin_contrast: bannerContrast('Thin sample'),
                          axis_contrast: bannerContrast('One axis dominates')};
                }"""
            )
            return res
        finally:
            browser.close()


_THIN_RUN = {
    "target_provider": "antigravity", "target_model": "gemini-3.1-pro",
    "aggregate_score": 0.833, "eval_id": "setA",
    "items_completed": 2, "items_total": 2, "items_failed": 0,
    "by_rejection_type": {"REFRAME": {"count": 2, "mean_score": 0.833, "min_score": 0.75, "max_score": 0.92}},
    "items": [], "completed_at": "20260609T120000", "started_at": "20260609T120000",
}

_BALANCED_RUN = {
    "target_provider": "claude", "target_model": "claude-opus-4-8",
    "aggregate_score": 0.79, "eval_id": "setA",
    "items_completed": 12, "items_total": 12, "items_failed": 0,
    "by_rejection_type": {
        "REFRAME": {"count": 3, "mean_score": 0.80, "min_score": 0.7, "max_score": 0.9},
        "REDIRECT": {"count": 3, "mean_score": 0.78, "min_score": 0.6, "max_score": 0.9},
        "SHARPENING": {"count": 3, "mean_score": 0.76, "min_score": 0.6, "max_score": 0.9},
        "COMPRESSION": {"count": 3, "mean_score": 0.82, "min_score": 0.7, "max_score": 0.95},
    },
    "items": [], "completed_at": "20260609T120010", "started_at": "20260609T120010",
}

_DOMINANT_RUN = {
    "target_provider": "codex", "target_model": "gpt-5.5",
    "aggregate_score": 0.71, "eval_id": "setA",
    "items_completed": 20, "items_total": 20, "items_failed": 0,
    "by_rejection_type": {
        "REFRAME": {"count": 18, "mean_score": 0.72, "min_score": 0.5, "max_score": 0.95},
        "REDIRECT": {"count": 2, "mean_score": 0.60, "min_score": 0.5, "max_score": 0.7},
    },
    "items": [], "completed_at": "20260609T120020", "started_at": "20260609T120020",
}


def test_eval_hero_low_confidence_shown_and_refused():
    pytest.importorskip("playwright.sync_api")

    failures: list[str] = []

    # THIN: a 2-item smoke run → "Thin sample" MUST show, score MUST still render.
    thin = _flags([_THIN_RUN])
    if not thin["thin"]:
        failures.append("THIN run (2 items): the hero 'Thin sample' caveat is NOT shown — a "
                        "2-item smoke score headlines as a clean publishable benchmark")
    if not thin["score"]:
        failures.append("THIN run: the aggregate score vanished — the caveat must DEMOTE, not HIDE the number")

    # BALANCED: a 12-item, 4-axis-even run → BOTH caveats MUST be refused.
    bal = _flags([_BALANCED_RUN])
    if bal["thin"]:
        failures.append("BALANCED run (12 items): the 'Thin sample' caveat fired above the 10-item "
                        "floor — it cries wolf on a legitimate benchmark")
    if bal["axis"]:
        failures.append("BALANCED run (4 even axes): the 'One axis dominates' caveat fired on a "
                        "balanced set — cries wolf")

    # DOMINANT: a 20-item run, 90% REFRAME → "One axis dominates" shown, "Thin sample" refused.
    dom = _flags([_DOMINANT_RUN])
    if not dom["axis"]:
        failures.append("DOMINANT run (90% REFRAME): the 'One axis dominates' caveat is NOT shown — "
                        "a single-axis score reads as a balanced 'YOUR kind of question' score")
    if dom["thin"]:
        failures.append("DOMINANT run (20 items): the 'Thin sample' caveat fired above the floor — "
                        "the two gates are not independent")

    assert not failures, "eval hero low-confidence caveats regressed:\n  " + "\n  ".join(failures)


def _subtitle(payloads: list[dict]) -> str:
    """Render a fresh /stats home and return the eval-card 'scored against …'
    subtitle text — the count-noun line that reads '1 of 1 items' when unbound."""
    from playwright.sync_api import sync_playwright
    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    page_path = _render(home, payloads)
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            return page.evaluate(
                """() => {
                  const card = document.querySelector('.eval-summary-card');
                  if (!card) return '';
                  for (const el of card.querySelectorAll('p.meta')) {
                    const t = (el.innerText || '').trim();
                    if (t.includes('scored against')) return t;
                  }
                  return '';
                }"""
            )
        finally:
            browser.close()


def test_eval_subtitle_items_count_noun_pluralizes():
    """The eval-card subtitle 'scored against N of M item(s)' pluralizes off
    M (items_total) — a single-item smoke run (eval set of 1, or `eval-run
    --limit 1`) must read 'of 1 item', NOT the ungrammatical 'of 1 items'.

    Founder-class symptom (the Iter-314/315 static-state-label vein): the count
    noun was a HARDCODED 'items' literal beside an interpolated count that
    reaches 1 on a reachable path (has_results gates on runs-exist, NOT on a
    minimum item count — a 1-item run renders 'scored against 1 of 1 items').

    Mutation-proof: revert the binding in launchpad_template.py back to a static
    'items' → the SINGULAR (items_total=1) case reds with 'of 1 items'; the
    PLURAL (items_total=2) case stays green (discriminating bite, not all-red).
    """
    pytest.importorskip("playwright.sync_api")

    one = {
        "target_provider": "codex", "target_model": "gpt-5.5",
        "aggregate_score": 0.83, "eval_id": "solo",
        "items_completed": 1, "items_total": 1, "items_failed": 0,
        "by_rejection_type": {"REFRAME": {"count": 1, "mean_score": 0.83, "min_score": 0.83, "max_score": 0.83}},
        "items": [], "completed_at": "20260609T120000", "started_at": "20260609T120000",
    }
    two = {
        "target_provider": "codex", "target_model": "gpt-5.5",
        "aggregate_score": 0.83, "eval_id": "duo",
        "items_completed": 2, "items_total": 2, "items_failed": 0,
        "by_rejection_type": {"REFRAME": {"count": 2, "mean_score": 0.83, "min_score": 0.7, "max_score": 0.95}},
        "items": [], "completed_at": "20260609T120001", "started_at": "20260609T120001",
    }

    failures: list[str] = []

    sub_one = _subtitle([one])
    if "of 1 item" not in sub_one or "of 1 items" in sub_one:
        failures.append(
            "items_total=1: the eval subtitle reads %r — expected the SINGULAR "
            "'of 1 item' (the '1 of 1 items' unbound-plural founder symptom)" % sub_one
        )

    sub_two = _subtitle([two])
    if "of 2 items" not in sub_two:
        failures.append(
            "items_total=2: the eval subtitle reads %r — expected the PLURAL "
            "'of 2 items'" % sub_two
        )

    assert not failures, "eval subtitle item count-noun pluralization regressed:\n  " + "\n  ".join(failures)


def test_eval_hero_caveat_banner_text_meets_wcag_aa():
    """The honesty-caveat banner TEXT clears WCAG AA (4.5:1) over its OWN amber tint.

    The 'Thin sample' / 'One axis dominates' banners paint their text over a
    rgba(138,109,59,0.08) amber tint that sits BEHIND the text — so the readable
    floor is the COMPOSITED contrast, not the text-on-white contrast. The banner
    fires exactly when the eval is degenerate (a thin smoke run, a single-axis
    REFRAME-heavy corpus), which is the moment the user most needs to read WHY the
    score is demoted. The eval card hardcoded `color: #8a6d3b` (4.25:1 over the
    composited tint — below the AA body floor), the lighter sibling the Iter-176
    `--warning`→`--warning-text` (#79591b, the AA-clean amber) split never reached.

    Mutation-proof: revert the three banner `color:` back to the literal `#8a6d3b`
    in launchpad_template.py → this reds at ~4.25:1 while the SHOWN/REFUSED
    visibility assertions above stay green (the bite is on the COMPOSITED contrast,
    not the presence of the banner).
    """
    pytest.importorskip("playwright.sync_api")

    failures: list[str] = []
    FLOOR = 4.5

    thin = _flags([_THIN_RUN])
    if thin["thin_contrast"] is None:
        failures.append("THIN run: 'Thin sample' banner not found to measure contrast")
    elif thin["thin_contrast"] < FLOOR:
        failures.append(
            f"THIN run: 'Thin sample' caveat text is {thin['thin_contrast']:.2f}:1 over its "
            f"amber tint — below WCAG AA {FLOOR} (the #8a6d3b-on-rgba(138,109,59,0.08) sub-AA "
            "regression; use --warning-text #79591b)"
        )

    dom = _flags([_DOMINANT_RUN])
    if dom["axis_contrast"] is None:
        failures.append("DOMINANT run: 'One axis dominates' banner not found to measure contrast")
    elif dom["axis_contrast"] < FLOOR:
        failures.append(
            f"DOMINANT run: 'One axis dominates' caveat text is {dom['axis_contrast']:.2f}:1 over "
            f"its amber tint — below WCAG AA {FLOOR} (the #8a6d3b-on-rgba(138,109,59,0.08) sub-AA "
            "regression; use --warning-text #79591b)"
        )

    assert not failures, "eval hero caveat banner contrast regressed:\n  " + "\n  ".join(failures)
