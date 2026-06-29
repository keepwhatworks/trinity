"""The composer's POLISH HINT must actually APPEAR when a polish task is typed
into the REAL side panel — a live reactive-binding guard, not a string check.

What this covers (the gap the existing suite leaves open):
  * `tests/test_polish_detection.py::TestLaunchpadPolishHint` asserts the hint's
    SOURCE STRINGS are present in the rendered HTML (`get isPolishLike()`,
    `v-if="polishHintVisible"`). It never DRIVES a keystroke, so it can't see
    whether the `polishHintVisible` getter actually FIRES and PAINTS the hint.
  * Every other real-side-panel composer test fills a prompt to drive Launch /
    validation — none types a POLISH task and asserts the in-card hint surfaces.

So a refactor that severs the reactive binding (renames/breaks `isPolishLike`,
makes `polishHintVisible` return a stale `false`, or drops the `@input`
recompute) silences the hint on the most-used control — the composer — while
the string-presence test stays GREEN. The hint is the ONLY surface that tells a
user iteration (Auto-chain) is available for a polish task; losing it silently is
Trinity's signature "green while the value is gone" shape on a useful affordance.

This boots the REAL unpacked extension (the only thing that exercises the sandbox
opaque-origin path the Chrome side panel actually uses), opens the panel at the
393px touch width, and:
  - types a POLISH task ("make this paragraph shorter and clearer") -> asserts the
    "Polish task detected" hint becomes VISIBLE,
  - types a NON-polish task -> asserts the hint HIDES (not a stuck always-on hint),
  - asserts the hint paints clean at 393px (no horizontal overflow, no {{ }} leak),
  - asserts typing fires NO dispatch (the hint is passive — window.__dispatchCount==0).

Mutation-proven: forcing `polishHintVisible` to `false` in the BUNDLED
sandbox/launchpad-init.js reds the FIRST assertion with the founder symptom (a
typed polish task surfaced no hint in the panel).

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

# A dispatcher that COUNTS dispatches — typing must never fire one (the hint is
# passive UI; it must not reach dispatch()).
_STUB_COUNT = """
() => {
  window.__dispatchCount = 0;
  window.__TRINITY_DISPATCH__ = {
    dispatch: function () { window.__dispatchCount++; },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""

_PROBE = r"""
() => {
  const vw = document.documentElement.clientWidth;
  const hint = [...document.querySelectorAll('p.meta')].find(
    p => p.textContent.includes('Polish task detected'));
  let hintInfo = null;
  if (hint) {
    hintInfo = {
      visible: hint.offsetParent !== null,
      text: hint.textContent.trim(),
      scrollW: hint.scrollWidth,
      clientW: hint.clientWidth,
    };
  }
  const over = [...document.querySelectorAll('*')]
    .filter(x => x.getBoundingClientRect().right > vw + 1).length;
  return {
    vw, docW: document.documentElement.scrollWidth, over,
    hint: hintInfo,
    raw: document.body.innerHTML.includes('{{'),
    dispatchCount: window.__dispatchCount,
  };
}
"""


def _settype(lf, text):
    lf.evaluate(
        "(t)=>{const e=document.querySelector('#council-prompt');e.value=t;"
        "e.dispatchEvent(new Event('input',{bubbles:true}));}", text)


def _boot_panel(p, tmp_path, monkeypatch):
    """Seed a synthetic home, stub the native host (delegating non-launchpad_data
    queries to the REAL capture host), load the real extension, open the side panel,
    return (ctx, ext_id, page) after the launchpad iframe mounts."""
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
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def test_polish_hint_appears_on_polish_task_in_panel(tmp_path, monkeypatch):
    """Typing a polish task into the REAL side-panel composer surfaces the
    'Polish task detected' hint; a non-polish task hides it; the hint paints clean
    and fires no dispatch."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Precondition: the in-panel opaque-origin path (where the worst bugs hide).
            assert lf.evaluate("()=>!!window.__TRINITY_HOST_FETCH__"), (
                "the in-panel host-fetch signal isn't set — not the real sandbox path"
            )
            lf.evaluate(_STUB_COUNT)

            # CASE A — a POLISH task surfaces the hint.
            _settype(lf, "make this paragraph shorter and clearer please")
            page.wait_for_timeout(350)
            a = lf.evaluate(_PROBE)
            assert a["hint"] is not None and a["hint"]["visible"], (
                "a POLISH task ('make this paragraph shorter and clearer') surfaced NO "
                "'Polish task detected' hint in the side-panel composer — the reactive "
                "polishHintVisible binding is dead (the hint that tells the user "
                "Auto-chain is available silently vanished)"
            )
            assert 'Auto-chain' in a["hint"]["text"], (
                f"the polish hint rendered without its Auto-chain guidance: {a['hint']['text']!r}"
            )
            # Passive: typing must not dispatch.
            assert a["dispatchCount"] == 0, (
                f"typing a polish task DISPATCHED something (__dispatchCount={a['dispatchCount']}) "
                "— the hint must be passive"
            )
            # Paint: the hint must not break the narrow 393px panel.
            assert not a["raw"], "raw {{ }} leaked — the launchpad app lost its mount"
            assert int(a["docW"]) <= int(a["vw"]) + 1 and not a["over"], (
                f"the polish hint overflows the {a['vw']}px panel: "
                f"docW={a['docW']} overflowing={a['over']}"
            )
            assert int(a["hint"]["scrollW"]) <= int(a["hint"]["clientW"]) + 1, (
                f"the polish hint text overflows its own box (scrollW={a['hint']['scrollW']} "
                f"> clientW={a['hint']['clientW']}) — an unbreakable run didn't wrap"
            )

            # CASE B — a NON-polish task HIDES the hint (not a stuck always-on hint).
            _settype(lf, "Compare three database indexing strategies for a write-heavy workload")
            page.wait_for_timeout(350)
            b = lf.evaluate(_PROBE)
            b_visible = bool(b["hint"] and b["hint"]["visible"])
            assert not b_visible, (
                "a NON-polish, full-sentence task ('compare three database indexing "
                "strategies …') still showed the 'Polish task detected' hint — the gate "
                "fired on everything, so the hint reads as noise"
            )
        finally:
            ctx.close()


if __name__ == "__main__":  # pragma: no cover - manual harness
    sys.exit(pytest.main([__file__, "-v", "-s"]))
