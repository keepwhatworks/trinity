"""Browser guard: the live council page's chainError banner heading must match
the ACTUAL situation. The PROACTIVE on-load staleness warning must NOT wear the
REACTIVE "Could not start next round" heading — no round was attempted.

THE BUG (found 2026-06-23 driving the live council page with an out-of-date
extension in the UX sweep): on ``init()`` the page probes the dispatcher and, if
it reports state ``'stale'`` (the installed extension is too old to accept this
page as a dispatch sender — ``rejected-sender``), it PROACTIVELY sets
``chainError`` to a "your extension is out of date, reload it" message BEFORE the
user clicks anything. But the banner heading was a STATIC literal "Could not
start next round". So a user who merely OPENED a completed council page with a
stale extension saw, before touching any control::

    Could not start next round
    Your Trinity extension is out of date — Refine / Continue won't work …

The heading asserts a failed action ("Could not start next round") that never
happened — the UNCLEAR / says-what-it-ISN'T copy class (it claims an event the
user never triggered).

THE FIX (council_review.py): a reactive-default ``chainErrorHeading`` data field
("Could not start next round") bound into the banner ``<strong>``, OVERRIDDEN at
the proactive stale-warning site ("Your Trinity extension is out of date") and
reset to the default at the round-start action clears (so a dismissed-stale →
reactive-failure sequence shows the right heading), plus a stop-specific heading
for a failed Stop.

This guard DRIVES the real surface: serves a seeded completed council over http,
injects a dispatcher stub reporting ``'stale'`` BEFORE the page script runs (so
the proactive probe fires), opens the page, and reads the RENDERED banner
heading. Two bites, both mutation-proven:
  (A) PROACTIVE stale → heading is the accurate stale phrasing, NEVER the phantom
      "Could not start next round" (catches a heading that drops the binding).
  (B) REACTIVE Continue-failure → heading IS "Could not start next round" (the
      complement — catches a binding that loses the reactive heading).

DOM assertions on the rendered heading, not source-string checks. The dispatcher
is stubbed before any interaction so nothing reaches a real extension / real
council. Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Reports state 'stale' (extension too old → 'rejected-sender'). probe(force)
# resolves to 'stale' so the proactive init() warning fires; onStateChange is the
# real dispatcher's interface (council_review subscribes at mount).
_STALE_STUB = """
window.__TRINITY_DISPATCH__ = {
  _listeners: new Set(),
  dispatch: function(opts){ opts && opts.onResult && opts.onResult({ok:false, reason:'extension-unreachable'}); return Promise.resolve({ok:false, reason:'extension-unreachable'}); },
  probe: function(force){ return Promise.resolve('stale'); },
  onStateChange: function(cb){ this._listeners.add(cb); return ()=>this._listeners.delete(cb); },
  get state(){ return 'stale'; }
};
"""

# A PRESENT (not stale) dispatcher whose dispatch FAILS — so clicking Continue
# fires a genuine REACTIVE round-start failure (no proactive banner on load).
_PRESENT_FAIL_STUB = """
window.__TRINITY_DISPATCH__ = {
  _listeners: new Set(),
  dispatch: function(opts){ opts && opts.onResult && opts.onResult({ok:false, reason:'native-host-unavailable'}); return Promise.resolve({ok:false, reason:'native-host-unavailable'}); },
  probe: function(force){ return Promise.resolve('present'); },
  onStateChange: function(cb){ this._listeners.add(cb); return ()=>this._listeners.delete(cb); },
  get state(){ return 'present'; }
};
"""

# A dispatcher that starts 'stale' (proactive warning fires on load) and exposes
# window.__flipToPresent() — flipping state to 'present' and NOTIFYING listeners,
# exactly what the real dispatcher's focus re-probe does after the user reloads
# the extension at chrome://extensions (launchpad-init.js: a focus event re-probes
# when state !== 'present', the now-fresh worker answers trinity-ping → setState
# 'present' → onStateChange fires for every listener). The proactive warning's
# init() subscription must CLEAR the banner on that recovery.
_STALE_THEN_RECOVER_STUB = """
window.__TRINITY_DISPATCH__ = (function(){
  var st = 'stale';
  var listeners = new Set();
  function setState(s){ st = s; listeners.forEach(function(cb){ try{cb(st);}catch(e){} }); }
  window.__flipToPresent = function(){ setState('present'); };
  return {
    dispatch: function(opts){ opts && opts.onResult && opts.onResult({ok:false, reason:'extension-unreachable'}); return Promise.resolve({ok:false}); },
    probe: function(force){ return Promise.resolve(st); },
    onStateChange: function(cb){ listeners.add(cb); return function(){ listeners.delete(cb); }; },
    get state(){ return st; }
  };
})();
"""

_PHANTOM = "Could not start next round"


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_completed_council(tmp_path, monkeypatch):
    """Seed a schema-correct completed council via the real writers, render the
    live page + portal. Returns (home_dir, council_id)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import importlib.util
    from pathlib import Path

    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import review_pages_dir

    spec = importlib.util.spec_from_file_location(
        "seedmod",
        str(Path(__file__).resolve().parent.parent / "scripts" / "seed_synthetic_home.py"),
    )
    seedmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seedmod)
    seedmod.seed(Path(tmp_path))  # writes council_syn00.. + outcome JSONP sidecars

    write_portal_html()
    write_live_council_page(force=True)
    _vendor.publish_vendor_files(review_pages_dir())
    return tmp_path, "council_syn00"


