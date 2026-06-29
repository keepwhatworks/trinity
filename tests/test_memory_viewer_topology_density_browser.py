"""Regression: the topology basin graph must render HONESTLY at DEGENERATE basin
densities — 0 (empty state), 1, 2, and an extreme count (150) — not just at the
~48-basin shape the original zoom-to-fit test (#294) covers.

Found 2026-06-18 by an un-driven-cell UX sweep (Iter 91): every existing topology
browser guard seeds 6–36 basins. The degenerate ends were NEVER driven through a
real browser:

  • 0 basins — only a SOURCE-STRING check existed (test_memory_viewer.py asserts the
    literal `if (basins.length === 0)` gate is PRESENT in the JS text). That proves
    the code exists, not that the page RENDERS the honest "No topics yet" empty-state
    instead of a broken empty SVG. The #1 Trinity bug shape is a green that survives
    while the rendered value is gone; a string-presence check is exactly that blind
    spot.
  • 1 basin — `fitToView` computes the node bounding box `bw = bh = 2*radiusFor(n)`.
    The fit then divides by `bw`/`bh` (`0.9 * min(W/bw, H/bh)`). The single-node fit
    hits the 4× scale clamp; a future refactor that dropped the `radiusFor` floor (a
    zero-size lone basin → r could collapse → bw→0) would yield a NaN/Infinity
    transform and the lone node would vanish off-screen, silently, with no test to
    catch it. (Today the `if (bw <= 0 || bh <= 0) return` guard + the `10 +` radius
    floor protect it — this PINS that protection.)
  • 150 basins — the fit must still frame the whole graph at the 0.3 scale floor
    (the wide-graph case the fitToView comment anticipates); a regression in the
    corralling forceX/forceY or the floor would scatter nodes off-screen.

This drives all four densities and asserts on the RENDERED DOM (node bounding boxes,
the viewport transform string, console/page errors), never a source string:
  - 0 basins  → ZERO <svg circle> AND the honest "No topics yet" empty-state IS in
    the DOM (a rendered check, replacing the source-string blind spot).
  - 1 / 150   → every basin node renders, ON-SCREEN (fit holds), the viewport
    transform is FINITE (no NaN/Infinity), and no JS error fires.

Slow + browser marked (spawns portal-html + chromium); skips when Playwright/chromium
are absent.
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
    out = []
    for i in range(n):
        out.append({
            "id": f"b{i}",
            "centroid": _vec(i, i),
            "size": 5 + (i % 17) * 2,
            "label": f"Basin label {i}",
            "top_terms": [f"t{i}", "theme", "topic"],
            "representatives": [],
        })
    return out


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


# Reads the rendered topology state: node count, off-screen count, the viewport
# transform string, whether that transform contains NaN/Infinity, and the rendered
# "No topics yet" empty-state text (if any). All from the DOM — no source strings.
_MEASURE = r"""(() => {
  const out = {};
  const svg = document.querySelector('.topics-graph-svg, svg');
  out.svgPresent = !!svg;
  out.emptyMsg = Array.from(document.querySelectorAll('p'))
    .map(p => (p.textContent || '').trim())
    .find(t => /no topics yet/i.test(t)) || null;
  if (!svg) return out;
  const nodes = Array.from(svg.querySelectorAll('circle'));
  out.nodeCount = nodes.length;
  const sb = svg.getBoundingClientRect();
  let off = 0, nanAttr = 0;
  nodes.forEach(n => {
    const b = n.getBoundingClientRect();
    if (b.left < sb.left - 2 || b.right > sb.right + 2 ||
        b.top < sb.top - 2 || b.bottom > sb.bottom + 2) off++;
    const cx = n.getAttribute('cx'), cy = n.getAttribute('cy');
    if (cx === 'NaN' || cy === 'NaN' || cx === null || cy === null) nanAttr++;
  });
  out.offscreen = off;
  out.nanAttr = nanAttr;
  const g = svg.querySelector('g.viewport, g');
  out.transform = g ? g.getAttribute('transform') : null;
  out.transformHasNaN = !!(out.transform && /NaN|Infinity/.test(out.transform));
  return out;
})()"""


def _drive(home: Path, n_basins: int, wait_ms: int):
    from playwright.sync_api import sync_playwright

    pages = _render_portal(home, n_basins)
    target = f"file://{pages / 'memory.html'}?file=topics.json"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium for the topology-density test: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1000}).new_page()
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
            # Force sim settles in ~4.5s (alphaDecay 0.025, alpha-driven so the
            # settle time is independent of node count); fitToView fires on `end`.
            page.wait_for_timeout(wait_ms)
            info = page.evaluate(_MEASURE)
            info["errors"] = errors
        finally:
            browser.close()
    return info


def test_topology_zero_basins_renders_honest_empty_state_not_broken_svg():
    """0 basins: the page must render the honest "No topics yet" empty-state and
    NO graph — not an empty/broken SVG. Replaces the source-string-only check
    (test_memory_viewer.py finds the literal gate) with a RENDERED-DOM assertion.
    Mutation: drop the `if (basins.length === 0) return` early-return so the code
    falls through into the graph build with zero nodes → an empty <svg> renders
    (svgPresent True) and the empty-state <p> never appears → this reds."""
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    info = _drive(home, 0, wait_ms=2500)

    assert not info["errors"], f"JS errors on the 0-basin render: {info['errors'][:4]}"
    assert info["emptyMsg"], (
        "0 basins did not render the honest 'No topics yet' empty-state — the "
        "viewer fell through to a broken/empty graph (the source-string gate check "
        "can't catch this; the rendered DOM must)."
    )
    assert not info["svgPresent"], (
        "0 basins rendered a topology <svg> — the empty early-return regressed and "
        "the graph build ran with zero nodes (an empty, useless canvas)."
    )


def test_topology_single_basin_frames_node_with_finite_transform():
    """1 basin: the lone node must render ON-SCREEN with a finite radius + a finite
    viewport transform. `fitToView` and the circle `r` both flow from `radiusFor`;
    a future refactor that let the lone basin collapse the radius (e.g. dropping the
    `10 +` floor on a zero-size singleton) makes the circle `r`/`cx`/`cy` NaN and the
    fit bbox NaN — the node vanishes silently. The single-node fit's bw→0 case is
    already absorbed by the scaleExtent clamp (`Math.min(4, W/0)` → 4, NOT NaN), so
    the live risk is the radius itself, not the divide. No existing browser test
    drives a 1-basin corpus. Mutation-proven: forcing `radiusFor` to NaN on a
    single-node graph (the documented radius-collapse) reds this on the `errors`
    check (`<circle> attribute r: Expected length, "NaN"`) + nanAttr."""
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    info = _drive(home, 1, wait_ms=6000)

    assert not info["errors"], f"JS errors on the 1-basin render: {info['errors'][:4]}"
    assert info["svgPresent"] and info["nodeCount"] == 1, (
        f"the single basin did not render exactly one node (got {info.get('nodeCount')})"
    )
    assert not info["transformHasNaN"], (
        f"the 1-basin fitToView produced a NaN/Infinity viewport transform "
        f"({info['transform']!r}) — the lone node's bounding box collapsed "
        "(bw/bh → 0) and the fit divided by zero."
    )
    assert info["nanAttr"] == 0, (
        "the lone basin node has a NaN/missing cx/cy — the force layout never "
        "placed it."
    )
    assert info["offscreen"] == 0, (
        "the single basin node rendered OFF-SCREEN — the 4× fit clamp pushed the "
        f"lone node out of frame ({info['transform']!r})."
    )


def test_topology_extreme_density_frames_all_nodes_with_finite_transform():
    """150 basins (well past the real ~48): the fit must frame the WHOLE graph at
    the 0.3 scale floor — zero nodes off-screen, a finite transform, no JS error.
    The original fit test tops out at 36 basins; a regression in the corralling
    forceX/forceY or the 0.3 floor that only bites at high density would ship
    silently. Mutation: drop the forceX/forceY corral → the orthogonal outliers
    scatter past the 0.3 clamp → offscreen > 0 → this reds."""
    pytest.importorskip("playwright.sync_api")
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    info = _drive(home, 150, wait_ms=9000)

    assert not info["errors"], f"JS errors on the 150-basin render: {info['errors'][:4]}"
    assert info["svgPresent"] and info["nodeCount"] == 150, (
        f"expected 150 basin nodes, got {info.get('nodeCount')}"
    )
    assert not info["transformHasNaN"], (
        f"the 150-basin fit produced a NaN/Infinity transform ({info['transform']!r})."
    )
    assert info["offscreen"] == 0, (
        f"{info['offscreen']} of 150 basin nodes render off-screen at extreme "
        "density — the corralling forceX/forceY or the 0.3 fit floor regressed."
    )
