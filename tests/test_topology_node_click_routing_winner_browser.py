"""Real-browser guard: clicking a basin NODE in the topology graph surfaces that
basin's routing winner + margin inline (#301) — the #298 cortex collapse made
visible where the user actually explores.

#301's usefulness: when a user clicks a basin in the topology graph and that basin
has a consolidated pick, the detail panel shows "Routes to <winner> · margin <X>"
— so they discover WHICH model wins for THIS kind of question right where they're
exploring their topics, without a trip to the picks Reader. It's the payoff of the
cortex-into-lens collapse (the routing basins ARE the topology basins).

That rendered line is unguarded today. The picks↔topology click-through test
(test_memory_viewer_picks_topology_clickthrough_browser) lands via the `?basin=`
URL deep-link and asserts the basin's LABEL ("design"/"debug") — not the winner or
margin. The topology-fit test CLICKS a node but seeds EMPTY representatives /no
picks, so the "Routes to …" line never renders there. So the basinIdToPick binding
or the detail panel's "Routes to <winner> · margin <X>" render could regress and
every existing test stays green while the feature silently goes blank.

This drives the actual exploration interaction: render the topology view on a
populated home (picks b00→claude/0.42, b01→codex/0.31, b02→antigravity/0.55),
CLICK each basin's node circle (d3's bound click handler, not a URL), and assert
the detail panel names that basin's winner BY MODEL BRAND (#275: codex→GPT,
antigravity→Gemini — the SAME picks.json winner the launchpad cheat-sheet brands)
AND its margin. Mutation-proven: drop the "Routes to" line (or break the
basinIdToPick lookup) → the winner/margin assertion reds; revert providerBrand →
the raw-slug-absent assertion reds with "Routes to codex"/"Routes to antigravity".

#275 (Iter 79): b01/b02 are the load-bearing cases — codex and antigravity are
the two slugs that DIFFER from their brand, so a slug leak ("Routes to codex") is
visible. b00→claude self-brands ("claude"→"Claude") and would HIDE the leak, which
is exactly how the un-branded sibling survived (the same trap as the eval judge).

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

# basin id → (winner_slug, expected_BRAND, margin string as rendered). picks keyed
# by the SAME ids as topics so the identity bridge resolves (post-#298). The detail
# panel must name the winner by MODEL BRAND (#275), never the raw dispatch slug.
# b01 (codex→GPT) + b02 (antigravity→Gemini) are the slugs that DIFFER from brand —
# the cases that expose a slug leak; b00 (claude→Claude) self-brands and hides it.
_EXPECTED = {
    "b00": ("claude", "claude", "0.42"),
    "b01": ("codex", "gpt", "0.31"),
    "b02": ("antigravity", "gemini", "0.55"),
}
# Raw dispatch slugs that must NOT appear in any branded routing line (claude is
# excluded — it is both the slug and lower("Claude"), the one self-branding case).
_LEAK_SLUGS = ("codex", "antigravity")


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": [1.0, 0.0, 0.0, 0.0], "size": 20, "label": "Design",
         "top_terms": ["design", "arch"], "representatives": [{"id": "r0", "snippet": "a design prompt"}]},
        {"id": "b01", "centroid": [0.0, 1.0, 0.0, 0.0], "size": 12, "label": "Debug",
         "top_terms": ["debug", "fix"], "representatives": [{"id": "r1", "snippet": "a debug prompt"}]},
        {"id": "b02", "centroid": [0.0, 0.0, 1.0, 0.0], "size": 10, "label": "Migrate",
         "top_terms": ["migrate", "schema"], "representatives": [{"id": "r2", "snippet": "a migration prompt"}]},
    ]}), encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c1"]},
        "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": ["c2"]},
        "b02": {"winner": "antigravity", "count": 7, "margin": 0.55, "n_episodes": 7, "evidence": ["c3"]},
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


def test_topology_node_click_shows_routing_winner_and_margin():
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
            page = browser.new_context(viewport={"width": 1400, "height": 1200}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'memory.html'}?file=topics.json", wait_until="load")
            page.wait_for_timeout(1600)  # d3 mounts + force settles

            for basin_id, (winner_slug, brand, margin) in _EXPECTED.items():
                # Click the basin's NODE circle via d3's bound data — the real
                # exploration interaction (not a `?basin=` URL deep-link).
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
                    failures.append(f"{basin_id}: no node circle bound to this basin id in the graph")
                    continue
                page.wait_for_timeout(400)
                detail = page.evaluate(
                    """() => { const d = document.querySelector('.topics-basin-detail, [class*=detail]');
                              return d ? (d.innerText || '').replace(/\\s+/g, ' ').toLowerCase() : ''; }"""
                )
                # #275: the winner must read as the MODEL BRAND, not the raw slug.
                if brand not in detail:
                    failures.append(f"{basin_id}: detail panel did not name the routing winner by brand {brand!r} (slug {winner_slug!r}): {detail[:200]!r}")
                # And the raw dispatch slug must be ABSENT (the leak Iter 78/79 close).
                if winner_slug in _LEAK_SLUGS and winner_slug in detail:
                    failures.append(f"{basin_id}: raw dispatch slug {winner_slug!r} leaked into the routing line — expected the brand {brand.upper()!r} (#275): {detail[:200]!r}")
                if margin not in detail:
                    failures.append(f"{basin_id}: detail panel did not show the margin {margin!r}: {detail[:200]!r}")
                # The "Routes to" framing is what makes it legible as a routing pick.
                if "routes to" not in detail:
                    failures.append(f"{basin_id}: detail panel lost the 'Routes to' routing line: {detail[:200]!r}")
            if errs:
                failures.append(f"JS errors during node-click exploration: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, "topology node-click routing-winner surfacing (#301) regressed:\n  " + "\n  ".join(failures)
