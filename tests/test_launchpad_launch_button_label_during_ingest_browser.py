"""The primary "Launch Council" button must not CLAIM a council is running while
what's actually running is an INGEST.

UNCLEAR / self-contradicting-state-label defect (UX sweep 2026-06-23): the Launch
button is `:disabled="busy"`, and `busy` is true for ANY in-flight operation —
including a transcript INGEST fired from the settings modal's "Ingest transcripts
once now" control. The label/tooltip used to hardcode council language:

  * label  : `busy ? 'Council in progress…' : 'Launch Council'`
  * tooltip : `busy ? 'A council is already running — open or stop it below first'`

So while an ingest ran, the button two rows below the hero (which correctly read
"Ingest in Progress") claimed "Council in progress…" with a tooltip telling the user
to "open or stop" a council that does not exist — and an ingest has no Open/Stop
affordance to act on. A flat self-contradiction on the launchpad's headline CTA, the
exact Iter-314/315/440 self-contradicting-state-label class on a NEW control,
reachable via the common Settings → Ingest path.

The fix keys the copy on `operation.kind` (`launchButtonLabel` / `launchButtonTitle`
getters): an ingest-busy button reads "Scanning transcripts…" with an honest tooltip;
only a council-busy button keeps the council language.

MUTATION-PROVEN to BITE: revert the label getter to the kind-agnostic
`busy ? 'Council in progress…' : 'Launch Council'` → this guard reds (the Launch
button reads "Council in progress…" while an INGEST is the only thing running) with
the founder symptom; restore → green. The council-busy SANITY in the same test
guarantees the fix didn't just blank the council label.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _launch_button(page):
    """The home view's primary CTA inside the council composer card."""
    return page.evaluate(
        "() => { const b = document.querySelector('.launch-grid .actions button.primary');"
        " return b ? { label: b.textContent.trim(), title: b.getAttribute('title') || '',"
        " disabled: b.disabled } : null; }"
    )


def test_launch_button_does_not_claim_council_during_ingest(tmp_path, monkeypatch):
    """While an INGEST runs, the Launch button must NOT read 'Council in progress…'
    nor offer an open/stop-a-council tooltip — it must name the scan. A council run
    SANITY keeps the council language correct."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    # HOME view — the settings gear AND the Launch button are both home-only
    # (.home-card; the /stats view hides them).
    html = render_launchpad_html(page_data=page_data, view="home")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    # An INGEST dispatcher that stays PENDING (never calls onResult) so the
    # operation remains busy with kind='ingest'. A council dispatcher likewise stays
    # pending so the council-busy SANITY can read the running label.
    init_dispatch = (
        "window.__DISPATCHED__ = [];"
        "window.__TRINITY_DISPATCH__ = { state: 'ready', extensionId: 'stub',"
        " onStateChange: function(){}, isAvailable: function(){return true;},"
        " probe: function(){ return Promise.resolve('ready'); },"
        " dispatch: function(o){ if (o && o.extensionAction)"
        "   window.__DISPATCHED__.push(o.extensionAction.kind); } };"
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                for width in (393, 1280):
                    page = browser.new_context(
                        viewport={"width": width, "height": 900}
                    ).new_page()
                    page.add_init_script(init_dispatch)
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

                    # PRECONDITION (non-vacuous): at rest the Launch button reads
                    # "Launch Council" and is enabled. If this fails the bite below
                    # would be vacuous.
                    rest = _launch_button(page)
                    assert rest is not None, "Launch button not found in the home view"
                    assert rest["label"] == "Launch Council" and not rest["disabled"], (
                        "precondition failed: at rest the primary CTA should read "
                        f"'Launch Council' and be enabled, saw {rest!r}"
                    )

                    # Fire an INGEST from the settings modal.
                    page.click('button[aria-label="Open settings"]')
                    page.wait_for_selector(
                        'button[aria-label="Ingest transcripts once now"]',
                        timeout=4000,
                    )
                    page.click('button[aria-label="Ingest transcripts once now"]')
                    # Wait until the operation is busy (hero flips to "Ingest in
                    # Progress") — proves the ingest is the only thing running.
                    page.wait_for_function(
                        "() => /Ingest in Progress/i.test("
                        " document.querySelector('.hero-shell h1').textContent)",
                        timeout=4000,
                    )
                    assert "ingest-recent" in page.evaluate(
                        "() => window.__DISPATCHED__"
                    ), "the Ingest control did not dispatch an ingest-recent action"

                    btn = _launch_button(page)
                    assert btn is not None and btn["disabled"], (
                        "the Launch button should be disabled while an ingest runs"
                    )

                    # BITE: the disabled Launch button must NOT claim a council is
                    # running, and must NOT offer a tooltip telling the user to
                    # "open or stop" a (nonexistent) council — that's the founder
                    # symptom (hero says 'Ingest in Progress', button says
                    # 'Council in progress…').
                    assert "Council in progress" not in btn["label"], (
                        "self-contradicting state label: the Launch button reads "
                        f"{btn['label']!r} (a COUNCIL claim) while an INGEST is the "
                        "only operation running and the hero correctly reads 'Ingest "
                        f"in Progress' (width={width}). The disabled label must name "
                        "the scan, not a council that isn't running."
                    )
                    assert "council is already running" not in btn["title"], (
                        "self-contradicting tooltip: the Launch button's title claims "
                        f"{btn['title']!r} while an INGEST (not a council) is running — "
                        "and an ingest has no open/stop-a-council affordance to act on "
                        f"(width={width})."
                    )
                    # POSITIVE: the honest copy names the scan.
                    assert "can" in btn["label"].lower() or "scan" in btn["label"].lower(), (
                        "the ingest-busy Launch button should name the transcript scan, "
                        f"saw {btn['label']!r} (width={width})"
                    )

                    # COUNCIL-BUSY SANITY: fire a real council and confirm the council
                    # language is still correct (the fix didn't just blank the label).
                    page2 = browser.new_context(
                        viewport={"width": width, "height": 900}
                    ).new_page()
                    page2.add_init_script(init_dispatch)
                    page2.goto(
                        f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                        wait_until="networkidle",
                        timeout=20000,
                    )
                    page2.wait_for_function(
                        "() => { const r = document.getElementById('launchpad-app');"
                        " return r && !r.hasAttribute('v-cloak'); }",
                        timeout=10000,
                    )
                    page2.fill("#council-prompt", "Which database for a multi-tenant SaaS?")
                    page2.click('.launch-grid .actions button.primary')
                    page2.wait_for_function(
                        "() => /Council in Progress/i.test("
                        " document.querySelector('.hero-shell h1').textContent)",
                        timeout=4000,
                    )
                    cbtn = _launch_button(page2)
                    assert cbtn["label"] == "Council in progress…", (
                        "regression: a genuinely-running COUNCIL must keep the "
                        f"'Council in progress…' label, saw {cbtn['label']!r} "
                        f"(width={width})"
                    )
                    page2.close()
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
