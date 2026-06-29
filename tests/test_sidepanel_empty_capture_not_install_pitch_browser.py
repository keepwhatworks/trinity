"""The side-panel browser-capture EMPTY state must not tell an extension user to
INSTALL the extension they're plainly already inside.

Founder-class defect (the cross-bootstrap card's inExtensionPanel reframe at
launchpad_template.py ~L1622 was applied; this empty-capture TWIN was the missed
sibling). A brand-new extension user opens the side panel before browsing any web
chats, so `browserCapture.has_data == False`. In the panel (`inExtensionPanel`
true — `window.__TRINITY_HOST_FETCH__` is set) the card painted:

  * summary  "Extension not installed — corpus is missing your web chats"
  * body     "…that means installing the v1.6 browser extension…" + a
             copy-the-install-command CTA (`trinity-local install-extension`)

Both are a flat contradiction (the extension is installed — you're in its side
panel) and the copy CTA is a dead affordance (nothing to install). The real gap
is captured CHATS, so the panel must reframe to "browse Claude / ChatGPT / Gemini
to start" and DROP the install pitch + the copy command.

Found 2026-06-22 driving the REAL no-capture side panel. This loads the page as a
real `chrome-extension://` page (sandboxed launchpad iframe + CSP-safe shell) with
a stub native host that answers `launchpad_data` from an empty-conversations home,
and asserts the rendered DOM. The file:// launchpad (extension may be absent) KEEPS
the install pitch — that branch is unchanged and is the negative control here.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import stat
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"


def _stub_host(payload_path: Path) -> str:
    return (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "qk = (msg or {}).get('query_kind')\n"
        f"out = open({str(payload_path)!r}).read().encode() if qk=='launchpad_data' else json.dumps({{'ok':True}}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )


def test_panel_empty_capture_reframes_install_pitch(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Empty-conversations home → browserCapture.has_data == False (a fresh
    # extension user who hasn't browsed a web chat yet). NEVER touches ~/.trinity.
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()

    # PRECONDITION B (discriminating, RENDER-INDEPENDENT): the seed genuinely puts
    # the card in its empty state. If a future change made has_data truthy the
    # empty card wouldn't render at all and the assertions would be vacuous — so
    # pin the data shape from the payload, NOT from the rendered DOM.
    bc = payload["pageData"].get("browserCapture")
    assert bc is not None and bc.get("has_data") is False, (
        f"seed did not produce an EMPTY browser-capture card (has_data must be "
        f"False to exercise the install-pitch reframe); got {bc!r}"
    )

    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(_stub_host(pl), encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(ud), headless=False,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
            )
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
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
            page.wait_for_timeout(4500)  # iframe load + bridge fetch + petite-vue mount

            frame = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert frame, f"the sandboxed launchpad iframe never loaded; frames={[f.url for f in page.frames]}"

            # PRECONDITION A: the page MOUNTED (no raw mustache leak) and we are
            # genuinely in the panel context (inExtensionPanel true) — without this
            # the reframe condition can't have fired and the test would be vacuous.
            mount = frame.evaluate(
                "() => ({"
                " raw: (document.body.innerHTML||'').includes('{{'),"
                " inPanel: !!window.__TRINITY_HOST_FETCH__,"
                "})"
            )
            assert not mount["raw"], "the side-panel launchpad shows raw {{ }} — petite-vue did not mount"
            assert mount["inPanel"], "inExtensionPanel was false in the panel — the reframe could not fire"

            # Expand the browser-capture <details> so its summary + the empty body
            # card are both in the rendered text.
            info = frame.evaluate("""()=>{
              const det = Array.from(document.querySelectorAll('details'))
                .find(d => /Browser capture/i.test(d.innerText||''));
              if (det) det.open = true;
              const card = Array.from(document.querySelectorAll('.browser-capture-card'))
                .find(c => getComputedStyle(c).display !== 'none');
              const summaryText = det ? (det.querySelector('summary')?.innerText||'') : '';
              const cardText = card ? (card.innerText||'') : '';
              // The install-command copy badge: present ONLY in the non-panel pitch.
              const copyBadge = card ? card.querySelector('.copy-badge') : null;
              return {
                summaryText: summaryText.replace(/\\s+/g,' ').trim(),
                cardText: cardText.replace(/\\s+/g,' ').trim(),
                hasInstallCmd: cardText.includes('install-extension'),
                hasCopyBadge: !!copyBadge,
              };
            }""")

            blob = (info["summaryText"] + " " + info["cardText"]).lower()

            # The founder symptom: a card telling an extension user to install the
            # extension they're already inside.
            assert "extension not installed" not in blob, (
                "FOUNDER SYMPTOM: the side-panel browser-capture card says "
                "'Extension not installed' to a user who is INSIDE the installed "
                "extension's side panel (inExtensionPanel true). The empty-capture "
                "card missed the cross-bootstrap card's inExtensionPanel reframe."
            )
            assert not info["hasInstallCmd"] and not info["hasCopyBadge"], (
                "the panel still shows the `trinity-local install-extension` copy CTA "
                "— a dead affordance (the extension is installed; nothing to install). "
                f"hasInstallCmd={info['hasInstallCmd']} hasCopyBadge={info['hasCopyBadge']}"
            )
            # …and it must say the TRUE next step instead.
            assert "no web chats captured yet" in blob, (
                "the panel empty-capture card must reframe to the real next step "
                f"('No web chats captured yet — browse …'); got summary={info['summaryText']!r} "
                f"card={info['cardText'][:160]!r}"
            )
        finally:
            ctx.close()
