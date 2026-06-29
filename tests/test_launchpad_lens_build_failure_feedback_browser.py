"""A FAILED lens-build Stop/Restart must surface CO-LOCATED feedback — not silently
reset while the only error lands in the unrelated council-composer ribbon.

Class: OPTIMISTIC-SUCCESS / NO-FEEDBACK-ON-FAILURE on a failable dispatch — the same
shape as the launch busy-button, stop-council, failed-dispatch, popup Open/Stop, and
me-card render. Found 2026-06-17 in the class sweep: driving the lens-build card's
"Stop"/"Restart" with a FAILING dispatcher, the card showed "Stopping…" for ~2s then
silently reset to "Stop" as if nothing happened, while the error string ("worker not
reachable") surfaced ONLY in `.launch-status` — the COUNCIL composer ribbon, a surface
far from (and on the home view not even visible alongside) the lens-build card. So a
failed Stop/Restart read as a dead no-op.

Root-cause fix: stopLensBuild/restartLensBuild route a failed `r.ok===false` through
`_resolveLensBuildAction(r, 'stop-failed'|'restart-failed')` which sets a co-located
`lensBuildError` + a `*-failed` action state (button → "Retry stop/restart" + an in-card
⚠ error line) instead of `handleDispatchResult` (which pushes failures to the council
ribbon). Success still resets to idle.

Mutation-provable: reroute the failure back through handleDispatchResult (the old
behavior) and the error leaves the lens-build card → the in-card assert reds.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_lens_build_failure_is_co_located_canary():
    """CI-runnable canary: the co-located failure plumbing must exist."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    assert "_resolveLensBuildAction" in src, "lost the shared lens-build resolver"
    assert "lensBuildError" in src, "lost the co-located lens-build error field"
    assert "'stop-failed'" in src, "stopLensBuild no longer routes to a co-located failed state"
    assert "'restart-failed'" in src, "restartLensBuild no longer routes to a co-located failed state"
    # The failure path must NOT hand a failed lens-build result to the generic
    # dispatch handler (that surfaces in the council composer ribbon, not the card).
    assert "onResult: (r) => this._resolveLensBuildAction(r, 'stop-failed')" in src, (
        "stopLensBuild failure no longer co-located — would route to the council ribbon"
    )
    assert "onResult: (r) => this._resolveLensBuildAction(r, 'restart-failed')" in src, (
        "restartLensBuild failure no longer co-located — would route to the council ribbon"
    )


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _render(tmp_path, building: bool):
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    pd["lensBuild"] = {
        "building": building, "stage": "stage2", "label": "Distilling tensions…",
        "pct": 40, "status": "running" if building else "failed",
        "error": None, "lensPopulated": False,
    }
    sub = "build" if building else "snag"
    pp = tmp_path / f"serve_{sub}" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(pp.parent)


def _drive_failure(page, port, click_text):
    page.add_init_script(
        # A dispatcher that FAILS the action (ok:false) — exactly the not-reachable
        # / extension-error case. The actionable string must land on the card.
        "window.__TRINITY_DISPATCH__={dispatch:(o)=>{"
        " if(o&&o.onResult) setTimeout(()=>o.onResult("
        "  {tier:'extension', ok:false, response:{error:'lens worker not reachable'}}),10); }};"
    )
    page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
              wait_until="networkidle", timeout=20000)
    page.wait_for_function(
        "() => { const r = document.getElementById('launchpad-app');"
        " return r && !r.hasAttribute('v-cloak'); }",
        timeout=10000,
    )
    page.click(f"button:has-text('{click_text}')", timeout=5000)
    page.wait_for_timeout(400)  # past the 10ms onResult, well before any reset
    return page.evaluate(
        "() => {"
        " const card = [...document.querySelectorAll('section.card')]"
        "   .find(c => /Building your lens|Lens build/.test(c.innerText));"
        " const ribbon = document.querySelector('.launch-status');"
        " return { card: card ? card.innerText : '', ribbon: ribbon ? ribbon.innerText : '' }; }"
    )


