"""Browser guard: stacked "Quote ↓" attribution blocks must keep their newlines.

Each member answer on the live council page carries a ``Quote ↓`` button. Clicking
it appends a markdown blockquote of that member's answer into the chain-refine field:

    > [Claude]: <up to 300 chars>

and STACKS multiple quotes joined by a blank line, so a two-quote stack reads:

    > [Claude]: Alpha answer with detail.

    > [Gemini]: Beta answer: use a tagged union.

That ``refinePrompt`` is sent VERBATIM as the next-round refine directive (where the
markdown blockquote form is meaningful to the chairman). The field used to be a
single-line ``<input type="text">`` — which silently strips EVERY newline — so two
stacked quotes collapsed into one unreadable run:

    > [Claude]: Alpha answer with detail.> [Gemini]: Beta answer: use a tagged union.

The attribution blocks jammed together with no separator, and the blockquote line
breaks vanished. Found 2026-06-17 driving Quote ↓ on the live ``?status_token=``
completed council page (the path EVERY launchpad-launched council takes) in the UX
sweep; a Trinity council (council_e93c34bafe23f020, winner claude, unanimous on the
root cause) chose the textarea fix because the feature's intent IS stacked markdown,
not just visible attribution.

The fix changed both chain-refine fields (live page + unified page — one class) to a
compact ``<textarea>`` and moved submit to ⌘/Ctrl+Enter (plain Enter now inserts a
newline, matching the multi-line intent). This guard pins: two stacked quotes keep
their blank-line separator and trailing newline. It serves an isolated, PII-free
synthetic council over http (file:// can't carry the ``?status_token=`` query
reliably). Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


_TOKEN = "tok_quote_stack_nl"

# A COMPLETED 2-member council reached via the poll (?status_token=) path. Both
# members responded, so both carry a Quote button.
_COMPLETED_STATUS = {
    "status": "completed",
    "status_token": _TOKEN,
    "task_text": "How should we model the council state machine?",
    "council_id": "c_quote_stack_nl",
    "memberOrder": ["claude", "antigravity"],
    "members": {
        "claude": {
            "status": "done",
            "model": "claude-opus-4-8",
            "response_text": "Alpha answer with detail.",
            "response_html": "<p>Alpha answer with detail.</p>",
        },
        "antigravity": {
            "status": "done",
            "model": "gemini-3.1-pro",
            "response_text": "Beta answer: use a tagged union.",
            "response_html": "<p>Beta answer: use a tagged union.</p>",
        },
    },
    "synthesis": {
        "status": "done",
        "response_text": "Synthesis verdict.",
        "response_html": "<p>Synthesis verdict.</p>",
        "routing_label": {"winner": "claude", "runner_up": "antigravity", "confidence": "high"},
    },
    "metadata": {
        "chairman_provider": "claude",
        "council_id": "c_quote_stack_nl",
        "members": ["claude", "antigravity"],
    },
}


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_stacked_quotes_keep_their_newlines(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
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
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(_TOKEN)}] = "
        f"{json.dumps(_COMPLETED_STATUS)};\n"
    )
    (status_dir / f"council_status_{_TOKEN}.js").write_text(sidecar, encoding="utf-8")

    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                # A stub dispatcher so a stray submit can't reach a real council.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    "  dispatch: () => Promise.resolve({ok:true}),"
                    "  probe: () => Promise.resolve('ready'),"
                    "  onStateChange: () => {},"
                    "  state: 'ready'"
                    "};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={_TOKEN}"
                )
                page.wait_for_timeout(2600)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                field_tag = page.evaluate(
                    "() => document.querySelector('.chain-refine-input').tagName"
                )
                quote_buttons = page.evaluate(
                    "() => document.querySelectorAll('.quote-member-btn').length"
                )
                # Stack two quotes: Claude then Gemini.
                page.evaluate("() => document.querySelectorAll('.quote-member-btn')[0].click()")
                page.wait_for_timeout(150)
                page.evaluate("() => document.querySelectorAll('.quote-member-btn')[1].click()")
                page.wait_for_timeout(150)
                value = page.evaluate(
                    "() => document.querySelector('.chain-refine-input').value"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert quote_buttons == 2, (
        f"precondition: expected 2 Quote buttons (one per responder), got {quote_buttons} "
        "— guard would be vacuous"
    )
    # The field MUST be a textarea: a single-line <input> strips every newline, which
    # is the exact bug this guard defends against.
    assert field_tag == "TEXTAREA", (
        f"the chain-refine field is a <{field_tag.lower()}>, not a <textarea>. A "
        "single-line <input> silently strips the blockquote newlines, jamming stacked "
        "Quote-attribution blocks into one unreadable run."
    )
    # Both attributed blocks present (Quote works at all).
    assert "[Claude]:" in value and "[Gemini]:" in value, (
        f"stacked quotes lost their attribution; got {value!r}"
    )
    # THE BUG: the two blockquotes must be separated by a blank line, not jammed.
    assert "\n\n> [Gemini]:" in value, (
        "stacked Quote-attribution blocks are JAMMED with no separator — the "
        "blockquote newlines were stripped (the field reverted to a single-line "
        "<input> that drops every newline), so two quotes read as one unreadable run "
        f"like '> [Claude]: …answer.> [Gemini]: …'. got: {value!r}"
    )
    assert value.count("\n") >= 3, (
        "the stacked quote block has fewer newlines than the two blockquotes + blank "
        f"separator require — newlines were stripped. got {value.count(chr(10))} in {value!r}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
