"""UX sweep Iter 88 — the STATIC council review page's refine-input placeholder
promises "⌘/Ctrl+Enter to send"; that key binding must actually be WIRED.

This is the Iter-87-class invariant generalized from the Quote ↓ defect: copy on
the council pages must not reference an affordance the surface lacks. Iter 87
guarded the Quote ↓ *button* reference. This guards the *keyboard* affordance: the
refine textarea placeholder on the static review page (``render_unified_council_
page``) tells the user "⌘/Ctrl+Enter to send", so both the Meta+Enter and the
Ctrl+Enter chords must trigger ``startRefine`` — otherwise the page promises a key
binding it doesn't wire (a refactor that strips the ``@keydown.enter.meta`` /
``@keydown.enter.ctrl`` handlers but leaves the placeholder would leave the user
pressing a documented shortcut that does nothing).

The static review page is the at-risk surface: it renders the chain-actions section
SERVER-SIDE, and unlike the live page it has NO Quote button, so the placeholder's
"send" affordance is the only refine shortcut its copy advertises. This guard drives
the real page in a browser, stubs ``__TRINITY_DISPATCH__`` so no real council fires,
types a directive, presses each chord, and asserts the wired handler dispatched.
Mutation-proven against dropping the keydown handlers (placeholder stays, chord dies).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


# Stub the dispatcher BEFORE the page script runs so pressing the chord routes
# through a recorded no-op instead of the real Chrome extension / a real council.
# onResult is intentionally never invoked so the app stays in its busy state — we
# only assert the dispatch was *requested*, which is what the keydown handler does.
_DISPATCH_STUB = """
window.__TRINITY_DISPATCH__ = {
  _calls: [],
  dispatch(opts){ this._calls.push(opts); }
};
"""


def _render_static_review_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_kbd_hint",
        task_cluster_id="cluster_kbd_hint",
        task_text="Pick the strongest fix for a multi-tenant caching collision.",
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-06-01T12:00:00+00:00",
    )
    members = [
        CouncilMemberResult(
            provider="claude",
            model="claude-opus-4-8",
            output_text="Claude reframes around tenancy isolation.",
        ),
        CouncilMemberResult(
            provider="codex",
            model="gpt-5.5",
            output_text="Codex enumerates the concurrency failure modes.",
        ),
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type="design",
        agreed_claims=["Namespace the cache key per tenant"],
        disagreed_claims=[],
        routing_lesson="prefer_per_call_for_multi_tenant_isolation",
    )
    outcome = CouncilOutcome(
        council_run_id="council_kbd_hint",
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="claude",
        member_results=members,
        synthesis_output="# Synthesis\n\nNamespace the key per tenant.",
        routing_label=routing_label,
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


def _drive_chord(page_file, modifier: str) -> tuple[bool, int, list[str]]:
    """Load the static review page, type into the refine textarea, press
    ``<modifier>+Enter``, and report (placeholder_promises_kbd, dispatch_calls,
    js_errors)."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1024, "height": 1100}
            ).new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(_DISPATCH_STUB)
            page.goto(f"file://{page_file}", wait_until="load", timeout=15000)
            page.wait_for_timeout(400)

            textarea = page.query_selector("textarea.chain-refine-input")
            assert textarea is not None, (
                "static review page rendered NO chain-refine textarea — the refine "
                "section did not mount, so the keyboard-hint invariant can't be "
                "checked (false-pass guard)"
            )
            placeholder = textarea.get_attribute("placeholder") or ""
            promises_kbd = ("⌘" in placeholder) or ("Ctrl+Enter" in placeholder)

            textarea.click()
            textarea.type("tighten the abstain gate")
            page.wait_for_timeout(80)

            page.keyboard.down(modifier)
            page.keyboard.press("Enter")
            page.keyboard.up(modifier)
            page.wait_for_timeout(250)

            calls = page.evaluate(
                "() => (window.__TRINITY_DISPATCH__ && "
                "window.__TRINITY_DISPATCH__._calls || []).length"
            )
        finally:
            browser.close()
    return promises_kbd, calls, errors


@pytest.mark.parametrize("modifier", ["Meta", "Control"])
def test_static_review_refine_keyboard_hint_is_wired(tmp_path, modifier):
    pytest.importorskip("playwright.sync_api")

    from trinity_local import vendor

    review_dir = tmp_path / "review_pages"
    review_dir.mkdir()
    portal_dir = tmp_path / "portal_pages"
    portal_dir.mkdir()
    # publish_vendor_files(<arg>) creates <arg>/vendor/; the page references
    # ../portal_pages/vendor/* so petite-vue mounts and the handlers bind.
    vendor.publish_vendor_files(portal_dir)

    page_file = review_dir / "council_kbd_hint.html"
    page_file.write_text(_render_static_review_html(), encoding="utf-8")

    promises_kbd, calls, errors = _drive_chord(page_file, modifier)

    assert not errors, f"static review page raised JS errors: {errors}"
    # The placeholder must actually advertise the shortcut (guards against the
    # invariant being vacuously satisfied if the hint copy is removed but the
    # handler stays — then this guard would also need updating).
    assert promises_kbd, (
        "harness assumption broken: the refine placeholder no longer advertises "
        "the ⌘/Ctrl+Enter shortcut — update this guard (the keyboard-hint "
        "invariant no longer applies if the copy doesn't promise the chord)"
    )
    # The invariant: the advertised chord must actually dispatch a refine. A
    # promise of "⌘/Ctrl+Enter to send" with no wired @keydown handler leaves the
    # user pressing a documented shortcut that does nothing (Iter-87-class:
    # council-page copy must not reference an affordance the surface lacks).
    assert calls >= 1, (
        f"the static council REVIEW page's refine placeholder promises "
        f"'⌘/Ctrl+Enter to send' but pressing {modifier}+Enter dispatched NOTHING "
        "— the @keydown.enter.meta/.ctrl handler is not wired on this surface, so "
        "the page advertises a keyboard shortcut that does nothing (a copy/control "
        "drift between the live and static council pages)."
    )
