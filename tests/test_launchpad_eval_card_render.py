"""Browser render guard for the launchpad's multi-provider eval-summary-card.

The eval card is the "Score them against YOUR taste" value-prop surface and it
has a bug HISTORY that string-level tests can't see:
  • #281 — a whole per-axis bar silently rendered from a null score
  • #283 — a long provider label overflowed its column into the bars (VISUAL)
  • #292 — a web-era 'gemini' slug split into a duplicate leaderboard row
`test_launchpad_eval_leaderboard_canon.py` guards the #292 dedup at the DATA
layer (it asserts on `_eval_summary()`'s dict). But nothing renders the card in
a real browser with multi-provider data, so a bar that paints at NaN/0 width, a
JS error that blanks the card, or a dedup that holds in the dict but splits in
the DOM would all ship green. This pins the render: bars paint at finite widths
proportional to the score, the merged Gemini row is ONE row in the DOM, and no
NaN/undefined leaks into the visible card.

Slow-marked (spawns portal-html + chromium); skips when Playwright/chromium are
absent. Synthetic PII-free eval results, same schema as the canon test.
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


def _result(target: str, model: str, score: float, axes: dict[str, tuple[float, int]]) -> dict:
    return {
        "target_provider": target,
        "target_model": model,
        "aggregate_score": score,
        "items_completed": 5,
        "items_total": 5,
        "eval_id": "setA",
        "completed_at": "2026-06-01T00:00:00",
        "by_rejection_type": {ax: {"mean_score": s, "count": c} for ax, (s, c) in axes.items()},
        "items": [{"judge_provider": "claude"}],
    }


def _seed_multi_provider(home: Path) -> None:
    """Three branded providers + a web-era 'gemini' alias of antigravity (so the
    render must merge it to ONE Gemini row — #292 at the DOM layer)."""
    (home / "evals").mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8"
    )
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    AX = lambda a, b, c: {"REFRAME": (a, 10), "REDIRECT": (b, 8), "SHARPENING": (c, 5)}
    seeds = [
        ("eval_setA__model_claude__4.json", _result("claude", "claude-opus-4-8", 0.80, AX(0.84, 0.73, 0.90)), 4000),
        ("eval_setA__model_codex__3.json", _result("codex", "gpt-5.5", 0.77, AX(0.70, 0.65, 0.93)), 3000),
        ("eval_setA__model_antigravity__2.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50, AX(0.53, 0.40, 0.41)), 2000),
        # web-era slug for the SAME provider — must NOT split into a 4th row.
        ("eval_setA__model_gemini__1.json", _result("gemini", "gemini-3.1-pro-preview", 0.44, AX(0.50, 0.38, 0.39)), 1000),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, (mtime, mtime))


def _render_portal(home: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "launchpad.html").exists()
    return pages


_EVAL_PROBE = """() => {
  const card = document.querySelector('.eval-summary-card');
  if (!card) return {found: false};
  // every score bar on the card (per-axis bars + leaderboard bars)
  const bars = Array.from(card.querySelectorAll('span[style*="width"]'))
    .map(s => parseFloat(s.style.width))
    .filter(v => !Number.isNaN(v) || true);  // keep NaN so we can catch it
  // leaderboard rows: list items whose text starts "<n>. "
  const lbRows = Array.from(card.querySelectorAll('li'))
    .map(li => (li.textContent || '').replace(/\\s+/g, ' ').trim())
    .filter(t => /^\\d+\\.\\s/.test(t));
  const visibleText = card.textContent.replace(/\\s+/g, ' ');
  return {
    found: true,
    bars,
    nBars: bars.length,
    badBars: bars.filter(v => Number.isNaN(v) || v < 0 || v > 100),
    lbRows,
    geminiRows: lbRows.filter(t => /Gemini/.test(t)).length,
    hasNaN: /\\bNaN\\b|undefined|: null\\b/.test(visibleText),
  };
}"""


def _deg_result(target, model, score, axes, *, eval_id, items_completed, items_total, items_failed):
    """Like `_result` but parameterizes the fields that trigger the eval card's
    honesty behaviors: eval_id (mixed-set drift), items_completed/total/failed
    (the excluded-from-aggregate disclosure)."""
    return {
        "target_provider": target,
        "target_model": model,
        "aggregate_score": score,
        "items_completed": items_completed,
        "items_total": items_total,
        "items_failed": items_failed,
        "eval_id": eval_id,
        "completed_at": "2026-06-01T00:00:00",
        "by_rejection_type": {ax: {"mean_score": s, "count": c} for ax, (s, c) in axes.items()},
        "items": [{"judge_provider": "claude"}],
    }


def _seed_degenerate(home: Path) -> None:
    """A deliberately THIN, MIXED ledger — the shape the card must stay honest on:
      * latest run (claude) has a low-n axis (REFRAME n=1) beside a healthy one
        (REDIRECT n=10) → the n<3 axis must visually DEMOTE, the n=10 must not;
      * the two providers were scored on DIFFERENT eval sets (setA vs setB) →
        the mixed-set warning must show AND the per-axis-leader chips must be
        SUPPRESSED (comparing across sets is the invalid op the warning names);
      * codex failed 1 of 5 items → the excluded-from-aggregate disclosure shows.
    """
    (home / "evals").mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8"
    )
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    seeds = [
        # latest by mtime → drives the per-axis bars: REFRAME n=1 (demote), REDIRECT n=10 (keep)
        ("eval_setA__model_claude__2.json",
         _deg_result("claude", "claude-opus-4-8", 0.80, {"REFRAME": (0.84, 1), "REDIRECT": (0.73, 10)},
                     eval_id="setA", items_completed=5, items_total=5, items_failed=0), 4000),
        # different eval set → mixed_eval_sets; 1 failed item → excluded disclosure
        ("eval_setB__model_codex__1.json",
         _deg_result("codex", "gpt-5.5", 0.77, {"REFRAME": (0.70, 8)},
                     eval_id="setB", items_completed=4, items_total=5, items_failed=1), 3000),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, (mtime, mtime))


