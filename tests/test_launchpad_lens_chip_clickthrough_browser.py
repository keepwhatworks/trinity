"""Cross-surface guard: the launchpad's "Your lens" memory-file chips CLICK THROUGH
to the memory viewer showing that file — the "inspect your lens" retention flow.

The launchpad's "Your lens" card renders one chip per cognitive-memory file:
`<a class="memory-chip" href="../portal_pages/memory.html?file=core.md">` (… lens.md,
topics.json, vocabulary.md). The href is a RELATIVE path that resolves out of and
back into `portal_pages/` plus a `?file=` param the viewer consumes. This is a
boundary contract between two surfaces tested only from each side: the launchpad
EMITS the chip href; the memory viewer CONSUMES `?file=`. The only test touching
these chips is a string check for the DIFFERENT `.cross-memory-chip` class
(test_lens_basin_chips) — nothing CLICKS a `.memory-chip` and asserts it lands on
the viewer. A regression on either side (the `../portal_pages/` prefix, the
`?file=` param, or the viewer's file lookup) would 404 the "inspect your lens" link
or render the wrong file, and every per-surface render test would stay green
([[test_the_boundary_and_the_action]]). Sibling of
test_launchpad_recent_card_clickthrough (recent card → live council).

Seeds a PII-free synthetic home (the gate seeder writes core.md/lens.md/topics.json
+ the memory viewer), renders the real launchpad, then CLICKS the lens.md and
core.md chips (not hand-built URLs) over the file:// substrate — the documented
`portal-html --open-browser` prod path — and asserts each navigates to its `?file=`
URL and renders that file's distinctive content with no 4xx / JS errors.

Mutation-proven: break the chip href template (drop the `?file=`) → the click lands
on the viewer's default file → the lens-content assertion reds. (Verified by hand:
the lens.md chip → ?file=lens.md, the Tensions body renders, no 4xx.)

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_lens_chip", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_your_lens_chips_click_through_to_the_viewer(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    _load_seeder().seed(home)  # writes the lens files + launchpad
    from trinity_local.memory_viewer import write_memory_viewer

    write_memory_viewer()  # the chip's target page
    launchpad = home / "portal_pages" / "stats.html"

    # (file, a lowercased substring proving THAT file rendered in the viewer)
    targets = [("lens.md", "tension"), ("core.md", "concrete")]
    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            for fname, marker in targets:
                page = browser.new_page(viewport={"width": 1280, "height": 2600})
                bad: list[str] = []
                page.on("response", lambda r: bad.append(f"{r.status} {r.url.split('/')[-1]}")
                        if r.status >= 400 and "favicon" not in r.url else None)
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))

                page.goto(f"file://{launchpad}", wait_until="load")
                page.wait_for_timeout(900)
                link = page.query_selector(f"a.memory-chip[href*='file={fname}']")
                if link is None:
                    failures.append(f"{fname}: 'Your lens' chip not present on the launchpad")
                    page.close()
                    continue
                # Same-tab navigation (no target=_blank).
                if link.get_attribute("target") not in (None, "", "_self"):
                    failures.append(f"{fname}: chip opens in a new tab — the inspect-flow shape changed")
                link.click()
                page.wait_for_load_state("load")
                page.wait_for_timeout(1100)

                if f"file={fname}" not in page.url:
                    failures.append(f"{fname}: chip did not navigate to the viewer (landed {page.url!r})")
                else:
                    content = page.query_selector("#content")
                    ctext = (content.inner_text() if content else "").lower()
                    if marker not in ctext:
                        failures.append(f"{fname}: the viewer didn't render that file's content ({ctext[:80]!r})")
                if bad:
                    failures.append(f"{fname}: 4xx during click-through: {bad[:3]}")
                if errs:
                    failures.append(f"{fname}: JS errors during click-through: {errs[:3]}")
                page.close()
        finally:
            browser.close()

    assert not failures, "launchpad 'Your lens' chip click-through regressed:\n  " + "\n  ".join(failures)
