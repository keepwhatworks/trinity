"""Cross-surface dead-target guard: a `?file=topics.json&basin=<id>` deep-link to a
basin that is NO LONGER in the topology must NOT land silently — it must surface the
"NOT FOUND · stale lens build" banner (with a working rebuild chip) INSIDE the detail
panel, and a VALID basin must NOT show that banner.

The picks reader's "View in topology →" xlink + the lens-card chips carry basin ids
from the lens-build run that produced them. If topology is later rebuilt with a
different cluster count, those ids no longer match any node — the link still resolves
to memory.html (the page renders), but the requested basin is gone. Without the
focusBasin else-branch (memory_viewer.py ~1973) the link lands with NO detail panel
opened and NO feedback: a silent dead-end the user reads as "the link is broken".

The existing guard (test_memory_viewer.py::TestStaleBasin) is a SOURCE-STRING check
(`'not in the current topology' in html`) — it proves the copy exists in the template
but NOT that the else-branch actually RENDERS the banner in the DOM, that the banner is
VISIBLE, that the rebuild chip works, or that a VALID basin is correctly discriminated
(no false "not found"). A regression where the else-branch throws, builds an invisible
banner, or mis-fires on a valid id would keep the string present and stay green while
the user hits a silent dead target. This drives the real cross-surface fallback.

Mutation-proven: replace the else-branch banner construction with a no-op (the link
lands but no banner renders) → test_stale_basin_deeplink_surfaces_not_found_banner reds
with the founder symptom ("landed silently with no panel and no feedback"). The valid-
basin contrast case stays green. (Verified by hand during authoring.)

Slow + browser marked; skips when Playwright/chromium are absent; runs in CI `browser`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_stale_basin", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _render(home: Path, monkeypatch) -> Path:
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _load_seeder().seed(home)
    from trinity_local.memory_viewer import write_memory_viewer

    return write_memory_viewer()


# The stale-basin banner lives INSIDE the topology detail panel (.topics-graph-detail).
# Scope every probe to that panel so the always-present per-file health banner (the
# seeded topics.json carries a PRE-THREAD-AWARE schema notice at the TOP of the view)
# can't shadow the result — driving this unscoped is exactly how an earlier hand-probe
# matched the wrong banner and read a false PASS.
_DETAIL_BANNER_PROBE = """() => {
  const dp = document.querySelector('.topics-graph-detail');
  const banner = dp ? dp.querySelector('.viewer-health-banner') : null;
  const status = banner ? banner.querySelector('.viewer-health-status') : null;
  const hint = banner ? banner.querySelector('.viewer-health-hint') : null;
  const chip = banner ? banner.querySelector('.viewer-health-cmd') : null;
  let visible = false;
  if (banner) {
    const r = banner.getBoundingClientRect();
    visible = r.width > 0 && r.height > 0 && !!banner.offsetParent;
  }
  return {
    detailPresent: !!dp,
    bannerInDetail: !!banner,
    bannerVisible: visible,
    status: status ? status.innerText.trim() : null,
    hint: hint ? hint.innerText.trim() : null,
    chipText: chip ? chip.innerText.trim() : null,
  };
}"""


def test_stale_basin_deeplink_surfaces_not_found_banner(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    mv = _render(tmp_path / "trinity", monkeypatch)

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.on(
                "console",
                lambda m: errs.append(m.text[:160])
                if m.type == "error" and "favicon" not in m.text.lower()
                else None,
            )

            # Deep-link a basin that does NOT exist in the seeded topology.
            page.goto(f"file://{mv}?file=topics.json&basin=b_DOES_NOT_EXIST", wait_until="load")
            page.wait_for_timeout(1500)  # d3 sim mounts + the focusBasin else-branch fires

            st = page.evaluate(_DETAIL_BANNER_PROBE)
            if not st["detailPresent"]:
                failures.append("topology detail panel never rendered")
            if not st["bannerInDetail"] or not st["bannerVisible"]:
                failures.append(
                    f"stale-basin deep-link landed SILENTLY — no visible NOT-FOUND banner "
                    f"in the detail panel (the #300 dead-target: link lands, basin is gone, "
                    f"no feedback). state={st}"
                )
            else:
                if not st["status"] or "not found" not in st["status"].lower():
                    failures.append(
                        f"detail banner status is {st['status']!r}, not the 'NOT FOUND' "
                        f"stale-basin notice"
                    )
                if not st["hint"] or "not in the current topology" not in st["hint"].lower():
                    failures.append(f"stale-basin hint copy regressed: {st['hint']!r}")
                if st["chipText"] != "trinity-local lens":
                    failures.append(
                        f"stale-basin rebuild chip is {st['chipText']!r}, not the "
                        f"'trinity-local lens' rebuild command"
                    )

            # The rebuild chip must give copy feedback (✓ Copied) when clicked.
            chip = page.query_selector(".topics-graph-detail .viewer-health-cmd")
            if chip is not None:
                chip.click()
                page.wait_for_timeout(300)
                chip_after = page.evaluate(
                    "() => { const c = document.querySelector('.topics-graph-detail "
                    ".viewer-health-cmd'); return c ? c.innerText.trim() : null; }"
                )
                if not chip_after or "copied" not in chip_after.lower():
                    failures.append(
                        f"stale-basin rebuild chip gave no ✓ Copied feedback on click "
                        f"(showed {chip_after!r}) — a dead-end fix-chip"
                    )

            if errs:
                failures.append(f"JS errors on the stale-basin deep-link: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "memory-viewer stale-basin deep-link dead-target regressed:\n  "
        + "\n  ".join(failures)
    )


def test_valid_basin_deeplink_does_not_show_not_found_banner(tmp_path, monkeypatch):
    """Contrast / false-positive guard: a deep-link to a basin that DOES exist (b00,
    seeded) must open its detail and must NOT show the 'NOT FOUND' stale banner — so a
    mutation that fires the else-branch unconditionally (or a `nodes.find` that never
    matches) is caught, not papered over by the positive case."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    mv = _render(tmp_path / "trinity", monkeypatch)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
            page.goto(f"file://{mv}?file=topics.json&basin=b00", wait_until="load")
            page.wait_for_timeout(1500)
            result = page.evaluate(
                """() => {
                  const dp = document.querySelector('.topics-graph-detail');
                  const status = dp ? dp.querySelector('.viewer-health-banner .viewer-health-status') : null;
                  const notFound = status && /not found/i.test(status.innerText);
                  const hasBasin = dp && /b00/.test(dp.innerText);
                  return { notFound: !!notFound, hasBasinDetail: !!hasBasin };
                }"""
            )
        finally:
            browser.close()

    assert not result["notFound"], (
        "a VALID basin deep-link (b00) wrongly showed the 'NOT FOUND' stale banner — "
        "the focusBasin else-branch is mis-firing on present basins"
    )
    assert result["hasBasinDetail"], (
        "a VALID basin deep-link (b00) did not open its detail panel"
    )
