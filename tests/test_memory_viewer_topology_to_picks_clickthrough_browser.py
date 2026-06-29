"""Real-browser guard: the topology basin detail's "Routes to <winner> →" xlink
must NAVIGATE to the picks Reader AND land focused on THAT basin's pick card —
the topology→picks cross-navigation direction.

WHY THIS EXISTS
---------------
The cross-memory navigation arc is two-directional:
  * picks Reader card → "View in topology →" → the topology basin detail
    (guarded by test_memory_viewer_picks_topology_clickthrough_browser), and
  * topology basin detail → "Routes to <brand> · margin <X> →" xlink →
    the picks Reader, FOCUSED on that basin's card.

The SECOND direction — the one a user takes when they're exploring the topology
graph, click a basin, see "Routes to Claude", and want to drill into the pick's
evidence — was guarded ONLY by a string-presence check in test_memory_viewer.py
(`assert ".topics-pick-xlink" in html`). That check stays GREEN if the xlink's
href targets the wrong file/param, if the click navigates nowhere, or if the
deep-linked picks card never gets `.pick-card-focused` — i.e. it cannot see the
founder symptom "clicked 'Routes to Claude' and landed on the picks page with NO
card highlighted / on the wrong page". A dead-end cross-link reads as broken.

This drives the REAL interaction over the documented `file://` prod path: render
the viewer on a populated home (topics b00..b02 with MATCHING picks ids), open
the topology, CLICK a basin node (d3's bound click handler), CLICK the
`.topics-pick-xlink`, and assert (a) the page is now at
`?file=picks.json&task=b00` and (b) the `.pick-card-focused` card is present and
names that basin. Mutation-proven: break the href (point `task=` at the wrong
basin, or `file=picks.json` at the wrong file) → the focused-card assertion reds
while the old string-presence check stays green.

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

# basin id → (winner_slug, rendered margin). picks keyed by the SAME ids as topics
# so the post-#298 identity bridge resolves. b00→claude (decisive margin) is the
# clickthrough target; the others ensure the focus lands on the RIGHT card, not
# just "a" card.
_PICKS = {
    "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c1"]},
    "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": ["c2"]},
    "b02": {"winner": "antigravity", "count": 7, "margin": 0.55, "n_episodes": 7, "evidence": ["c3"]},
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
    (home / "scoreboard" / "picks.json").write_text(json.dumps(_PICKS), encoding="utf-8")
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


def test_topology_routes_to_xlink_navigates_to_focused_pick_card():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    target_basin = "b00"
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

            # Click the target basin's NODE circle via d3's bound data — the real
            # exploration interaction that opens its detail panel.
            clicked = page.evaluate(
                """(bid) => {
                  const c = [...document.querySelectorAll('#content svg circle')]
                    .find(x => x.__data__ && x.__data__.id === bid);
                  if (!c) return false;
                  c.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                  return true;
                }""",
                target_basin,
            )
            if not clicked:
                failures.append(f"{target_basin}: no node circle bound to this basin id in the graph")
            else:
                page.wait_for_timeout(400)
                # The "Routes to <brand> →" cross-link must exist AND point at the
                # picks Reader for THIS basin. A non-vacuous precondition: the xlink
                # rendered (so a regression that drops it is a distinct, named failure).
                xlink_href = page.evaluate(
                    "() => { const a = document.querySelector('.topics-pick-xlink'); return a ? a.getAttribute('href') : null; }"
                )
                if not xlink_href:
                    failures.append(
                        f"{target_basin}: detail panel rendered NO '.topics-pick-xlink' "
                        "(the 'Routes to <brand> →' cross-link to the picks Reader is gone)"
                    )
                else:
                    # THE BITE: actually click it and confirm the navigation landed on
                    # the picks Reader FOCUSED on this basin's card — not the wrong page,
                    # not an unfocused dead-end. file:// preserves the query string here
                    # (same-document SPA nav reads location.search), so this exercises
                    # the real href the user clicks.
                    page.evaluate("() => document.querySelector('.topics-pick-xlink').click()")
                    page.wait_for_timeout(700)
                    landed = page.evaluate(
                        """() => {
                          const focused = document.querySelector('.pick-card-focused');
                          return {
                            search: location.search,
                            focusedExists: !!focused,
                            focusedText: focused ? (focused.innerText || '').replace(/\\s+/g, ' ') : '',
                          };
                        }"""
                    )
                    if "file=picks.json" not in landed["search"] or f"task={target_basin}" not in landed["search"]:
                        failures.append(
                            f"the 'Routes to →' xlink did not navigate to "
                            f"?file=picks.json&task={target_basin} — landed at {landed['search']!r} "
                            "(a dead-end / wrong-target cross-link reads as broken)"
                        )
                    if not landed["focusedExists"]:
                        failures.append(
                            "clicked 'Routes to Claude →' and the picks Reader rendered NO "
                            "'.pick-card-focused' card — the deep-link landed UNFOCUSED, a "
                            f"dead-end the user reads as broken: {landed!r}"
                        )
                    elif target_basin not in landed["focusedText"].lower():
                        failures.append(
                            f"the focused pick card is NOT this basin ({target_basin!r}) — "
                            f"the xlink's task= param points at the wrong card: {landed['focusedText'][:200]!r}"
                        )
            if errs:
                failures.append(f"JS errors during topology→picks click-through: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "topology→picks 'Routes to →' cross-navigation regressed (founder symptom: "
        "clicked the routing line in the topology and landed on the picks page with NO "
        "card highlighted / on the wrong page):\n  " + "\n  ".join(failures)
    )
