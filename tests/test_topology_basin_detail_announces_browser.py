"""Real-browser STATUS-MESSAGE guard: activating a topology basin node ANNOUNCES the
detail panel it populated to a screen reader (WCAG 4.1.3 Status Messages).

The founder symptom this bites: a basin <circle> node carries tabindex/role/keydown
(Iter 232 made it keyboard-OPERABLE), and Enter/Space fires showDetail — but showDetail
silently rewrote the `.topics-graph-detail` panel with NO announcement. Browser-confirmed:
after a keyboard user presses Enter on a focused node, document.activeElement STAYS on
the <circle>, and that detail panel sits EARLIER in the DOM than the node ("detail
PRECEDES node") — it holds the ONLY "Launch council on this topic" + per-rep Replay
controls, reachable only by Shift+Tab BACKWARD past the whole graph. A sighted user sees
the panel change; a screen-reader user heard SILENCE — no signal the panel updated, which
basin opened, or how it routes. The viewer's one populate-on-activation panel was mute.

The fix (Iter 282): showDetail pushes a concise summary — "Showing basin <label>. <N>
threads. routes to <brand>." (or "leans <brand>, a near-tie" below the routing floor) —
through the SAME #sr-status live region the copy chips already use (announceCopy()).
Focus intentionally stays on the node (the user keeps exploring); the announcement is
what makes the silent panel-swap perceivable.

Why the existing topology tests don't catch it:
  - test_topology_node_keyboard_activate_browser asserts the detail TEXT changed (a
    VISUAL/DOM check) — it stays GREEN while the announcement is absent.
  - test_memory_viewer_copy_announces_to_screen_reader_browser asserts the COPY chip
    announces — a DIFFERENT action (clipboard), reachable only AFTER the panel is open.
None drives a keyboard activation and reads #sr-status for the PANEL-OPENED status.

Guard quality (the two BITE preconditions, met render-independently):
  (A) the page paints + the node actually takes keyboard focus (activeElement IS the
      circle) AND the detail TEXT transitions out of its empty "Click a basin" state —
      so a no-render / no-activation can't vacuously pass;
  (B) the seed (b00→claude margin 0.42, b01→codex margin 0.31) is checked independently,
      so the announcement's brand+verb are discriminating, not boilerplate.

Mutation-proven (Iter 282): delete the announceCopy(...) status push at the end of
showDetail (src/trinity_local/memory_viewer.py) → the detail text still transitions
(positive control GREEN) but #sr-status stays empty after Enter → the announcement
assertion reds with the founder symptom. Restore → GREEN.

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


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    # Two basins with picks so showDetail's announcement carries a real routing
    # verb+brand: b00 routes to Claude (Enter path), b01 routes to GPT (codex≠brand,
    # Space path) — both margins above the floor so neither is a near-tie.
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": [1.0, 0.0, 0.0, 0.0], "size": 20, "thread_count": 7,
         "label": "Design", "top_terms": ["design", "arch"],
         "representatives": [{"id": "r0", "snippet": "a design prompt"}]},
        {"id": "b01", "centroid": [0.0, 1.0, 0.0, 0.0], "size": 12, "thread_count": 4,
         "label": "Debug", "top_terms": ["debug", "fix"],
         "representatives": [{"id": "r1", "snippet": "a debug prompt"}]},
    ]}), encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c1"]},
        "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": ["c2"]},
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


def _focus_node(page, basin_id: str) -> bool:
    return page.evaluate(
        """(bid) => {
          const c = [...document.querySelectorAll('#content svg circle')]
            .find(x => x.__data__ && x.__data__.id === bid);
          if (!c) return false;
          c.focus();
          return document.activeElement === c;
        }""",
        basin_id,
    )


def _detail_text(page) -> str:
    return page.evaluate(
        """() => { const d = document.querySelector('.topics-graph-detail');
                  return d ? (d.innerText || '').replace(/\\s+/g, ' ').toLowerCase() : ''; }"""
    )


def _sr_text(page) -> str:
    return page.evaluate(
        "() => { const r = document.getElementById('sr-status'); return r ? r.textContent : '<none>'; }"
    )


def test_topology_basin_detail_activation_announces_to_screen_reader():
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
            page.wait_for_timeout(1700)  # d3 mounts + force settles

            # Precondition (A): the live region exists and starts empty — a stale /
            # missing region would make a later read vacuously pass or fail wrongly.
            sr_before = _sr_text(page)
            if sr_before != "":
                failures.append(f"precondition: #sr-status not empty at rest: {sr_before!r}")
            before = _detail_text(page)
            if "click a basin" not in before:
                failures.append(f"precondition: detail panel not in its empty pre-click state: {before[:120]!r}")

            # ---- Enter path (b00 → Claude) ----------------------------------
            if not _focus_node(page, "b00"):
                failures.append("b00: could not place keyboard focus on the node circle")
            else:
                page.keyboard.press("Enter")
                page.wait_for_timeout(400)
                # Positive control: the panel DID transition (so the announcement
                # assertion below isn't blamed for a render that never happened).
                after = _detail_text(page)
                if "routes to" not in after or "0.42" not in after:
                    failures.append(
                        f"b00: Enter did NOT open the basin detail (no 'Routes to … 0.42') — "
                        f"can't attribute a missing announcement: {after[:160]!r}"
                    )
                else:
                    sr = _sr_text(page)
                    low = (sr or "").lower()
                    # The founder symptom: the panel changed VISUALLY but #sr-status
                    # stayed empty — the activation is mute to a screen reader.
                    if not low or "showing basin" not in low:
                        failures.append(
                            "b00: activating the focused basin node by Enter rewrote the "
                            "detail panel but #sr-status stayed silent (text=%r) — the "
                            "panel-open is mute to a screen reader (WCAG 4.1.3). Focus "
                            "stays on the node, so this announcement is the ONLY signal "
                            "the panel changed." % sr
                        )
                    else:
                        # It must NAME the basin + its routing pick (discriminating —
                        # not a bare 'updated'): the seed proves brand+label.
                        if "design" not in low:
                            failures.append(f"b00: announcement doesn't name the basin (Design): {sr!r}")
                        if "claude" not in low:
                            failures.append(f"b00: announcement doesn't carry the routing brand (Claude): {sr!r}")

            # ---- Space path (b01 → GPT) -------------------------------------
            # A second node + Space proves the announcement is wired to showDetail
            # (shared by both key paths + click), not one key.
            if not _focus_node(page, "b01"):
                failures.append("b01: could not place keyboard focus on the node circle")
            else:
                page.keyboard.press(" ")
                page.wait_for_timeout(400)
                sr2 = (_sr_text(page) or "").lower()
                if "showing basin" not in sr2 or "debug" not in sr2:
                    failures.append(
                        f"b01: Space activation did NOT announce the opened basin "
                        f"(expected 'Showing basin Debug …'): {_sr_text(page)!r}"
                    )

            if errs:
                failures.append(f"JS errors during keyboard activation: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "topology basin-detail activation is mute to a screen reader (the panel-open "
        "Status Message regressed — WCAG 4.1.3):\n  " + "\n  ".join(failures)
    )
