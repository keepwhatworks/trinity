"""Real-browser guard: clicking a topology basin shows that basin, and no frozen
picks.json winner on it.

Until 2026-10-26 the basin detail read "Routes to <model> · margin <X>" from
picks.json (#301). That tally froze when its writer was retired on 2026-08-11, and
basin ids are positional and redrawn on every lens build, so on a real machine 23 of
its 24 keys named other topics by then: the line put an old winner on an unrelated
basin, using a JS copy of a router that no longer runs. council_9b8da321624a8f0f chose
to remove it (amd_0612) and to pin the absence rather than delete the old guard
(amd_0613). This file replaces test_topology_node_click_routing_winner_browser.

The seed is the worst case for the removed code: picks keyed by the SAME ids as
topics.json, every margin above the old 0.15 routing floor, and two winners whose
brand differs from their slug (codex → GPT, antigravity → Gemini), so a returning
routing line shows up as a winner name, a brand, a margin or the "routes to" verb.

Bite: the positive control (each click opens THAT basin's top terms) proves the
detail panel rendered, so the absence assertions read a real panel, not an empty one.

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

# basin id -> (its own top terms as the detail renders them, the frozen winner's
# slug and brand, its margin). None of the last three may appear in the detail.
_SEED = {
    "b00": ("design, arch", "claude", "claude", "0.42"),
    "b01": ("debug, fix", "codex", "gpt", "0.31"),
    "b02": ("migrate, schema", "antigravity", "gemini", "0.55"),
}


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
        "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "effective_n": 9.0},
        "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "effective_n": 6.0},
        "b02": {"winner": "antigravity", "count": 7, "margin": 0.55, "n_episodes": 7, "effective_n": 7.0},
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


def test_topology_node_click_names_no_frozen_pick():
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

            for basin_id, (terms, slug, brand, margin) in _SEED.items():
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
                    """() => { const d = document.querySelector('.topics-graph-detail');
                              return d ? (d.innerText || '').replace(/\\s+/g, ' ').toLowerCase() : ''; }"""
                )
                if terms not in detail:
                    failures.append(f"{basin_id}: the click did not open this basin's detail (no {terms!r}): {detail[:200]!r}")
                    continue
                back = [w for w in ("routes to", "leans ", "margin", slug, brand, margin) if w in detail]
                if back:
                    failures.append(f"{basin_id}: the detail names the frozen picks.json winner ({back}): {detail[:200]!r}")
            ring = page.evaluate("() => document.querySelectorAll('#content svg circle.pick-basin').length")
            if ring:
                failures.append(f"{ring} node(s) still carry the pick-basin ring")
            if errs:
                failures.append(f"JS errors during node-click exploration: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, "the topology view shows frozen picks.json routing again:\n  " + "\n  ".join(failures)