@pytest.mark.slow
@pytest.mark.browser
@pytest.mark.parametrize("building,click_text,verb", [
    (True, "Stop", "stop"),
    (False, "Restart", "restart"),
])
def test_lens_build_failure_feedback_is_co_located(tmp_path, monkeypatch, building, click_text, verb):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    httpd, port = _render(tmp_path, building)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1100}).new_page()
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                state = _drive_failure(page, port, click_text)

                # The error message must be CO-LOCATED on the lens-build card.
                assert "lens worker not reachable" in state["card"], (
                    f"failed lens-build {verb} gave NO co-located error on the card "
                    f"— it read as a no-op: card={state['card']!r}"
                )
                assert f"Couldn't {verb} the lens build" in state["card"], (
                    f"no honest co-located {verb}-failure wording on the card: {state['card']!r}"
                )
                # And it must NOT have leaked into the unrelated council composer ribbon
                # (the original misrouting via handleDispatchResult).
                assert "lens worker not reachable" not in state["ribbon"], (
                    "lens-build failure leaked into the council composer ribbon "
                    f"(unrelated surface): {state['ribbon']!r}"
                )
                # The button must offer a retry, not silently revert to its idle label.
                assert f"Retry {verb}" in state["card"], (
                    f"button did not surface a retry affordance after a failed {verb}: {state['card']!r}"
                )
                assert not errs, f"console errors on failed lens-build {verb}: {errs}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def _render_complete_empty(tmp_path):
    """Render the lens-build card on the COMPLETE-but-no-tensions path the way the
    launchpad does after build_me_via_lens_pipeline finishes on a thin/cold corpus:
    the pipeline ALWAYS ends with write_progress("done", status="complete") whose
    STAGES label is the success string "Lens ready", while the launchpad reads
    lensPopulated=False (no taste tensions extracted). Reproduce that EXACT shape so
    a regression at the template render site bites."""
    from trinity_local.lens_progress import _STAGE_LABEL
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    # The terminal stage written by the real pipeline. Pull the label from the
    # source table so the test can't drift from the prod "done" label.
    pd["lensBuild"] = {
        "building": False, "stage": "done", "label": _STAGE_LABEL["done"],
        "pct": 100, "status": "complete", "error": None,
        "lensPopulated": False,
    }
    # Sanity: the prod stage label really is the success string the bug re-asserts.
    assert pd["lensBuild"]["label"] == "Lens ready"
    pp = tmp_path / "serve_completeempty" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(pp.parent)


