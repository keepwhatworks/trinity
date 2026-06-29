"""Real-browser guard: the bare "margin 0.42" badge on the memory viewer's picks
scoreboard MUST carry a first-timer gloss explaining what the number means.

A first-timer opening the picks.json Reader (or clicking a topology basin) reads a
card like:

    b00  Design   Use Claude   [margin 0.42]   [n=9]

"Use Claude" is plain, but "margin 0.42" is bare routing jargon — the value is the
routing-confidence proxy (how decisively the chairman's winner beat the runner-up,
and whether it clears the gate `ask` actually routes on), yet nothing on the surface
tells a first-timer what 0.42 means, which direction is better, or that it gates
routing. The badge is even color-coded by the margin (low/med/high via
trustBadgeClass), but the color alone can't say what the scale is. The launchpad
cheat-sheet already glosses the IDENTICAL value (margin-over-runner-up + the gate);
the viewer's picks Reader badge and the topology basin-detail xlink were the two
un-glossed siblings — a load-bearing jargon label appearing bare with no gloss at
its first occurrence on this surface.

Fix (memory_viewer.py): a shared `marginGloss()` feeds a :title on BOTH the picks
Reader margin badge AND the topology basin-detail xlink — without changing the
visible label. The gloss names the scale ("0 = coin-flip, 1 = unanimous") and the
routing consequence (routes / decisive / near-tie → kNN).

This seeds a real picks.json + topics.json, renders the real portal, and asserts:
  * picks Reader — the "margin 0.42" badge carries a title that NAMES the metric
    ("how decisively the chairman's winner beat the runner-up") AND the scale
    ("0 = coin-flip"). A bare value with an empty/absent title reds it.
  * topology detail (real node click) — the "Routes to … · margin 0.42 →" xlink
    title likewise carries the margin gloss (not just the click-action hint).

Mutation-proven: remove the `badge.title = marginGloss(mval)` line (revert to the
bare badge) → the picks-Reader title assertion reds with the founder symptom. The
"badge paints" + "value visible" preconditions pass FIRST so the bite is the gloss,
not a vacuous green. Verified by hand 2026-06-21.

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

# The metric name + the scale anchor a first-timer needs — both must be in the gloss.
_GLOSS_METRIC = "beat the runner-up"
_GLOSS_SCALE = "coin-flip"


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps(
            {
                "basins": [
                    {
                        "id": "b00",
                        "centroid": [1.0, 0.0, 0.0, 0.0],
                        "size": 20,
                        "label": "Design",
                        "top_terms": ["design", "arch"],
                        "representatives": [{"id": "r0", "snippet": "a design prompt"}],
                    },
                    {
                        "id": "b01",
                        "centroid": [0.0, 1.0, 0.0, 0.0],
                        "size": 12,
                        "label": "Debug",
                        "top_terms": ["debug", "fix"],
                        "representatives": [{"id": "r1", "snippet": "a debug prompt"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (home / "scoreboard").mkdir(parents=True)
    # Both above the 0.15 gate → both "Routes to"; margins 0.42 / 0.31 are the
    # discriminating values the badge paints.
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps(
            {
                "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c1"]},
                "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": ["c2"]},
            }
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def test_margin_badge_carries_first_timer_gloss():
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

            # ---- picks Reader margin badge ----
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)
            margin_badge = page.evaluate(
                """() => {
                  const b = [...document.querySelectorAll('.pick-badge')]
                    .find(x => /^margin\\s/.test((x.textContent || '').trim()));
                  if (!b) return null;
                  const r = b.getBoundingClientRect();
                  return {
                    text: (b.textContent || '').trim(),
                    title: b.getAttribute('title') || '',
                    visible: r.width > 0 && r.height > 0,
                  };
                }"""
            )

            # PRECONDITION 1: the margin badge actually paints (bite the gloss, not absence).
            if not margin_badge:
                failures.append("picks Reader: no '.pick-badge' rendered the margin value at all")
            else:
                if not margin_badge["visible"]:
                    failures.append(f"picks Reader: margin badge is not visible: {margin_badge!r}")
                # PRECONDITION 2: it's the margin badge carrying the bare value a
                # first-timer can't parse.
                if "margin 0.42" not in margin_badge["text"]:
                    failures.append(
                        f"picks Reader: the badge under test must paint the seeded 'margin 0.42', got {margin_badge['text']!r}"
                    )
                title = margin_badge["title"]
                # THE BITE: the bare "margin 0.42" must carry a gloss that names the
                # metric AND the scale, or a first-timer can't tell what 0.42 means.
                if not title.strip():
                    failures.append(
                        "picks Reader: the 'margin 0.42' badge has NO gloss — a first-timer "
                        "can't tell what 0.42 means, which way is better, or that it gates routing"
                    )
                else:
                    if _GLOSS_METRIC not in title:
                        failures.append(
                            f"picks Reader: margin gloss does not NAME the metric "
                            f"('{_GLOSS_METRIC}') — got {title[:160]!r}"
                        )
                    if _GLOSS_SCALE not in title:
                        failures.append(
                            f"picks Reader: margin gloss does not anchor the SCALE "
                            f"('{_GLOSS_SCALE}', i.e. what 0 vs 1 mean) — got {title[:160]!r}"
                        )

            # ---- topology basin-detail xlink ----
            page.goto(f"file://{pages / 'memory.html'}?file=topics.json", wait_until="load")
            page.wait_for_timeout(1600)  # d3 mounts + force settles
            clicked = page.evaluate(
                """() => {
                  const c = [...document.querySelectorAll('#content svg circle')]
                    .find(x => x.__data__ && x.__data__.id === 'b00');
                  if (!c) return false;
                  c.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                  return true;
                }"""
            )
            if not clicked:
                failures.append("topology: no node circle bound to b00")
            else:
                page.wait_for_timeout(400)
                xlink = page.evaluate(
                    """() => {
                      const x = document.querySelector('.topics-pick-xlink');
                      if (!x) return null;
                      return { text: (x.textContent || '').trim(), title: x.getAttribute('title') || '' };
                    }"""
                )
                if not xlink:
                    failures.append("topology: basin detail rendered no .topics-pick-xlink")
                else:
                    # PRECONDITION: the xlink paints the bare margin value.
                    if "margin 0.42" not in xlink["text"]:
                        failures.append(
                            f"topology: basin-detail xlink must paint 'margin 0.42', got {xlink['text']!r}"
                        )
                    # THE BITE: that xlink's title must gloss the margin (not only the
                    # click-action), so the hovered "margin 0.42" is explained.
                    if _GLOSS_METRIC not in xlink["title"]:
                        failures.append(
                            "topology: the 'Routes to … · margin 0.42' xlink title does not NAME "
                            f"the margin metric ('{_GLOSS_METRIC}') — got {xlink['title'][:160]!r}"
                        )

            if errs:
                failures.append(f"JS errors during viewer render: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "the memory viewer leaked a bare 'margin 0.42' a first-timer can't parse "
        "(no inline gloss naming the metric/scale):\n  " + "\n  ".join(failures)
    )
