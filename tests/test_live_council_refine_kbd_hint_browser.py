"""The LIVE council page's refine-input placeholder promises "⌘/Ctrl+Enter to
send"; that key binding must actually be WIRED on the page users actually reach.

This is the Iter-87/Iter-88-class invariant — copy on the council pages must not
reference an affordance the surface lacks — codified for the LIVE council page
(``render_live_council_page`` → ``review_pages/live_council.html``), the page a
launching/completed council actually opens. The DEAD ``render_unified_council_
page`` (#311) already had this guarded by
``test_council_review_refine_kbd_hint_browser`` — but its refine textarea is a
SEPARATE DUPLICATE of the live page's: the live ``.chain-refine-input`` carries
its OWN ``@keydown.enter.meta.prevent="startRefine"`` /
``@keydown.enter.ctrl.prevent="startRefine"`` handlers (the live ``startRefine``
routes through ``_startChainAction('council_refine', …)`` → the chain-iterate
dispatcher). A refactor that strips the live page's keydown handlers while leaving
its placeholder copy would leave the user pressing a documented shortcut that does
nothing — and the dead-page guard would stay GREEN (it drives the dead duplicate).

This guard drives the REAL live page in a completed (canChainNext) state, stubs
``__TRINITY_DISPATCH__`` so no real council fires, types a directive into the live
``.chain-refine-input``, presses each chord, and asserts the wired handler
dispatched a ``council-iterate`` with the typed prompt. Mutation-proven against
dropping the LIVE page's keydown handlers (placeholder stays, chord dies) — the
dead-page guard stays green under that same mutation.
"""

from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


# Stub the dispatcher BEFORE the page script runs so pressing the chord routes
# through a recorded no-op instead of the real Chrome extension / a real council.
# onResult is never invoked so the app stays in its busy state — we only assert
# the dispatch was *requested*, which is what the keydown handler does.
#
# onStateChange / probe are the mount-time wiring the live page calls on the
# dispatcher (the proactive stale-extension warning). probe() resolves a NON-stale
# state so the stale banner never fires and the chain composer stays mounted.
_DISPATCH_STUB = """
window.__TRINITY_DISPATCH__ = {
  _calls: [],
  dispatch(opts){ this._calls.push(opts); },
  onStateChange(){ },
  probe(){ return Promise.resolve('connected'); }
};
"""

_CID = "council_refine_kbd_live"


def _seed_completed_council() -> None:
    """Seed a COMPLETED 2-distinct-provider council so the live page renders the
    "Continue the thread" composer (canChainNext: completed + councilId +
    non-solo) where the ``.chain-refine-input`` lives."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(
            provider=p, model="m", output_text=f"Answer from {p}. " * 30
        )
        for p in ("claude", "codex")
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type="design",
        agreed_claims=["Namespace the cache key per tenant"],
        disagreed_claims=[],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID,
            bundle_id=_CID,
            task_cluster_id="cluster_refine_kbd",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache in-process or per-call?"},
            member_results=members,
            synthesis_prompt="Review the two answers.",
            synthesis_output="In-process caching wins for this tenancy model.",
            routing_label=routing_label,
            created_at="2026-06-05T00:00:00+00:00",
        )
    )
    write_portal_html()  # writes vendor/ assets the page references
    write_live_council_page()


def _serve(tmp_path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.parametrize("modifier", ["Meta", "Control"])
def test_live_council_refine_keyboard_hint_is_wired(tmp_path, monkeypatch, modifier):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    _seed_completed_council()
    httpd, port = _serve(tmp_path)
    url = (
        f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}"
    )

    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1200}
                ).new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(_DISPATCH_STUB)
                page.goto(url, wait_until="load", timeout=15000)
                # outcome-script hydration + petite-vue mount → canChainNext composer
                page.wait_for_timeout(2200)

                textarea = page.query_selector("textarea.chain-refine-input")
                assert textarea is not None, (
                    "the LIVE council page rendered NO chain-refine textarea — the "
                    "Continue-the-thread composer did not mount (canChainNext), so "
                    "the keyboard-hint invariant can't be checked (false-pass guard)"
                )
                placeholder = textarea.get_attribute("placeholder") or ""
                # The invariant only applies if the copy PROMISES the chord. If a
                # refactor removed the hint copy too, the guard would no longer
                # apply (and would need updating) — assert the promise is present
                # so this can't pass vacuously after the copy is stripped.
                assert ("⌘" in placeholder) or ("Ctrl+Enter" in placeholder), (
                    "harness assumption broken: the LIVE refine placeholder no "
                    "longer advertises the ⌘/Ctrl+Enter shortcut — update this "
                    "guard (the keyboard-hint invariant no longer applies if the "
                    "copy doesn't promise the chord)"
                )

                page.evaluate("() => { window.__TRINITY_DISPATCH__._calls = []; }")
                textarea.click()
                textarea.type("tighten the abstain gate")
                page.wait_for_timeout(80)

                page.keyboard.down(modifier)
                page.keyboard.press("Enter")
                page.keyboard.up(modifier)
                page.wait_for_timeout(300)

                calls = page.evaluate(
                    "() => (window.__TRINITY_DISPATCH__ && "
                    "window.__TRINITY_DISPATCH__._calls || [])"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errors, f"live council page raised JS errors: {errors}"
    # THE GUARD: the advertised chord must actually dispatch a refine. A promise of
    # "⌘/Ctrl+Enter to send" with no wired @keydown handler leaves the user pressing
    # a documented shortcut that does nothing — the Iter-87/88 class (council-page
    # copy must not reference an affordance the surface lacks), here on the LIVE page
    # users actually reach (NOT the dead render_unified_council_page #311 twin).
    assert len(calls) >= 1, (
        f"the LIVE council page's refine placeholder promises '⌘/Ctrl+Enter to "
        f"send' but pressing {modifier}+Enter dispatched NOTHING — the live "
        "@keydown.enter.meta/.ctrl='startRefine' handlers are not wired on this "
        "surface, so the page users actually reach advertises a keyboard shortcut "
        "that does nothing (a copy/control drift the dead-page-only "
        "test_council_review_refine_kbd_hint_browser guard would NOT catch)."
    )
    # The dispatch must be the chain-iterate refine, carrying the typed directive —
    # not some unrelated dispatch that merely fired on Enter.
    iterate_calls = [
        c
        for c in calls
        if isinstance(c, dict)
        and isinstance(c.get("extensionAction"), dict)
        and c["extensionAction"].get("kind") == "council-iterate"
        and c["extensionAction"].get("prompt") == "tighten the abstain gate"
    ]
    assert iterate_calls, (
        f"the {modifier}+Enter chord dispatched, but not a council-iterate refine "
        f"carrying the typed directive — got: {calls!r}"
    )