@pytest.mark.slow
@pytest.mark.browser
def test_complete_but_empty_lens_build_card_does_not_re_assert_ready(tmp_path, monkeypatch):
    """FOUNDER SYMPTOM: a lens build that COMPLETES but extracts zero stable taste
    tensions (the cold-start / #295 preserved-degenerate path — the launchpad's
    worst first-run moment) painted a SELF-CONTRADICTING card: the honest header
    "Lens build finished — no tensions yet" with the stage-label meta line
    "Lens ready" rendered DIRECTLY below it. The pipeline always ends on stage
    "done" (label "Lens ready"), and the template's stage-label `<p>` was gated only
    on `status !== 'failed'`, so the success string leaked onto the no-tensions path
    and re-asserted the readiness the header was specifically written to DENY
    (green-while-degenerate, #35 — the exact contradiction
    _lens_build_for_launchpad's docstring forbids).

    The card must keep the honest "no tensions yet" header + the "needs more of your
    decisions" guidance, and must NOT show a "Lens ready" line anywhere.

    Mutation-provable: revert the stage-label `<p>` v-if (drop the
    `&& status !== 'complete'` clause) and the "Lens ready"-leak assert reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    httpd, port = _render_complete_empty(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1100}).new_page()
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                card_text = page.evaluate(
                    "() => { const c = [...document.querySelectorAll('section.card')]"
                    "  .find(c => /Lens build finished|Building your lens|Your lens is ready/.test(c.innerText));"
                    " return c ? c.innerText : ''; }"
                )
                assert card_text, "the complete-but-empty lens-build card did not render"
                # The honest no-tensions header + guidance must be present.
                assert "no tensions yet" in card_text, (
                    f"card lost the honest 'no tensions yet' header on the empty-complete "
                    f"path: {card_text!r}"
                )
                assert "needs more of your decisions" in card_text, (
                    f"card lost the honest 'needs more of your decisions' guidance: {card_text!r}"
                )
                # THE BITE: the success stage label "Lens ready" must NOT re-assert
                # readiness on the path whose header explicitly denied it.
                assert "Lens ready" not in card_text, (
                    "the complete-but-empty lens-build card re-asserted 'Lens ready' — "
                    "the 'done' stage label contradicts the honest 'no tensions yet' header "
                    f"(green-while-degenerate, #35): card={card_text!r}"
                )
                # And it must not also claim the ✓ ready header.
                assert "Your lens is ready" not in card_text, (
                    f"card wrongly claimed the ✓ ready header with no tensions: {card_text!r}"
                )
                assert not errs, f"console errors on complete-empty lens-build card: {errs}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def _render_failed_progress(tmp_path, exc):
    """Render the lens-build card the way the launchpad does on a FAILED
    auto-kicked first build: the cold-start kick (cold_start.maybe_kick_first_lens_build)
    catches the exception and writes `error=_lens_build_user_error(exc)` to the
    progress file, which _lens_build_for_launchpad surfaces verbatim. We feed the
    SAME helper output through the SAME template so a regression at the write site
    OR the render site bites."""
    from trinity_local.cold_start import _lens_build_user_error
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    # The cold-start kick writes write_progress("failed", ...) → stage/label both
    # the bare key "failed" (no STAGES mapping); reproduce that exact shape.
    pd["lensBuild"] = {
        "building": False, "stage": "failed", "label": "failed", "pct": 0,
        "status": "failed", "error": _lens_build_user_error(exc),
        "lensPopulated": False,
    }
    pp = tmp_path / "serve_rawexc" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(pp.parent)


@pytest.mark.slow
@pytest.mark.browser
def test_failed_lens_build_card_does_not_leak_raw_exception(tmp_path, monkeypatch):
    """FOUNDER SYMPTOM: the auto-kicked FIRST lens build fails on a thin/corrupt
    fresh corpus (the exact state it fires in) and the launchpad's 'Lens build hit
    a snag' card painted a raw `str(exc)` — "— 'centroid'" (a KeyError),
    "— Expecting value: line 1 column 1 (char 0)" (a JSONDecodeError), or even
    "— [Errno 2] No such file or directory: '/Users/…/.trinity/…'" (an OSError
    leaking a filesystem PATH). Same raw-{exc!r}-into-a-user-surface defect class
    as the iter-140 lens-health noise self-test. The card must read plain English
    naming only the exception TYPE, never the exception payload/path.

    Mutation-provable: revert cold_start._lens_build_user_error to
    `error=str(exc)[:200]` at the write site and the leaked-marker asserts red.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # Real exceptions whose str() carries a leak the user must NOT see: a bare
    # quoted dict key, a json-parser position string, and a filesystem path.
    exc = FileNotFoundError(2, "No such file or directory",
                            "/Users/x/.trinity/prompts/prompt_nodes.jsonl")
    # Markers that ONLY appear if the raw str(exc) leaked through.
    RAW_MARKERS = [
        "[Errno 2]", "No such file or directory: '/", "/.trinity/prompts/",
        "'centroid'", "Expecting value", "line 1 column 1",
    ]

    httpd, port = _render_failed_progress(tmp_path, exc)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc2:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc2}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1100}).new_page()
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                card_text = page.evaluate(
                    "() => { const c = [...document.querySelectorAll('section.card')]"
                    "  .find(c => /Lens build hit a snag/.test(c.innerText));"
                    " return c ? c.innerText : ''; }"
                )
                assert card_text, "the failed lens-build card did not render"
                # The card must NOT leak the raw exception payload / a filesystem path.
                leaked = [m for m in RAW_MARKERS if m in card_text]
                assert not leaked, (
                    "the 'Lens build hit a snag' card LEAKED a raw str(exc) into the UI "
                    f"(iter-140 class, raw-exc-into-user-surface): markers {leaked} "
                    f"in card={card_text!r}"
                )
                # And it must NOT show the bare stage key "failed —" (redundant with the
                # heading) — the regression the template label-suppression guards.
                assert "failed —" not in card_text and "failed\n" not in card_text, (
                    f"card still paints the bare 'failed' stage label: {card_text!r}"
                )
                # It MUST still be honest + actionable: name the exception type and
                # point at the recovery command.
                assert "FileNotFoundError" in card_text, (
                    f"card dropped the exception TYPE (bug-report signal): {card_text!r}"
                )
                assert "trinity-local lens --force" in card_text, (
                    f"card lost the actionable recovery command: {card_text!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
    # Belt-and-suspenders: the helper itself never returns a raw payload.
    from trinity_local.cold_start import _lens_build_user_error
    msg = _lens_build_user_error(KeyError("centroid"))
    assert "'centroid'" not in msg and "KeyError" in msg, (
        f"_lens_build_user_error leaked the exception payload: {msg!r}"
    )
