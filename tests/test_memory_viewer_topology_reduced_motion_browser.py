"""Regression (WCAG 2.3.3 — Animation from Interactions / vestibular safety): the
memory-viewer TOPOLOGY force-directed graph must SETTLE INSTANTLY when the OS asks
for reduced motion — it must NOT animate every node into place over hundreds of
ticks.

Found 2026-06-21 by an a11y UX sweep (Iter 237) DRIVING the real Chromium topology
under `reduced_motion="reduce"`. `renderTopicsReader` (memory_viewer.py) builds a
d3 force simulation with `.alpha(1).alphaDecay(0.025)`, which animates EVERY node
for ~273 ticks (~4.5s) on load — the longest, largest motion anywhere in Trinity's
surfaces. Two reasons the existing reduced-motion handling could never reach it:

  1. The memory viewer builds its OWN <head>/<style> and does NOT include
     design_system.SHARED_CSS, so the global `@media (prefers-reduced-motion:
     reduce)` zero (that neutralizes the launchpad/council CSS spinners) is absent.
  2. Even if it WERE present, the node motion is a JS requestAnimationFrame-driven
     d3 tick loop that moves nodes via `.attr("cx", …)` per tick — a CSS media
     block cannot stop a JS animation.

Driven proof of the un-fixed founder symptom: under `reduced_motion="reduce"` the
page's `matchMedia('(prefers-reduced-motion: reduce)').matches` returned true, yet
all 20 basin nodes' `cx`/`cy` kept CHANGING across a 150ms + 400ms sampling window
— the force graph animated identically to the default. A continuously-settling
force graph with no reduced-motion fallback is a vestibular-safety failure.

The fix (memory_viewer.py): a `matchMedia("(prefers-reduced-motion: reduce)")` gate
right after the sim's tick handler + fitToView are wired — when reduced motion is
requested, `sim.stop()`, run every tick synchronously (drain alpha to alphaMin),
then paint the final layout once and fit. The SAME final graph renders, with zero
visible node motion.

This guard DRIVES the real topology under emulated reduced motion and asserts the
nodes do NOT move across a sampling window (settled in the first paint), AND drives
the DEFAULT (no emulation) and asserts the nodes DO move — so it discriminates a
genuine reduced-motion settle from a broken/empty graph. Mutation-proven: removing
the `if (reduceMotion) {…}` settle block re-animates the nodes under reduced motion
and reds the reduced-motion assertion with the named founder symptom.

Slow + browser marked (spawns portal-html + chromium); skips when Playwright/
chromium are absent.
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
_DIM = 24


def _vec(dominant: int, jitter: int) -> list[float]:
    v = [0.02 * ((jitter + k) % 5) for k in range(_DIM)]
    v[dominant % _DIM] = 1.0
    return v


def _basins(n: int) -> list[dict]:
    return [
        {
            "id": f"b{i}",
            "centroid": _vec(i, i),
            "size": 5 + (i % 17) * 2,
            "label": f"Basin label {i}",
            "top_terms": [f"t{i}", "theme", "topic"],
            "representatives": [],
        }
        for i in range(n)
    ]


def _render_portal(home: Path, n_basins: int) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": _basins(n_basins)}), encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


# Snapshot every node's cx/cy — the d3 sim writes these per tick, so a change
# between two snapshots == the node is still animating.
_SAMPLE = r"""(() => {
  const svg = document.querySelector('.topics-graph-svg, svg');
  if (!svg) return null;
  return Array.from(svg.querySelectorAll('circle'))
    .map(n => [n.getAttribute('cx'), n.getAttribute('cy')]);
})()"""


def _moved(a, b) -> int | None:
    if a is None or b is None:
        return None
    return sum(1 for (ax, ay), (bx, by) in zip(a, b) if ax != bx or ay != by)


def _drive(home: Path, n_basins: int, reduced: bool) -> dict:
    from playwright.sync_api import sync_playwright

    pages = _render_portal(home, n_basins)
    target = f"file://{pages / 'memory.html'}?file=topics.json"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium for the reduced-motion test: {exc}")
        try:
            ctx_kwargs = {"viewport": {"width": 1280, "height": 1000}}
            if reduced:
                # Precondition (B): reduced-motion is ACTUALLY emulated at the
                # context level — render-independent, set before navigation.
                ctx_kwargs["reduced_motion"] = "reduce"
            page = browser.new_context(**ctx_kwargs).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:200]}"))
            page.on(
                "console",
                lambda m: errors.append(f"console.error: {m.text[:200]}")
                if m.type == "error" and "favicon" not in m.text.lower()
                and "woff" not in m.text.lower()
                else None,
            )
            page.goto(target)
            # Precondition (A): the topology renders with real <circle> nodes.
            page.wait_for_selector(".topics-graph-svg circle", timeout=8000)
            mm = page.evaluate(
                "window.matchMedia('(prefers-reduced-motion: reduce)').matches")
            s0 = page.evaluate(_SAMPLE)
            page.wait_for_timeout(150)
            s1 = page.evaluate(_SAMPLE)
            page.wait_for_timeout(400)
            s2 = page.evaluate(_SAMPLE)
            info = {
                "node_count": len(s0) if s0 else 0,
                "mm_reduce": mm,
                "moved_0_1": _moved(s0, s1),
                "moved_1_2": _moved(s1, s2),
                "errors": errors,
            }
        finally:
            browser.close()
    return info


def test_topology_force_sim_animates_by_default():
    """CONTROL / discriminator: with NO reduced-motion emulation the force sim
    MUST animate — nodes keep moving across the sampling window. This proves the
    reduced-motion assertion below is measuring a real settle (motion vs. no
    motion), not an empty/broken graph that never moves either way."""
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    info = _drive(home, 20, reduced=False)

    assert not info["errors"], f"JS errors on the default topology render: {info['errors'][:4]}"
    assert info["node_count"] == 20, (
        f"expected 20 basin nodes for the animation control, got {info['node_count']}"
    )
    assert info["mm_reduce"] is False, (
        "the default context unexpectedly reported reduced-motion — the control is "
        "not measuring the animated path."
    )
    # The force sim is mid-settle in the first ~550ms (alphaDecay 0.025 ≈ 4.5s to
    # settle), so SOME node must move between snapshots.
    assert (info["moved_0_1"] or 0) > 0 or (info["moved_1_2"] or 0) > 0, (
        "the topology force simulation did NOT animate by default "
        f"(moved 0→1={info['moved_0_1']}, 1→2={info['moved_1_2']}) — the graph is "
        "static even without reduced motion, so the reduced-motion settle test "
        "below can't discriminate. Did the sim stop running, or did the basins "
        "render with no positions?"
    )


def test_topology_force_sim_settles_instantly_under_reduced_motion():
    """WCAG 2.3.3: under `reduced_motion="reduce"` the force graph must settle in
    the FIRST paint — no node moves across the sampling window. Mutation-proven:
    deleting the `if (reduceMotion) { sim.stop(); … }` settle block in
    memory_viewer.py re-animates every node under reduced motion and reds the
    moved-count assertion below with this exact founder symptom."""
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    info = _drive(home, 20, reduced=True)

    assert not info["errors"], (
        f"JS errors on the reduced-motion topology render: {info['errors'][:4]}"
    )
    # Precondition (A): the graph actually rendered its nodes.
    assert info["node_count"] == 20, (
        f"expected 20 basin nodes under reduced motion, got {info['node_count']} — "
        "the topology didn't render, so the settle assertion would be vacuous."
    )
    # Precondition (B): reduced-motion really IS emulated on this page.
    assert info["mm_reduce"] is True, (
        "the page did not see prefers-reduced-motion: reduce — the emulation never "
        "reached the page, so a 'no motion' result would be meaningless."
    )
    # THE ASSERTION: under reduced motion every node is already at its resting
    # position in the first paint, so NOTHING moves between snapshots.
    assert info["moved_0_1"] == 0 and info["moved_1_2"] == 0, (
        "the memory-viewer TOPOLOGY force simulation STILL ANIMATES under "
        "prefers-reduced-motion: reduce "
        f"({info['moved_0_1']} nodes moved 0→150ms, {info['moved_1_2']} moved "
        "150→550ms) — the ~273-tick (~4.5s) every-node settle plays unreduced for "
        "a motion-sensitive user. The JS d3 tick loop is not gated on "
        "matchMedia('(prefers-reduced-motion: reduce)'); it must sim.stop() + run "
        "the ticks synchronously so the SAME final layout renders with zero visible "
        "motion (WCAG 2.3.3 Animation from Interactions)."
    )
