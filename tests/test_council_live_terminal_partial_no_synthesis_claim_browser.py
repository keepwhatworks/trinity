"""Browser guard: a TERMINAL (stopped/failed) council that still had one member
land an answer must NOT claim "this synthesis is over the N that responded" — there
is no synthesis on a council that died before the chairman ran.

THE DEFECT (found driving the live council page in the canceled 3-member state).
The live page's "Full Responses" disclosure banner (``council_review.render_live_council_page``)
fired ``⚠ N provider(s) attempted but failed and {was,were} excluded — this synthesis
is over the M that responded.`` whenever ``failedMembersFor(seg) > 0 &&
respondedMembersFor(seg) > 0`` — with NO gate on the segment's terminal state.

A council the runner kills after one member lands (status ``failed``), or one the user
hits Stop on mid-run (status ``canceled``), reaches this branch: one member is ``done``
with real text, another is ``failed``/coerced-stopped. The synthesis SECTION on that
same page is suppressed (``v-if="... && !seg.failed && !seg.canceled ..."``) because the
chairman never ran — yet the disclosure banner asserted "this synthesis is over the 1
that responded", a synthesis that NEVER happened. Visually: a red "Council stopped" banner
sits directly above a line claiming a synthesis exists. It is the exact #238/0-responder
self-contradiction the sibling branch already guards ("Every provider failed — there's no
synthesis to show"), just on a council that stopped mid-flight with a partial responder.

THE FIX: gate the "this synthesis is over N" copy on ``seg.completed``; the
terminal-with-partial-responders case ``(!seg.completed)`` gets honest copy that names
what happened — the answers landed but were never synthesized.

This guard drives the REAL ``?status_token=`` POLL path over http (the path that
actually renders a live stop/fail): it seeds a ``canceled`` status sidecar with a
done member + a failed member, then reads the RENDERED disclosure text — text content,
not a source-string check.

Mutation-proven: reverting the ``&& seg.completed`` gate on the disclosure banner (so
the "this synthesis is over the N that responded" line fires on the canceled segment
again) REDS this guard with the founder symptom.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _seed_canceled_partial(token: str) -> None:
    """A STOPPED 3-member council: claude landed a real answer, codex failed,
    antigravity was pending when the stop hit. Terminal status: canceled."""
    from trinity_local import council_status, vendor, state_paths
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html

    council_status.init_council_run_state(
        token,
        task_text="Design a rate limiter for a multi-tenant API gateway",
        bundle_id="bundle_cancel_partial",
        members=["claude", "codex", "antigravity"],
    )
    council_status.update_member_progress(
        token, "claude", "Use a token bucket per tenant keyed in Redis."
    )
    council_status.write_council_status(
        token,
        status="running",
        members={
            "claude": {
                "status": "done",
                "response_text": "Use a token bucket per tenant keyed in Redis.",
                "model": "claude-opus-4-8",
            },
            "codex": {"status": "failed", "error_text": "rate limited", "model": "gpt-5.5"},
            "antigravity": {"status": "pending", "model": "gemini-3.1-pro"},
        },
    )
    # Terminal: the user hit Stop (or the runner died) before the chairman ran.
    council_status.write_council_status(token, status="canceled", error="Council stopped.")

    write_portal_html()  # publishes vendor next to review_pages/
    write_live_council_page(force=True)
    # write_portal_html publishes vendor into portal_pages/vendor — the live page
    # loads ../portal_pages/vendor/petite-vue.iife.js relative to review_pages/.
    vendor.publish_vendor_files(state_paths.portal_pages_dir())


def _serve(tmp_path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_canceled_partial_council_makes_no_synthesis_claim(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    token = "cancel_partial_tok01"
    _seed_canceled_partial(token)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1400}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    "dispatch:(a,cb)=>{if(cb)cb({ok:true});},"
                    "probe:()=>Promise.resolve({state:'ready'}),"
                    "subscribe:()=>{},onStateChange:()=>{}};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?status_token={token}"
                )
                page.wait_for_timeout(1200)

                info = page.evaluate(
                    """() => {
                      const txt = document.body.innerText;
                      const disclosures = [...document.querySelectorAll('.meta.status-error')]
                        .map(e => e.textContent.trim())
                        .filter(t => /provider|synthes|respond/i.test(t));
                      return {
                        stoppedBanner: /Council stopped/.test(txt),
                        fullResponses: /Full Responses/.test(txt),
                        synthesisClaim: /this synthesis is over/.test(txt),
                        disclosures,
                        bodyLen: txt.length,
                      };
                    }"""
                )

                # ---- Preconditions (non-vacuous bite) -----------------------
                assert not errs, f"JS errors rendering the live council page: {errs[:3]}"
                assert info["bodyLen"] > 100, (
                    "page rendered empty — petite-vue likely failed to mount "
                    "(check vendor publish). The synthesis-claim bite would be "
                    f"vacuous: {info!r}"
                )
                assert info["stoppedBanner"], (
                    "the 'Council stopped' terminal banner did not render — the "
                    "canceled segment wasn't recognized, so the disclosure-banner "
                    f"bite is vacuous: {info!r}"
                )
                assert info["fullResponses"], (
                    "the 'Full Responses' section did not render — the partial "
                    "responder's row is missing, so the disclosure banner never "
                    f"fires and the bite is vacuous: {info!r}"
                )
                # The disclosure banner MUST be present (the failed member is real),
                # otherwise the synthesis-claim assertion below can't bite.
                assert info["disclosures"], (
                    "no partial-council disclosure banner rendered at all — the "
                    "failed member wasn't counted, so there's nothing to assert "
                    f"the wording of: {info!r}"
                )

                # ---- The defect ---------------------------------------------
                assert not info["synthesisClaim"], (
                    "FOUNDER SYMPTOM: a STOPPED council with one landed answer claims "
                    "'this synthesis is over the 1 that responded' — but the synthesis "
                    "section is suppressed for canceled/failed segments because the "
                    "chairman NEVER RAN. A red 'Council stopped' banner sits right above "
                    "a line asserting a synthesis exists (the #238/0-responder self-"
                    "contradiction, on a council that died mid-flight). The disclosure "
                    "banner must gate the 'synthesis is over N' copy on seg.completed: "
                    f"{info['disclosures']!r}"
                )
                # Positive: the terminal-partial branch states what actually happened.
                joined = " ".join(info["disclosures"])
                assert "never synthesized" in joined, (
                    "the terminal-partial disclosure should honestly say the landed "
                    "answer(s) were never synthesized (the council died before the "
                    f"chairman ran), got: {info['disclosures']!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
