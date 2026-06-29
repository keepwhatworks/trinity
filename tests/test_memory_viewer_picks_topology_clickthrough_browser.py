"""Cross-surface guard: the picks.json reader's "View in topology →" link must
click through to the topology with THAT basin's detail panel open.

This is the #300-class cross-link (picks ↔ topology key spaces). Post-#298 both
picks.json and topics.json key by the lens basin id (b00..), so a pick links to the
topology basin of the same id — a plain identity match. If that keying regresses
(picks keyed by task_type again, or the deep-link `?basin=` handler breaks), the
"View in topology →" link lands on a basin that isn't in topics → an empty/absent
detail panel, and the "see WHY this model wins here" navigation silently dies.

browser_smoke covers this (surfaces 21–23) but browser_smoke is NOT in CI — only the
`-m browser` pytest job is. The pytest viewer tests cover the routing reader's n=1
filter and the picks-reader WRONG-TYPE fallback, but nothing in CI drives the picks
reader's populated click-through to the topology. This pins it: seed picks.json +
topics.json with MATCHING basin ids, render the real viewer, click the pick's
"View in topology →", and assert it lands on `?basin=<id>` with a populated detail
panel.

Mutation-proven: point the picks basin id at one absent from topics (the #300
key-space mismatch) and the detail panel comes up empty → this reds. (Verified by
hand during authoring against matching + mismatched ids.)

Slow + browser marked; skips when Playwright/chromium are absent.
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


def _vec(i: int) -> list[float]:
    v = [0.0, 0.0, 0.0, 0.0]
    v[i % 4] = 1.0
    return v


def _seed(home: Path) -> None:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": _vec(0), "size": 20, "label": "Design",
         "top_terms": ["design", "arch"], "representatives": [{"id": "r0", "snippet": "a design prompt"}]},
        {"id": "b01", "centroid": _vec(1), "size": 12, "label": "Debug",
         "top_terms": ["debug", "fix"], "representatives": [{"id": "r1", "snippet": "a debug prompt"}]},
    ]}), encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True)
    # picks keyed by the SAME basin ids as topics → identity cross-link resolves.
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c1"]},
        "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": ["c2"]},
    }), encoding="utf-8")


def _render_portal(home: Path) -> Path:
    _seed(home)
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
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


def test_picks_reader_view_in_topology_link_opens_the_basin_detail():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:140]))
            page.on(
                "console",
                lambda m: errs.append(m.text[:140])
                if m.type == "error" and "favicon" not in m.text.lower()
                else None,
            )

            page.goto(f"file://{pages / 'memory.html'}?file=picks.json")
            page.wait_for_timeout(1000)

            # The picks reader renders a "View in topology →" link per basin, href
            # `memory.html?file=topics.json&basin=<id>`.
            link = page.query_selector("a[href*='basin=']")
            assert link is not None, "picks reader rendered no 'View in topology →' cross-link"
            href = link.get_attribute("href") or ""
            assert "basin=b00" in href or "basin=b01" in href, f"unexpected cross-link href: {href}"
            basin_id = "b00" if "basin=b00" in href else "b01"

            link.click()
            page.wait_for_timeout(1800)  # topology mounts + deep-link focuses the basin

            landed = page.url
            detail_text = page.evaluate(
                """() => { const d = document.querySelector('.topics-basin-detail, [class*=detail]');
                          return d ? (d.innerText || '').trim() : ''; }"""
            )
        finally:
            browser.close()

    assert f"basin={basin_id}" in landed, (
        f"the 'View in topology →' link did not navigate to ?basin={basin_id} "
        f"(landed at {landed!r}) — the picks→topology deep-link broke"
    )
    # The basin's OWN content must be in the panel — NOT just the >20-char
    # "Click a basin…" placeholder (which would false-pass a broken deep-link).
    # b00 → label "Design" / top-terms design,arch; b01 → "Debug" / debug,fix.
    expected = "design" if basin_id == "b00" else "debug"
    assert expected in detail_text.lower(), (
        f"the topology basin-detail panel did not open basin {basin_id}'s content "
        f"after the picks cross-link — the ?basin= deep-link didn't focus the basin "
        f"(picks↔topology #300 class). Expected {expected!r} in detail={detail_text!r}"
    )
    assert not errs, f"JS errors during the picks→topology click-through: {errs[:4]}"
