"""Browser guard: the LAUNCHPAD's PRIMARY dynamic status must live inside an
aria-live region (WCAG 4.1.3 Status Messages, Level AA).

THE DEFECT (found 2026-06-18 driving the served launchpad in the UX sweep, Iter 99 —
the launchpad twin of the live-council fix from Iter 98): the launchpad surfaces two
dynamic-status surfaces that appear/update IN RESPONSE TO A USER ACTION (clicking
Launch) but carried NO live-region role:

  • the dispatch-FAILURE banner (``v-if="dispatchBannerOpen"``) — "No dispatch path is
    wired up" / "Extension installed, host not registered" + the install/repair remedy,
    which opens when a Launch click finds no route;
  • the ``.launch-status`` card (``v-if="operation || launchError"``) — "Council
    running" + the rotating status message, and the ``.status-error`` failure ribbon.

Driving a stubbed FAILED dispatch (``window.__TRINITY_DISPATCH__`` resolving
``{tier:'install-prompt'}``) and clicking Launch made the red banner appear with
``all_live_regions == []`` — ZERO live regions on the whole page. A sighted user sees
the banner; a screen-reader user clicked Launch, the dispatch FAILED, and they were
told NOTHING.

THE FIX (launchpad_template.py): a PERSISTENT visually-hidden ``.sr-only``
``role=status aria-live=polite`` mirror at the top of the launchpad ``<main>`` whose
text is the ``liveAnnouncement`` getter (dispatch-failure first, then launchError, then
the running operation) — present from first render (so the region is reliably
announced) and mutating on each transition; plus ``role=status aria-live=polite`` on
the visible dispatch banner + ``.launch-status`` card for correct in-place semantics.
(``.sr-only`` is the shared visually-hidden-but-AT-available utility in SHARED_CSS,
added with the live-council fix.)

This guard DRIVES a stubbed FAILED dispatch (the exact founder path) and asserts:
  (1) the dispatch banner that appears sits inside a role=status/aria-live region; AND
  (2) the persistent ``.sr-only`` mirror announces the dispatch FAILURE (so an AT user
      is TOLD their Launch failed) — the load-bearing assertion, since it proves the
      action→announcement edge, not merely that a static region exists.
It also drives a RUNNING launch (stub resolving ok) and asserts the mirror announces
"running" + the ``.launch-status`` card carries the live role.

Serves the rendered launchpad over http and reads the RENDERED DOM (file:// strips no
state here, but http matches the served-launchpad prod path). Slow-marked; skips
without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir

    write_portal_html()  # publishes vendor + writes launchpad.html
    return portal_pages_dir()


# Walk a selector's self-or-ancestor chain for any aria-live / role=status / role=alert.
_LIVE_ANCESTOR = """
(sel) => {
  const els = [...document.querySelectorAll(sel)].filter(e => getComputedStyle(e).display !== 'none');
  // pick the dispatch banner specifically by its copy
  let el = els.find(e => {
    const t = e.innerText || '';
    return t.includes('No dispatch path is wired up')
        || t.includes('host not registered')
        || t.includes('Install the Trinity browser extension');
  }) || els[0];
  if (!el) return {found: false};
  let node = el;
  while (node && node !== document.documentElement) {
    const role = (node.getAttribute && node.getAttribute('role')) || '';
    const live = (node.getAttribute && node.getAttribute('aria-live')) || '';
    if (live === 'polite' || live === 'assertive' || role === 'status' || role === 'alert') {
      return {found: true, inLiveRegion: true, tag: node.tagName, role, live};
    }
    node = node.parentElement;
  }
  return {found: true, inLiveRegion: false};
}
"""

_MIRROR_TEXT = """
() => {
  const m = document.querySelector('#live-announcement');
  if (!m) return {found: false};
  const cs = getComputedStyle(m);
  return {
    found: true,
    text: (m.textContent || '').trim(),
    // must be visually hidden but NOT display:none (display:none drops it from
    // the accessibility tree, defeating the announcement)
    hidden_not_display_none: cs.display !== 'none' && cs.clip === 'rect(0px, 0px, 0px, 0px)',
  };
}
"""


def _drive(port, *, dispatch_result, prompt):
    """Stub a dispatch result, click Launch, return (banner_ancestor, mirror)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"http://127.0.0.1:{port}/launchpad.html")
            page.wait_for_selector("main", timeout=8000)
            # Stub the dispatcher BEFORE any click so it never hits a real extension.
            page.evaluate(
                "(r) => { window.__TRINITY_DISPATCH__ = { dispatch: ({onResult}) => onResult(r) }; }",
                dispatch_result,
            )
            page.fill("textarea", prompt)
            page.query_selector("button.button.primary").click()
            page.wait_for_timeout(500)
            banner = page.evaluate(_LIVE_ANCESTOR, "section.card")
            mirror = page.evaluate(_MIRROR_TEXT)
            launch_status_role = page.evaluate(
                """() => { const s=document.querySelector('section.launch-status');
                           return s ? {role:s.getAttribute('role'), live:s.getAttribute('aria-live'),
                                       visible: getComputedStyle(s).display !== 'none'} : null; }"""
            )
            assert not errs, f"launchpad pageerror(s): {errs}"
            return banner, mirror, launch_status_role
        finally:
            browser.close()


