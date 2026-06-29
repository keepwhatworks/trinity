"""The Launch Council button must EXPLAIN itself while a council is running.

Founder report (2026-06-17): "clicked Launch Council while another council was
running, it didn't do anything." Root cause — the button is `:disabled="busy"`,
and a bare greyed button gives NO feedback: the click is swallowed and nothing on
the button says why. The class is NO-FEEDBACK on a disabled control.

The fix makes the disabled state self-explanatory: while busy the button reads
"Council in progress…" (not the actionable "Launch Council") and carries a title
tooltip; the Open/Stop actions already render in the status panel below.

`busy` hydrates from `pageData.activeOperation` (a running council restored on
load), so this drives the REAL petite-vue render with a seeded running council and
asserts the button text + disabled + title — a binding that only resolves in a JS
engine. Mutation-provable: revert the dynamic text to the static "Launch Council"
and the visible-label assertion reds.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_launch_button_binding_is_dynamic_not_static():
    """CI-runnable canary: the button text + title must be bound to `busy`, not a
    static label that silently disables (fails fast in plain pytest).

    Iter 442 replaced the inline ``busy ? 'Council in progress…' : 'Launch Council'``
    ternary with the kind-aware ``launchButtonLabel`` / ``launchButtonTitle`` getters
    (so an INGEST-busy state no longer falsely claims a council is running). The
    binding is STILL dynamic and keyed on ``busy`` — just routed through a getter —
    so this canary asserts that invariant against the getter form rather than the old
    inline syntax. It must still RED if the label reverts to a static string or the
    getter stops gating on ``busy``."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    # The button label + title are DYNAMIC bindings to the kind-aware getters
    # (doubled braces in the f-string template → `{{ launchButtonLabel }}` in HTML).
    assert "{{{{ launchButtonLabel }}}}" in src, (
        "Launch button label is no longer a dynamic binding to launchButtonLabel"
    )
    assert ':title="launchButtonTitle"' in src, "Launch button lost its dynamic busy-state tooltip"
    assert ">Launch Council</button>" not in src, (
        "Launch button still has a STATIC 'Launch Council' label — it won't explain "
        "why it's disabled while a council runs (the NO-FEEDBACK bug)"
    )
    # The label getter gates the actionable 'Launch Council' on NOT busy — so a busy
    # state always relabels (to "Council in progress…" for a council, "Scanning
    # transcripts…" for an ingest), never silently disabling under the actionable
    # label. This ties the static string to the not-busy branch (dynamic, busy-keyed).
    assert "if (!this.busy) return 'Launch Council';" in src, (
        "launchButtonLabel no longer gates the actionable label on `busy` — it could "
        "show 'Launch Council' while disabled (the NO-FEEDBACK regression)"
    )
    assert "Council in progress" in src, "Launch button lost its council-busy label"
    assert "already running" in src, "Launch button lost its busy-state tooltip copy"


# ── Real-browser proof: a seeded running council relabels the button ──
pytestmark_browser = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_launch_button_shows_progress_when_a_council_runs(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html

    payload = build_launchpad_payload()
    # Seed a RUNNING council so the app mounts in the busy state.
    payload["activeOperation"] = {
        "kind": "council",
        "status": "running",
        "task_text": "why is the sky blue?",
        "memberOrder": ["claude", "codex"],
    }
    html = render_launchpad_html(page_data=payload)

    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 720, "height": 1200}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                # Wait for petite-vue to mount (root sheds v-cloak).
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                btn = page.evaluate(
                    "() => { const b = document.querySelector('.actions button.button.primary');"
                    " return b ? { text: b.textContent.trim(), disabled: b.disabled,"
                    " title: b.getAttribute('title') || '' } : null; }"
                )
                assert btn, "Launch button not found"
                assert btn["disabled"] is True, "button should be disabled while a council runs"
                assert btn["text"] == "Council in progress…", (
                    f"disabled button still reads {btn['text']!r} — gives no feedback "
                    "that a council is already running (the founder's bug)"
                )
                assert "already running" in btn["title"], f"no explanatory tooltip: {btn['title']!r}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
