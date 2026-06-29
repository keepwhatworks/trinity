"""Real-browser KEYBOARD-operability guard: a basin NODE in the topology graph is
reachable AND activatable by keyboard alone (WCAG 2.1.1 Keyboard, 2.4.7 Focus
Visible), not mouse-click-only (Iter 232).

The founder symptom this bites: the topology graph's d3/SVG <circle> nodes carried
ONLY an .on("click") handler — no tabindex, no role, no keydown. A keyboard-only
user could never reach a node, so the basin DETAIL panel (showDetail) was
unreachable by keyboard — and that panel is the SOLE surface carrying the per-basin
"Launch council on this topic" button + the per-rep "Replay" chips. The Raw JSON
view (keyboard-reachable) exposes the raw terms but NONE of those interactive
affordances, so there was no keyboard-equivalent path to launch a council on a
basin: a real 2.1.1 FAIL on a shipped interactive control. Fix: the nodes get
tabindex=0 + role=button + aria-label + a keydown(Enter/Space) that fires the SAME
showDetail + highlightNeighborhood as the click.

Why the existing topology tests don't catch it: every one of them opens the detail
panel by a MOUSE click (dispatch MouseEvent) or a `?basin=` URL deep-link — none
focuses a node via the keyboard and presses Enter/Space, so the keyboard wiring
could vanish and they all stay green.

Guard quality (the two BITE preconditions, met render-independently):
  (A) the node actually received FOCUS via keyboard — document.activeElement IS the
      circle (a tabindex/role regression makes .focus() a no-op or leaves focus on
      <body>, and the assertion reds);
  (B) the pre-activation detail panel is the OTHER state (empty "Click a basin …"),
      so the post-Enter routing-winner line proves a REAL keyboard transition, not a
      pre-populated panel.
The effect assertion keys on the keydown handler's showDetail output ("Routes to
<brand> · margin <X>" + the Launch button), the sole thing the binding produces.

Mutation-proven (Iter 232): strip the .attr("tabindex"/"role") + .on("keydown")
block in renderTopicsReader (src/trinity_local/memory_viewer.py) → focusing the
circle leaves activeElement on <body> and Enter does nothing → the
active-element-is-circle assertion AND the detail-transition assertion both red with
the founder symptom. Revert only the keydown (keep tabindex) → the node focuses but
Enter no-ops → the transition assertion reds.

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
    # Two basins with picks so showDetail renders the "Routes to <brand>" line +
    # the Launch button — the keyboard-only-reachable payload. b00→claude (Enter
    # path), b01→codex/GPT (Space path; codex≠its brand so the line is non-trivial).
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": [1.0, 0.0, 0.0, 0.0], "size": 20, "label": "Design",
         "top_terms": ["design", "arch"],
         "representatives": [{"id": "r0", "snippet": "a design prompt"}]},
        {"id": "b01", "centroid": [0.0, 1.0, 0.0, 0.0], "size": 12, "label": "Debug",
         "top_terms": ["debug", "fix"],
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
    """Focus the basin's <circle> the way a keyboard user reaches it (.focus()),
    then confirm document.activeElement IS that circle — precondition (A)."""
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


def test_topology_node_is_keyboard_focusable_and_activatable():
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

            # The nodes must advertise themselves as buttons in the focus order —
            # a keyboard user can't reach a node without tabindex (WCAG 2.1.1).
            node_attrs = page.evaluate(
                """() => [...document.querySelectorAll('#content svg circle.node')].slice(0, 2)
                       .map(n => ({ tabindex: n.getAttribute('tabindex'),
                                    role: n.getAttribute('role'),
                                    aria: n.getAttribute('aria-label') }))"""
            )
            if not node_attrs:
                failures.append("no .node circles rendered in the topology graph")
            for i, a in enumerate(node_attrs):
                if a.get("tabindex") != "0":
                    failures.append(f"node {i}: not in the keyboard focus order (tabindex={a.get('tabindex')!r}) — mouse-only (WCAG 2.1.1)")
                if a.get("role") != "button":
                    failures.append(f"node {i}: missing role=button (got {a.get('role')!r}) — AT can't tell it's activatable")
                if not (a.get("aria") or ""):
                    failures.append(f"node {i}: missing aria-label — focusing it announces nothing")

            # ---- Enter path (b00) -------------------------------------------
            # (B) pre-activation state is the OTHER state: the empty prompt.
            before = _detail_text(page)
            if "click a basin" not in before:
                failures.append(f"precondition: detail panel not in its empty pre-click state: {before[:120]!r}")
            # (A) focus the node via keyboard; activeElement must BE the circle.
            if not _focus_node(page, "b00"):
                failures.append("b00: could not place keyboard focus on the node circle (focus() no-op / not focusable) — keyboard user cannot reach this basin")
            else:
                page.keyboard.press("Enter")
                page.wait_for_timeout(350)
                after = _detail_text(page)
                # The effect: the SAME detail the click produces — routing winner +
                # the keyboard-only-reachable Launch button — proving the transition.
                if "routes to" not in after or "0.42" not in after:
                    failures.append(f"b00: Enter on the focused node did NOT open the basin detail (no 'Routes to … 0.42') — keyboard activation is a no-op: {after[:200]!r}")
                launch_present = page.evaluate(
                    """() => [...document.querySelectorAll('.topics-graph-detail button')]
                           .some(b => /launch council/i.test(b.textContent || ''))"""
                )
                if not launch_present:
                    failures.append("b00: 'Launch council on this topic' button NOT reachable after keyboard activation — the interactive payload stays mouse-only")

            # ---- Space path (b01) -------------------------------------------
            # A second node, activated with Space, proves the key set (not just Enter).
            if not _focus_node(page, "b01"):
                failures.append("b01: could not place keyboard focus on the node circle")
            else:
                page.keyboard.press(" ")
                page.wait_for_timeout(350)
                after2 = _detail_text(page)
                if "routes to" not in after2 or "0.31" not in after2:
                    failures.append(f"b01: Space on the focused node did NOT open the basin detail (no 'Routes to … 0.31'): {after2[:200]!r}")

            if errs:
                failures.append(f"JS errors during keyboard exploration: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "topology node KEYBOARD operability regressed (mouse-only basin nodes — "
        "WCAG 2.1.1):\n  " + "\n  ".join(failures)
    )
