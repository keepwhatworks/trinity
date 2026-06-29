"""Real-browser guard: every memory-viewer file view renders its DISTINCTIVE
content on a populated home — none strands the user on "Loading…".

The memory viewer is the retention surface — how a user explores their lens. Its
content pane starts as `<div class="empty">Loading…</div>` and the inline JS
replaces it based on `?file=<name>`, dispatching to a different renderer per file
KIND: markdown (core/lens/vocabulary), the d3 topology graph (topics.json), and
the picks/routing Reader tables. A regression in any one renderer (the topology
build throwing, a markdown view never replacing the placeholder, the routing table
not building rows) strands the user on "Loading…" or a blank pane — and the
EXISTING memory-viewer browser tests miss it: test_memory_viewer_xss_browser
iterates `?file=` views but only asserts no-script-execution; the cold-start test
only covers the EMPTY home; the picks↔topology test covers one cross-link.
Nothing asserts each of the six views RENDERS its correct content on a populated
home.

This drives all six over the file:// substrate on a PII-free seeded home (the
36-surface gate's synthetic seeder) and asserts the content KIND per file:
markdown text for core/lens/vocabulary, an SVG node graph for topics.json, table
rows for routing.json, and a non-empty Reader for picks.json — every view free of
"Loading…" and console errors.

Mutation-proven: break a renderer (e.g. make the topology builder a no-op, or a
markdown view never clear the placeholder) → that view's distinctive-content
assertion reds. (Verified by hand during authoring — the topology view yields 8
SVG nodes, routing 3 table rows; zeroing either reds.)

Slow + browser marked; skips without Playwright/chromium; runs in the CI `browser`
job.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_viewer_views", _SEEDER)
    assert spec and spec.loader, "could not load scripts/seed_synthetic_home.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# (file, kind) — kind drives which distinctive-content assertion applies.
_VIEWS = [
    ("core.md", "markdown"),
    ("lens.md", "markdown"),
    ("topics.json", "topology"),
    ("vocabulary.md", "markdown"),
    ("picks.json", "reader"),
    ("routing.json", "table"),
]


def test_every_memory_file_view_renders_its_content(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    mod = _load_seeder()
    mod.seed(home)  # writes the 6 files + renders portal/live pages
    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            for fname, kind in _VIEWS:
                page = browser.new_page(viewport={"width": 1400, "height": 1200})
                errs: list[str] = []
                page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
                page.goto(f"file://{mv}?file={fname}", wait_until="load")
                page.wait_for_timeout(1300)  # inline renderer + d3 for topology

                content = page.query_selector("#content")
                ctext = (content.inner_text() if content else "").strip()
                # Count the basin NODE circles specifically — NOT `svg g`, whose
                # structural wrappers (viewport / link / node / label groups) exist
                # even when zero nodes bind (a comma selector including `g` gives a
                # false pass; caught by mutation during authoring).
                svg_nodes = len(page.query_selector_all("#content svg circle"))
                table_rows = len(page.query_selector_all("#content table tr"))
                page.close()

                # Universal: never stranded on the placeholder, never a JS error.
                if "Loading…" in ctext and len(ctext) < 30:
                    failures.append(f"{fname}: stranded on 'Loading…' placeholder")
                if errs:
                    failures.append(f"{fname}: console/page errors {errs[:2]}")

                # Per-kind distinctive content.
                if kind == "markdown":
                    # The file's body must have replaced the placeholder with real
                    # prose (the seeded files all carry >50 chars of body).
                    if len(ctext) < 60:
                        failures.append(f"{fname}: markdown body too thin ({len(ctext)} chars): {ctext[:80]!r}")
                elif kind == "topology":
                    if svg_nodes == 0:
                        failures.append(f"{fname}: topology graph rendered NO svg nodes — the d3 basin graph didn't build")
                elif kind == "table":
                    if table_rows == 0:
                        failures.append(f"{fname}: routing Reader rendered NO table rows")
                elif kind == "reader":
                    if len(ctext) < 60:
                        failures.append(f"{fname}: picks Reader rendered no content ({len(ctext)} chars)")
        finally:
            browser.close()

    assert not failures, "memory-viewer file views failed to render:\n  " + "\n  ".join(failures)
