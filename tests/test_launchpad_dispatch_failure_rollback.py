"""Regression: when the Chrome extension dispatch fails (no extension
installed in this browser, or native host missing), the launchpad must
ROLL BACK the optimistic Vue state so the user isn't stuck staring at:

  - "Council in Progress" panel showing forever (operation polling a
    status file that will never be written)
  - Launch button disabled (.busy stuck true)
  - Prompt textarea empty (user has to retype)

Surfaced 2026-05-26 during e2e Chrome testing of the launchpad —
exactly the symptom the user reported with launch_mpm0bght_gx1y9v.
The fix is in handleDispatchResult: on dispatch failure, call
clearOperation() + restore this.pendingPrompt → this.prompt.
"""
from __future__ import annotations

import pytest


def test_handle_dispatch_result_rolls_back_on_install_prompt_tier():
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})

    # The handler must check for failure (install-prompt OR extension !ok).
    assert "tier === 'install-prompt'" in html
    # ... AND it must call clearOperation() to drop the optimistic state.
    # The order matters: rollback before banner display.
    handler_start = html.index("handleDispatchResult(result)")
    handler_excerpt = html[handler_start:handler_start + 3000]
    assert "this.clearOperation()" in handler_excerpt, (
        "handleDispatchResult must clearOperation() on failed dispatch "
        "to unstick the busy state — otherwise Launch button stays disabled "
        "and 'Council in Progress' panel polls forever"
    )
    # And restore the prompt the user typed.
    assert "this.pendingPrompt" in handler_excerpt
    assert "this.prompt = this.pendingPrompt" in handler_excerpt


def test_launch_council_snapshots_prompt_before_clearing():
    """The rollback in handleDispatchResult needs the original text — it
    must be snapshotted before launchCouncil clears `this.prompt`."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})
    launch_start = html.index("launchCouncil()")
    launch_block = html[launch_start:launch_start + 1500]
    snapshot_idx = launch_block.find("this.pendingPrompt = prompt")
    clear_idx = launch_block.find("this.prompt = ''")
    assert snapshot_idx != -1, "launchCouncil must snapshot prompt into pendingPrompt"
    assert clear_idx != -1, "launchCouncil should still clear this.prompt for the optimistic UX"
    assert snapshot_idx < clear_idx, (
        "snapshot must happen BEFORE clear — otherwise pendingPrompt is empty "
        "when the user's text is wiped"
    )


def test_pending_prompt_initialized_in_reactive_state():
    """pendingPrompt must be declared in the reactive data block so the
    handler can read/write it. petite-vue only reacts to keys present
    at app-mount time."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})
    assert "pendingPrompt: ''" in html, (
        "pendingPrompt must be initialized in the createApp({...}) data block"
    )


def test_provider_grid_hidden_on_terminal_failure_with_no_progress():
    """The live member-progress grid (provider-status-list) must HIDE on a
    terminal failed/canceled operation whose members never made progress.

    Found 2026-06-02 driving the cold-start "Launch Council" on an empty home:
    there are TWO dispatch-failure paths. The no-extension path already
    clearOperation()s (test_handle_dispatch_result_rolls_back...). But the
    POLLER-GIVEUP path (dispatch accepted, status file never appears → line
    ~3099 merges {status:'failed'} onto the prior operation, KEEPING the
    all-'pending' members) left the grid rendering "Claude QUEUED … Waiting for
    member responses." UNDER a "Council failed" header + "dispatch may not have
    started" error — a contradiction. showProviderRows must gate on the
    terminal status, hiding the stale all-pending grid while KEEPING an
    informative partial-progress grid (some Done/Failed/Running).
    Browser-verified: failed banner → grid hidden, error + Dismiss remain.
    """
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})
    start = html.index("get showProviderRows()")
    getter = html[start:start + 2000]
    # Must consult the operation STATUS, not just kind + rows-exist.
    assert "status === 'failed' || status === 'canceled'" in getter, (
        "showProviderRows must gate the grid on terminal failed/canceled "
        "status — otherwise the stale all-'pending' member grid renders under "
        "a 'Council failed' header (the cold-start contradiction)"
    )
    # On a terminal state it shows the grid ONLY if a member made real progress
    # (non-pending) — preserving an informative partial-failure grid while
    # hiding the all-queued contradiction.
    assert "row.statusClass !== 'pending'" in getter, (
        "on terminal failure the grid must show only when some member made "
        "real progress (non-pending), not blindly"
    )
    # Anti-regression: the old unconditional form must be gone.
    assert "return this.operation?.kind === 'council' && this.providerStatusRows.length > 0;" not in html, (
        "the old status-blind showProviderRows must be replaced"
    )


