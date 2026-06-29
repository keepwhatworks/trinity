"""Dismissing a banner must NOT strand keyboard focus on <body> (WCAG 2.4.3).

Founder symptom (driven 2026-06-23, the keyboard sweep of the live-council error
state + the launchpad portal-notice): a `v-if`-gated banner whose "Dismiss" link
clears the reactive flag that mounts it removes the <section> that CONTAINS the
focused link. petite-vue patches the DOM, the focused element vanishes, and focus
falls to <body> — a keyboard user who Tabbed to Dismiss and pressed Enter is
stranded at the top of the document with no re-home (the focus-loss sibling of the
already-fixed launchpad `_restoreTriggerFocus` / dispatch-focus-restoration class).

Three surfaces, one class:

  1. LIVE COUNCIL (`render_live_council_page`) — the chainError banner's Dismiss
     (`@click.prevent="dismissChainError"`). Before the fix it was an inline
     `chainError = ''` with no focus move. After: `dismissChainError` re-homes to
     `.topbar-back`.

  2. LAUNCHPAD (`render_launchpad_html`) — the portal-open notice's Dismiss
     (`dismissPortalNotice`). Before the fix it cleared `portalNotice` with no focus
     move; the dispatch-banner's `dismissDispatchBanner` is the identical-shaped
     sibling. After: both call `_focusAfterDismiss`, which re-homes to the first
     present + visible + enabled focusable (the launch button on home).

  3. LAUNCHPAD launch-status section (`render_launchpad_html`) — the Dismiss button
     (`@click="dismissOperation"`) inside `<section v-if="operation || launchError">`
     that appears on a FAILED / canceled council. Driven 2026-06-23: this third
     dismiss was MISSED by the iter-462 pass — `dismissOperation` cleared
     `launchError` + `clearOperation()` (operation → null) so the whole section
     un-mounted, dropping the focused <button> to <body>. After: `dismissOperation`
     also calls `_focusAfterDismiss`. (It's a <button>, not an <a> like the other
     two — the same class with a different tag, which is exactly why a per-instance
     fix keeps re-opening; this guard covers the button form.)

Each test reproduces the EXACT keyboard stranding: Tab to the Dismiss control,
press Enter, and assert `document.activeElement` is NOT <body>. A pure DOM probe —
no source-string check — so a syntax-preserving refactor that drops the focus move
reds here.

Mutation proof (recorded in the sweep log): revert `dismissChainError` to the inline
`chainError = ''` (and `dismissPortalNotice`/`dismissDispatchBanner` to bare flag
clears) → both tests red with "focus stranded on <body> after Dismiss"; restore →
green. The control assertion (the banner actually un-mounted) passes either way, so
the bite is the focus re-home, not a vacuous probe.
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

_CID = "dismiss_focus_browsertest"


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_completed_two_member_council() -> None:
    """A completed 2-distinct-provider council so canChainNext is True and the
    chain composer (+ its chainError banner path) renders."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(provider=p, model="m", output_text=f"Answer from {p}. " * 30)
        for p in ("claude", "codex")
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type="design",
        agreed_claims=["Validate at the boundary."],
        disagreed_claims=[
            {
                "claim": "cache per-call?",
                "providers_for": ["claude"],
                "providers_against": ["codex"],
                "why_matters": "leaks",
            }
        ],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID,
            bundle_id=_CID,
            task_cluster_id="cluster",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache in-process or per-call?"},
            member_results=members,
            synthesis_prompt="Review the answers.",
            synthesis_output="In-process wins.",
            routing_label=routing_label,
            created_at="2026-06-05T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


