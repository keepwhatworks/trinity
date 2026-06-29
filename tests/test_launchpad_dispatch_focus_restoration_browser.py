"""A dispatch that DISABLES its trigger button must not dump focus to <body>.

Founder symptom (WCAG 2.4.3 Focus Order): a keyboard / screen-reader user types a
council prompt, presses "Launch Council", and is thrown to the TOP of the document
with no place to resume. Root cause — the Launch button is `:disabled="busy"`, and
the browser drops focus to <body> whenever the currently-focused element becomes
disabled. The secondary action buttons (Refresh memory / Repair extension) have the
SAME class: each disables itself on click while it's the focused control.

The codebase already recognizes this exact failure for the settings modal
(openSettings/closeSettings: "without this a keyboard user is dumped to <body> top
of page"); the dispatch buttons never got the same remedy.

The fix moves focus onto the next contextually-relevant control:
  * launchCouncil → the freshly-rendered ".launch-status" action region
    ("Open council page" / "Stop council"), via _focusOperationActions().
  * Refresh memory / Repair extension → back to the trigger once it re-enables,
    via _restoreTriggerFocus().

This drives the REAL petite-vue render over file:// (the same runtime JS the
extension panel bundles) and asserts document.activeElement after the dispatch — a
focus property that only resolves in a JS engine. The file:// page is served
directly by render_launchpad_html, so NO bundle rebuild is needed for this guard.

Mutation-proven: remove the _focusOperationActions() call from launchCouncil and
the LAUNCH assertion reds (focus falls to BODY); drop the _restoreTriggerFocus()
call and the secondary assertion reds.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# A dispatcher stub that ALWAYS resolves onResult with success — mirrors a real
# launched council (host accepted the action; the council now runs and resolves
# later via status polling). Set BEFORE any click so it never reaches the real
# extension / a real council.
_STUB_DISPATCH = (
    "window.__TRINITY_DISPATCH_CALLS__ = [];"
    "window.__TRINITY_DISPATCH__ = {"
    "  dispatch: (o) => { window.__TRINITY_DISPATCH_CALLS__.push(o);"
    "    o.onResult && o.onResult({ tier: 'extension', ok: true, response: { ok: true } }); },"
    "  probe: () => Promise.resolve('present'),"
    "  subscribe: () => {},"
    "};"
)


def _mount(page, port: str, path: str) -> None:
    page.goto(f"http://127.0.0.1:{port}/portal_pages/{path}", wait_until="networkidle", timeout=20000)
    page.wait_for_function(
        "() => { const r = document.getElementById('launchpad-app');"
        " return r && !r.hasAttribute('v-cloak'); }",
        timeout=10000,
    )
    # Stub the dispatcher AFTER mount (so it never reaches the real extension /
    # a real council). The page's own __TRINITY_DISPATCH__ is set on load; we
    # overwrite it here before any click fires.
    page.evaluate(_STUB_DISPATCH)


def _render(view: str) -> str:
    # The LIVE builder (no page_data) — passing build_launchpad_payload()'s
    # {pageData:…} shape as page_data is a known TEST bug (its defaultMembers is
    # not the iterable launchCouncil expects, so the launch throws before
    # dispatch). This renders the real production page-data assembly.
    from trinity_local.launchpad_page import render_launchpad_html

    return render_launchpad_html(view=view)


def _seed_home(home: Path) -> None:
    """Populate a throwaway $TRINITY_HOME with realistically-shaped data so the
    memory-health card (and its Refresh memory / Repair extension buttons)
    renders — it is v-if-gated on memoryHealth.issues, silent on a fresh home."""
    import sys

    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # type: ignore

    seed_synthetic_home.seed(home)


def _setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    _seed_home(home)
    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(_render("home"), encoding="utf-8")
    (pp / "stats.html").write_text(_render("stats"), encoding="utf-8")
    publish_vendor_files(pp)
    return _serve(tmp_path / "serve")


def test_launch_council_moves_focus_to_the_status_actions_not_body(tmp_path, monkeypatch):
    """The PRIMARY common-path control: after Launch disables itself, focus must
    land on the new .launch-status action region — NOT <body>."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _setup(tmp_path, monkeypatch)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 393, "height": 800}).new_page()
                _mount(page, port, "launchpad.html")

                # ── Bite precondition A: the composer paints and there is no raw
                # template leak (a broken mount can't prove a focus regression). ──
                composer = page.query_selector("#council-prompt")
                assert composer is not None, "composer #council-prompt never rendered — page did not mount"
                body_html = page.evaluate("() => document.body.innerHTML")
                assert "{{" not in body_html, "raw petite-vue {{ }} leak — page did not mount"

                # Type a prompt and FOCUS the composer (the realistic pre-launch state).
                page.fill("#council-prompt", "Which database for a 10k-row analytics dashboard?")
                page.focus("#council-prompt")
                assert page.evaluate("() => document.activeElement.id") == "council-prompt"

                # Fire the launch.
                page.click(".actions button.button.primary")
                page.wait_for_timeout(450)  # let beginOperation render + rAF focus run

                # ── Bite precondition B (discriminating, render-independent): the
                # launch ACTUALLY fired a dispatch AND the disabling/region change
                # really happened — so a focus assertion is meaningful, not vacuous. ──
                calls = page.evaluate("() => window.__TRINITY_DISPATCH_CALLS__.length")
                assert calls == 1, f"launch did not fire exactly one dispatch (fired {calls}) — nothing to focus past"
                btn_disabled = page.evaluate(
                    "() => document.querySelector('.actions button.button.primary')?.disabled"
                )
                assert btn_disabled is True, "Launch button did not disable — the focus-drop precondition is absent"
                region = page.evaluate("() => !!document.querySelector('.launch-status')")
                assert region is True, ".launch-status action region did not render after launch"

                # ── THE GUARD: focus must NOT be on <body> and MUST be inside the
                # status action region. ──
                state = page.evaluate(
                    "() => ({ tag: document.activeElement.tagName,"
                    " inStatus: !!(document.activeElement.closest"
                    " && document.activeElement.closest('.launch-status')) })"
                )
                assert state["tag"] != "BODY", (
                    "FOUNDER SYMPTOM: after Launch Council the keyboard/SR user's focus "
                    "was dumped to <body> (top of document) — the disabled Launch button "
                    "dropped focus and nothing moved it (WCAG 2.4.3 Focus Order)"
                )
                assert state["inStatus"] is True, (
                    "focus left the disabled Launch button but did NOT land on the "
                    ".launch-status actions (Open council page / Stop council) — the "
                    "keyboard user has no place to resume"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_secondary_action_button_returns_focus_to_itself_not_body(tmp_path, monkeypatch):
    """Refresh memory / Repair extension disable themselves on click, then re-
    enable when their status settles — focus must return to the trigger, not body."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _setup(tmp_path, monkeypatch)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
                _mount(page, port, "stats.html")

                # Bite precondition A: the stats view mounted, no raw leak.
                body_html = page.evaluate("() => document.body.innerHTML")
                assert "{{" not in body_html, "raw petite-vue {{ }} leak — stats page did not mount"

                # Re-stub the dispatcher to resolve onResult AFTER a delay — a real
                # native host replies over a round-trip, NOT synchronously. The
                # delay makes the 'running' (disabled) window REAL: without the fix
                # focus drops to <body> during it and never returns. (A synchronous
                # onResult re-enables the button in the same microtask, so the
                # browser never gets to drop focus — that would mask the bug.)
                page.evaluate(
                    "window.__TRINITY_DISPATCH_CALLS__ = [];"
                    "window.__TRINITY_DISPATCH__ = {"
                    "  dispatch: (o) => { window.__TRINITY_DISPATCH_CALLS__.push(o);"
                    "    setTimeout(() => o.onResult && o.onResult({ tier: 'extension',"
                    "      ok: true, response: { ok: true } }), 250); },"
                    "  probe: () => Promise.resolve('present'), subscribe: () => {},"
                    "};"
                )

                # Locate the "Refresh memory" trigger by its visible label.
                handle = page.evaluate_handle(
                    "() => [...document.querySelectorAll('button')].find(b =>"
                    " b.offsetParent !== null && /refresh memory/i.test(b.textContent || ''))"
                )
                el = handle.as_element()
                assert el is not None, "Refresh memory button not found / not visible on /stats"

                el.focus()
                el.click()

                # ── Bite precondition B (discriminating): DURING the disabled
                # window the button has lost focus to <body> (the bug state the fix
                # must recover from) AND the dispatch fired exactly once. If focus
                # never left, there is nothing to restore and the assertion is
                # vacuous. ──
                page.wait_for_timeout(80)
                calls = page.evaluate("() => window.__TRINITY_DISPATCH_CALLS__.length")
                assert calls == 1, f"Refresh memory did not fire exactly one dispatch (fired {calls})"
                during = page.evaluate(
                    "() => ({ tag: document.activeElement.tagName,"
                    " disabled: !![...document.querySelectorAll('button')].find(b =>"
                    " /refreshing/i.test(b.textContent || '') && b.disabled) })"
                )
                assert during["disabled"] is True, (
                    "Refresh memory button never entered the disabled 'Refreshing…' "
                    "window — the focus-drop precondition is absent"
                )
                assert during["tag"] == "BODY", (
                    "expected focus to drop to <body> while the trigger is disabled "
                    f"(the bug state to recover from); got {during['tag']!r} instead — "
                    "precondition not discriminating"
                )

                # Let onResult → finish() flip status to 'done' (re-enabling the
                # button) and the rAF in _restoreTriggerFocus run.
                page.wait_for_timeout(500)

                tag = page.evaluate("() => document.activeElement.tagName")
                assert tag != "BODY", (
                    "FOUNDER SYMPTOM: clicking the self-disabling 'Refresh memory' "
                    "button dropped the keyboard user's focus to <body> — focus never "
                    "returned to the trigger once it re-enabled (WCAG 2.4.3)"
                )
                is_trigger = page.evaluate(
                    "() => /refresh memory|updated/i.test(document.activeElement.textContent || '')"
                )
                assert is_trigger is True, (
                    "focus did not return to the Refresh memory trigger after it "
                    f"re-enabled — landed on {tag!r} instead"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
