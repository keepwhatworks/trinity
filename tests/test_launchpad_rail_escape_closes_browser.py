"""The mobile councils rail drawer must close on Escape — the OTHER branch of the
launchpad's global Escape dispatcher.

The single global keydown(Escape) handler (launchpad_template.init) routes BOTH
dismissible overlays: the settings modal wins first
(`if (this.settingsOpen) { this.settingsOpen = false; return; }`), otherwise it
falls through to `this.closeRail()` for the narrow-width councils drawer. Iter 62
fixed + guarded the modal branch; this pins the rail branch — the keyboard exit for
the OTHER content-covering overlay on the launchpad (a fixed translateX drawer over a
dimmed `.rail-scrim`, z-index 55, that traps the narrow viewport when open).

Before Iter 62 the handler was `if (e.key === 'Escape') this.closeRail();` — so the
rail branch was the ONLY thing Escape did. The dismissible-overlay class is now
closed (modal + rail both Escape-dismissable), but only the modal branch had a guard;
a future edit that drops `this.closeRail()` from the `else` path (or the whole
keydown listener) would silently strand a keyboard user with the drawer open and only
the scrim-click / a council-link click to escape. This reds on that.

Drives the REAL petite-vue launchpad over http at 393px (mobile, where the drawer is
the nav): opens the rail via the `.rail-toggle` hamburger, asserts `body.rail-open`,
presses Escape, asserts the drawer closed (`body` no longer carries `rail-open`).

Mutation-proof: revert the handler to `if (e.key === 'Escape') this.closeRail();`
(keep) is fine — instead DROP the `this.closeRail();` line from the keydown handler
(so the `else` branch becomes a no-op) and this reds with "rail drawer STILL OPEN
after Escape". Verified during authoring: removing `this.closeRail();` from the
keydown listener → body still carries `rail-open` after Escape → this test fails with
that exact message; restoring → green.
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


@pytest.mark.slow
@pytest.mark.browser
def test_mobile_rail_drawer_closes_on_escape(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # The UNWRAPPED inner page_data is the served-launchpad shape. Passing the
    # wrapped {pageData:...} would leave pageData.defaultMembers undefined (a
    # test-only artifact), but Escape/rail logic doesn't depend on it either way.
    inner = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=inner)

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # 393px → mobile: the councils rail is a translateX drawer, not the
                # always-visible desktop sidebar.
                page = browser.new_context(viewport={"width": 393, "height": 900}).new_page()
                errs: list[str] = []

                def _on_err(e):
                    errs.append(str(e)[:160])
                    return None

                page.on("pageerror", _on_err)
                # Stub the dispatcher so nothing reaches a real extension/council.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: (o) => {"
                    " if (o && o.onResult) o.onResult({tier:'extension', ok:true});"
                    " return Promise.resolve({ok:true}); } };"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )

                toggle = page.query_selector(".rail-toggle")
                assert toggle is not None, (
                    "no .rail-toggle hamburger at 393px — the mobile rail drawer "
                    "can't be opened, so its Escape exit can't be exercised"
                )
                toggle.click()
                page.wait_for_timeout(300)
                assert page.evaluate(
                    "() => document.body.classList.contains('rail-open')"
                ), "the rail drawer did not open after clicking the .rail-toggle hamburger"

                # Press Escape — the drawer must close (the else-branch of the global
                # Escape dispatcher).
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                still_open = bool(
                    page.evaluate("() => document.body.classList.contains('rail-open')")
                )
                assert not still_open, (
                    "rail drawer STILL OPEN after Escape — the global keydown(Escape) "
                    "handler must fall through to this.closeRail() when no modal is "
                    "open. A keyboard user is stranded with the narrow-viewport drawer "
                    "(over the .rail-scrim) and only a scrim-click to escape — the "
                    "rail sibling of the settings-modal Escape gap (Iter 62)."
                )
                assert not errs, f"JS errors while driving the rail Escape path: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
