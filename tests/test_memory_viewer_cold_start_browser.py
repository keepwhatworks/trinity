"""Browser guard: the COLD (empty-home) memory viewer — the literal first thing a
brand-new user sees if they open the viewer before building anything — must render
error-free, non-blank, and with a USEFUL empty state, not a broken/blank page.

Coverage gap this fills (found 2026-06-07 dogfooding a fresh home): the viewer's
empty-home tests (`isolated_home`) are STRING-level (no browser, no JS-error check),
and the XSS *browser* test SEEDS lens.md/topics.json — so it only exercises the
POPULATED render. A regression that throws or blanks the page specifically on an
EMPTY home (e.g. a null-deref on missing lens.md, or the optional generators tab
leaking as an empty tab) would slip past both. This pins the cold-start path:

  • no uncaught JS / console errors,
  • a non-blank page with the useful "Not built yet … run trinity-local dream"
    empty state (a LIVE rebuild verb, not a retired one),
  • the optional generators tier stays HIDDEN with no generators.md (inverse of the
    XSS test, which seeds generators.md and asserts the tab shows),
  • the core memory tabs render in the nav.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _render_cold_viewer(home: Path) -> Path:
    from trinity_local.memory_viewer import render_memory_viewer_html
    from trinity_local.vendor import publish_vendor_files

    (home / "memories").mkdir(parents=True, exist_ok=True)
    pp = home / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "memory.html").write_text(render_memory_viewer_html(), encoding="utf-8")
    publish_vendor_files(pp)
    return pp / "memory.html"


def test_cold_start_memory_viewer_renders_useful_and_error_free(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    mv = _render_cold_viewer(tmp_path)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:160]))
            page.on(
                "console",
                lambda m: errs.append("console.error: " + m.text[:160])
                if m.type == "error" and "favicon" not in m.text.lower()
                else None,
            )
            page.goto(f"file://{mv}")
            page.wait_for_timeout(1000)

            assert not errs, f"cold-start memory viewer threw JS errors: {errs[:4]}"

            body = page.evaluate("document.body.innerText")
            assert len(body) > 100, (
                f"cold viewer rendered a near-blank page ({len(body)} chars) — a "
                "broken empty-home render, not a useful empty state"
            )
            # A useful empty state pointing at a LIVE rebuild verb — not a blank
            # tab, and not a retired command (the builder.py:176 class of bug).
            assert "Not built yet" in body, (
                "cold viewer missing the 'Not built yet' empty-state copy — a new "
                "user sees a blank/confusing tab instead of a next step"
            )
            assert "trinity-local dream" in body, (
                "cold viewer empty state must surface a live rebuild command "
                "(trinity-local dream) so the user knows what to run"
            )

            # The OPTIONAL generators tier must NOT show an empty tab on a cold home.
            gen = page.query_selector("a.memory-nav-link[href*='generators.md']")
            assert gen is None, (
                "the generators tab rendered on a cold home (no generators.md) — the "
                "optional-tab contract broke; it must appear only once the file exists"
            )

            # The core memory tabs render in the nav.
            hrefs = page.eval_on_selector_all(
                "a.memory-nav-link", "els => els.map(e => e.getAttribute('href') || '')"
            )
            for fname in ("lens.md", "topics.json", "vocabulary.md"):
                assert any(fname in h for h in hrefs), (
                    f"{fname} tab missing from the cold viewer nav (hrefs={hrefs})"
                )
        finally:
            browser.close()


def _render_viewer(home: Path) -> Path:
    """Render the viewer into home/portal_pages with vendor files alongside."""
    from trinity_local.memory_viewer import render_memory_viewer_html
    from trinity_local.vendor import publish_vendor_files

    (home / "memories").mkdir(parents=True, exist_ok=True)
    pp = home / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "memory.html").write_text(render_memory_viewer_html(), encoding="utf-8")
    publish_vendor_files(pp)
    return pp / "memory.html"


def test_rebuild_chip_says_BUILD_on_unbuilt_file_REBUILD_on_populated(tmp_path, monkeypatch):
    """The header chip must NOT say '↻ Rebuild' on a not-built-yet file.

    UX sweep iter 73: on a cold install the lens.md tab rendered a chip labeled
    '↻ Rebuild' (copying `trinity-local lens`) directly above the body's "Not built
    yet. Run `trinity-local lens` to GENERATE it." — a self-contradiction about the
    identical command ("rebuild" implies refreshing an existing build that does not
    exist). renderHeader() is called UNCONDITIONALLY for every tab incl. the empty
    ones, so the lie spanned all 7 files. The fix makes the label state-aware:
    '↻ Build' on an unbuilt file, '↻ Rebuild' only once it carries content. This
    guard drives the REAL rendered chip in both states and BITES on a regression to
    a hardcoded label (un-fixed code reds because the cold chip reads '↻ Rebuild').
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # COLD home — nothing built.
    cold = tmp_path / "cold"
    monkeypatch.setenv("TRINITY_HOME", str(cold))
    cold_mv = _render_viewer(cold)

    # WARM home — lens.md is built, topics.json is NOT (same render, two states).
    warm = tmp_path / "warm"
    (warm / "memories").mkdir(parents=True, exist_ok=True)
    (warm / "memories" / "lens.md").write_text(
        "# Lens\n\n## Tension 1\nReject: surface-form X\nAccept: semantic Y\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRINITY_HOME", str(warm))
    warm_mv = _render_viewer(warm)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()

            def read_chip(mv: Path, fname: str) -> dict:
                page.goto(f"file://{mv}?file={fname}")
                page.wait_for_timeout(500)
                return page.evaluate(
                    """() => {
                        const c = document.querySelector('.viewer-rebuild-chip');
                        const empty = document.querySelector('.empty');
                        return {
                            label: c ? c.textContent.trim() : null,
                            built: c ? c.dataset.built : null,
                            visible: c ? (c.getBoundingClientRect().width > 0) : false,
                            hasEmpty: !!empty,
                            bodyLen: document.body.innerText.length,
                        };
                    }"""
                )

            # 1) COLD lens.md — unbuilt → '↻ Build', and the body IS the empty state.
            r = read_chip(cold_mv, "lens.md")
            assert r["visible"], "the rebuild/build chip vanished on a cold lens.md tab"
            assert r["hasEmpty"], "cold lens.md should render the 'Not built yet' empty state"
            assert r["label"] == "↻ Build", (
                "UX iter 73: the header chip on a NOT-built-yet lens.md reads "
                f"{r['label']!r} — '↻ Rebuild' is a lie above 'Not built yet … to "
                "GENERATE it' (nothing exists to re-build). It must read '↻ Build'."
            )
            assert r["built"] == "0", "the chip's dataset.built must be 0 on an unbuilt file"

            # 2) WARM lens.md — populated → '↻ Rebuild' (the affordance still exists).
            r = read_chip(warm_mv, "lens.md")
            assert not r["hasEmpty"], "a populated lens.md must NOT render the empty state"
            assert r["label"] == "↻ Rebuild", (
                "a POPULATED lens.md must keep the '↻ Rebuild' affordance — the fix "
                f"must not strip it; got {r['label']!r}"
            )
            assert r["built"] == "1", "the chip's dataset.built must be 1 on a built file"

            # 3) WARM topics.json — still unbuilt in the same home → '↻ Build'
            #    (proves the label is PER-FILE state, not a whole-page flag).
            r = read_chip(warm_mv, "topics.json")
            assert r["label"] == "↻ Build", (
                "an unbuilt topics.json in a partially-built home must read '↻ Build' "
                f"(per-file state, not a page-wide flag); got {r['label']!r}"
            )
        finally:
            browser.close()
