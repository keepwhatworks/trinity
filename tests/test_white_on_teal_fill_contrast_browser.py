"""Real-browser WCAG-AA guard for the two WHITE-text-on-WORDMARK-TEAL fills that the
brand-mark sweep left sub-AA (measured from the COMPUTED color composited over the
full ancestor background, NOT a source string).

FOUNDER SYMPTOM (UX sweep 2026-06-21): the brand-mark sweep repointed --success and
--accent green → the WORDMARK teal #4f9095. White text on #4f9095 is 3.65:1 — below
the WCAG AA 4.5:1 body floor. Two surfaces drew WHITE text on that wordmark teal:

  1. the council UNIFIED REVIEW page `.rank-badge` "Lens pick" pill
     (council_review.py `background: var(--success)`), and
  2. the memory viewer `.view-toggle button.active` SELECTED Reader/Raw nav toggle
     (memory_viewer.py `background: var(--accent)`),

so the SELECTED nav state's label and the lens-pick marker were the LEAST-readable
text on each surface. ROOT CAUSE / CLASS: --success / --accent IS the WCAG-exempt
brand wordmark teal, not a white-text fill — the brand already ships --action
(#3f777c, white 5.07:1 AA) / --primary in the viewer scope for exactly this (the CTA
button + the Iter-198 .done-badge split both use the readable family). These two pills
were the leftover white-on-wordmark-teal siblings. Fixed by repointing each fill to
the AA-readable teal (--action / --primary), keeping the teal identity.

Mutation-proven: revert either `background:` to var(--success)/var(--accent), and the
matching test reds (~3.65). The fix clears it (~5.07). A source-string grep can't see
the COMPUTED color composited over the real fill — only execution reveals it.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# Composited-contrast helper run IN the page: folds the element's COMPUTED color over
# the entire ancestor background stack (GPU-style), returns the real WCAG ratio.
_CONTRAST_JS = r"""
(sel) => {
  function parse(c){ const m=(c||'').match(/[\d.]+/g); if(!m) return null;
    const n=m.map(Number); return {r:n[0],g:n[1],b:n[2],a:n.length>3?n[3]:1}; }
  function over(fg,bg){ const a=fg.a; return {r:fg.r*a+bg.r*(1-a), g:fg.g*a+bg.g*(1-a),
    b:fg.b*a+bg.b*(1-a), a:1}; }
  function lum(c){ const f=x=>{x/=255; return x<=0.03928?x/12.92:Math.pow((x+0.055)/1.055,2.4);};
    return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b); }
  function ratio(a,b){ const la=lum(a),lb=lum(b),hi=Math.max(la,lb),lo=Math.min(la,lb);
    return (hi+0.05)/(lo+0.05); }
  const el = document.querySelector(sel);
  if(!el) return {found:false};
  const cs = getComputedStyle(el);
  const fg = parse(cs.color);
  let stack = []; let node = el;
  while(node){ const b = parse(getComputedStyle(node).backgroundColor); if(b && b.a>0) stack.unshift(b); node = node.parentElement; }
  let acc = {r:255,g:255,b:255,a:1};
  for(const b of stack){ acc = over(b, acc); }
  const fgOver = over(fg, acc);
  return {found:true, visible: el.offsetParent !== null, text: (el.innerText||'').trim(),
          fontSize: parseFloat(cs.fontSize), fontWeight: cs.fontWeight, colorRaw: cs.color,
          bg: [Math.round(acc.r), Math.round(acc.g), Math.round(acc.b)],
          ratio: +ratio(fgOver, acc).toFixed(3)};
}
"""


def _render_unified_review_page() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_teal",
        task_cluster_id="cluster_teal",
        task_text="Which caching strategy for the read path?",
        goal="Pick the strongest answer.",
        created_at="2026-06-21T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_teal01",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="antigravity",
        member_results=[
            CouncilMemberResult(provider="claude", model="opus-4-8",
                                output_text="Use an LRU cache keyed on the read path."),
            CouncilMemberResult(provider="antigravity", model="gemini-3.1-pro",
                                output_text="Memoize at the boundary; cache the rarely-changing state."),
        ],
        synthesis_output="# Verdict\n\nThe **Gemini** answer wins on specificity.",
        # winner == antigravity → lensPickProvider matches the Gemini card → the
        # `.rank-badge` "Lens pick" pill renders (the element under test).
        routing_label=CouncilRoutingLabel(winner="antigravity", task_type="architecture"),
        created_at="2026-06-21T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_council_lens_pick_rank_badge_clears_aa():
    """The council review page's `.rank-badge` "Lens pick" pill paints WHITE text on a
    teal fill. It must clear WCAG AA 4.5:1 (13px/600 = normal text) — measured from the
    COMPUTED color composited over the real fill.

    FOUNDER SYMPTOM: the pill painted white on --success #4f9095 (the WORDMARK teal) at
    3.65:1 — below AA — when the brand-mark sweep repointed --success green→teal. Must
    use the AA-readable --action teal (the CTA family), not the wordmark fill.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.vendor import publish_vendor_files

    root = Path(tempfile.mkdtemp(prefix="trinity-teal-badge-"))
    try:
        (root / "review_pages").mkdir()
        (root / "portal_pages").mkdir()
        publish_vendor_files(root / "portal_pages")
        page_path = root / "review_pages" / "council_teal01.html"
        page_path.write_text(_render_unified_review_page(), encoding="utf-8")

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(f"file://{page_path}", wait_until="load")
                page.wait_for_timeout(900)  # petite-vue mount (v-if on the badge)

                d = page.evaluate(_CONTRAST_JS, ".rank-badge")
                # PRECONDITION / non-vacuous: the Lens-pick badge must actually paint.
                assert d.get("found") and d.get("visible"), (
                    "the .rank-badge 'Lens pick' pill never rendered (v-if did not mount?) "
                    "— the contrast assertion would be vacuous."
                )
                assert d["text"], "the .rank-badge had no visible text — sample is vacuous."
                # It is small text (the AA-normal 4.5 floor applies, not large-text 3:1).
                assert d["fontSize"] <= 16 and float(d["fontWeight"]) < 700 or d["fontSize"] < 18.66, (
                    f"the .rank-badge is no longer the small-text pill (got {d['fontSize']}px/"
                    f"{d['fontWeight']}) — re-check the contrast threshold."
                )
                assert d["ratio"] >= 4.5, (
                    f"council review .rank-badge 'Lens pick' is {d['ratio']}:1 (text {d['text']!r}, "
                    f"color {d['colorRaw']}, {d['fontSize']}px/{d['fontWeight']}, effective bg {d['bg']}) "
                    f"— below WCAG AA 4.5 for small text. FOUNDER SYMPTOM: white on --success #4f9095 "
                    f"(the WORDMARK teal) was 3.65:1. The Lens-pick fill must use the AA-readable "
                    f"--action teal (#3f777c, 5.07:1), not the wordmark --success/--accent."
                )
            finally:
                browser.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _render_memory_viewer_portal(home: Path) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    # picks.json is a non-array object → renderPicksReader supports the Reader/Raw
    # view-toggle (the element under test). Any winner key works.
    # evidence is a list of council/bundle ID STRINGS (dedup-keyed in the page
    # builder), NOT dicts — matches the real consolidate() picks.json shape.
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 4, "margin": 0.5, "n_episodes": 6,
                "evidence": ["council_1a5b74fb1df3fda0"]},
    }), encoding="utf-8")
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(json.dumps({
        "basins": [{"basin_id": "b00", "label": "schema design",
                    "top_terms": ["schema", "model"], "size": 6}],
    }), encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def test_memory_viewer_active_view_toggle_clears_aa():
    """The memory viewer's `.view-toggle button.active` SELECTED Reader/Raw nav toggle
    paints WHITE text on a teal fill. It must clear WCAG AA 4.5:1 (12px = normal text)
    — measured from the COMPUTED color composited over the real fill.

    FOUNDER SYMPTOM: the SELECTED toggle's label (Reader / Raw JSON) painted white on
    --accent #4f9095 (the WORDMARK teal) at 3.65:1 — the LEAST-readable state was the
    one the user is currently on. Must use the AA-readable --primary teal.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp(prefix="trinity-teal-toggle-")) / "trinity"
    home.mkdir(parents=True)
    try:
        pages = _render_memory_viewer_portal(home)
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
                page.wait_for_timeout(1000)

                d = page.evaluate(_CONTRAST_JS, ".view-toggle button.active")
                # PRECONDITION / non-vacuous: the active toggle must render (the picks
                # Reader mounted). Without it the contrast sample is vacuous.
                assert d.get("found") and d.get("visible"), (
                    "the .view-toggle .active button never rendered — the picks Reader "
                    "view-toggle did not mount; the contrast assertion would be vacuous."
                )
                assert d["text"], "the active toggle had no visible label — sample is vacuous."
                assert d["fontSize"] < 18.66, (
                    f"the view-toggle is no longer small text (got {d['fontSize']}px) — "
                    "re-check the contrast threshold."
                )
                assert d["ratio"] >= 4.5, (
                    f"memory viewer .view-toggle .active is {d['ratio']}:1 (label {d['text']!r}, "
                    f"color {d['colorRaw']}, {d['fontSize']}px, effective bg {d['bg']}) — below WCAG "
                    f"AA 4.5 for small text. FOUNDER SYMPTOM: the SELECTED Reader/Raw toggle painted "
                    f"white on --accent #4f9095 (the WORDMARK teal) at 3.65:1 — the least-readable "
                    f"state was the one the user is on. The active fill must use the AA-readable "
                    f"--primary teal (#3f777c, 4.96:1), not the wordmark --accent."
                )
            finally:
                browser.close()
    finally:
        shutil.rmtree(home.parent, ignore_errors=True)
