"""Cold-start (fresh-install / empty TRINITY_HOME) render guard.

The browser smoke (scripts/browser_smoke.py) runs every surface against the
REAL ~/.trinity (rich dev data) — its "Surface 1 cold-render" even asserts
*chart bars present*, which needs data. So the launch-critical FRESH-INSTALL
render — what every new user sees on first run — has NO automated guard. A
change that works with the founder's 3 years of data but crashes, returns the
wrong shape, or drops an empty-state CTA on empty data would ship unnoticed.

These pin the cold-start build + render: build_page_data must not crash on an
empty home, the data-derived sections must be sensibly empty (a regression
here is how the eval/elo/timeline masking bugs entered), and the rendered HTML
must still carry the empty-state CTAs that tell a new user what to run.

Verified live in-browser 2026-05-31: the cold-start launchpad rendered 12 CTA
cards with zero undefined/NaN/[object Object]/mustache leakage and zero console
errors. This is the CI-runnable floor under that manual check — the Vue
render-leak assertion itself needs a real JS engine (the gated smoke).

build_page_data scans the host's CLI adapter dirs (~/.claude, ...) which can be
slow on a dev machine, so the fixture is module-scoped: build once, reuse.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cold_page():
    """build_page_data against a fresh, empty TRINITY_HOME."""
    tmp = tempfile.mkdtemp(prefix="trinity-cold-")
    prev = os.environ.get("TRINITY_HOME")
    os.environ["TRINITY_HOME"] = tmp
    try:
        from trinity_local.launchpad_data import build_page_data
        yield build_page_data(live_review_path=Path("/tmp/x.html"), recent_councils=[])
    finally:
        if prev is None:
            os.environ.pop("TRINITY_HOME", None)
        else:
            os.environ["TRINITY_HOME"] = prev
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_page_data_does_not_crash_on_empty_home(cold_page):
    assert isinstance(cold_page, dict) and cold_page


def test_cold_start_data_sections_are_sensibly_empty(cold_page):
    # eval: no scored runs yet (NOT a degenerate has_results=True with null score)
    assert cold_page.get("evalSummary", {}).get("has_results") is False
    # timeline: no chapters
    assert cold_page.get("timeline") == []
    # elo: no councils → no provider bars (and no 1500-default noise rows)
    assert cold_page.get("eloChart", {}).get("labels") == []
    # cortex: not consolidated yet
    cortex = cold_page.get("cortexRules")
    assert cortex is None or not cortex.get("rules"), f"cortex not empty: {cortex}"
    # recent councils: zero
    assert cold_page.get("recentCouncilsCount", 0) == 0


def test_cold_start_html_renders_with_empty_state_ctas(cold_page):
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data=cold_page)
    assert html and len(html) > 1000
    # The new user must be told what to do — these CTA headings ARE the
    # cold-start guidance; a refactor that drops them strands first-run users.
    for cta in ("Run a Council", "Ask every model at once"):
        assert cta in html, f"cold-start CTA missing from rendered HTML: {cta!r}"


def test_cold_start_page_data_has_no_python_value_leak(cold_page):
    import json
    blob = json.dumps(cold_page)
    # JSON null is fine (Vue handles it). A literal "None" string or bare NaN
    # means a Python value leaked through str()/an unguarded float instead of
    # being serialized properly — those render as visible garbage in the card.
    assert '"None"' not in blob, "a Python None leaked into the page data as a string"
    assert "NaN" not in blob, "a NaN float leaked into the page data"


def _render_cold_portal_to_disk(home: Path) -> Path:
    """Render the launchpad from an empty `home` via the real CLI subprocess.
    TRINITY_AUTOSCAN_DISABLED=1 → both lens kicks no-op on an empty corpus (zero
    `claude -p`); mirrors the populated render harness in
    test_mobile_viewport_overflow.py."""
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(_REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"cold portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "launchpad.html").exists(), "portal-html didn't write launchpad.html"
    return pages


@pytest.mark.slow
@pytest.mark.browser
def test_cold_start_launchpad_mounts_in_a_real_browser_with_no_console_errors():
    """CI-runnable floor under the MANUAL in-browser check the module docstring
    records (the Vue render-leak assertion "needs a real JS engine"). Now that
    Playwright is in CI, automate it: on a FRESH install (empty home) petite-vue
    must actually MOUNT — no raw mustache leakage — with ZERO console errors /
    pageerrors, real rendered content, and the first-run CTAs present. This is
    the make-or-break first impression; a Vue-scope ReferenceError that only
    fires on EMPTY data (a v-for over an undefined array, a missing empty-guard)
    would strand every new user while the string-level asserts above stay green
    — the exact dogfood lesson (pair string asserts with one real-browser smoke).
    scripts/browser_smoke.py can't cover this: it runs the REAL home (Surface 1
    asserts chart bars, which need data). Re-verified manually 2026-06-03;
    this makes it permanent. Skips when chromium isn't installed."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="trinity-cold-browser-"))
    home = root / "trinity"
    home.mkdir(parents=True)
    try:
        pages = _render_cold_portal_to_disk(home)
        errors: list[str] = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # chromium not installed in this env
                pytest.skip(f"no launchable chromium for the cold-start browser test: {exc}")
            try:
                page = browser.new_page()
                page.on("console",
                        lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                page.goto(f"file://{pages / 'launchpad.html'}", wait_until="networkidle")
                page.wait_for_timeout(1200)  # let petite-vue mount
                body = page.inner_text("body")
            finally:
                browser.close()
        # 1) Vue MOUNTED — no unrendered mustache leaked to the first-run user.
        raw = body.count("{{") + body.count("}}")
        assert raw == 0, (
            f"cold-start launchpad leaked {raw} raw mustache markers — petite-vue "
            "did not mount on empty data (Vue-scope error or missing empty-guard)."
        )
        # 2) zero console errors / pageerrors on first run.
        assert not errors, (
            "cold-start launchpad threw in a real browser:\n  " + "\n  ".join(errors)
        )
        # 3) real rendered content + the first-run guidance CTAs.
        assert len(body) > 500, f"cold-start body too short ({len(body)} chars) — render stalled"
        for cta in ("Run a Council", "Ask every model at once"):
            assert cta in body, f"first-run CTA missing from the rendered page: {cta!r}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.slow
@pytest.mark.browser
def test_cold_start_has_no_empty_expanded_cards():
    """A card that MOUNTS but renders BLANK — its Vue template populated nothing
    on empty data with no empty-state — is invisible to the mustache + console-
    error checks above (petite-vue mounted fine; the card is just empty). On the
    cold launchpad that's a blank box greeting a new user.

    Guard: every VISIBLE card that is NOT deliberately demoted into a collapsed
    <details> must render real text. The browser-capture + import-export cards
    ARE collapsed-details by design (launchpad_template ~2010, council-approved
    2026-05-21), so they're excluded via closest('details:not([open])') — their
    <section> content is display:none while collapsed (innerText='' but
    textContent full), which is NOT a bug. Dogfood 2026-06-06: the cold first-run
    had no empty EXPANDED cards; this locks that in. The exclusion encodes the
    lesson that `innerText==0` on a tall element is often a collapsed <details>,
    not a broken card. Skips when chromium isn't installed."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="trinity-cold-emptycard-"))
    home = root / "trinity"
    home.mkdir(parents=True)
    try:
        pages = _render_cold_portal_to_disk(home)
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 3200})
                page.goto(f"file://{pages / 'launchpad.html'}", wait_until="networkidle")
                page.wait_for_timeout(1500)  # let petite-vue mount + lay out
                empties = page.evaluate(
                    """() => {
                      const bad = [];
                      for (const c of document.querySelectorAll('section.card, .card')) {
                        // Skip cards deliberately demoted into a collapsed <details>
                        // (their content is display:none while collapsed — by design).
                        if (c.closest('details:not([open])')) continue;
                        const r = c.getBoundingClientRect();
                        if (r.height < 40) continue;  // thin/icon/hidden — not a content box
                        if ((c.innerText || '').trim().length < 5)
                          bad.push((c.className || c.tagName) + ' h=' + Math.round(r.height));
                      }
                      return bad;
                    }"""
                )
            finally:
                browser.close()
        assert not empties, (
            "cold-start launchpad has VISIBLE but EMPTY (textless) expanded cards — "
            "a Vue template populated nothing on empty data with no empty-state, so "
            f"a new user sees blank boxes: {empties}"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