def _open(page, port, council_id):
    page.goto(
        f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={council_id}"
    )
    page.wait_for_timeout(1600)


def test_proactive_stale_warning_does_not_claim_a_round_failed(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home, cid = _seed_completed_council(tmp_path, monkeypatch)
    httpd, port = _serve(home)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # (A) PROACTIVE stale warning on load — the user clicked NOTHING.
                page = browser.new_context(
                    viewport={"width": 393, "height": 900}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(_STALE_STUB)
                _open(page, port, cid)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                banner = page.query_selector("section[role='alert']")
                assert banner and banner.is_visible(), (
                    "the proactive stale-extension warning did not render — the guard "
                    "precondition (a visible chainError banner on load) failed, so the "
                    "heading assertion below would be vacuous."
                )
                heading = page.eval_on_selector(
                    "section[role='alert'] strong", "el => el.textContent.trim()"
                )
                body = page.eval_on_selector(
                    "section[role='alert'] span", "el => el.textContent.trim()"
                )
                # Precondition: this IS the proactive stale warning, not some other
                # banner (else the heading check is testing the wrong thing). The
                # body points the user at the chrome://extensions reload — a stable
                # marker independent of whether "out of date" lands in heading or body.
                assert "chrome://extensions" in body.lower(), (
                    f"expected the proactive stale-extension warning body, got {body!r}"
                )
                # THE BITE: a page-load warning must NOT claim a round failed.
                assert heading != _PHANTOM, (
                    "the PROACTIVE on-load stale-extension warning wore the REACTIVE "
                    f"heading {_PHANTOM!r} — a user who merely OPENED a completed "
                    "council with an out-of-date extension is told a round 'could not "
                    "start' before they clicked anything (UNCLEAR / claims an event "
                    f"that never happened). Banner was: {heading!r} / {body!r}."
                )
                assert "out of date" in heading.lower(), (
                    "the stale-warning heading should lead with the actual situation "
                    f"(the extension is out of date); got {heading!r}."
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_reactive_continue_failure_keeps_its_round_start_heading(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home, cid = _seed_completed_council(tmp_path, monkeypatch)
    httpd, port = _serve(home)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # (B) REACTIVE failure — dispatcher present, NOT stale, but the
                # Continue dispatch fails. The heading MUST be the round-start phrasing.
                page = browser.new_context(
                    viewport={"width": 1280, "height": 900}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(_PRESENT_FAIL_STUB)
                _open(page, port, cid)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                # No banner before any click (not stale).
                pre = page.query_selector("section[role='alert']")
                assert not (pre and pre.is_visible()), (
                    "a banner rendered on load with a non-stale dispatcher — the "
                    "reactive-only precondition failed."
                )
                cont = page.query_selector("section.chain-actions button.primary")
                assert cont and cont.is_visible(), (
                    "the 'Continue (one round)' button did not render on a completed "
                    "council — the reactive-failure precondition failed."
                )
                cont.click()
                page.wait_for_timeout(900)

                banner = page.query_selector("section[role='alert']")
                assert banner and banner.is_visible(), (
                    "the reactive Continue-failure produced no banner — dispatch "
                    "failure must give feedback (the NO-FEEDBACK class)."
                )
                heading = page.eval_on_selector(
                    "section[role='alert'] strong", "el => el.textContent.trim()"
                )
                # THE COMPLEMENT BITE: the reactive round-start failure keeps its
                # accurate heading. (A binding that lost the reactive heading would
                # red here while (A) stays green — the discriminating pair.)
                assert heading == _PHANTOM, (
                    "a REACTIVE Continue-failure should be headed "
                    f"{_PHANTOM!r}, got {heading!r} — the round-start failure heading "
                    "regressed."
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_proactive_stale_warning_clears_after_the_extension_recovers(tmp_path, monkeypatch):
    """The proactive stale warning must CLEAR once the user does exactly what it
    prescribes — reload the extension at chrome://extensions — and a focus re-probe
    reports a healthy state.

    THE BUG (found 2026-06-23, UX sweep, the recovery sibling of the iter-384 fix):
    the init() proactive-probe subscription only SET the "your extension is out of
    date" banner on state 'stale' and never CLEARED it when the state transitioned
    away from stale. So a user who opened a completed council with an out-of-date
    extension, went to chrome://extensions and RELOADED it (the warning's own
    instruction), then returned to the page (a focus re-probe → 'present'), still
    saw the stale warning lying on screen — the corrective action succeeded but the
    UI kept the now-false banner up (the NO-FEEDBACK stale-state class).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home, cid = _seed_completed_council(tmp_path, monkeypatch)
    httpd, port = _serve(home)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 393, "height": 900}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(_STALE_THEN_RECOVER_STUB)
                _open(page, port, cid)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                # Precondition: the proactive stale warning IS up on load.
                pre = page.query_selector("section[role='alert']")
                assert pre and pre.is_visible(), (
                    "the proactive stale-extension warning did not render on load — "
                    "the recovery precondition (a visible stale banner) failed, so "
                    "the clear-on-recovery assertion below would be vacuous."
                )
                pre_heading = page.eval_on_selector(
                    "section[role='alert'] strong", "el => el.textContent.trim()"
                )
                assert "out of date" in pre_heading.lower(), (
                    f"expected the proactive stale warning on load; got {pre_heading!r}."
                )

                # The user reloads the extension at chrome://extensions and returns
                # to this page — the dispatcher's focus re-probe reports 'present'
                # and notifies its onStateChange listeners.
                page.evaluate("window.__flipToPresent()")
                page.wait_for_timeout(400)

                # THE BITE: the now-false stale warning must be GONE.
                post = page.query_selector("section[role='alert']")
                still_visible = bool(post and post.is_visible())
                post_text = ""
                if still_visible:
                    post_text = page.eval_on_selector(
                        "section[role='alert']", "el => el.textContent.trim()"
                    )
                assert not still_visible, (
                    "the PROACTIVE stale-extension warning was STILL on screen after "
                    "the extension recovered (state stale → present via a focus "
                    "re-probe) — the user did exactly what the banner prescribed "
                    "(reload at chrome://extensions) and the UI kept lying that the "
                    f"extension is out of date. Banner still read: {post_text!r}."
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_dismiss_link_clears_the_proactive_stale_warning(tmp_path, monkeypatch):
    """Clicking the chainError banner's own "Dismiss" link must REMOVE the banner
    from the DOM (``@click.prevent="dismissChainError"`` clears chainError → the
    ``v-if="chainError"`` section unmounts; the handler also re-homes keyboard focus
    — see test_dismiss_banner_focus_rehome_browser).

    THE GAP (found 2026-06-23, UX sweep, driving the live council page): the
    Dismiss link is the user's only way to clear the proactive stale-extension
    warning WITHOUT triggering another failed round. The only existing browser
    test that clicks Dismiss (test_council_chain_action_browser.py) does so purely
    as a between-scenarios SETUP step — it then immediately types a refine
    directive that re-sets ``chainError``, so it NEVER asserts the Dismiss click
    actually cleared the banner. Mutation-proven: neutering the live-page handler
    to ``@click.prevent="0"`` leaves that test GREEN (3 passed) while the banner
    silently persists — a dead Dismiss anchor (the NO-OP class) ships unnoticed.
    This guard DRIVES the real click and asserts the banner is gone.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home, cid = _seed_completed_council(tmp_path, monkeypatch)
    httpd, port = _serve(home)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # Proactive stale warning fires on load (the user clicked nothing).
                page = browser.new_context(
                    viewport={"width": 393, "height": 900}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(_STALE_STUB)
                _open(page, port, cid)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                # PRECONDITION (non-vacuous): the banner + its Dismiss link are on
                # screen before the click.
                banner = page.query_selector("section[role='alert']")
                assert banner and banner.is_visible(), (
                    "the proactive stale-extension warning did not render — the guard "
                    "precondition (a visible chainError banner with a Dismiss link) "
                    "failed, so the clears-on-dismiss assertion would be vacuous."
                )
                dismiss = page.query_selector("section[role='alert'] a")
                assert (
                    dismiss
                    and dismiss.is_visible()
                    and dismiss.inner_text().strip() == "Dismiss"
                ), "the chainError banner is missing its 'Dismiss' link."

                # THE BITE: clicking Dismiss must clear chainError → the v-if banner
                # unmounts. A dead Dismiss anchor leaves the banner on screen forever.
                dismiss.click()
                page.wait_for_timeout(400)
                post = page.query_selector("section[role='alert']")
                still_visible = bool(post and post.is_visible())
                post_text = ""
                if still_visible:
                    post_text = page.eval_on_selector(
                        "section[role='alert']", "el => el.textContent.trim()"
                    )
                assert not still_visible, (
                    "clicking 'Dismiss' did NOT clear the chainError banner — the "
                    "live council page's Dismiss link is a NO-OP, so the user can't "
                    "dismiss the proactive stale-extension warning without triggering "
                    f"another failed round. Banner still read: {post_text!r}."
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
