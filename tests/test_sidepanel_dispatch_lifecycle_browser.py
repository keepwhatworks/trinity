"""A stubbed council dispatch in the REAL side panel must show the full operation
lifecycle — and on failure roll back co-located, NOT leave the user stuck.

The composer's Launch button is the most-used control, but every existing busy-state
guard (test_launchpad_busy_button_browser.py) drives a `file://`-over-http render with
a PRE-SEEDED `pageData.activeOperation` — it never (a) CLICKS Launch to drive the real
`launchCouncil → beginOperation` transition, (b) runs in the opaque-origin sandbox the
Chrome side panel actually uses, or (c) exercises the side-panel-specific
`liveCouncilUrl` host-fetch branch (`window.__TRINITY_HOST_FETCH__ ? './live_council.html'
: liveReviewUrlBase`) — the "Open council page" link that ONLY the side panel resolves to
the sibling sandbox page (a file:// render takes the OTHER branch). If `beginOperation`,
the busy getter, the `./live_council.html` host-fetch branch, or the failure rollback
(handleDispatchResult) regressed, every council launched FROM THE PANEL would silently
break — stuck on a never-resolving "Council in progress…" or a dead "Open council page"
that 404s — while every test stayed green. That's Trinity's signature "green while the
value is gone" shape on the launch path.

These drive the REAL extension (the only thing that exercises the sandbox opaque origin)
with a STUBBED `window.__TRINITY_DISPATCH__` (so nothing hits a real council), CLICK
Launch, and assert:
  1. RUNNING: button relabels "Council in progress…" (disabled), hero flips to "Council
     in Progress", the spinner-row + "Council running" heading render, the prompt clears,
     the dispatch fires, AND "Open council page" resolves to the in-panel
     ./live_council.html?status_token=… (the host-fetch branch), with "Stop council".
  2. FAILED: a stubbed r.ok===false rolls back — button re-enables to "Launch Council",
     the prompt is RESTORED for retry, the spinner clears, and the error is co-located in
     the launch-status (.status-error), NOT swallowed.

Mutation-provable: revert the liveCouncilUrl host-fetch branch (panel falls back to the
empty/file base → dead "Open council page"), or drop the beginOperation busy transition,
and the RUNNING assertions red; reroute the failure away from handleDispatchResult and
the FAILED rollback assertions red.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"

# A dispatcher whose dispatch() NEVER calls onResult => the optimistic operation
# stays 'running' so the busy lifecycle can be read. probe/subscribe stub out the
# warm-probe so the real chrome.runtime path is never touched.
_STUB_RUNNING = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) { window.__lastDispatch = opts && opts.extensionAction; },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""

# A dispatcher that resolves onResult with an extension FAILURE => exercises the
# handleDispatchResult rollback (clearOperation + restore prompt + launchError).
_STUB_FAIL = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) {
      window.__lastDispatch = opts && opts.extensionAction;
      if (opts && opts.onResult) setTimeout(function () {
        opts.onResult({ tier: 'extension', ok: false, response: { error: 'stubbed council dispatch failure' } });
      }, 50);
    },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host, load the real extension, open
    the side panel, and return (ctx, ext_id, page, errors) after the launchpad mounts."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    # launchpad_data uses the prebuilt payload; every OTHER query delegates to the
    # REAL capture-host handlers (so this never fabricates a false answer).
    stub = (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json, os\n"
        f"os.environ['TRINITY_HOME'] = {str(home)!r}\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from trinity_local.capture_host import QUERY_HANDLERS\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "msg = msg or {}\n"
        "qk = msg.get('query_kind')\n"
        "if qk == 'launchpad_data':\n"
        f"    out = open({str(pl)!r}).read().encode()\n"
        "elif qk in QUERY_HANDLERS:\n"
        "    out = json.dumps(QUERY_HANDLERS[qk](msg), default=str).encode()\n"
        "else:\n"
        "    out = json.dumps({'ok': True}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )
    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(stub, encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    try:
        ctx = p.chromium.launch_persistent_context(
            str(ud), headless=False,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no launchable chromium: {exc}")

    sw = None
    for _ in range(50):
        if ctx.service_workers:
            sw = ctx.service_workers[0]
            break
        try:
            sw = ctx.wait_for_event("serviceworker", timeout=2000)
            break
        except Exception:
            time.sleep(0.1)
    assert sw, "extension service worker never registered (manifest invalid?)"
    ext_id = sw.url.split("/")[2]
    (nm / f"{HOST}.json").write_text(json.dumps({
        "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }), encoding="utf-8")

    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page, errors


def test_launch_in_panel_shows_running_lifecycle_with_in_panel_open_link(tmp_path, monkeypatch):
    """Clicking Launch in the REAL side panel must drive the full RUNNING lifecycle,
    and "Open council page" must resolve to the in-panel ./live_council.html sibling
    (the __TRINITY_HOST_FETCH__ branch a file:// render never takes)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )

            # Stub dispatch so the optimistic operation stays RUNNING; click Launch.
            lf.evaluate(_STUB_RUNNING)
            lf.fill("#council-prompt", "why is the sky blue?")
            lf.locator(".actions button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(800)

            state = lf.evaluate(
                "()=>{"
                "const b=document.querySelector('.actions button.button.primary');"
                "const h=[...document.querySelectorAll('h1')].find(e=>e.offsetParent!==null);"
                "const spinnerRow=document.querySelector('.launch-status .spinner-row');"
                "const heading=(document.querySelector('.launch-status .spinner-row .status-message')||{}).textContent||'';"
                "const openLink=document.querySelector('.launch-status-actions a.button.ghost');"
                "const stopBtns=[...document.querySelectorAll('.launch-status-actions button')].map(x=>x.textContent.trim());"
                "return {"
                " btnText:b?b.textContent.trim():null, btnDisabled:b?b.disabled:null,"
                " hero:h?h.textContent.trim():null,"
                " spinnerVisible:!!(spinnerRow&&spinnerRow.offsetParent!==null), heading:heading.trim(),"
                " openHref:openLink?openLink.getAttribute('href'):null,"
                " openVisible:!!(openLink&&openLink.offsetParent!==null),"
                " stopBtns, dispatchKind:window.__lastDispatch&&window.__lastDispatch.kind,"
                " promptVal:(document.querySelector('#council-prompt')||{}).value,"
                " raw:document.body.innerHTML.includes('{{'),"
                " docW:document.documentElement.scrollWidth, vw:document.documentElement.clientWidth};}"
            )

            # The dispatch actually fired (not a swallowed click).
            assert state["dispatchKind"] == "launch-council", (
                f"clicking Launch did NOT fire a launch-council dispatch: {state['dispatchKind']!r}"
            )
            # Button + hero explain the busy state.
            assert state["btnDisabled"] is True, "Launch button not disabled while the council runs"
            assert state["btnText"] == "Council in progress…", (
                f"disabled Launch button reads {state['btnText']!r} — gives no feedback that a "
                "council is running (the founder's NO-FEEDBACK bug), in the REAL side panel"
            )
            assert state["hero"] == "Council in Progress", (
                f"the side-panel hero didn't flip to 'Council in Progress' on launch: {state['hero']!r}"
            )
            # The running panel renders the spinner + heading + cleared prompt.
            assert state["spinnerVisible"], "no spinner-row appeared after launching from the side panel"
            assert state["heading"] == "Council running", f"running heading wrong: {state['heading']!r}"
            assert state["promptVal"] == "", "the composer prompt did not clear on launch"
            # THE side-panel-specific branch: Open council page → in-panel sibling.
            assert state["openVisible"], "no 'Open council page' link in the running panel"
            href = state["openHref"] or ""
            assert href.startswith("./live_council.html?"), (
                f"'Open council page' did NOT resolve to the in-panel ./live_council.html sibling "
                f"(the __TRINITY_HOST_FETCH__ branch a file:// render never takes): {href!r}"
            )
            assert "status_token=launch_" in href, (
                f"the in-panel council link lost its status_token — the live page can't poll: {href!r}"
            )
            assert "/review_pages/" not in href, (
                f"the in-panel council link points at the BLOCKED ../review_pages/ path: {href!r}"
            )
            assert state["stopBtns"] == ["Stop council"], f"Stop council action missing: {state['stopBtns']}"
            # Paint / mount sanity in the panel.
            assert not state["raw"], "raw {{ }} leaked — the app un-mounted during the launch"
            assert state["docW"] <= state["vw"], f"horizontal overflow after launch @393: {state['docW']}>{state['vw']}"
            assert errors == [], f"console errors during the in-panel launch lifecycle: {errors}"
        finally:
            ctx.close()


def test_failed_dispatch_in_panel_rolls_back_with_co_located_error(tmp_path, monkeypatch):
    """A stubbed FAILED dispatch in the REAL side panel must roll back: re-enable the
    Launch button, restore the prompt for retry, clear the spinner, and surface the
    error co-located in the launch-status — never leave the user stuck."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            lf.evaluate(_STUB_FAIL)
            lf.fill("#council-prompt", "why is the sky blue?")
            lf.locator(".actions button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(600)  # the stub resolves onResult after 50ms

            state = lf.evaluate(
                "()=>{"
                "const b=document.querySelector('.actions button.button.primary');"
                "const errEl=document.querySelector('.launch-status .status-error');"
                "const h=[...document.querySelectorAll('h1')].find(e=>e.offsetParent!==null);"
                "return {"
                " btnText:b?b.textContent.trim():null, btnDisabled:b?b.disabled:null,"
                " errText:errEl?errEl.textContent.trim():null,"
                " errVisible:!!(errEl&&errEl.offsetParent!==null),"
                " hero:h?h.textContent.trim():null,"
                " promptRestored:(document.querySelector('#council-prompt')||{}).value,"
                " spinnerGone:!document.querySelector('.launch-status .spinner-row'),"
                " raw:document.body.innerHTML.includes('{{')};}"
            )

            # Button re-enabled (busy cleared) — the stuck-launch bug must not recur.
            assert state["btnDisabled"] is False, (
                "the Launch button stayed DISABLED after a failed dispatch — the stuck-launch "
                "state (.busy never clears) the side panel must roll back"
            )
            assert state["btnText"] == "Launch Council", (
                f"button didn't roll back to 'Launch Council' after the failure: {state['btnText']!r}"
            )
            # Error co-located in the launch-status, not swallowed.
            assert state["errVisible"], "the dispatch failure surfaced NO co-located error in the side panel"
            assert state["errText"] == "stubbed council dispatch failure", (
                f"the co-located error text is wrong/missing: {state['errText']!r}"
            )
            # Prompt restored so the user can retry without retyping.
            assert state["promptRestored"] == "why is the sky blue?", (
                f"the prompt was NOT restored on failure (user must retype): {state['promptRestored']!r}"
            )
            assert state["spinnerGone"], "the running spinner persisted after a failed dispatch"
            assert state["hero"] == "Run a Council", (
                f"the hero didn't roll back from the busy state on failure: {state['hero']!r}"
            )
            assert not state["raw"], "raw {{ }} leaked after the failed dispatch rollback"
            assert errors == [], f"console errors during the failed-dispatch rollback: {errors}"
        finally:
            ctx.close()
