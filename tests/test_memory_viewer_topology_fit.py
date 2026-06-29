"""Regression: the topology basin graph must frame ALL nodes in the default view.

Found 2026-06-01 by EYEBALLING the real memory viewer's basin-relation graph
(memory.html?file=topics.json): 24 of the founder's 48 basin nodes rendered
*off-screen* by default. Two compounding bugs:

  1. No zoom-to-fit. `forceCenter` only translates the centroid; the graph could
     settle anywhere and at any scale, so half the nodes sat outside the SVG.
  2. Satellite scatter. `forceCenter` applies NO per-node force, so the
     weakly-linked singleton basins (no strong edge pulling them toward the
     cluster) flew to the far corners under the -380 charge — a tiny central
     blob ringed by scattered, clipped satellites.

The fix (v1.7.224): a transition-free zoom-to-fit on simulation end PLUS a mild
`forceX`/`forceY` (0.06) that corrals every node toward the middle. NB the
zoom-to-fit must NOT route through `zoom.transform()` — that calls
`selection.interrupt()`, which lives in d3-transition (we vendor d3-selection +
d3-force + d3-zoom + d3-interpolate but NOT d3-transition), throwing
"i.interrupt is not a function" and silently NOT applying the transform.

This pins the BEHAVIOUR: with a corpus shaped exactly like the real one (a tight
high-cosine core + near-orthogonal outlier basins), the default view must leave
zero nodes off-screen and raise no d3-transition error. Reverting either the
corralling force or the fit re-scatters the outliers and reds this.

Slow-marked (spawns portal-html + chromium); runs in the slow shard, skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# portal-html subprocess + chromium → real-browser/subprocess test. Marked slow so
# the default `pytest -q` stays fast (runs via TRINITY_SLOW=1 / `pytest -m slow`).
pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# Sized to reproduce the REAL 48-basin corpus's failure mode: ~20 edgeless,
# mutually-repelling outliers spread the layout wide enough to trip the fit's 0.3
# scale-clamp, so the zoom-to-fit ALONE can't frame them — the corralling
# forceX/forceY is what closes the last ~9 off-screen nodes (verified: fit-only on
# the real corpus = 9 off-screen, fit+corral = 0). A milder outlier set stays
# under the clamp and the fit alone suffices, which wouldn't guard the corral.
_DIM = 24
_N_CORE = 16  # tight cluster, dominant in dim 0 — mutually high cosine
_N_OUTLIER = 20  # each dominant in a DISTINCT dim 1..20 — near-orthogonal singletons


def _vec(dominant: int, jitter: int) -> list[float]:
    """A unit-ish centroid dominant in `dominant`, with tiny deterministic noise
    in the other dims so no two basins are byte-identical."""
    v = [0.02 * ((jitter + k) % 5) for k in range(_DIM)]
    v[dominant] = 1.0
    return v


def _synthetic_topics() -> dict:
    """A corpus shaped like the real one: a high-cosine core whose pairwise
    similarities monopolize the top-3n edge budget, leaving the orthogonal
    outliers edgeless — the exact shape that scatters without forceX/forceY."""
    basins = []
    for i in range(_N_CORE):
        basins.append({
            "id": f"core{i}",
            "centroid": _vec(0, i),
            "size": 8 + i * 3,
            "label": f"Core basin {i}",
            "top_terms": [f"core{i}", "shared", "theme"],
            "representatives": [],
        })
    for j in range(_N_OUTLIER):
        basins.append({
            "id": f"out{j}",
            "centroid": _vec(1 + j, 100 + j),  # dominant in dims 1..8
            "size": 1 + (j % 3),
            "label": f"Outlier basin {j}",
            "top_terms": [f"out{j}", "isolated"],
            "representatives": [],
        })
    return {"basins": basins}


# A node-click renders the basin's `representatives` into the detail panel via
# `el("li", "topics-rep", rep.snippet)` — and `el()` sets textContent, so
# corpus-derived snippets are escaped. But basin reps come from CAPTURED CHATS
# (attacker-influenceable, [[memory_viewer_xss_real_browser]]), the detail panel is
# a SEPARATE render path from the lens/topics markdown the XSS browser test covers,
# and the other topology tests use empty `representatives` so this path is never
# exercised. A future "rich rep formatting" change to innerHTML would reintroduce
# stored XSS here. This payload renders harmlessly as text iff showDetail stays on
# textContent; it executes (sets window.__XSS_FIRED__) iff someone switches to
# innerHTML.
_XSS_SNIPPET = '<img src=x onerror="window.__XSS_FIRED__=true">click-payload-onerror'


def _topics_with_xss() -> dict:
    """A few well-separated basins, EACH carrying an XSS-bearing representative so a
    click on any node renders the payload into the detail panel."""
    basins = []
    for i in range(6):
        basins.append({
            "id": f"b{i}",
            "centroid": _vec(i, i),  # distinct dominant dim → spread out, easy to hit
            "size": 6 + i * 4,
            "label": f"Basin {i}",
            "top_terms": [f"b{i}", "theme"],
            "representatives": [
                {"id": f"rep{i}", "snippet": _XSS_SNIPPET},
            ],
        })
    return {"basins": basins}


def _render_portal_with_topics(home: Path, topics: dict | None = None) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps(topics if topics is not None else _synthetic_topics()),
        encoding="utf-8",
    )
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


_MEASURE_JS = """(() => {
  const svg = document.querySelector('.topics-graph-svg, svg');
  if (!svg) return {svg: false};
  const nodes = svg.querySelectorAll('circle, .node, g.node');
  const sb = svg.getBoundingClientRect();
  let off = 0;
  nodes.forEach(n => {
    const b = n.getBoundingClientRect();
    if (b.left < sb.left - 2 || b.right > sb.right + 2 ||
        b.top < sb.top - 2 || b.bottom > sb.bottom + 2) off++;
  });
  return {svg: true, nodes: nodes.length, offscreen: off};
})()"""


def test_topology_graph_frames_all_nodes_on_load():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal_with_topics(home)
    target = f"{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed in this env
            pytest.skip(f"no launchable chromium for the topology-fit test: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1000}
            ).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on(
                "console",
                lambda m: errors.append(m.text) if m.type == "error" else None,
            )
            page.goto(f"file://{target}")
            # The force sim settles in ~4.5s (alphaDecay 0.025); fitToView fires on
            # `end`. Wait past that so we measure the settled, fitted layout.
            page.wait_for_timeout(7000)
            info = page.evaluate(_MEASURE_JS)
        finally:
            browser.close()

    assert info.get("svg"), "topology SVG never rendered"
    assert info["nodes"] == _N_CORE + _N_OUTLIER, (
        f"expected {_N_CORE + _N_OUTLIER} basin nodes, got {info['nodes']}"
    )
    # The d3-transition-free fit must not throw — `zoom.transform()` would.
    interrupt_errs = [e for e in errors if "interrupt" in e]
    assert not interrupt_errs, (
        "zoom-to-fit routed through zoom.transform() — d3-transition's "
        f"selection.interrupt() is not vendored: {interrupt_errs}"
    )
    assert info["offscreen"] == 0, (
        f"{info['offscreen']} of {info['nodes']} basin nodes render off-screen by "
        "default — the corralling forceX/forceY or the zoom-to-fit regressed "
        "(satellites scattered out of frame)."
    )


def test_topology_zoom_and_pan_do_not_throw():
    """The INTERACTIVE sibling of #294. The load-time zoom-to-fit sidesteps
    d3-transition's interrupt() (fitToView sets node.__zoom directly), and
    test_topology_graph_frames_all_nodes_on_load guards THAT path. But d3-zoom
    v3's own wheel/mousedown handlers call `interrupt(node)` (minified source:
    `r.interrupt(this)`), and the browser-global UMD binds every missing dep —
    including d3-transition — to `window.d3`, so the call is literally
    `window.d3.interrupt(this)`. We vendor d3-selection/force/zoom/interpolate but
    NOT d3-transition, so `window.d3.interrupt` was undefined and EVERY user
    scroll-zoom / pan threw "interrupt is not a function" (browser-found
    2026-06-02 — the load fix never covered user gestures). A no-op
    `window.d3.interrupt` polyfill fixes it (the graph runs a force sim, never a
    transition, so there's nothing to cancel). Mutation: drop the polyfill and the
    first wheel below throws → interrupt_errs non-empty → reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal_with_topics(home)
    target = f"{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed in this env
            pytest.skip(f"no launchable chromium for the topology-zoom test: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1000}
            ).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on(
                "console",
                lambda m: errors.append(m.text) if m.type == "error" else None,
            )
            page.goto(f"file://{target}")
            page.wait_for_timeout(7000)  # force sim settles + fitToView fires

            box = page.evaluate(
                "(() => { const s = document.querySelector('svg');"
                " const r = s.getBoundingClientRect();"
                " return {x: r.x, y: r.y, w: r.width, h: r.height}; })()"
            )
            t_before = page.evaluate(
                "(() => { const g = document.querySelector('svg g');"
                " return g ? g.getAttribute('transform') : null; })()"
            )
            cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
            # REAL user gestures over the graph — the exact paths that called
            # interrupt(): scroll-zoom in, scroll-zoom out, and a click-drag pan.
            page.mouse.move(cx, cy)
            page.mouse.wheel(0, -240)
            page.wait_for_timeout(150)
            page.mouse.wheel(0, 180)
            page.wait_for_timeout(150)
            page.mouse.move(box["x"] + 40, box["y"] + 40)
            page.mouse.down()
            page.mouse.move(box["x"] + 150, box["y"] + 110)
            page.mouse.up()
            page.wait_for_timeout(200)
            t_after = page.evaluate(
                "(() => { const g = document.querySelector('svg g');"
                " return g ? g.getAttribute('transform') : null; })()"
            )
        finally:
            browser.close()

    interrupt_errs = [e for e in errors if "interrupt" in e]
    assert not interrupt_errs, (
        "user scroll-zoom / pan threw — d3-zoom v3 calls window.d3.interrupt(this) "
        "from the un-vendored d3-transition dep; the no-op window.d3.interrupt "
        f"polyfill regressed: {interrupt_errs}"
    )
    # Sanity: the gestures actually reached d3-zoom (the viewport transform moved),
    # so the no-throw assertion isn't a false pass on a dead/unbound handler.
    assert t_after and t_after != t_before, (
        f"zoom/pan never changed the viewport transform ({t_before!r} -> "
        f"{t_after!r}) — the gestures didn't reach d3-zoom, so 'no interrupt error' "
        "would be vacuous"
    )


def test_topology_node_click_detail_panel_is_xss_safe():
    """Clicking a basin node renders its corpus-derived representatives into the
    detail panel. That panel is a SEPARATE render path from the lens/topics
    markdown the XSS browser test guards, and the other topology tests use empty
    `representatives` so it's never exercised. Reps come from captured chats
    (attacker-influenceable) — if showDetail ever switches from textContent to
    innerHTML, an injected `<img onerror>` executes. This clicks a node, confirms
    the detail panel populates with the payload as ESCAPED TEXT, and that nothing
    executed. Mutation: switch the rep `el("li", "topics-rep", rep.snippet)` to
    innerHTML → the img's onerror fires → window.__XSS_FIRED__ → reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal_with_topics(home, topics=_topics_with_xss())
    target = f"{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium for the topology-click test: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"file://{target}")
            page.wait_for_timeout(7000)  # force sim settles + fitToView fires

            # Click a basin node (any — every basin carries the payload rep). d3
            # binds the click via .on('click'); a real element click dispatches it.
            page.locator("circle").first.click(force=True)
            page.wait_for_timeout(300)

            result = page.evaluate(
                """(() => {
                  const panel = document.querySelector('.topics-graph-detail');
                  const rep = panel ? panel.querySelector('.topics-rep') : null;
                  return {
                    panel: !!panel,
                    populated: !!rep,                                  // showDetail ran
                    repText: rep ? rep.textContent : null,             // payload as text
                    imgInPanel: panel ? panel.querySelectorAll('img').length : -1,  // 0 iff escaped
                    xssFired: !!window.__XSS_FIRED__,                  // true iff innerHTML
                  };
                })()"""
            )
        finally:
            browser.close()

    assert not errors, f"JS errors during node-click detail render: {errors[:4]}"
    assert result["panel"], "detail panel container missing"
    assert result["populated"], "node click did not populate the detail panel (showDetail never ran)"
    # The invariant: the payload is inert TEXT, not a live element.
    assert result["xssFired"] is False, (
        "the basin representative's <img onerror> EXECUTED — showDetail rendered "
        "corpus content via innerHTML instead of textContent (stored XSS)"
    )
    assert result["imgInPanel"] == 0, (
        f"{result['imgInPanel']} <img> element(s) materialized in the detail panel "
        "from a representative snippet — it was parsed as HTML, not escaped as text"
    )
    assert result["repText"] and "onerror" in result["repText"], (
        f"the payload did not render as literal text in the rep ({result['repText']!r}) "
        "— the textContent path may have changed"
    )


def _topics_for_routing_detail() -> dict:
    basins = []
    for i in range(3):
        basins.append({
            "id": f"b{i}",
            "centroid": _vec(i, i),
            "size": 30 - i * 5,
            "label": f"Basin {i}",
            "top_terms": [f"term{i}", "topic"],
            "representatives": [{"id": f"rep{i}", "snippet": f"a representative prompt {i}"}],
        })
    return {"basins": basins}


def _scoreboard_picks() -> dict:
    # Flat post-#298 picks keyed by the SAME basin ids as the topics above, so the
    # topology basin → routing pick map resolves on click.
    return {
        "b0": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c1"]},
        "b1": {"winner": "codex", "count": 6, "margin": 0.21, "n_episodes": 6, "evidence": ["c2"]},
        "b2": {"winner": "antigravity", "count": 4, "margin": 0.31, "n_episodes": 4, "evidence": ["c3"]},
    }


def test_topology_node_click_shows_routing_winner_inline():
    """#301: clicking a basin in the topology graph must surface that basin's
    routing PICK inline ("Routes to <provider> · margin <x>") — the "which model
    for this domain" answer right where the user is looking.

    The fit + XSS topology browser tests seed topics.json but NEVER picks.json,
    so the routing-winner render path was real-browser-UNtested; the
    string-presence guard (test_detail_shows_routing_winner_inline) only proves
    the CODE exists, not that it RENDERS with picks data. A break in the
    picks→basin keying (the #300 key-space class) would silently drop the routing
    line while the string test stays green. Verified rendering 2026-06-06."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps(_topics_for_routing_detail()), encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps(_scoreboard_picks()), encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)[:160]))
            page.goto(f"file://{pages / 'memory.html'}?file=topics.json", wait_until="load")
            page.wait_for_timeout(2500)  # d3-force settle
            assert page.locator("svg circle").count() >= 1, "no topology nodes rendered"
            page.locator("svg circle").first.click(force=True)
            page.wait_for_timeout(400)
            detail = page.evaluate(
                """() => {
                  const d = document.querySelector('.topics-basin-detail, [class*=detail]');
                  const t = d ? (d.innerText || '') : '';
                  return {
                    text: t.slice(0, 240),
                    routesTo: /routes to\\s+(claude|codex|gpt|antigravity|gemini)/i.test(t),
                    hasMargin: /margin\\s+0?\\.\\d/i.test(t),
                  };
                }""")
        finally:
            browser.close()

    assert not errors, f"JS errors on the routing-detail render: {errors[:3]}"
    assert detail["routesTo"], (
        "clicking a basin did not surface its routing winner inline (#301) — the "
        f"detail lacks 'Routes to <provider>': {detail['text']!r}"
    )
    assert detail["hasMargin"], (
        f"the routing margin did not render in the basin detail: {detail['text']!r}"
    )


def _topics_two_clusters() -> dict:
    """Two tight, mutually-orthogonal clusters (5 'alpha' basins dominant in dim 0,
    5 'beta' basins dominant in dim 1). The within-cluster pairs (high cosine)
    monopolize the top-3n edge budget, so a click on an alpha node lights its alpha
    neighbors and DIMS the beta cluster — a stable, observable neighbor/non-neighbor
    split regardless of the force layout's random seed."""
    basins = []
    for i in range(5):
        basins.append({
            "id": f"a{i}",
            "centroid": _vec(0, i),  # cluster alpha — dominant dim 0
            "size": 20 - i,
            "label": f"Alpha {i}",
            "top_terms": [f"a{i}", "alpha"],
            "representatives": [{"id": f"ra{i}", "snippet": f"an alpha prompt {i}"}],
        })
    for j in range(5):
        basins.append({
            "id": f"b{j}",
            "centroid": _vec(1, 50 + j),  # cluster beta — dominant dim 1
            "size": 15 - j,
            "label": f"Beta {j}",
            "top_terms": [f"b{j}", "beta"],
            "representatives": [{"id": f"rb{j}", "snippet": f"a beta prompt {j}"}],
        })
    return {"basins": basins}


# Force-clicks node `targetId` by dispatching a real click on its SVG element, then
# measures which nodes/links DIMMED. (A direct `dispatchEvent('click')` is enough —
# d3 binds the node handler with `.on('click')`; we do NOT synthesize drag/zoom
# gestures, which would need a full sourceEvent.view and aren't what this guards.)
_HIGHLIGHT_JS = """((targetId) => {
  const nodes = Array.from(document.querySelectorAll('.topics-graph-svg .node'));
  const target = nodes.find(n => n.__data__ && n.__data__.id === targetId);
  if (!target) return {clicked: false};
  const r = target.getBoundingClientRect();
  target.dispatchEvent(new MouseEvent('click', {
    bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2}));
  const dim = (els) => Array.from(els).filter(
    e => parseFloat(getComputedStyle(e).opacity) < 0.9);
  const dimmedNodes = dim(document.querySelectorAll('.topics-graph-svg .node'))
    .map(n => n.__data__.id);
  const fullNodes = nodes
    .filter(n => parseFloat(getComputedStyle(n).opacity) >= 0.9)
    .map(n => n.__data__.id);
  const links = Array.from(document.querySelectorAll('.topics-graph-svg line'));
  const dimmedLinks = links.filter(
    l => parseFloat(getComputedStyle(l).opacity) < 0.2).length;
  return {clicked: true, dimmedNodes, fullNodes, totalLinks: links.length, dimmedLinks};
})"""

_CLEAR_HIGHLIGHT_JS = """(() => {
  const svg = document.querySelector('.topics-graph-svg');
  const r = svg.getBoundingClientRect();
  svg.dispatchEvent(new MouseEvent('click', {
    bubbles: true, clientX: r.x + 2, clientY: r.y + 2}));
  return Array.from(document.querySelectorAll('.topics-graph-svg .node'))
    .filter(n => parseFloat(getComputedStyle(n).opacity) < 0.9).length;
})"""


def test_topology_node_click_highlights_neighborhood_and_background_click_clears():
    """The topology graph's CORE advertised interaction (the hint chip reads
    "Drag nodes · scroll to zoom · click for detail", and the whole pitch is "SEE
    which subjects cluster vs which sit alone"): clicking a basin must LIGHT its
    neighborhood and DIM the non-neighbor nodes + non-incident links, and a
    background click must restore everything.

    Every existing topology browser guard covers the load-time FIT (all nodes
    on-screen), the node-click DETAIL panel (XSS-safe text + the inline routing
    winner), and the picks→topology deep-link — but NOTHING asserts the
    `highlightNeighborhood` / `clearHighlight` VISUAL. If that opacity binding
    regressed (an off-by-one in `neighborsOf`, a broken `.style("opacity")`), the
    cluster-reveal — the reason the graph is a force layout at all — would silently
    die while every test stayed green (the "green while the value is gone" shape
    this surface is most prone to).

    Mutation-proven: stub `highlightNeighborhood` to a no-op in the built page → a
    click dims NOTHING → this reds with the exact symptom. (Verified by hand during
    authoring.) Slow + browser marked; skips when Playwright/chromium are absent."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal_with_topics(home, topics=_topics_two_clusters())

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium for the highlight test: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1000}).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)[:160]))
            page.on(
                "console",
                lambda m: errors.append(m.text[:160])
                if m.type == "error" and "favicon" not in m.text.lower()
                and "woff" not in m.text.lower()
                else None,
            )
            page.goto(f"file://{pages / 'memory.html'}?file=topics.json",
                      wait_until="load")
            page.wait_for_timeout(2800)  # d3-force settle + fitToView
            assert page.locator(".topics-graph-svg .node").count() == 10, (
                "the two-cluster topology did not render its 10 basin nodes"
            )

            res = page.evaluate(_HIGHLIGHT_JS, "a0")
            cleared = page.evaluate(_CLEAR_HIGHLIGHT_JS)
        finally:
            browser.close()

    assert res["clicked"], "could not locate basin node 'a0' to click"
    # The whole beta cluster is orthogonal to a0 → none of b0..b4 is a0's neighbor,
    # so at least the bulk of them MUST dim. (b4 occasionally catches a budget-
    # filling cross edge; assert the dominant non-neighbor signal, not all 5.)
    assert len(res["dimmedNodes"]) >= 3, (
        "clicking basin a0 dimmed NO non-neighbor nodes — the "
        "`highlightNeighborhood` cluster-reveal (the reason the graph is a force "
        f"layout) is dead. dimmed={res['dimmedNodes']}, full={res['fullNodes']}"
    )
    # The clicked node + its own alpha cluster must stay lit (a node is always its
    # own neighbor).
    assert "a0" in res["fullNodes"], (
        f"the clicked basin a0 was itself dimmed — neighborsOf is broken: "
        f"full={res['fullNodes']}"
    )
    assert all(d != "a0" for d in res["dimmedNodes"]), (
        "the clicked basin appeared in the DIMMED set — neighborsOf excludes self"
    )
    # Non-incident links dim too (the edge-highlight half of the interaction).
    assert res["dimmedLinks"] >= 1, (
        f"no links dimmed on a basin click — the link-opacity highlight is dead "
        f"(totalLinks={res['totalLinks']})"
    )
    # Background click clears the whole highlight.
    assert cleared == 0, (
        f"a background click did NOT clear the neighborhood highlight — "
        f"{cleared} node(s) stayed dimmed (clearHighlight is broken)"
    )
    assert not errors, f"JS errors during the highlight interaction: {errors[:4]}"
