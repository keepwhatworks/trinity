"""The councils-rail toggle (the ☰ hamburger) must expose aria-expanded in
lockstep with whether the council-history rail is currently SHOWN — WCAG 4.1.2
Name/Role/Value.

The `.rail-toggle` button shows/hides the council-history rail on BOTH layouts —
on desktop (>=1024) it COLLAPSES the persistent sidebar (`body.rail-collapsed`,
the rail translates off-canvas and `<main>` reclaims the width); on narrow it
OPENS the off-canvas drawer (`body.rail-open`). Before this guard the button
carried only `aria-label="Toggle councils"` with NO aria-expanded, so a screen
reader announced a bare "Toggle councils, button" with no programmatic
collapsed/expanded state — and after activating it, no confirmation of the new
state. It was the asymmetric sibling of the disclosure-state gaps the codebase
already closed for the memory-viewer thread rep (memory_viewer.py aria-expanded
lockstep) and the live-council round divider (council_review.py :aria-expanded).

The state is DUAL-MODE, so a naive `:aria-expanded="railOpen"` would be WRONG on
desktop (railOpen is always false there, yet the rail SHOWS): railExpanded folds
the breakpoint in — desktop → `!railCollapsed`, narrow → `railOpen`.

Drives the REAL petite-vue launchpad over http at BOTH widths and asserts the
attribute tracks the actual rail visibility (the body class) through a toggle.

Mutation-proof (verified during authoring): drop the `:aria-expanded` binding from
the `.rail-toggle` button (or revert railExpanded to a flat `railOpen`) → this reds
because at 1440 the attribute reads None/"false" while the desktop rail is shown
(the bite is the missing/wrong state, not a missing toggle — the toggle-present
precondition passes first). Restoring → green.
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))

    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    inner = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=inner)

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(tmp_path / "serve")


def _open_page(browser, port, width):
    page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)[:160]))
    page.add_init_script(
        "window.__TRINITY_DISPATCH__ = { dispatch: (o) => {"
        " if (o && o.onResult) o.onResult({tier:'extension', ok:true});"
        " return Promise.resolve({ok:true}); },"
        " probe: () => Promise.resolve({ok:true}), onStateChange: () => {},"
        " state: 'ready', extensionId: 'stub' };"
    )
    page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html", wait_until="networkidle", timeout=20000)
    page.wait_for_function(
        "() => { const r = document.getElementById('launchpad-app');"
        " return r && !r.hasAttribute('v-cloak'); }",
        timeout=10000,
    )
    return page, errs


@pytest.mark.slow
@pytest.mark.browser
def test_rail_toggle_aria_expanded_tracks_visibility(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _boot(tmp_path, monkeypatch)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # ── DESKTOP (1440): rail SHOWS by default → aria-expanded must be
                # "true"; collapse → "false"; restore → "true". (A flat railOpen
                # binding fails this: railOpen is false on desktop, so it would read
                # "false" while the rail is plainly shown.)
                page, errs = _open_page(browser, port, 1440)
                toggle = page.query_selector(".rail-toggle")
                # PRECONDITION (bite-not-vacuous): the toggle exists and points at
                # the rail it controls.
                assert toggle is not None, "no .rail-toggle hamburger at 1440 — nothing to assert state on"
                assert toggle.get_attribute("aria-controls") == "council-rail", (
                    "rail-toggle must aria-controls the council-history rail "
                    "(the <aside id='council-rail'> it shows/hides)"
                )
                assert page.query_selector("#council-rail") is not None, (
                    "the controlled rail must carry id='council-rail' so aria-controls resolves"
                )

                initial = toggle.get_attribute("aria-expanded")
                assert initial == "true", (
                    "DESKTOP: the persistent councils rail is SHOWN on first paint, but "
                    "the toggle announced aria-expanded=%r — a screen reader hears 'Toggle "
                    "councils, button' with no/wrong state (WCAG 4.1.2). Expected 'true'." % initial
                )
                toggle.click()
                page.wait_for_timeout(350)
                collapsed = toggle.get_attribute("aria-expanded")
                body_collapsed = page.evaluate("() => document.body.classList.contains('rail-collapsed')")
                assert collapsed == "false" and body_collapsed, (
                    "DESKTOP: after collapsing the rail (body.rail-collapsed=%r) the toggle "
                    "must announce aria-expanded='false' — got %r. The disclosure state must "
                    "track the actual rail visibility in lockstep." % (body_collapsed, collapsed)
                )
                toggle.click()
                page.wait_for_timeout(350)
                restored = toggle.get_attribute("aria-expanded")
                assert restored == "true", (
                    "DESKTOP: re-showing the rail must flip aria-expanded back to 'true' — got %r" % restored
                )
                assert not errs, f"JS errors while driving the desktop rail toggle: {errs[:4]}"
                page.close()

                # ── MOBILE (393): drawer HIDDEN by default → aria-expanded "false";
                # open → "true"; Esc-close → "false".
                page, errs = _open_page(browser, port, 393)
                toggle = page.query_selector(".rail-toggle")
                assert toggle is not None, "no .rail-toggle hamburger at 393 — mobile drawer can't be opened"
                m_initial = toggle.get_attribute("aria-expanded")
                assert m_initial == "false", (
                    "MOBILE: the rail drawer is HIDDEN by default, but the toggle announced "
                    "aria-expanded=%r — expected 'false' (a closed disclosure)." % m_initial
                )
                toggle.click()
                page.wait_for_timeout(350)
                m_open = toggle.get_attribute("aria-expanded")
                body_open = page.evaluate("() => document.body.classList.contains('rail-open')")
                assert m_open == "true" and body_open, (
                    "MOBILE: opening the drawer (body.rail-open=%r) must announce "
                    "aria-expanded='true' — got %r." % (body_open, m_open)
                )
                page.keyboard.press("Escape")
                page.wait_for_timeout(350)
                m_closed = toggle.get_attribute("aria-expanded")
                assert m_closed == "false", (
                    "MOBILE: closing the drawer (Esc) must flip aria-expanded back to 'false' — got %r" % m_closed
                )
                assert not errs, f"JS errors while driving the mobile rail toggle: {errs[:4]}"
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