def test_live_council_dismiss_chainerror_does_not_strand_focus(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_completed_two_member_council()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                # A dispatcher that FAILS synchronously → onResult({ok:false}) → the
                # chain rollback sets chainError → the banner (with Dismiss) renders.
                # The real dispatcher exposes probe/onStateChange (called at mount),
                # so stub them too or the page errors before the composer is usable.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    "  state: 'absent',"
                    "  probe: () => ({state: 'absent'}),"
                    "  onStateChange: () => {},"
                    "  dispatch: ({onResult}) => { if (onResult) onResult({ok:false, error:'sim fail', reason:'absent'}); }"
                    "};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2000)
                # Fire Continue → failed dispatch → chainError banner appears.
                page.click("button.primary")
                page.wait_for_timeout(1000)
                assert page.evaluate(
                    "() => !!document.querySelector('section[role=alert]')"
                ), "chainError banner did not render (the failed-dispatch path broke)"

                # Tab to the Dismiss link, then activate it with the keyboard.
                page.evaluate("document.body.focus();")
                reached = False
                for _ in range(40):
                    page.keyboard.press("Tab")
                    if page.evaluate(
                        "() => { const e = document.activeElement;"
                        " return !!e && e.tagName === 'A'"
                        " && (e.textContent || '').trim().startsWith('Dismiss'); }"
                    ):
                        reached = True
                        break
                assert reached, "Dismiss link was not reachable by keyboard Tab"
                page.keyboard.press("Enter")
                page.wait_for_timeout(400)

                state = page.evaluate(
                    "() => { const e = document.activeElement;"
                    " return { banner_gone: !document.querySelector('section[role=alert]'),"
                    "   on_body: e === document.body,"
                    "   active_tag: e ? e.tagName.toLowerCase() : null,"
                    "   active_cls: e ? (e.className || '').toString() : null }; }"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # Control (non-vacuous): the Dismiss actually removed the banner.
    assert state["banner_gone"], "Dismiss did not remove the chainError banner"
    # The bite: focus must NOT be stranded on <body> after the focused link vanished.
    assert not state["on_body"], (
        "focus stranded on <body> after Dismiss removed the chainError banner that "
        "CONTAINED the focused link — a keyboard user is dumped at the top of the "
        f"document (WCAG 2.4.3). active={state['active_tag']}.{state['active_cls']}"
    )


def test_launchpad_dismiss_portal_notice_does_not_strand_focus(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(
        render_launchpad_html(page_data=build_launchpad_payload()), encoding="utf-8"
    )
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1000}
                ).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # The portal-open notice is the deterministic, isolated way to render
                # this banner class (the runtime fires this event when a side-panel
                # memory-chip deep-link bounces to the full dashboard).
                page.evaluate(
                    "window.dispatchEvent(new CustomEvent('trinity:portal-open',"
                    " {detail: {label: 'the memory viewer'}}))"
                )
                page.wait_for_timeout(400)
                assert page.evaluate(
                    "() => !!document.querySelector('.portal-open-notice')"
                ), "portal-open notice did not render"

                page.evaluate("document.body.focus();")
                reached = False
                for _ in range(80):
                    page.keyboard.press("Tab")
                    if page.evaluate(
                        "() => { const e = document.activeElement;"
                        " return !!e && e.matches('.portal-open-notice a'); }"
                    ):
                        reached = True
                        break
                assert reached, "portal-notice Dismiss was not reachable by keyboard Tab"
                page.keyboard.press("Enter")
                page.wait_for_timeout(400)

                state = page.evaluate(
                    "() => { const e = document.activeElement;"
                    " return { banner_gone: !document.querySelector('.portal-open-notice'),"
                    "   on_body: e === document.body,"
                    "   active_tag: e ? e.tagName.toLowerCase() : null,"
                    "   active_cls: e ? (e.className || '').toString() : null }; }"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert state["banner_gone"], "Dismiss did not remove the portal-open notice"
    assert not state["on_body"], (
        "focus stranded on <body> after Dismiss removed the portal-open notice that "
        "CONTAINED the focused link — a keyboard user is dumped at the top of the "
        f"launchpad (WCAG 2.4.3). active={state['active_tag']}.{state['active_cls']}"
    )


def test_launchpad_dismiss_operation_does_not_strand_focus(tmp_path, monkeypatch):
    """The launch-status Dismiss <button> (on a FAILED council) — the third dismiss
    of the same class, missed by iter 462. Renders deterministically via a seeded
    `activeOperation` with status='failed' (busy → false, so the Dismiss button
    shows), then drives the exact keyboard stranding."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    # A FAILED council: operation truthy + status != 'running' → busy is false, so
    # `v-if="!busy"` renders the Dismiss button inside `v-if="operation || launchError"`.
    pd["activeOperation"] = {
        "kind": "council",
        "status": "failed",
        "statusToken": "launch_failed_focustest",
        "error": "Council failed.",
        "task_text": "Compare three rate-limiting strategies",
        "memberOrder": ["claude", "codex"],
    }
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(
        render_launchpad_html(page_data=pd), encoding="utf-8"
    )
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1000}
                ).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                page.wait_for_timeout(400)
                assert page.evaluate(
                    "() => { const b = [...document.querySelectorAll("
                    "'.launch-status-actions button')].find("
                    "x => /dismiss/i.test(x.textContent)); return !!b; }"
                ), "launch-status Dismiss button did not render on a failed council"

                page.evaluate("document.body.focus();")
                reached = False
                for _ in range(120):
                    page.keyboard.press("Tab")
                    if page.evaluate(
                        "() => { const e = document.activeElement;"
                        " return !!e && e.tagName === 'BUTTON'"
                        " && /dismiss/i.test((e.textContent || '').trim())"
                        " && !!e.closest('.launch-status-actions'); }"
                    ):
                        reached = True
                        break
                assert reached, "launch-status Dismiss button was not reachable by keyboard Tab"
                page.keyboard.press("Enter")
                page.wait_for_timeout(450)

                state = page.evaluate(
                    "() => { const e = document.activeElement;"
                    " return { section_gone: !document.querySelector('.launch-status'),"
                    "   on_body: e === document.body,"
                    "   active_tag: e ? e.tagName.toLowerCase() : null,"
                    "   active_cls: e ? (e.className || '').toString() : null }; }"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # Control (non-vacuous): the Dismiss actually removed the launch-status section.
    assert state["section_gone"], "Dismiss did not remove the launch-status section"
    # The bite: focus must NOT be stranded on <body> after the focused BUTTON vanished.
    assert not state["on_body"], (
        "focus stranded on <body> after Dismiss removed the launch-status section "
        "that CONTAINED the focused button — a keyboard user who dismissed a FAILED "
        "council is dumped at the top of the launchpad (WCAG 2.4.3). "
        f"active={state['active_tag']}.{state['active_cls']}"
    )
