"""The council COMPOSER in the REAL side panel must BLOCK an empty OR whitespace-only
prompt — surface the inline validation ribbon AND fire NO dispatch — never silently
launch a real (empty) council from the panel.

Why this needs a REAL-panel guard the existing suite doesn't give:
  * `test_launchpad_launch_form.py::test_launch_form_validates_empty_and_forwards_status_token`
    asserts an EMPTY prompt is rejected — but on a `file://` render at the default
    width, and ONLY the literal empty string. It never drives WHITESPACE-only.
  * Every existing real-side-panel test (test_sidepanel_dispatch_lifecycle_browser,
    test_sidepanel_dispatch_banner_in_panel_copy_browser) fills a VALID prompt before
    clicking Launch — none drives the BLOCK path in the opaque-origin sandbox the
    Chrome side panel actually uses, at the 393px panel width.

The whitespace case is the load-bearing one: `launchCouncil` blocks via
`const prompt = this.prompt.trim(); if (!prompt) …`. A refactor that drops the
`.trim()` (keeping the `!prompt` empty-string check) would let "   \t\n  " read as
truthy → a real council dispatches with a whitespace task, and EVERY existing test
stays green because none drives whitespace in the panel and none asserts the dispatch
was SUPPRESSED there. That is Trinity's signature "green while the value is gone" shape
on the most-used control.

This drives the REAL extension (the only thing that exercises the sandbox opaque
origin) with a STUBBED `window.__TRINITY_DISPATCH__` that COUNTS dispatches, clicks
Launch on an empty prompt then a whitespace-only prompt, and asserts for BOTH:
  - the `.status-error` validation ribbon is visible ("Please enter a task first."),
  - dispatch was NEVER called (window.__dispatchCount stays 0 — no empty council),
  - no `{{ }}` leak / no horizontal overflow at the 393px panel width.
Then it types a valid task and asserts the ribbon CLEARS without auto-dispatching.

Mutation-proven: dropping the `.trim()` from launchCouncil in the BUNDLED
sandbox/launchpad-init.js (so whitespace reads truthy) reds the WHITESPACE assertion
with the founder symptom (a whitespace council dispatched from the panel).

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

# A dispatcher that COUNTS dispatches. A blocked (empty/whitespace) launch must
# never reach dispatch() => __dispatchCount stays 0.
_STUB_COUNT = """
() => {
  window.__dispatchCount = 0;
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (o) { window.__dispatchCount++; window.__lastDispatch = o && o.extensionAction; },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host (delegating non-launchpad_data
    queries to the REAL capture host), load the real extension, open the side panel,
    and return (ctx, ext_id, page, errors) after the launchpad mounts."""
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


def _probe(lf):
    return lf.evaluate(
        "()=>{const vw=document.documentElement.clientWidth;"
        "const e=document.querySelector('.launch-status .status-error');"
        "const over=[...document.querySelectorAll('*')]"
        ".filter(x=>x.getBoundingClientRect().right>vw+1).length;"
        "return {errText:e?e.innerText.trim():null,"
        " errVisible:!!(e&&e.offsetParent!==null),"
        " dispatchCount:window.__dispatchCount,"
        " promptVal:(document.querySelector('#council-prompt')||{}).value,"
        " docW:document.documentElement.scrollWidth, vw:vw, over:over,"
        " raw:document.body.innerHTML.includes('{{')};}"
    )


def test_empty_and_whitespace_prompt_blocked_with_no_dispatch_in_panel(tmp_path, monkeypatch):
    """An empty OR whitespace-only Launch in the REAL side panel surfaces the inline
    validation ribbon and fires NO dispatch; a subsequent valid keystroke clears it
    without auto-dispatching."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page, errors = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Precondition: the in-panel opaque-origin path (where the worst bugs hide).
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )
            lf.evaluate(_STUB_COUNT)
            launch = lf.locator(".actions button.button.primary").first

            # CASE A — empty prompt -> blocked, no dispatch.
            lf.evaluate("()=>{const t=document.querySelector('#council-prompt');t.value='';"
                        "t.dispatchEvent(new Event('input',{bubbles:true}));}")
            launch.click(timeout=5000)
            page.wait_for_timeout(400)
            a = _probe(lf)
            assert a["errVisible"], (
                "an EMPTY-prompt Launch in the side panel surfaced NO inline validation "
                "ribbon — the click read as no-feedback (NO-FEEDBACK bug)"
            )
            assert "task" in (a["errText"] or "").lower(), (
                f"the empty-prompt validation ribbon text is wrong/missing: {a['errText']!r}"
            )
            assert a["dispatchCount"] == 0, (
                f"an EMPTY-prompt Launch DISPATCHED a council from the side panel "
                f"(__dispatchCount={a['dispatchCount']}) — an empty council was fired"
            )

            # CASE B — whitespace-only prompt -> STILL blocked, STILL no dispatch.
            # This is the load-bearing case: it relies on launchCouncil's `.trim()`.
            lf.evaluate("()=>{const t=document.querySelector('#council-prompt');"
                        "t.value='   \\t\\n  ';t.dispatchEvent(new Event('input',{bubbles:true}));}")
            launch.click(timeout=5000)
            page.wait_for_timeout(400)
            b = _probe(lf)
            assert b["errVisible"], (
                "a WHITESPACE-ONLY prompt Launch in the side panel surfaced NO inline "
                "validation ribbon — '   ' read as a valid task with no feedback"
            )
            assert b["dispatchCount"] == 0, (
                "a WHITESPACE-ONLY prompt ('   \\t\\n  ') DISPATCHED a real council from the "
                f"side panel (__dispatchCount={b['dispatchCount']}) — launchCouncil's `.trim()` "
                "block was lost, so a blank council was fired from the most-used control"
            )
            # Paint: the ribbon must not break the narrow 393px panel.
            assert not b["raw"], "raw {{ }} leaked — the launchpad app lost its mount"
            assert int(b["docW"]) <= int(b["vw"]) + 1 and not b["over"], (
                f"the validation ribbon overflows the {b['vw']}px panel: "
                f"docW={b['docW']} overflowing={b['over']}"
            )

            # CASE C — a valid keystroke clears the ribbon and does NOT auto-dispatch.
            lf.evaluate("()=>{const t=document.querySelector('#council-prompt');"
                        "t.value='Compare three caching strategies';"
                        "t.dispatchEvent(new Event('input',{bubbles:true}));}")
            page.wait_for_timeout(400)
            c = _probe(lf)
            assert not c["errVisible"], (
                "the 'Please enter a task first.' ribbon stayed pinned under a now-full "
                f"textarea (self-contradicting): {c['errText']!r}"
            )
            assert c["dispatchCount"] == 0, (
                f"typing a valid task AUTO-DISPATCHED a council before Launch was clicked "
                f"(__dispatchCount={c['dispatchCount']})"
            )
            assert errors == [], f"console errors during the in-panel empty/whitespace validation: {errors}"
        finally:
            ctx.close()


if __name__ == "__main__":  # pragma: no cover - manual harness
    sys.exit(pytest.main([__file__, "-v", "-s"]))
