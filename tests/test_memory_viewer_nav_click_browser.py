"""Real-browser guard: CLICKING a memory-viewer nav tab switches files — the core
exploration interaction — over the file:// substrate.

How a user explores their lens in the memory viewer is by CLICKING the nav tabs
(core.md → lens.md → topics.json → …). Each tab is `<a href="memory.html?file=
<name>">` — a RELATIVE path + query string the browser resolves against the
current page. Every existing memory-viewer browser test loads `?file=<name>`
DIRECTLY via `page.goto` (test_memory_viewer_all_file_views, the XSS test, the
cold-start tests) — none CLICKS a nav tab, so the actual navigation contract is
unpinned: if the href regressed (an absolute `/memory.html`, a dropped `?file=`,
or a file://-specific query-string resolution break — [[file_substrate_browser_testing]]),
clicking a tab would land on the wrong page or fail to switch, and the user would
be stuck on one file with every per-file render test still green. This is the
memory-viewer sibling of test_launchpad_recent_card_clickthrough ("the #1 thing a
user does").

Drives it on the file:// substrate (the documented `portal-html --open-browser`
prod path the MCP browser tools can't reach): seed a PII-free synthetic home, land
on the default view, CLICK the lens.md tab then the topics.json tab (not hand-built
URLs), and assert each navigates to its `?file=` URL, marks that tab active, and
renders that file's distinctive content (markdown body for lens.md, the d3 SVG
graph for topics.json) with no JS errors.

Mutation-proven: break the nav-link href template (drop the `memory.html?file=`
prefix) → the click lands wrong / doesn't render lens.md → reds. (Verified by hand:
clicking lens.md → ?file=lens.md, active=lens.md, the Tensions body renders.)

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_nav_click", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clicking_nav_tabs_switches_files(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    _load_seeder().seed(home)
    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1100})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)

            # Land on the default view (first file — core.md), no ?file=.
            page.goto(f"file://{mv}", wait_until="load")
            page.wait_for_timeout(900)

            # (filename, a substring that proves THAT file's content rendered)
            for fname, marker, kind in (("lens.md", "tension", "text"),
                                        ("topics.json", None, "graph")):
                link = page.query_selector(f"a.memory-nav-link[href*='file={fname}']")
                if link is None:
                    failures.append(f"{fname}: nav tab not present to click")
                    continue
                link.click()
                page.wait_for_load_state("load")
                page.wait_for_timeout(1100)  # navigation + d3/markdown render

                if f"file={fname}" not in page.url:
                    failures.append(f"{fname}: clicking the tab did not navigate (landed {page.url!r})")
                    continue
                active = page.evaluate(
                    "() => { const a = document.querySelector('a.memory-nav-link.active');"
                    " return a ? a.getAttribute('data-file') : null; }"
                )
                if active != fname:
                    failures.append(f"{fname}: active tab is {active!r}, not the clicked file")
                content = page.query_selector("#content")
                ctext = (content.inner_text() if content else "").lower()
                if kind == "text":
                    if marker and marker not in ctext:
                        failures.append(f"{fname}: file content didn't render after click ({ctext[:80]!r})")
                else:  # the topology graph view
                    nodes = len(page.query_selector_all("#content svg circle"))
                    if nodes == 0:
                        failures.append(f"{fname}: topology graph didn't render after the nav click")
            if errs:
                failures.append(f"JS errors during nav-tab navigation: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, "memory-viewer nav-tab click-through regressed:\n  " + "\n  ".join(failures)