@pytest.mark.slow
@pytest.mark.browser
def test_failed_launch_rolls_back_in_the_live_dom():
    """The string tests above assert the rollback CODE exists in the JS source;
    this drives it in a REAL browser to prove it RUNS with petite-vue reactivity.

    The stuck-launch bug (2026-05-26, user-reported) was: a failed dispatch left
    the Launch button disabled (`busy` stuck) + the prompt cleared. A string
    test can't catch a reactivity regression (e.g. `operation` set on a
    non-reactive path, or clearOperation racing the optimistic set) — only a
    live click can. This fills a prompt, clicks Launch with a stubbed FAILING
    dispatch (the no-extension / install-prompt case), and asserts the rollback:
    the Launch button RE-ENABLES (busy derives from operation.status==='running',
    which clearOperation nulls) and the typed prompt is RESTORED.

    Mutation: drop `clearOperation()` from handleDispatchResult → operation stays
    'running' → busy stays true → button stays disabled → this reds.
    """
    import functools
    import http.server
    import tempfile
    import threading
    from pathlib import Path

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    html = render_launchpad_html(page_data={
        "defaultGoal": "Choose the strongest answer.",
        "defaultPrimaryProvider": "claude",
        "defaultMembers": ["claude", "codex"],
    })
    serve = Path(tempfile.mkdtemp())
    pp = serve / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(serve))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    prompt_text = "SQLite or DuckDB for an analytics workload?"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1100, "height": 1400}).new_page()
                # Dispatcher present (it always is) but FAILS asynchronously — the
                # no-extension / install-prompt case the rollback must survive.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: (o) => {"
                    "  if (o && o.onResult) setTimeout(() => o.onResult({ tier: 'install-prompt', ok: false }), 15);"
                    "} };"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_selector("#council-prompt", timeout=10000)
                page.fill("#council-prompt", prompt_text)
                page.evaluate(
                    "() => { const b=[...document.querySelectorAll('button')]"
                    ".find(x=>/Launch Council/i.test(x.innerText)); b.click(); }"
                )
                page.wait_for_timeout(500)  # async failure (15ms) + rollback
                state = page.evaluate(
                    """() => {
                      const btn = [...document.querySelectorAll('button')].find(x=>/Launch Council/i.test(x.innerText));
                      const ta = document.getElementById('council-prompt');
                      return { btnDisabled: btn ? btn.disabled : null,
                               promptValue: ta ? ta.value : null };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert state["btnDisabled"] is False, (
        "Launch button stayed disabled after a failed dispatch — `busy` never "
        "cleared (the stuck-launch regression: operation polls a status file "
        "that will never be written)"
    )
    assert state["promptValue"] == prompt_text, (
        f"the typed prompt was not restored after a failed launch (got "
        f"{state['promptValue']!r}) — the user would have to retype it"
    )


@pytest.mark.slow
@pytest.mark.browser
def test_failed_launch_surfaces_the_dispatch_banner_in_the_live_dom():
    """The OTHER half of a failed launch: rolling back the button + prompt
    (test_failed_launch_rolls_back_in_the_live_dom) makes the page USABLE again,
    but without the dispatch banner the user just sees their prompt reappear with
    no explanation — "I clicked Launch and nothing happened." handleDispatchResult
    sets dispatchBannerOpen=true / dispatchBannerReason='no-route', and the
    `v-if="dispatchBannerOpen"` banner tells them WHY (no dispatch path wired up)
    and WHAT to do (install the extension).

    The rollback test asserts the button/prompt reset but NOT the banner, so a
    regression that stops setting dispatchBannerOpen (or removes the banner's
    v-if) leaves the rollback test green while the failed launch goes silent. This
    drives the real click and asserts the banner appears with its actionable copy
    — the affordance, not just the cleanup.

    Mutation: drop `this.dispatchBannerOpen = true` from the install-prompt branch
    of handleDispatchResult → the banner never shows → this reds.
    """
    import functools
    import http.server
    import tempfile
    import threading
    from pathlib import Path

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # The no-route banner's heading is unique to the banner (the top cross-bootstrap
    # card says "Install the Chrome extension …", a different string), so its
    # presence/absence is a clean before/after signal that the banner rendered.
    BANNER_HEADING = "Install the Trinity browser extension to dispatch from any platform"

    html = render_launchpad_html(page_data={
        "defaultGoal": "Choose the strongest answer.",
        "defaultPrimaryProvider": "claude",
        "defaultMembers": ["claude", "codex"],
    })
    serve = Path(tempfile.mkdtemp())
    pp = serve / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(serve))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1100, "height": 1400}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: (o) => {"
                    "  if (o && o.onResult) setTimeout(() => o.onResult({ tier: 'install-prompt', ok: false }), 15);"
                    "} };"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_selector("#council-prompt", timeout=10000)

                banner_before = page.evaluate(
                    "(h) => document.body.innerText.includes(h)", BANNER_HEADING
                )
                page.fill("#council-prompt", "Kafka or RabbitMQ for this pipeline?")
                page.evaluate(
                    "() => { const b=[...document.querySelectorAll('button')]"
                    ".find(x=>/Launch Council/i.test(x.innerText)); b.click(); }"
                )
                page.wait_for_timeout(500)  # async failure (15ms) + banner render
                after = page.evaluate(
                    """(h) => {
                      const body = document.body.innerText;
                      return { heading: body.includes(h),
                               eyebrow: body.includes('NO DISPATCH PATH IS WIRED UP'),
                               install_step: /chrome:\\/\\/extensions/.test(body),
                               dismiss: /Dismiss/.test(body) };
                    }""",
                    BANNER_HEADING,
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert banner_before is False, (
        "the dispatch banner was already visible before any launch — it must only "
        "appear AFTER a failed dispatch, so this before/after signal is invalid"
    )
    assert after["heading"], (
        "the dispatch banner did NOT appear after a failed launch — "
        "handleDispatchResult didn't set dispatchBannerOpen, so the user sees their "
        "prompt reappear with no explanation of why nothing launched"
    )
    # The eyebrow is uppercased via CSS text-transform, so innerText returns the
    # UPPERCASE form — assert the rendered shape, not the source-case string
    # ([[raw_slug_display_275_scope]]).
    assert after["eyebrow"], "the banner rendered without its 'no dispatch path' eyebrow"
    assert after["install_step"], (
        "the banner appeared but without the actionable install step "
        "(chrome://extensions) — it must tell the user HOW to fix it, not just that "
        "it failed"
    )
    assert after["dismiss"], "the dispatch banner must be dismissible"
