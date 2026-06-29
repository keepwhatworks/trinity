"""Regression: the topology-graph GESTURE HINT must (1) name the gesture the
user's INPUT MODALITY can actually perform, and (2) clear the 4.5:1 WCAG AA body
floor over the dark graph canvas.

Found 2026-06-21 by an un-driven-cell UX sweep (memory-viewer topology view at
touch widths). The hint at the bottom-right of the dark graph canvas
(`.topics-graph-hint`) carried TWO shipped defects, both read straight off the
rendered pixels:

  1. WRONG-MODALITY COPY — the hint hardcoded "Drag nodes · scroll to zoom · click
     for detail" on EVERY device. "scroll to zoom" is a mouse-WHEEL gesture; on a
     touch phone (the common consumer device) a scroll pans the PAGE, never the
     graph, so the instruction was actively wrong. d3-zoom v3 DOES zoom on a
     two-finger pinch on touch (verified by dispatching a synthetic TouchEvent
     pinch → the viewport transform changed), so the working gesture EXISTS — the
     copy just named the wrong one, and a touch user would never discover it.

  2. CONTRAST — the hint painted white@0.4 over the #221c18→#14110f radial canvas,
     compositing to ~3.8:1 (BELOW the 4.5:1 AA floor for 11px informational text).
     The "Graph library not loaded — try the Raw JSON view." recovery hint shares
     the class, so the broken-state guidance was sub-AA too.

This guard drives the REAL browser in a COARSE-pointer (touch) context AND a
FINE-pointer (mouse) context and asserts on the RENDERED DOM:
  - touch  → hint says "pinch to zoom" + "tap for detail" and NOT "scroll"/"click".
  - mouse  → hint says "scroll to zoom" + "click … for detail" + the keyboard
             path "Tab + Enter for detail" (Iter 232: nodes became keyboard-operable).
  - both   → the painted hint color, composited over the darkest gradient stop,
             clears 4.5:1.

Mutation-proven to bite: reverting the pointer-aware string makes the touch half
RED; reverting the @0.62 color makes the contrast assertion RED.

Slow + browser marked (spawns portal-html + chromium); skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
_SEEDER = REPO / "scripts" / "seed_synthetic_home.py"

# Darkest stop of the .topics-graph-svg radial gradient (memory_viewer.py) — the
# worst-case background a sub-AA hint would have to clear.
_CANVAS_DARKEST = (20, 17, 15)  # #14110f
_AA_FLOOR = 4.5


def _render_portal(home: Path) -> Path:
    home.mkdir(parents=True)
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Seed via the canonical seeder (publishes vendor d3 + portal deps) under the
    # isolated TRINITY_HOME — 4 basins → a real graph with a visible hint.
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed_home_topo_hint", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    old = os.environ.get("TRINITY_HOME")
    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    try:
        spec.loader.exec_module(mod)
        mod.seed(home)
    finally:
        if old is not None:
            os.environ["TRINITY_HOME"] = old
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


_HINT_PROBE = r"""() => {
  const hint = document.querySelector('.topics-graph-hint');
  if (!hint) return { present: false };
  const r = hint.getBoundingClientRect();
  const cs = getComputedStyle(hint);
  return {
    present: true,
    text: (hint.innerText || '').trim(),
    color: cs.color,
    visible: r.width > 0 && r.height > 0 && !!hint.offsetParent,
  };
}"""


def _lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _ratio(fg, bg) -> float:
    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _parse_color(s: str):
    """rgb(...) / rgba(...) → ((r,g,b), alpha)."""
    inner = s[s.find("(") + 1:s.find(")")]
    parts = [p.strip() for p in inner.split(",")]
    rgb = tuple(int(round(float(p))) for p in parts[:3])
    alpha = float(parts[3]) if len(parts) > 3 else 1.0
    return rgb, alpha


def _probe(pages: Path, *, touch: bool, viewport_w: int):
    from playwright.sync_api import sync_playwright

    target = f"file://{pages / 'memory.html'}?file=topics.json"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium for the topology-hint test: {exc}")
        try:
            ctx = browser.new_context(
                viewport={"width": viewport_w, "height": 800},
                has_touch=touch, is_mobile=touch)
            page = ctx.new_page()
            page.goto(target, wait_until="load")
            page.wait_for_timeout(1400)  # let d3 settle + the hint mount
            return page.evaluate(_HINT_PROBE)
        finally:
            browser.close()


def test_topology_hint_is_pointer_aware(tmp_path):
    pytest.importorskip("playwright.sync_api")

    pages = _render_portal(tmp_path / "trinity")

    # COARSE pointer (touch phone): must name the touch gesture.
    touch = _probe(pages, touch=True, viewport_w=375)
    assert touch["present"] and touch["visible"], (
        "topology gesture hint absent/invisible on touch — "
        f"got {touch!r}")
    t = touch["text"].lower()
    assert "pinch to zoom" in t, (
        "FOUNDER SYMPTOM: the topology hint named a MOUSE-ONLY gesture on a touch "
        "phone — it must say 'pinch to zoom' (d3-zoom v3 zooms on a two-finger "
        f"pinch on touch), not 'scroll to zoom'. Got: {touch['text']!r}")
    assert "tap for detail" in t, (
        f"touch hint should say 'tap for detail', not 'click'. Got: {touch['text']!r}")
    assert "scroll to zoom" not in t and "click for detail" not in t, (
        "FOUNDER SYMPTOM: the touch hint still leaks the mouse-only "
        f"'scroll to zoom'/'click for detail' copy. Got: {touch['text']!r}")

    # FINE pointer (mouse): keeps the wheel/click gesture copy.
    mouse = _probe(pages, touch=False, viewport_w=1280)
    assert mouse["present"] and mouse["visible"], (
        f"topology gesture hint absent/invisible on mouse — got {mouse!r}")
    m = mouse["text"].lower()
    # Keeps the wheel/click gesture copy AND advertises the keyboard path (Iter 232:
    # the basin nodes became Tab + Enter operable, so the hint names that route too).
    assert "scroll to zoom" in m and "click" in m and "for detail" in m, (
        f"mouse hint should keep 'scroll to zoom · click … for detail'. Got: {mouse['text']!r}")
    assert "tab + enter" in m, (
        "mouse hint should advertise the keyboard path 'Tab + Enter for detail' "
        f"(the topology nodes are keyboard-operable, Iter 232). Got: {mouse['text']!r}")


def test_topology_hint_clears_wcag_aa_over_dark_canvas(tmp_path):
    pytest.importorskip("playwright.sync_api")

    pages = _render_portal(tmp_path / "trinity")
    info = _probe(pages, touch=False, viewport_w=1280)
    assert info["present"] and info["visible"], (
        f"topology gesture hint absent/invisible — got {info!r}")

    rgb, alpha = _parse_color(info["color"])
    # Composite the (possibly translucent) hint ink over the darkest gradient stop —
    # the worst-case background the informational hint has to clear.
    composited = tuple(
        round(alpha * f + (1 - alpha) * b) for f, b in zip(rgb, _CANVAS_DARKEST))
    ratio = _ratio(composited, _CANVAS_DARKEST)
    assert ratio >= _AA_FLOOR, (
        "FOUNDER SYMPTOM: the topology gesture/recovery hint (white over the dark "
        f"graph canvas) reads {ratio:.2f}:1 — BELOW the {_AA_FLOOR}:1 WCAG AA body "
        f"floor for 11px informational text. painted color={info['color']!r}, "
        f"composited over #14110f → {composited}.")
