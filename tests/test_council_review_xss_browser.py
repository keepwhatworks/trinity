"""Real-browser XSS regression test for the COUNCIL review page (the lead surface).

The council review page (`render_unified_council_page`) renders member responses
+ chairman synthesis — corpus content a malicious/echoing provider can influence —
via `render_markdown` into the page. The sanitizer is unit-tested (#288) and the
MEMORY VIEWER has a real-browser XSS test (#287), but the council page — the lead
product output — had none: a string/source-grep assert can't see what only
execution reveals (a payload that survives sanitizing and fires when the page
mounts). This loads the real rendered page in a headless browser, weaves a battery
of payloads through every corpus-content field (member output_text, synthesis,
task_text), and asserts none execute while the (sanitized) content still renders.

Mirrors test_memory_viewer_xss_browser.py. Renders into a prod-shaped layout
(review_pages/council.html + portal_pages/vendor/) so petite-vue actually MOUNTS
(the page loads it from ../portal_pages/vendor/petite-vue.iife.js) — exercising the
full render, not just the static server-rendered body. Synthetic data only; no PII.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Each payload, if it executes, bumps window.__xss_fired. A correct sanitizer
# neutralizes every one — the flag stays unset and no dialog fires.
_PAYLOADS = " ".join([
    '<img src=x onerror="window.__xss_fired=(window.__xss_fired||0)+1">',
    "<script>window.__xss_fired=(window.__xss_fired||0)+1</script>",
    '<svg onload="window.__xss_fired=(window.__xss_fired||0)+1"></svg>',
    '<iframe src="javascript:window.__xss_fired=1"></iframe>',
    "[md-link](javascript:window.__xss_fired=1)",
    "<details open ontoggle=window.__xss_fired=1>x</details>",
    '<a href="javascript:window.__xss_fired=1">click</a>',
])


def _render_council_with_payloads() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_xss",
        task_cluster_id="cluster_xss",
        task_text="Compare these options " + _PAYLOADS,
        goal="Pick the strongest.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-06-01T12:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_xss",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                                output_text="Claude reframes. " + _PAYLOADS),
            CouncilMemberResult(provider="codex", model="gpt-5.5",
                                output_text="Codex enumerates. " + _PAYLOADS),
        ],
        synthesis_output="# Synthesis\n\nClaude reframes; Codex enumerates. " + _PAYLOADS,
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def test_council_review_page_neutralizes_xss_in_real_browser():
    """A payload woven through member responses + synthesis + task_text must NOT
    execute when the council review page renders in a real browser — no
    window.__xss_fired, no alert/confirm dialog, no raw <script>/<iframe> adopted
    into the markdown body, no live <img onerror>. The page must still render the
    sanitized content (guards against a false pass on a blank render). Skips when
    chromium isn't installed."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.vendor import publish_vendor_files

    root = Path(tempfile.mkdtemp(prefix="trinity-council-xss-"))
    try:
        # Prod-shaped layout so the page's ../portal_pages/vendor/petite-vue.iife.js
        # resolves and petite-vue MOUNTS (exercises the full render).
        (root / "review_pages").mkdir()
        (root / "portal_pages").mkdir()
        publish_vendor_files(root / "portal_pages")
        page_path = root / "review_pages" / "council_xss.html"
        page_path.write_text(_render_council_with_payloads(), encoding="utf-8")

        dialogs: list[str] = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # chromium not installed in this env
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()

                def _on_dialog(d) -> None:
                    dialogs.append(d.message)
                    d.dismiss()

                page.on("dialog", _on_dialog)
                page.goto(f"file://{page_path}")
                page.wait_for_timeout(1500)  # let petite-vue mount + any payload fire
                fired = page.evaluate("() => window.__xss_fired || 0")
                body_len = len(page.inner_text("body"))
                rendered = page.evaluate(
                    "() => /reframes|enumerates|Synthesis/i.test(document.body.innerText)"
                )
                raw_embeds = page.evaluate(
                    "() => document.querySelectorAll("
                    "'.markdown-body script, .markdown-body iframe').length"
                )
                live_onerror = page.evaluate(
                    "() => [...document.querySelectorAll('img')]"
                    ".filter(i => i.getAttribute('onerror')).length"
                )
            finally:
                browser.close()

        assert fired == 0, (
            f"an XSS payload EXECUTED on the council review page (window.__xss_fired={fired}) "
            "— a malicious/echoed provider response would run script on the lead surface"
        )
        assert not dialogs, f"a payload triggered a dialog on the council page: {dialogs}"
        assert raw_embeds == 0, (
            f"{raw_embeds} raw <script>/<iframe> survived sanitizing into the markdown body"
        )
        assert live_onerror == 0, "an <img onerror=> survived into the live DOM"
        # Guard against a false pass on a blank render: the sanitized content must show.
        assert body_len > 80 and rendered, (
            f"council page rendered empty (body_len={body_len}, rendered={rendered}) — "
            "the XSS assertions would be a false pass"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
