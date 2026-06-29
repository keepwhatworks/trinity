"""Regression: the memory viewer's picks.json reader must display the "Use
<provider>" routing rule as the MODEL BRAND — folding web-era CAPTURE slugs first,
then branding the dispatch slug (codex → GPT, antigravity → Gemini).

Found 2026-06-01 by EYEBALLING the real picks.json reader: three cortex cards
read "Use chatgpt" while every other surface showed "codex". `chatgpt` is a
web-era CAPTURE slug; a picks.json consolidated before the #249/#260 outcome-slug
canon still carries `routing_rule.primary: "chatgpt"`. The fix folds
chatgpt→codex / claude_ai→claude / gemini→antigravity in the reader's
`canonProviderSlug`, mirroring council_schema.normalize_provider_slug.

#275 (Iter 79, 2026-06-18): the BRAND display followed. The picks Reader's
"Use <X>" is a USER-FACING routing recommendation — the SAME picks.json winner
the launchpad cheat-sheet already brands "GPT"/"Gemini". It was the leftover
un-branded sibling (the founder call landed 2026-06-06; the eval-leaderboard
judge sibling closed in Iter 78). So the display now reads the model brand via
`providerBrand` (canonProviderSlug + the Claude/GPT/Gemini map), and this test
asserts the BRAND — a raw dispatch slug ("Use codex") here is the regression.

Slow-marked (spawns portal-html + chromium); skips when Playwright/chromium absent.
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

# basin -> (stored primary slug, expected displayed MODEL BRAND after canon+brand)
# Web-era capture slug folds to its dispatch slug, then brands to the model trio.
_CASES = {
    "basin_chatgpt": ("chatgpt", "GPT"),       # chatgpt → codex → GPT
    "basin_gemini": ("gemini", "Gemini"),      # gemini → antigravity → Gemini
    "basin_claude_ai": ("claude_ai", "Claude"),  # claude_ai → claude → Claude
    "basin_codex": ("codex", "GPT"),           # codex → GPT
    "basin_claude": ("claude", "Claude"),      # claude → Claude (self-brands)
}


def _synthetic_picks() -> dict:
    out = {}
    for i, (basin, (primary, _expected)) in enumerate(_CASES.items()):
        out[basin] = {
            "routing_rule": {"primary": primary, "challenger": "claude",
                             "reason": "synthetic"},
            "trust_score": {"value": 0.5, "interpretation": "use rule"},
            "n_episodes": 3 + i,
        }
    return out


def _render_portal(home: Path) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps(_synthetic_picks()), encoding="utf-8"
    )
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


def test_picks_reader_canonicalizes_web_era_primary_slugs():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1100}).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json")
            page.wait_for_timeout(1200)
            result = page.evaluate(
                """() => {
                  const out = {};
                  document.querySelectorAll('.pick-card').forEach(card => {
                    const basin = card.dataset.task;
                    const prim = card.querySelector('.pick-primary');
                    out[basin] = prim ? prim.textContent : null;
                  });
                  return out;
                }"""
            )
        finally:
            browser.close()

    assert not errors, f"picks reader threw a page error (render crashed?): {errors}"
    # The render must have produced a card + primary for every basin.
    assert len(result) == len(_CASES), f"missing pick cards: {result}"
    for basin, (_stored, expected) in _CASES.items():
        shown = result.get(basin)
        assert shown == f"Use {expected}", (
            f"{basin}: expected 'Use {expected}', got {shown!r} "
            "(provider not branded at the display boundary — #275 raw-slug leak)"
        )
    # Belt-and-suspenders: no raw DISPATCH/CAPTURE slug leaks into the rendered
    # labels. We can only assert ABSENCE of slugs that don't collide with a brand:
    #   - "codex" / "antigravity" — the #275 dispatch-slug leak (brand is GPT/Gemini)
    #   - "chatgpt" / "claude_ai" — web-era capture slugs (never a brand string)
    # EXCLUDED: "gemini" (== lower("Gemini"), the legitimate brand) and "claude"
    # (== lower("Claude")) self-brand, so their lowercased form is expected to
    # appear — they cannot distinguish a leak from a correct brand here.
    joined = " ".join(v or "" for v in result.values()).lower()
    for slug in ("chatgpt", "claude_ai", "codex", "antigravity"):
        assert slug not in joined, (
            f"raw provider slug {slug!r} leaked into the picks display (expected the "
            f"model brand Claude/GPT/Gemini, #275): {result}"
        )