def test_failed_dispatch_banner_is_announced(tmp_path, monkeypatch):
    """A stubbed FAILED dispatch (the founder's no-route Launch) must be announced to
    AT: the banner sits in a live region AND the persistent mirror states the failure."""
    pages_dir = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(pages_dir)
    try:
        banner, mirror, _ = _drive(
            port, dispatch_result={"tier": "install-prompt"}, prompt="Should we ship the cache rewrite?"
        )
    finally:
        httpd.shutdown()

    assert banner.get("found"), "dispatch banner never appeared after the failed Launch"
    assert banner.get("inLiveRegion"), (
        "FOUNDER SYMPTOM (WCAG 4.1.3): the launchpad dispatch-FAILURE banner appeared "
        "after Launch but is NOT inside any aria-live / role=status / role=alert region "
        "— a screen-reader user clicked Launch, the dispatch failed, and they were told "
        f"NOTHING. ancestor probe: {banner}"
    )
    assert mirror.get("found"), "persistent .sr-only role=status mirror is missing from the launchpad"
    assert mirror.get("hidden_not_display_none"), (
        "the sr-only mirror must be visually hidden via clip (not display:none — that "
        f"removes it from the accessibility tree). got: {mirror}"
    )
    assert "dispatch failed" in (mirror.get("text") or "").lower(), (
        "the persistent live mirror does NOT announce the dispatch FAILURE — an AT user "
        f"is not told their Launch did nothing. mirror text: {mirror.get('text')!r}"
    )


def test_running_launch_is_announced(tmp_path, monkeypatch):
    """A launched council (stub resolves ok) must announce 'running' via the mirror and
    carry role=status on the visible .launch-status card."""
    pages_dir = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(pages_dir)
    try:
        _, mirror, launch_status_role = _drive(
            port, dispatch_result={"tier": "extension", "ok": True}, prompt="Compare three caching strategies"
        )
    finally:
        httpd.shutdown()

    assert mirror.get("found"), "persistent .sr-only role=status mirror is missing"
    assert "running" in (mirror.get("text") or "").lower(), (
        "the live mirror does NOT announce that the council is running — an AT user gets "
        f"no acknowledgment their Launch started. mirror text: {mirror.get('text')!r}"
    )
    assert launch_status_role is not None and launch_status_role.get("visible"), (
        "the .launch-status card never became visible after a launch"
    )
    assert launch_status_role.get("role") == "status" and launch_status_role.get("live") in (
        "polite",
        "assertive",
    ), (
        "FOUNDER SYMPTOM (WCAG 4.1.3): the launchpad .launch-status card updates its "
        "status text live but carries no role=status / aria-live — its transitions are "
        f"silent to AT. got: {launch_status_role}"
    )


def _drive_copy_click(port):
    """Open the served launchpad, click the first visible 'Copy …' button, and read
    the persistent .sr-only role=status mirror BEFORE and shortly AFTER the click."""
    from playwright.sync_api import sync_playwright

    def _mirror_text(page):
        return page.evaluate(
            "() => { const m=document.querySelector('#live-announcement');"
            " return m ? (m.textContent||'').trim() : '<<missing>>'; }"
        )

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            # Drive stats.html (view=stats, written alongside launchpad.html by
            # write_portal_html) — every analytics/diagnostics card with its
            # Copy-command buttons is visible there regardless of corpus depth.
            page.goto(f"http://127.0.0.1:{port}/stats.html")
            page.wait_for_selector("main", timeout=8000)
            page.wait_for_timeout(400)
            label = page.evaluate(
                "() => { const bs=[...document.querySelectorAll('button[aria-label]')]"
                ".filter(b=>/copy/i.test(b.getAttribute('aria-label')) && b.offsetParent!==null);"
                " return bs.length ? bs[0].getAttribute('aria-label') : null; }"
            )
            if not label:
                pytest.skip("no visible Copy button on the rendered launchpad to drive")
            at_rest = _mirror_text(page)
            page.click(f"button[aria-label={label!r}]")
            page.wait_for_timeout(180)
            after = _mirror_text(page)
            assert not errs, f"launchpad pageerror(s): {errs}"
            return label, at_rest, after
        finally:
            browser.close()


def test_copy_command_button_announces_to_screen_reader(tmp_path, monkeypatch):
    """A 'Copy command' click on the launchpad must ANNOUNCE the copy to AT.

    FOUNDER SYMPTOM (WCAG 4.1.3, UX sweep Iter 130): clicking a Copy button swapped
    the button glyph to '✓ Copied' (sighted feedback) but left the persistent
    sr-only role=status mirror EMPTY — the clipboard write and the icon swap are both
    mute to a screen reader, so an AT user who copies a command hears SILENCE. The
    launchpad is command-copy-centric without the extension (install commands, the
    council/lens CTAs, the eval commands, the embedder download), so this is the whole
    copy-button class, not one button. The fix routes copyText + copyLens through a
    copyAnnouncement that liveAnnouncement surfaces at lowest precedence.
    """
    pages_dir = _seed(tmp_path, monkeypatch)
    httpd, port = _serve(pages_dir)
    try:
        label, at_rest, after = _drive_copy_click(port)
    finally:
        httpd.shutdown()

    # Quiet at rest — the mirror must not announce spuriously before any action.
    assert at_rest == "", (
        f"the sr-only status mirror was non-empty BEFORE any copy click: {at_rest!r}"
    )
    assert "copied" in after.lower(), (
        "FOUNDER SYMPTOM (WCAG 4.1.3): clicking the launchpad "
        f"{label!r} button left the persistent sr-only role=status mirror EMPTY "
        f"({after!r}) — the ✓ glyph swap is visual-only, so a screen-reader user who "
        "copies a command is told NOTHING. The copy must announce via the live region."
    )
