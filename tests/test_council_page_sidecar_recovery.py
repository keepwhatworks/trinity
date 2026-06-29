"""The live council page must recover which council to show even when its URL
has NO query string — because macOS strips the `?status_token=…` query (and the
fragment) from `file://` URLs opened via `open`/`open location`, which is exactly
what `webbrowser.open` (and thus the popup's "Open council page") uses.

Founder report 2026-06-12 (Image #10): clicking "Open council page" opened
`file:///…/review_pages/live_council.html` with the query GONE → the page had no
status_token → it rendered a blank card. Verified the OS behavior directly:
`osascript -e 'open location "file://…?status_token=X"'` → Chrome receives the
bare path, query dropped.

The earlier dispatch harness never caught this because it did
`page.goto("…?status_token=X")` — a hand-built URL that bypasses the OS open
path entirely. THE CLASS of bug: testing the page with a URL it never actually
receives. The fix: the host writes a sidecar pointer (`_active_council.js`) next
to the page, and the page reads it when it has no URL params — the file://-safe
channel (same <script> injection as the status JSONP). This guard pins BOTH
sides: the host writes the sidecar, AND the bare page recovers from it.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import shutil
import threading
from pathlib import Path

import pytest

from trinity_local import capture_host


def _seed_home(tmp_path, monkeypatch):
    home = tmp_path / ".trinity"
    (home / "review_pages").mkdir(parents=True)
    (home / "portal_pages" / "status").mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    return home


def test_open_council_page_writes_sidecar(tmp_path, monkeypatch):
    home = _seed_home(tmp_path, monkeypatch)
    from trinity_local.council_review import write_live_council_page

    # Headless CI has no browser, so webbrowser.open returns False and the call
    # reports ok=False even though the sidecar IS written. Stub it so the test
    # asserts the SIDECAR-write contract, not the (env-dependent) browser launch.
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)
    write_live_council_page(force=True)
    r = capture_host._open_council_page(
        {"status_token": "launch_abc123", "task": "why is the sky blue?", "members": ["claude", "codex"]}
    )
    assert r.get("ok") is True
    sidecar = home / "review_pages" / "_active_council.js"
    assert sidecar.exists(), "open-council-page must write the _active_council.js sidecar"
    body = sidecar.read_text(encoding="utf-8")
    assert body.startswith("window.__TRINITY_ACTIVE_COUNCIL__ = ")
    # Parse the JSON object out and check the pointer.
    m = re.search(r"= (\{.*\});", body)
    assert m is not None, f"sidecar body not in the expected shape: {body!r}"
    obj = json.loads(m.group(1))
    assert obj["status_token"] == "launch_abc123"
    assert obj["task"] == "why is the sky blue?"
    assert obj["members"] == ["claude", "codex"]


def test_open_council_page_rejects_bad_token_without_writing_sidecar(tmp_path, monkeypatch):
    home = _seed_home(tmp_path, monkeypatch)
    from trinity_local.council_review import write_live_council_page

    write_live_council_page(force=True)
    r = capture_host._open_council_page({"status_token": "../../etc/passwd"})
    assert r.get("ok") is False
    assert not (home / "review_pages" / "_active_council.js").exists()


def test_page_emits_active_council_loader():
    """The page must contain the sidecar-recovery wiring (no-browser smoke)."""
    from trinity_local.council_review import render_live_council_page

    html = render_live_council_page()
    assert "loadActiveCouncilScript" in html, "page lost the sidecar loader"
    assert "_active_council.js" in html, "page no longer references the sidecar file"
    assert "__TRINITY_ACTIVE_COUNCIL__" in html


# ── Real-browser guard: the bare page (macOS-stripped URL) recovers the council ──

pytestmark_browser = [pytest.mark.slow, pytest.mark.browser]


@pytest.mark.slow
@pytest.mark.browser
def test_bare_council_page_recovers_from_sidecar(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = _seed_home(tmp_path, monkeypatch)
    # The page mounts petite-vue from ../portal_pages/vendor — copy it in.
    real_vendor = Path.home() / ".trinity" / "portal_pages" / "vendor"
    if not real_vendor.exists():
        pytest.skip("no vendored petite-vue to serve")
    shutil.copytree(real_vendor, home / "portal_pages" / "vendor")

    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_status import write_council_status

    write_live_council_page(force=True)
    token = "launch_sidecarguard"
    write_council_status(token, status="running", task_text="SIDECAR-RECOVERS-THIS",
                         bundle_id="b", council_id="c", metadata={"members": ["claude"]})
    capture_host._open_council_page({"status_token": token, "task": "SIDECAR-RECOVERS-THIS",
                                     "members": ["claude"]})

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(home))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no chromium: {exc}")
            try:
                page = browser.new_context().new_page()
                # NO query string — exactly what the OS hands the browser.
                page.goto(f"http://127.0.0.1:{port}/review_pages/live_council.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(1500)
                s = page.evaluate(
                    "() => ({"
                    " mustache: (document.body.innerHTML||'').includes('{{'),"
                    " sidecar: !!window.__TRINITY_ACTIVE_COUNCIL__,"
                    " hasTask: (document.body.innerText||'').includes('SIDECAR-RECOVERS-THIS'),"
                    "})"
                )
                assert s["sidecar"], "the page never loaded the _active_council.js sidecar"
                assert not s["mustache"], "petite-vue didn't mount (raw {{ }} left in the DOM)"
                assert s["hasTask"], (
                    "the bare page did not recover the council from the sidecar — it would "
                    "render blank for the founder's 'Open council page' click"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
