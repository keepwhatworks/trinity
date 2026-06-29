"""Real-browser guard: a near-tie basin BELOW the routing gate must not be shown as
a confident route in the memory viewer — neither the picks Reader nor the topology
basin detail may claim the router "Routes to"/"Use"s it.

picks.json keeps every basin with >=2 real-contest councils, but `ask` only ROUTES
on a basin whose winner-margin clears lens_routing.WINNER_MARGIN_FLOOR (0.15) — below
that it's a near-tie and `ask` falls to kNN. The viewer was rendering ALL picks as
confident: the topology detail said "Routes to <winner> · margin 0.08" and the picks
Reader said "Use <winner>" with a badge colored by a hardcoded 0.4/0.7 cut that
predated #298/#299. On the real corpus the margin median is ~0.17, so ~half the picks
are near the floor and ~11/23 basins are sub-floor coin-flips — every one of which
the viewer falsely advertised as a routed pick (the green-while-degenerate shape the
launchpad cheat-sheet already guards against via #299; this is that fix propagated to
the viewer's two confidence surfaces).

The existing #301 guard (test_topology_node_click_routing_winner_browser) seeds ONLY
above-floor margins (0.42 / 0.31), so it never exercised the sub-floor branch — both
surfaces could (and did) overclaim a route for a coin-flip with every test green.

This seeds one sub-floor basin (b00, margin 0.08) and one confident basin (b01,
margin 0.42), renders the real portal, and asserts:
  * picks Reader — b00 shows "Lean Claude" + "near-tie" (NOT "Use Claude"); b01 shows
    "Use GPT"; the picks Reader never says "Routes to". (Provider display is the
    MODEL BRAND, not the dispatch slug — #275, commit 58452a15: codex→GPT.)
  * topology detail (real node click) — b00 says "leans"/"near-tie"/"knn" and NOT
    "routes to claude"; b01 says "routes to gpt".

Mutation-proven: revert the WINNER_MARGIN_FLOOR gate (so sub-floor renders "Use"/
"Routes to") → the b00 negative assertions red. Verified by hand 2026-06-09.

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

# b00 is BELOW WINNER_MARGIN_FLOOR (0.15) → near-tie, ask routes via kNN.
# b01 is ABOVE → a real route. Same ids in topics + picks → identity bridge resolves.
_SUBFLOOR = "b00"
_CONFIDENT = "b01"


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": [1.0, 0.0, 0.0, 0.0], "size": 20, "label": "Design",
         "top_terms": ["design", "arch"], "representatives": [{"id": "r0", "snippet": "a design prompt"}]},
        {"id": "b01", "centroid": [0.0, 1.0, 0.0, 0.0], "size": 12, "label": "Debug",
         "top_terms": ["debug", "fix"], "representatives": [{"id": "r1", "snippet": "a debug prompt"}]},
    ]}), encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        # sub-floor near-tie — ask does NOT route on this; viewer must not claim it does
        "b00": {"winner": "claude", "count": 8, "margin": 0.08, "n_episodes": 8, "evidence": ["c1"]},
        # confident route
        "b01": {"winner": "codex", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c2"]},
    }), encoding="utf-8")
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
    assert (pages / "memory.html").exists()
    return pages


def test_subfloor_basin_not_shown_as_routed_in_viewer():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))

            # ---- picks Reader ----
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)
            cards = page.evaluate(
                """() => {
                  const out = {};
                  document.querySelectorAll('.pick-card').forEach(c => {
                    const id = c.dataset.task || '';
                    const badges = [...c.querySelectorAll('.pick-badge')];
                    const badge = badges[0] || null;
                    // The two head badges in render order: the margin/confidence
                    // badge ("margin 0.08 · near-tie") then the sample badge
                    // ("n=8"). Captured SEPARATELY so the value assertion targets
                    // the exact derived number, not the whole-card innerText (which
                    // also carries the basin id "b00" — a substring that would let a
                    // count→margin field-swap rendering "margin 8.00" still match a
                    // naive "0.08 absent?" check via the id).
                    out[id] = {
                      text: (c.innerText || '').replace(/\\s+/g, ' '),
                      badgeClass: badge ? badge.className : '',
                      badgeTexts: badges.map(b => (b.textContent || '').replace(/\\s+/g, ' ').trim()),
                    };
                  });
                  return out;
                }"""
            )
            sub = cards.get(_SUBFLOOR, {})
            conf = cards.get(_CONFIDENT, {})
            sub_text = (sub.get("text") or "")
            # Provider display is the MODEL BRAND, not the dispatch slug (#275,
            # commit 58452a15: claude→Claude, codex→GPT).
            if "Lean Claude" not in sub_text:
                failures.append(f"picks Reader: sub-floor basin should read 'Lean Claude', got {sub_text[:160]!r}")
            if "near-tie" not in sub_text:
                failures.append(f"picks Reader: sub-floor basin missing 'near-tie' hint: {sub_text[:160]!r}")
            if "Use Claude" in sub_text:
                failures.append(f"picks Reader: sub-floor basin OVERCLAIMS 'Use Claude' (it routes via kNN): {sub_text[:160]!r}")
            if "low" not in (sub.get("badgeClass") or ""):
                failures.append(f"picks Reader: sub-floor badge not 'low' (margin 0.08 < floor): {sub.get('badgeClass')!r}")
            if "Use GPT" not in (conf.get("text") or ""):
                failures.append(f"picks Reader: confident basin should read 'Use GPT', got {(conf.get('text') or '')[:160]!r}")

            # ---- DERIVED-VALUE bindings: the margin NUMBER + the n COUNT ----
            # The head renders two separate derived numbers off the picks.json
            # tally: `margin <p.margin.toFixed(2)>` and `n=<p.count>` (memory_viewer
            # renderPicksReader). The label-flip checks above (Lean/Use/near-tie/low)
            # all key on `mval = p.margin` via the FLOOR comparison, so a binding
            # that swapped the displayed margin number for `count` (rendering
            # "margin 8.00" / "margin 9.00") OR the n badge for `margin` (rendering
            # "n=0.08") would leave every one of them GREEN while the scoreboard
            # painted a nonsense confidence number. Pin the exact strings against a
            # DISCRIMINATING fixture (margin 0.08 ≠ count 8, margin 0.42 ≠ count 9)
            # so the number a user reads to gauge a pick's confidence can't drift
            # from the tally — the surface-binding sibling of the drift-window class.
            sub_badges = sub.get("badgeTexts") or []
            conf_badges = conf.get("badgeTexts") or []
            # b00: margin 0.08, count 8.
            if not any(b == "margin 0.08 · near-tie" for b in sub_badges):
                failures.append(
                    "picks Reader: sub-floor margin badge must render the SEEDED margin "
                    f"'margin 0.08 · near-tie' (p.margin=0.08, NOT p.count=8) — got {sub_badges!r}"
                )
            if not any(b == "n=8" for b in sub_badges):
                failures.append(
                    "picks Reader: sub-floor sample badge must render 'n=8' (p.count=8, "
                    f"NOT p.margin=0.08) — got {sub_badges!r}"
                )
            # b01: margin 0.42, count 9. No near-tie (above floor) → bare 'margin 0.42'.
            if not any(b == "margin 0.42" for b in conf_badges):
                failures.append(
                    "picks Reader: confident margin badge must render the SEEDED margin "
                    f"'margin 0.42' (p.margin=0.42, NOT p.count=9) — got {conf_badges!r}"
                )
            if not any(b == "n=9" for b in conf_badges):
                failures.append(
                    "picks Reader: confident sample badge must render 'n=9' (p.count=9, "
                    f"NOT p.margin=0.42) — got {conf_badges!r}"
                )

            # The picks Reader uses Use/Lean, never the topology's "Routes to" framing.
            page_text_picks = page.evaluate("() => document.body.innerText")
            if "Routes to" in page_text_picks:
                failures.append("picks Reader leaked the topology 'Routes to' framing")

            # ---- topology detail (real node click) ----
            page.goto(f"file://{pages / 'memory.html'}?file=topics.json", wait_until="load")
            page.wait_for_timeout(1600)  # d3 mounts + force settles
            for basin_id in (_SUBFLOOR, _CONFIDENT):
                clicked = page.evaluate(
                    """(bid) => {
                      const c = [...document.querySelectorAll('#content svg circle')]
                        .find(x => x.__data__ && x.__data__.id === bid);
                      if (!c) return false;
                      c.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                      return true;
                    }""",
                    basin_id,
                )
                if not clicked:
                    failures.append(f"topology: no node circle bound to {basin_id}")
                    continue
                page.wait_for_timeout(400)
                detail = page.evaluate(
                    """() => { const d = document.querySelector('.topics-basin-detail, [class*=detail]');
                              return d ? (d.innerText || '').replace(/\\s+/g, ' ').toLowerCase() : ''; }"""
                )
                if basin_id == _SUBFLOOR:
                    if "routes to claude" in detail:
                        failures.append(f"topology: sub-floor basin FALSELY claims 'Routes to claude' (near-tie → kNN): {detail[:200]!r}")
                    if "near-tie" not in detail and "knn" not in detail:
                        failures.append(f"topology: sub-floor basin missing the near-tie/kNN clarifier: {detail[:200]!r}")
                    if "leans claude" not in detail:
                        failures.append(f"topology: sub-floor basin should say 'leans claude': {detail[:200]!r}")
                else:
                    # detail is lowercased; codex brands to GPT → "routes to gpt".
                    if "routes to gpt" not in detail:
                        failures.append(f"topology: confident basin lost 'Routes to GPT': {detail[:200]!r}")
            if errs:
                failures.append(f"JS errors during viewer render: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, "viewer over-claimed a sub-floor basin as routed:\n  " + "\n  ".join(failures)
