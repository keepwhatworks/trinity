"""Browser guard: a TERMINAL FAILED council on the live page must offer a working
"Try again" affordance — re-dispatching the SAME task as a fresh council — and must
NOT silently no-op when the dispatcher is absent.

THE DEAD-END (found 2026-06-19 driving the live page with a seeded FAILED council in
the UX sweep, the finding Iter 126 explicitly DEFERRED): a council whose status flips
to ``failed`` showed the "Council failed" banner with ZERO buttons. The only forward
path on this page is the chain composer ("Continue the thread"), which gates on
``canChainNext`` = ``last.completed && last.councilId`` — a failed council has NEITHER
(no completed flag, no council id), so the composer never renders. The user was
stranded: no retry, no re-run, no next step.

THE FIX (council_review.py):
  * ``canRetryFailed(seg, segIndex)`` — true only for the LAST segment when it is
    ``failed`` (NOT ``canceled`` — a stop was deliberate) and the page holds the task
    text (``threadTaskText``, which the poll reads off the status sidecar).
  * ``retryFailedCouncil()`` — re-dispatches the SAME task via the
    ``launch-council`` action (capture_host's ACTION_ALLOWLIST — only ``task`` is
    required), NOT a chain-iterate (a failed council has no responses to iterate FROM).
    Flips the dead segment to a fresh running attempt (the optimistic ACK), and on a
    dispatcher-absent / failed dispatch rolls back to the FAILED state and surfaces the
    honest ``dispatchErrorMessage`` in the ``chainError`` ribbon — NO silent no-op
    (the Iter-112/115 NO-FEEDBACK class).
  * A "Try again" button in the failure card, gated on ``canRetryFailed``.

This guard drives the REAL failed page on the poll path and reads the rendered DOM +
the stubbed dispatch payload (the dispatch is stubbed before any click so it NEVER
hits a real extension / a real council). Asserts:
  (A) failed council → "Try again" button visible, 44px+ tap target;
  (B) click → fires kind:'launch-council' with the ORIGINAL task, UI flips to
      "Council running";
  (C) dispatcher absent → click still surfaces an honest error ribbon (no silent
      no-op), UI does NOT falsely show "Council running";
  (D) a CANCELED council (deliberate stop) gets NO "Try again".

Geometry/DOM/payload assertions, NOT source-string checks. Serves an isolated,
PII-free synthetic council over http (file:// can't carry ``?status_token=``
reliably). Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_MEMBERS = ["claude", "codex", "antigravity"]
_TASK = "Should I migrate the build from Make to Bazel?"

# A dispatch stub installed BEFORE any click so a "Try again" NEVER reaches a real
# extension / launches a real council. Records each extensionAction and resolves
# ok per the requested mode. Includes onStateChange (the real dispatcher exposes it;
# council_review subscribes at mount) so no unrelated pageerror masks the assertions.
_STUB = """
window.__TRINITY_DISPATCH_CALLS__ = [];
window.__TRINITY_DISPATCH__ = {
  state: '%STATE%',
  onStateChange(cb) { return () => {}; },
  probe() {},
  dispatch(opts) {
    window.__TRINITY_DISPATCH_CALLS__.push(opts.extensionAction);
    setTimeout(() => opts.onResult && opts.onResult(%RESULT%), 5);
  }
};
"""


def _failed_status(token: str) -> dict:
    return {
        "status": "failed",
        "status_token": token,
        "task_text": _TASK,
        "error": "All members failed to respond (dispatch error: provider quota exhausted).",
        "memberOrder": _MEMBERS,
        "members": {p: {"status": "failed"} for p in _MEMBERS},
        "synthesis": {"status": "failed"},
    }


def _canceled_status(token: str) -> dict:
    return {
        "status": "canceled",
        "status_token": token,
        "task_text": _TASK,
        "error": "Council stopped.",
        "memberOrder": ["claude", "codex"],
        "members": {
            "claude": {"status": "done", "response_text": "Bazel above ~30 modules.",
                       "response_html": "<p>Bazel above ~30 modules.</p>"},
            "codex": {"status": "pending"},
        },
        "synthesis": {"status": "pending"},
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed(tmp_path, monkeypatch, status):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    token = status["status_token"]
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(status)};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(sidecar, encoding="utf-8")


def _stub(state: str, result: dict) -> str:
    return _STUB.replace("%STATE%", state).replace("%RESULT%", json.dumps(result))


def _open(port, token, members, stub, width=393):
    """Open the failed page with the dispatch stubbed; return (page, browser, ctx, errs)."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as exc:  # pragma: no cover - env-dependent
        pw.stop()
        pytest.skip(f"no launchable chromium: {exc}")
    ctx = browser.new_context(viewport={"width": width, "height": 1200})
    page = ctx.new_page()
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)[:160]))
    page.add_init_script(stub)
    page.goto(
        f"http://127.0.0.1:{port}/review_pages/live_council.html"
        f"?status_token={token}&members={','.join(members)}"
    )
    page.wait_for_timeout(2600)
    return page, browser, pw, errs


