"""Long-unbreakable-token overflow on free-text ERROR/STATUS surfaces.

THE CLASS (UX sweep 2026-06-23): a DOM element that displays a writer-supplied
error string (a CLI/host error echoing a user path, a download-failure message, a
dispatch-failure detail) MUST break an unbreakable token, or that token streams off
its card and horizontal-scrolls the whole surface on a narrow phone/panel. The
founder hit the popup ``.status`` sibling of this exact class at Iter 407 (a quota
URL surviving ``safeErrorMessage`` streamed off the 440px popup). The bug is
cross-cutting at (surface × STATE × error-code-path × element): each error-display
element has its OWN paint path, so a "complete" census keeps missing per-path
siblings whose CSS carries ``white-space: pre-wrap`` / nothing but NOT
``overflow-wrap``.

The global long-token guard reads each surface's INITIAL DOM; these error/status
states only mount AFTER an interaction (a failed Probe / a failed download / a
failed chain dispatch), so they are that guard's structural blind spot. Each test
here DRIVES the real failure and asserts no page-level horizontal overflow.

THREE elements were unprotected (overflow-wrap: normal) and are fixed by adding
``overflow-wrap: anywhere; word-break: break-word; min-width: 0`` at source:

1. LAUNCHPAD /stats bulk-import error banner — ``importProbeResult.error`` renders
   the CLI's ``f"path not found: {root}"`` (import_export.py), which echoes the
   user-pasted path VERBATIM. A content-hash/UUID-named download path (the canonical
   no-separator filename) is a separator-free token → documentElement.scrollWidth
   ~815px on a 320px panel.  (launchpad_template.py import-error <div>)

2. SIDEPANEL standalone download status (#dl-status .status) — sidepanel-shell.js
   sets ``"Download failed: " + chrome.runtime.lastError.message``; that message can
   carry a separator-free token (a quota/billing host, a blob URL, a native-host
   error id). The popup ``.status`` twin already breaks this; the sidepanel ``.status``
   was the asymmetric miss → documentElement.scrollWidth ~639px on a 320px panel.
   (sidepanel.html .status — pre-wrap, no overflow-wrap)

3. LIVE COUNCIL chain-error banner (the running-state ``<section class="card"
   v-if="chainError">``) — ``dispatchErrorMessage`` returns
   ``String(r.response.error)`` verbatim on a failed chain dispatch, so a host error
   token streams off → documentElement.scrollWidth ~761px on a 320px panel.
   (council_review.py render_live_council_page — NOT the dead #311
   render_unified_council_page.)

MUTATION PROOF (per element): flip its ``overflow-wrap: anywhere`` back to
``normal`` at source (rebuild the bundle for the bundled live page) → the matching
test reds with documentElement.scrollWidth >> the viewport; restore + rebuild →
green (byte-identical bundle). The three tests are independent: a mutant on one
element leaves the other two green.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"

# A genuinely separator-free token (no /, -, _, ., space) — the kind that survives a
# CSS soft-wrap and forces a page-wide horizontal scroll if the element doesn't break.
HASH_PATH_SEGMENT = (
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15"
    "b0f00a08deadbeefcafef00dba5eba11cafebabe1234567890abcdef"
)
HOST_ERROR_TOKEN = (
    "ExtensionHostReturnedHTTP402PaymentRequiredVisitBillingConsole"
    "ToReactivateYourSubscriptionImmediatelyWithNoSeparatorsAtAll"
)


def _serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _doc_geom(page) -> tuple[int, int]:
    info = page.evaluate(
        "() => ({ s: document.documentElement.scrollWidth,"
        " c: document.documentElement.clientWidth })"
    )
    return int(info["s"]), int(info["c"])


def test_launchpad_import_error_long_path_does_not_overflow_at_320px(tmp_path, monkeypatch):
    """A failed Probe whose CLI error echoes a content-hash-named path (separator-free)
    must NOT horizontal-scroll the /stats page off its 320px viewport."""
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
    html = render_launchpad_html(page_data=page_data, view="stats")
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    user_path = "/Users/me/Downloads/" + HASH_PATH_SEGMENT
    cli_err = json.dumps({"ok": False, "error": "path not found: " + user_path})
    init = (
        "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
        "  if (!o || !o.extensionAction || !o.onResult) return;"
        "  if (o.extensionAction.kind === 'import-export-dry-run') {"
        "    o.onResult({ ok:false, stdout: " + json.dumps(cli_err) + " }); }"
        " }, onStateChange: function(){}, isAvailable: function(){ return true; } };"
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 320, "height": 1100}
                ).new_page()
                page.add_init_script(init)
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                page.fill("section.import-export-card input[type=text]", "/x")
                page.evaluate(
                    "() => { document.querySelector('section.import-export-card')"
                    " .querySelector('button').click(); }"
                )
                page.wait_for_function(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " return Array.from(c.querySelectorAll('div'))"
                    "   .some(d => /path not found/.test(d.innerText)); }",
                    timeout=4000,
                )
                # FALSE-PASS GUARD: the error banner must actually be on screen.
                err_shown = page.evaluate(
                    "() => Array.from(document.querySelectorAll('section.import-export-card div'))"
                    ".some(d => /path not found/.test(d.innerText))"
                )
                assert err_shown, "import-error banner never mounted — overflow check would false-pass"

                s, c = _doc_geom(page)
                assert s <= c, (
                    f"@320px: the bulk-import error banner streamed the user-pasted path "
                    f"off-screen — documentElement.scrollWidth={s} > clientWidth={c}. The "
                    f"importProbeResult.error <div> needs overflow-wrap:anywhere so a "
                    f"content-hash-named path (path not found: <token>) wraps instead of "
                    f"horizontal-scrolling the /stats page."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_sidepanel_download_failed_long_token_does_not_overflow_at_320px():
    """A failed download whose chrome.runtime.lastError.message carries a separator-free
    token must NOT horizontal-scroll the side panel (the popup .status twin breaks it;
    the sidepanel .status was the asymmetric miss)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(EXT)
    set_status = (
        "() => { document.getElementById('loading').hidden = true;"
        " document.getElementById('standalone').hidden = false;"
        " const s = document.getElementById('dl-status');"
        " s.className = 'status';"
        " s.textContent = 'Download failed: " + HOST_ERROR_TOKEN + "'; }"
    )
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 320, "height": 700}
                ).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/sidepanel.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.evaluate(set_status)
                shown = page.evaluate(
                    "() => /Download failed/.test("
                    "document.getElementById('dl-status').textContent)"
                )
                assert shown, "#dl-status never showed the failure — overflow check would false-pass"

                s, c = _doc_geom(page)
                assert s <= c, (
                    f"@320px: the sidepanel download-failure status streamed "
                    f"chrome.runtime.lastError.message off-screen — "
                    f"documentElement.scrollWidth={s} > clientWidth={c}. sidepanel.html "
                    f".status had white-space:pre-wrap but NO overflow-wrap (the popup "
                    f".status twin already breaks this exact case)."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_live_council_chain_error_long_token_does_not_overflow_at_320px(tmp_path, monkeypatch):
    """A failed chain dispatch whose response.error carries a separator-free host token
    must NOT horizontal-scroll the live council page (dispatchErrorMessage returns the
    raw String(r.response.error))."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    cid = "council_chain_overflow_guard"
    save_council_outcome(
        CouncilOutcome(
            council_run_id=cid,
            bundle_id=cid,
            task_cluster_id="cluster_overflow",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache the embedder in-process or per-call?"},
            member_results=[
                CouncilMemberResult(provider="claude", model="opus", output_text="In-process."),
                CouncilMemberResult(provider="codex", model="gpt", output_text="Per-call."),
            ],
            synthesis_prompt="Review.",
            synthesis_output="In-process caching wins.",
            routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
            created_at="2026-06-02T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()
    httpd, port = _serve(tmp_path)

    stub = (
        "window.__TRINITY_DISPATCH__ = { dispatch: ({extensionAction, onResult}) => {"
        " window.__captured = extensionAction;"
        " setTimeout(() => onResult({ ok:false, response:{ error: '"
        + HOST_ERROR_TOKEN
        + "' } }), 60); } };"
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 320, "height": 900}
                ).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={cid}"
                )
                page.wait_for_timeout(1200)
                page.evaluate(stub)
                page.evaluate(
                    "() => [...document.querySelectorAll('button')]"
                    ".find(x => /Continue \\(one round\\)/.test(x.textContent)).click()"
                )
                # The chain-error banner mounts with the raw host token.
                page.wait_for_function(
                    "() => [...document.querySelectorAll('section.card')]"
                    ".some(s => /" + HOST_ERROR_TOKEN[:24] + "/.test(s.textContent))",
                    timeout=5000,
                )
                shown = page.evaluate(
                    "() => [...document.querySelectorAll('section.card')]"
                    ".some(s => /" + HOST_ERROR_TOKEN[:24] + "/.test(s.textContent))"
                )
                assert shown, "chainError banner never mounted — overflow check would false-pass"

                s, c = _doc_geom(page)
                assert s <= c, (
                    f"@320px: the live-council chain-error banner streamed the host "
                    f"response.error off-screen — documentElement.scrollWidth={s} > "
                    f"clientWidth={c}. The chainError <section>/<span> needs "
                    f"overflow-wrap:anywhere so String(r.response.error) wraps instead of "
                    f"horizontal-scrolling the live council page."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