_DEGEN_PROBE = """() => {
  const card = document.querySelector('.eval-summary-card');
  if (!card) return {found: false};
  // Per-axis bar rows: the <li>s before the leaderboard whose text starts with an
  // axis name + 'n='. Read opacity to verify selective demotion.
  const axisLis = Array.from(card.querySelectorAll('li'))
    .filter(li => /^(REFRAME|REDIRECT|SHARPENING|COMPRESSION)\\b/.test((li.textContent||'').trim()));
  const axisOpacity = {};
  axisLis.forEach(li => {
    const name = (li.textContent||'').trim().split(/\\s+/)[0];
    axisOpacity[name] = parseFloat(getComputedStyle(li).opacity);
  });
  const text = (card.textContent || '').replace(/\\s+/g, ' ');
  // per-axis-leader chips are green spans like 'REFRAME: Claude 0.84'
  const leaderChips = Array.from(card.querySelectorAll('span'))
    .filter(s => /^(REFRAME|REDIRECT|SHARPENING|COMPRESSION):\\s/.test((s.textContent||'').trim()))
    .map(s => (s.textContent||'').trim());
  return {
    found: true,
    axisOpacity,
    mixedWarning: /span multiple eval sets/i.test(text),
    excludedDisclosure: /Excluded from the aggregate/i.test(text),
    leaderChips,
  };
}"""