def test_failed_council_retry_fires_launch_council_and_flips_to_running(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_retry_ok"
    _seed(tmp_path, monkeypatch, _failed_status(token))
    httpd, port = _serve(tmp_path)
    try:
        page, browser, pw, errs = _open(port, token, _MEMBERS, _stub("ready", {"ok": True}))
        try:
            assert not errs, f"JS pageerrors: {errs[:3]}"
            body = page.evaluate("() => document.body.innerText")
            assert "Council failed" in body, f"expected the failure banner; body: {body[:300]!r}"

            btn = page.query_selector("text=Try again")
            # THE DEAD-END: a failed council had NO forward affordance.
            assert btn is not None and btn.is_visible(), (
                "a FAILED council offered NO 'Try again' affordance — the page dead-ended "
                "on the failure banner (the chain composer gates on canChainNext, which a "
                "failed council never satisfies)."
            )
            box = btn.bounding_box()
            assert box and box["height"] >= 44, (
                f"the 'Try again' tap target is under 44px on a touch width: {box!r}"
            )

            btn.click()
            page.wait_for_timeout(400)
            calls = page.evaluate("() => window.__TRINITY_DISPATCH_CALLS__")
            assert calls and calls[0].get("kind") == "launch-council", (
                "'Try again' must re-dispatch the SAME task as a FRESH council "
                f"(kind:'launch-council') — got dispatch calls: {calls!r}"
            )
            assert calls[0].get("task") == _TASK, (
                f"the retry must re-run the ORIGINAL task, got: {calls[0].get('task')!r}"
            )
            after = page.evaluate("() => document.body.innerText")
            assert "Council running" in after, (
                "after a successful retry the failure banner must be replaced by the live "
                f"'Council running' ack; body: {after[:300]!r}"
            )
        finally:
            browser.close()
            pw.stop()
    finally:
        httpd.shutdown()


def test_failed_council_retry_no_dispatcher_surfaces_error_not_silent_noop(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_retry_fail"
    _seed(tmp_path, monkeypatch, _failed_status(token))
    httpd, port = _serve(tmp_path)
    try:
        # Dispatcher present but unreachable: the council can't actually start.
        page, browser, pw, errs = _open(
            port, token, _MEMBERS, _stub("absent", {"ok": False, "reason": "extension-unreachable"})
        )
        try:
            assert not errs, f"JS pageerrors: {errs[:3]}"
            btn = page.query_selector("text=Try again")
            assert btn is not None and btn.is_visible(), "missing 'Try again' on a failed council"
            btn.click()
            page.wait_for_timeout(400)
            calls = page.evaluate("() => window.__TRINITY_DISPATCH_CALLS__")
            assert calls and calls[0].get("kind") == "launch-council", (
                f"the retry should still attempt a launch-council dispatch; got {calls!r}"
            )
            alert = page.query_selector("section[role=alert]")
            alert_text = alert.inner_text() if alert else ""
            # THE NO-FEEDBACK CLASS (Iter 112/115): a failed/absent dispatch must NOT
            # be a silent no-op — surface an honest error ribbon.
            assert alert and "Couldn't reach the Trinity extension" in alert_text, (
                "a retry that couldn't reach the extension surfaced NO feedback — the "
                f"click read as a silent no-op. alert ribbon: {alert_text[:200]!r}"
            )
            after = page.evaluate("() => document.body.innerText")
            # And it must NOT falsely claim the council is running — it rolled back to failed.
            assert "Council running" not in after, (
                "a FAILED retry dispatch left the optimistic 'Council running' ack standing "
                f"(a lie — no council started); body: {after[:300]!r}"
            )
            assert "Council failed" in after, (
                f"a failed retry must roll back to the 'Council failed' state; body: {after[:300]!r}"
            )
        finally:
            browser.close()
            pw.stop()
    finally:
        httpd.shutdown()


def test_canceled_council_offers_no_retry(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_cancel_noretry"
    _seed(tmp_path, monkeypatch, _canceled_status(token))
    httpd, port = _serve(tmp_path)
    try:
        page, browser, pw, errs = _open(
            port, token, ["claude", "codex"], _stub("ready", {"ok": True})
        )
        try:
            assert not errs, f"JS pageerrors: {errs[:3]}"
            body = page.evaluate("() => document.body.innerText")
            assert "Council stopped" in body, f"expected the 'Council stopped' banner; body: {body[:300]!r}"
            # A deliberate stop must NOT auto-offer a re-run of the same task.
            assert page.query_selector("text=Try again") is None, (
                "a CANCELED council (a deliberate stop) offered 'Try again' — re-running a "
                "council the user deliberately stopped is wrong; retry is for FAILED only."
            )
        finally:
            browser.close()
            pw.stop()
    finally:
        httpd.shutdown()
