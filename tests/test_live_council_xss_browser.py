"""Real-browser XSS regression test for the LIVE council page (the streaming
"watch it" surface) — the sibling of test_council_review_xss_browser.py.

`render_live_council_page` renders member responses + chairman synthesis via
`v-html="row.responseHtml"` (member) and `v-html="analysisRowFor(seg).responseHtml"`
(synthesis), where responseHtml is the SERVER-rendered `output_html` /
`synthesis_html` the council runner computes through `render_markdown`
(council_runtime.py). So the live page IS sanitized at the data layer — BUT it's a
SEPARATE v-html sink on a SEPARATE render path (loads the outcome via the
`council_<id>.js` script, not a direct render call), so the static-page XSS test
(`test_council_review_xss_browser.py`, which renders `render_unified_council_page`
directly) does NOT exercise it. A refactor that computes responseHtml client-side
via raw `marked`, adds a new v-html sink, or drops the server sanitize would open
stored-XSS here (a malicious/echoed provider response, or a captured malicious web
page surfaced as a member output, is attacker-influenceable) and NEITHER the
static-page test nor the render_markdown unit test would catch it.

Loads the real live page over HTTP with `?council_id=` (file:// can't carry the
query + the outcome-script fetch), weaves a payload battery through every
corpus-content field, and asserts none execute while the sanitized content still
renders. Synthetic data only; no PII. Slow + browser marked.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_live_xss_browsertest"

# Each payload bumps window.__xss_fired if it executes. A correct sanitizer
# neutralizes every one. Mirrors test_council_review_xss_browser.py.
_PAYLOADS = " ".join([
    '<img src=x onerror="window.__xss_fired=(window.__xss_fired||0)+1">',
    "<script>window.__xss_fired=(window.__xss_fired||0)+1</script>",
    '<svg onload="window.__xss_fired=(window.__xss_fired||0)+1"></svg>',
    '<iframe src="javascript:window.__xss_fired=1"></iframe>',
    "[md-link](javascript:window.__xss_fired=1)",
    "<details open ontoggle=window.__xss_fired=1>x</details>",
    '<a href="javascript:window.__xss_fired=1">click</a>',
])


def _seed_xss_council() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                            output_text="Claude reframes. " + _PAYLOADS),
        CouncilMemberResult(provider="codex", model="gpt-5.5",
                            output_text="Codex enumerates. " + _PAYLOADS),
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude", runner_up="codex", confidence="high", task_type="design",
        agreed_claims=["Both agree on the cache key " + _PAYLOADS],
        disagreed_claims=[{
            "claim": "Per-call vs in-process " + _PAYLOADS,
            "providers_for": ["claude"], "providers_against": ["codex"],
            "why_matters": "tenancy leak " + _PAYLOADS,
        }],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID, bundle_id=_CID, task_cluster_id="cluster_xss",
            primary_provider="claude", winner_provider="claude",
            metadata={"task_text": "Compare these options " + _PAYLOADS},
            member_results=members,
            synthesis_prompt="Review.",
            synthesis_output="# Synthesis\n\nClaude reframes; Codex enumerates. " + _PAYLOADS,
            routing_label=routing_label,
            created_at="2026-06-07T00:00:00+00:00",
        )
    )
    write_portal_html()  # vendor assets the page references
    write_live_council_page()


def _serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_live_council_page_neutralizes_xss_in_real_browser(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_xss_council()

    httpd, port = _serve(tmp_path)
    dialogs: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()

                def _on_dialog(d) -> None:
                    dialogs.append(d.message)
                    d.dismiss()

                page.on("dialog", _on_dialog)
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}",
                    wait_until="load", timeout=15000,
                )
                page.wait_for_timeout(2200)  # outcome-script hydration + petite-vue mount + any fire
                fired = page.evaluate("() => window.__xss_fired || 0")
                rendered = page.evaluate(
                    "() => /reframes|enumerates|Synthesis/i.test(document.body.innerText)"
                )
                raw_embeds = page.evaluate(
                    "() => document.querySelectorAll('.markdown-body script, .markdown-body iframe').length"
                )
                live_onerror = page.evaluate(
                    "() => [...document.querySelectorAll('img')].filter(i => i.getAttribute('onerror')).length"
                )
                rows = page.evaluate("() => document.querySelectorAll('.provider-status-row').length")
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert fired == 0, (
        f"an XSS payload EXECUTED on the LIVE council page (window.__xss_fired={fired}) "
        "— a malicious/echoed provider response would run script on the streaming surface"
    )
    assert not dialogs, f"a payload triggered a dialog on the live council page: {dialogs}"
    assert raw_embeds == 0, f"{raw_embeds} raw <script>/<iframe> survived sanitizing into a v-html sink"
    assert live_onerror == 0, "an <img onerror=> survived into the live DOM"
    # False-pass guard: the sanitized member rows + content must actually render.
    assert rows == 2 and rendered, (
        f"live council rendered empty (rows={rows}, rendered={rendered}) — "
        "the XSS assertions would be a false pass"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