def test_eval_card_degenerate_data_stays_honest():
    """Principle #35 at the DOM layer for the eval card's honesty behaviors. The
    happy-path test pins finite bars + dedup; this pins that THIN/MIXED data REFUSES
    the green: low-n axis demotes, mixed-set warns + suppresses per-axis chips, and
    failed items are disclosed. The eval card has a bug history (#281 null bar, #283
    overflow, #292 dup) — exactly where green-while-degenerate hides."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_degenerate(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_DEGEN_PROBE)

            assert d["found"], "eval-summary-card did not render on the degenerate ledger"
            assert not errs, f"JS errors on the degenerate eval card: {errs[:3]}"
            # 1. Selective per-axis demotion: n=1 axis dimmed, n=10 axis full.
            assert d["axisOpacity"].get("REFRAME") is not None, f"REFRAME axis row missing: {d['axisOpacity']}"
            assert d["axisOpacity"]["REFRAME"] < 0.6, (
                f"the n=1 REFRAME axis rendered at full authority (opacity "
                f"{d['axisOpacity']['REFRAME']}) — low-sample demotion regressed (#281 class)"
            )
            assert d["axisOpacity"].get("REDIRECT", 0) > 0.9, (
                f"the n=10 REDIRECT axis was demoted (opacity {d['axisOpacity'].get('REDIRECT')}) "
                "— the demotion is supposed to be SELECTIVE on low n, not blanket"
            )
            # 2. Mixed eval sets → warning shown AND per-axis-leader chips suppressed.
            assert d["mixedWarning"], "rows span two eval sets but the mixed-set warning did not render"
            assert not d["leaderChips"], (
                f"per-axis leader chips rendered under mixed eval sets ({d['leaderChips']}) — "
                "comparing per-axis scores across different sets is the invalid op the warning names"
            )
            # 3. A failed item → excluded-from-aggregate disclosure shown.
            assert d["excludedDisclosure"], (
                "codex failed 1 of 5 items but the excluded-from-aggregate disclosure did not render "
                "— the leaderboard reads as more apples-to-apples than the data is"
            )
        finally:
            browser.close()


_HEADLINE_PROBE = """() => {
  const card = document.querySelector('.eval-summary-card');
  if (!card) return {found: false};
  const h2 = card.querySelector('h2');
  const text = (card.textContent || '').replace(/\\s+/g, ' ');
  return {
    found: true,
    headline: (h2 ? h2.textContent : '').replace(/\\s+/g, ' ').trim(),
    text,
    hasMostRecent: /Most recent run:/.test(text),
  };
}"""


def test_eval_card_headline_is_winner_with_latest_run_line():
    """#303 at the DOM layer: the eval-card HEADLINE must show the STRONGEST model
    (the value proof), and when the most-recent run is a different, weaker model
    the 'Most recent run: …' line must render so the freshness isn't lost. Seeds
    the WEAKEST model as the NEWEST run (Gemini 0.50 newest, Claude 0.80 older) —
    so a recency-headline would wrongly show Gemini 0.50 in the hero. Pins:
    headline reads Claude 0.80, the secondary line names Gemini, no JS error."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    (home / "evals").mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8"
    )
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    AX = lambda a: {"REFRAME": (a, 10)}
    seeds = [
        # Strong Claude is OLDER; weak Gemini is the NEWEST (just benchmarked).
        ("eval_setA__model_claude__1.json", _result("claude", "claude-opus-4-8", 0.80, AX(0.80)), 1000),
        ("eval_setA__model_antigravity__2.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50, AX(0.50)), 2000),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, (mtime, mtime))
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_HEADLINE_PROBE)

            assert d["found"], "eval-summary-card did not render"
            assert not errs, f"JS errors on the eval card: {errs[:3]}"
            # Headline = the WINNER (Claude 0.80), NOT the newest-but-weaker Gemini 0.50.
            assert "Claude" in d["headline"] and "0.80" in d["headline"], (
                f"eval headline is not the winner (Claude 0.80): {d['headline']!r}"
            )
            assert "0.50" not in d["headline"], (
                f"the newest-but-weaker run (0.50) headlined instead of the winner: {d['headline']!r}"
            )
            # The freshness line names the most-recent (weaker) run.
            assert d["hasMostRecent"], "the 'Most recent run:' freshness line did not render"
            assert "Most recent run: Gemini" in d["text"].replace("  ", " "), (
                f"the 'Most recent run' line did not name Gemini: {d['text'][:400]!r}"
            )
        finally:
            browser.close()


def test_multi_provider_eval_card_renders_correctly():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_multi_provider(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_EVAL_PROBE)

            assert d["found"], "eval-summary-card did not render with multi-provider results"
            assert not errs, f"JS errors blanked/broke the eval card: {errs[:3]}"
            # Bars must paint at finite, in-range widths proportional to scores
            # (the #281 null-score-bar class would surface as NaN here).
            assert d["nBars"] >= 6, f"expected >=6 score bars (3 axes + 3 leaderboard), got {d['nBars']}"
            assert not d["badBars"], f"score bars at NaN / out-of-range width: {d['badBars']}"
            # #292 at the DOM layer: web-era 'gemini' + 'antigravity' = ONE row,
            # 3 providers total — not 4, not a split Gemini.
            assert len(d["lbRows"]) == 3, f"leaderboard should render 3 provider rows, got {len(d['lbRows'])}: {d['lbRows']}"
            assert d["geminiRows"] == 1, f"web-era gemini split the leaderboard — {d['geminiRows']} Gemini rows: {d['lbRows']}"
            assert not d["hasNaN"], "NaN/undefined/null leaked into the visible eval card"
        finally:
            browser.close()


def test_eval_card_promoted_not_collapsed_and_mobile_safe():
    """The eval moat card was PROMOTED out of its <details> wrapper 2026-06-07
    (founder: "promote the eval moat" — the rejection-signal benchmark is the one
    artifact no model vendor / request-router can copy). It must now render as a
    PROMINENT value card: visible without a click (offsetParent not null), NOT
    nested in a <details>, leading with the moat copy. And because it's now shown
    to everyone, its cross-provider leaderboard grid must not overflow a 375px
    phone. Mutation: re-wrap the card in <details> → offsetParent null / closest
    details truthy → reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_multi_provider(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 375, "height": 1800}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'stats.html'}")
            page.wait_for_timeout(1200)
            d = page.evaluate(
                """() => {
                  const card = document.querySelector('.eval-summary-card');
                  if (!card) return { found: false };
                  return {
                    found: true,
                    visible: card.offsetParent !== null,
                    inDetails: !!card.closest('details'),
                    // The eyebrow is CSS text-transform:uppercase, so innerText
                    // reads UPPERCASE — match the RENDERED form (the #275 lesson),
                    // and read from the CARD (the moat <p> is case-preserved).
                    moatShown: /proven on your rejections/i.test(card.innerText || '')
                               && /no model vendor or request-router can copy/i.test(card.innerText || ''),
                    overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                  };
                }"""
            )
        finally:
            browser.close()

    assert d["found"], "eval-summary-card did not render with multi-provider results"
    assert not errs, f"JS errors on the promoted eval card: {errs[:3]}"
    assert d["visible"], "the promoted eval card is not visible (still collapsed?) — offsetParent is null"
    assert not d["inDetails"], (
        "the eval card is still nested in a <details> — it must be promoted to a "
        "prominent value card (founder: promote the eval moat)"
    )
    assert d["moatShown"], "the promoted eval card must lead with the rejection-signal moat copy"
    assert d["overflow_x"] <= 4, (
        f"the promoted eval card's leaderboard overflows the 375px phone viewport "
        f"by {d['overflow_x']}px (scrollWidth={d['scrollWidth']}) — it's now shown to "
        f"everyone, so it must be mobile-safe"
    )
