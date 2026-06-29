"""Browser guard: a MALFORMED runtime JSONP sidecar must NOT strand the live
council UI — the JS sibling of the Iter-64/65 Python wrong-type state-FILE sweep.

The live council page loads two runtime payloads over JSONP (file:// blocks fetch,
so a ``<script src>`` injects the sidecar which sets a global the loader reads):

* ``portal_pages/status/council_status_<token>.js`` — the poll status (poll path).
* ``council_outcomes/<council_id>.js`` — the canonical outcome (?council_id= path).

Both are written by Trinity as ``json.dumps`` of a dict, but a truncated /
concurrently-written / old-schema sidecar can leave a WRONG-TYPE payload on disk.
Pre-fix, two real stranding failures (driven 2026-06-18 in the UX robustness sweep):

1. **Forever-spinner** — a wrong-TYPE status payload (a string/number/array) is
   TRUTHY, so the poller RESETS its ``MAX_MISSING_POLLS`` give-up counter every
   tick, yet matches NO terminal branch (running/completed/failed/canceled). The
   spinner spun "Council running / Generating witty dialog…" FOREVER — past the
   give-up window, no pageerror, no honest state. (Verified the strand survived 16s,
   well past the ~12s give-up.)
2. **Blank render via uncaught throw** — a wrong-TYPE outcome payload (or an OBJECT
   outcome whose ``member_results`` is a string/number/object, or holds a ``null``
   member) flowed into ``(outcome.member_results || []).map((m) => m.provider)`` →
   ``TypeError: (...).map is not a function`` (or ``Cannot read properties of null``)
   → the ?council_id= render stranded BLANK (~69 chars) with no "Could not load
   council outcome" banner.

The class-level fix coerces a wrong-TYPE payload to ``null`` at the JSONP loader
chokepoint (``__trinityCoerceObj`` in launchpad_runtime — shared by ALL three status
pollers + every outcome/thread consumer) so the wrong-type root degrades to the
"no usable payload" path every consumer already handles; plus ``outcomeToRunState``
``Array.isArray``-gates ``member_results`` and drops non-object members (the OBJECT
outcome with a wrong-type nested field the loader can't catch). This guard pins:
* the status poll path reaches the honest "never started" give-up (no forever-spin),
* the outcome path renders with NO pageerror (no ``.map`` throw, no blank strand).

Serves an isolated, PII-free home over http (file:// can't carry ?status_token=).
Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _setup_home():
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import (
        council_outcomes_dir,
        portal_pages_dir,
        review_pages_dir,
    )

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    out_dir = council_outcomes_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    return status_dir, out_dir


def _write_status_sidecar(status_dir, token, payload_literal):
    """payload_literal is a RAW JS literal so we can write non-JSON wrong shapes."""
    js = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {payload_literal};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(js, encoding="utf-8")


def _write_outcome_sidecar(out_dir, council_id, payload_literal):
    js = (
        "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
        f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(council_id)}] = {payload_literal};\n"
    )
    (out_dir / f"{council_id}.js").write_text(js, encoding="utf-8")


# Wrong-TYPE outcome payloads that pre-fix threw an uncaught TypeError and stranded
# the ?council_id= render blank. Each must now render with NO pageerror.
_OUTCOME_MALFORMED = {
    "outcome_root_string": '"garbage outcome"',
    "outcome_root_number": "7",
    "outcome_member_results_string": '{"member_results":"not-an-array","synthesis_output":"x"}',
    "outcome_member_results_number": '{"member_results":5}',
    "outcome_member_results_object": '{"member_results":{"claude":"x"}}',
    "outcome_member_results_null_elem": '{"member_results":[null]}',
}


def test_outcome_malformed_payload_does_not_throw_or_strand(tmp_path, monkeypatch):
    """A wrong-TYPE outcome JSONP must NOT throw an uncaught JS exception (the
    ``(outcome.member_results || []).map is not a function`` strand) — it must
    render an honest page (the "Could not load council outcome" banner or an empty
    member grid), never a blank page from a stranded render."""
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _status_dir, out_dir = _setup_home()
    for cid, lit in _OUTCOME_MALFORMED.items():
        _write_outcome_sidecar(out_dir, cid, lit)

    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    failures = {}
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
            try:
                for cid in _OUTCOME_MALFORMED:
                    page = ctx.new_page()
                    errs: list[str] = []

                    def _on_err(e, _errs=errs):
                        _errs.append(str(e)[:200])
                        return None

                    page.on("pageerror", _on_err)
                    page.goto(
                        f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={cid}"
                    )
                    page.wait_for_timeout(2800)
                    info = page.evaluate(
                        """() => {
                          const t = (document.body.innerText || '').trim();
                          return {
                            // An HONEST degrade mounts the app and renders real
                            // content: either the "Council failed / Could not load
                            // council outcome" banner (a coerced-to-null root) or a
                            // full page with task text + Analysis + chain controls and
                            // an empty member grid (an object outcome whose wrong-type
                            // member_results was dropped). The mount marker is the
                            // page chrome ("← Launchpad" + the "Council" header). A raw
                            // `{{` template leak (un-mounted petite-vue) or a sub-~40-
                            // char body (the pre-fix .map-throw blank ~67 chars w/o the
                            // banner) is a STRAND.
                            mounted: t.indexOf('Launchpad') !== -1 && t.length >= 40,
                            rawTemplate: t.indexOf('{{') !== -1,
                            len: t.length,
                          };
                        }"""
                    )
                    page.close()
                    if errs:
                        failures[cid] = ("THREW", errs[0])
                    elif info["rawTemplate"]:
                        failures[cid] = ("RAW_TEMPLATE_LEAK", f"body {info['len']} chars")
                    elif not info["mounted"]:
                        # A stranded render: the app didn't mount usable content
                        # (pre-fix the .map throw aborted the render).
                        failures[cid] = ("BLANK_STRAND", f"body {info['len']} chars, not mounted")
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not failures, (
        "a malformed outcome JSONP threw an uncaught JS exception / stranded the "
        "?council_id= render blank — the live council page consumed a wrong-type "
        "outcome via `(outcome.member_results || []).map` without an Array.isArray "
        f"guard: {failures}"
    )


def test_status_malformed_payload_reaches_honest_give_up_not_forever_spinner(tmp_path, monkeypatch):
    """A wrong-TYPE status JSONP must NOT spin the council spinner forever. Pre-fix
    a truthy non-object status RESET the MAX_MISSING_POLLS counter every poll and
    matched no terminal branch, so the page sat "Council running…" past the give-up
    window with no honest state. The loader chokepoint now coerces it to null so the
    poller's "This council never started" give-up fires."""
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    status_dir, _out_dir = _setup_home()

    token = "tok_malformed_strand"
    # A string status payload — truthy, but no .status field => no terminal branch.
    _write_status_sidecar(status_dir, token, '"garbage status payload"')

    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
            try:
                page = ctx.new_page()
                errs: list[str] = []

                def _on_err(e):
                    errs.append(str(e)[:200])
                    return None

                page.on("pageerror", _on_err)
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}"
                )
                # Poll @1500ms, MAX_MISSING_POLLS=8 ~= 12s give-up. Wait past it.
                page.wait_for_timeout(15000)
                state = page.evaluate(
                    """() => {
                      const t = (document.body.innerText || '');
                      return {
                        stillRunning: /running|generating|working|witty|in progress/i.test(t),
                        saidNeverStarted: /never started|unavailable|could not/i.test(t),
                        spinners: document.querySelectorAll('.spinner, .live-actions, .chain-loading').length,
                      };
                    }"""
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errs, f"malformed status JSONP threw a pageerror: {errs[:2]}"
    # THE BUG: after the give-up window the page must NOT still be spinning. The
    # honest "This council never started" give-up must have fired.
    assert not state["stillRunning"], (
        "the live council spinner spun FOREVER on a wrong-type status payload — "
        "the truthy non-object reset the MAX_MISSING_POLLS give-up counter every "
        f"poll and matched no terminal branch (state: {state})"
    )
    assert state["saidNeverStarted"], (
        "the give-up never surfaced an honest 'this council never started' state on "
        f"a malformed status payload (state: {state})"
    )
    assert state["spinners"] == 0, (
        f"a live spinner is still mounted after the give-up window (state: {state})"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
