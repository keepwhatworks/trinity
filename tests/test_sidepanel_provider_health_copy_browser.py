"""The settings-modal PROVIDER-HEALTH install-copy chip must copy the RIGHT command
IN THE REAL SANDBOXED SIDE PANEL — and the rows must fit the narrowest panel width.

Coverage gap this closes (UX sweep). The provider-health list (the "Providers"
section that appears when a CLI provider is missing — the new-user "I installed the
Chrome extension but not the CLI yet" cockpit state) had real-browser coverage ONLY
on the FILE:// substrate: tests/test_settings_provider_health_browser.py renders a
plain ``launchpad.html`` over http.server at a single 393px width, asserts the rows
render + the copy chip flashes ⧉→✓, but it (a) never reproduces the SANDBOXED iframe
the side panel actually runs in — opaque origin, relaxed CSP for petite-vue, clipboard
governed by the sandbox, the postMessage bridge — exactly the environment a file://
render can NOT reproduce and where the loop's worst bugs have hidden (the nav-broker
"blocked by Chrome", the blank provider row, the #6 spinner — all green on file://,
all real in the panel); (b) never drives the 320px NARROW draggable panel width; and
(c) asserts only that the chip flashes ✓ — NEVER that the string actually written to
the clipboard equals the displayed install command. A chip that flashes ✓ while
copying the wrong (or empty) command is a SILENT LIE the user only discovers when
they paste garbage into their terminal — and that lie is invisible to a ✓-only assert.

This drives the GENUINE ``chrome-extension://…/sidepanel.html`` (the real sandboxed
panel, missing-provider payload injected through the host stub) and asserts, IN THE
PANEL, at BOTH 393 and 320:
  1. the Providers section renders the missing-provider install rows (label · Missing
     badge · install command · copy chip),
  2. each row's install-command code box stays inside the panel viewport — no
     horizontal scroll on the narrowest draggable width, and
  3. clicking a row's copy chip writes the EXACT displayed install command to the
     clipboard (captured via a hooked navigator.clipboard.writeText in the sandboxed
     frame) AND flashes ⧉→✓.

Mutation-proven (see the module test-log): break the @click bind so the chip copies
the wrong string and assertion (3) reds with the "flashed ✓ but copied the wrong
command" symptom; the file:// guard stays GREEN (it never reads the clipboard).

Slow + browser marked; skips cleanly without Playwright/chromium. Registers the stub
native-messaging host ONLY in a temp profile dir (never the real Chrome dir).
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


def _boot_panel_with_missing_providers(p, tmp_path, monkeypatch, *, width=393, height=852):
    """Seed a synthetic home, INJECT a missing-provider health block into the
    launchpad_data payload (this machine has every provider installed, so the natural
    render is hasMissing=False and the Providers section never appears), stub the
    native host (delegating every other query to the REAL capture-host handlers), load
    the real extension, open the side panel, and return (ctx, ext_id, page) once the
    sandboxed launchpad mounts."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_data import _provider_install_help
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()

    # The real install-help map drives the labels + commands, so the fixture shape
    # can't drift from production. The antigravity row is the deliberate stress case:
    # its install command is a no-space-breakable URL token
    # (https://antigravity.google/cli/install.sh) — the worst case for the 320px width.
    def _missing(provider: str, detail: str) -> dict[str, object]:
        label, command = _provider_install_help(provider)
        return {
            "provider": provider, "label": label, "installed": False,
            "detail": detail, "installCommand": command,
        }

    payload["pageData"]["providerHealth"] = {
        "providers": [
            _missing("codex", "codex not found in PATH"),
            _missing("antigravity", "agy not found in PATH"),
        ],
        "missingCount": 2,
        "hasMissing": True,
        "footerNote": (
            "After installing, open a new terminal and run `trinity-local status`. "
            "Trinity will pick up newly installed providers automatically."
        ),
    }

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
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def _open_settings_and_hook_clipboard(lf, page):
    """Hook navigator.clipboard.writeText + execCommand in the SANDBOXED frame so a
    real copy is captured no matter which path copyText takes, then open the gear."""
    lf.evaluate(
        "() => {"
        "  window.__copied = [];"
        "  if (navigator.clipboard) {"
        "    navigator.clipboard.writeText = (t) => { window.__copied.push(String(t)); return Promise.resolve(); };"
        "  }"
        "  const td = document.execCommand && document.execCommand.bind(document);"
        "  document.execCommand = (cmd) => {"
        "    if (cmd === 'copy') {"
        "      const sel = document.getSelection ? String(document.getSelection()) : '';"
        "      if (sel) window.__copied.push(sel);"
        "    }"
        "    return true;"
        "  };"
        "}"
    )
    lf.locator("button[aria-label='Open settings']").first.click()
    page.wait_for_timeout(600)


def _drive_one_width(p, tmp_path, monkeypatch, width):
    ctx, _ext_id, page = _boot_panel_with_missing_providers(
        p, tmp_path, monkeypatch, width=width
    )
    try:
        lf = page.frames[-1]
        assert "sandbox/launchpad.html" in (lf.url or ""), (
            f"launchpad iframe missing in the panel: {lf.url}"
        )
        _open_settings_and_hook_clipboard(lf, page)

        rows = lf.evaluate(
            "(vw) => {"
            "  const list = document.querySelector('.provider-health-list');"
            "  if (!list) return { present: false };"
            "  const items = Array.from(list.querySelectorAll('.provider-health-item')).map(it => {"
            "    const code = it.querySelector('.provider-command code');"
            "    const r = code ? code.getBoundingClientRect() : null;"
            "    return {"
            "      label: (it.querySelector('.provider-health-head strong') || {}).textContent,"
            "      badge: (it.querySelector('.badge') || {}).textContent,"
            "      command: code ? code.textContent : null,"
            "      codeRight: r ? Math.round(r.right) : null,"
            "      hasCopyBtn: !!it.querySelector('.provider-command .icon-action'),"
            "    };"
            "  });"
            "  return {"
            "    present: true, items,"
            "    raw: list.innerText.includes('{{'),"
            "    docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,"
            "    vw,"
            "  };"
            "}",
            width,
        )

        # (1) the missing-provider rows render in the REAL sandboxed panel.
        assert rows["present"], (
            f"[@{width}px] the Providers section is GONE in the REAL side panel for a "
            "missing provider — the new user has no in-settings install affordance "
            "(file:// coverage can't catch a sandbox-only regression here)."
        )
        assert len(rows["items"]) == 2, (
            f"[@{width}px] expected 2 missing-provider rows in the panel, got {rows['items']}"
        )
        labels = {r["label"] for r in rows["items"]}
        assert labels == {"Codex CLI", "Antigravity"}, f"[@{width}px] wrong labels: {labels}"
        for r in rows["items"]:
            assert r["badge"] == "Missing", f"[@{width}px] missing-provider badge wrong: {r}"
            assert r["command"], f"[@{width}px] install command missing for {r['label']}"
            assert r["hasCopyBtn"], (
                f"[@{width}px] no copy chip for {r['label']} — the copy feels unobservable"
            )
        assert not rows["raw"], (
            f"[@{width}px] raw petite-vue template ({{{{ }}}}) leaked in the provider list in the panel"
        )

        # (2) the install-command code box + the whole list stay inside the panel
        # viewport — no horizontal scroll on the narrowest draggable width (320px is
        # NOT exercised by the file:// guard, which only renders 393).
        assert rows["docOverflow"] <= 1, (
            f"[@{width}px] the settings modal added horizontal scroll in the REAL panel "
            f"(documentElement scrollWidth exceeds clientWidth by {rows['docOverflow']}px) — "
            "a missing-provider install row blew past the narrow panel."
        )
        for r in rows["items"]:
            assert r["codeRight"] is not None and r["codeRight"] <= width + 1, (
                f"[@{width}px] the install command for {r['label']} ran off the right edge "
                f"of the panel (code right={r['codeRight']} > vw={width}) — the "
                "no-space install URL didn't wrap inside the narrow panel."
            )

        # (3) clicking the copy chip writes the EXACT displayed install command to the
        # clipboard in the sandboxed frame (NOT just a ✓ flash) AND flashes ⧉→✓. This is
        # the lie the file:// ✓-only assert can't catch: a chip that flashes ✓ while
        # copying the wrong/empty string.
        first_cmd = rows["items"][0]["command"]
        chip = lf.locator(
            ".provider-health-list .provider-health-item .provider-command .icon-action"
        ).first
        before = chip.inner_text().strip()
        assert before == "⧉", f"[@{width}px] copy chip did not start as ⧉ (was {before!r})"
        chip.click()
        page.wait_for_timeout(400)
        result = lf.evaluate(
            "() => ({"
            "  copied: window.__copied || [],"
            "  chipText: (document.querySelector("
            "    '.provider-health-list .provider-health-item .provider-command .icon-action'"
            "  ) || {}).textContent,"
            "})"
        )
        assert first_cmd in result["copied"], (
            f"[@{width}px] the install-copy chip flashed but did NOT write the displayed "
            f"command to the clipboard in the REAL sandboxed panel. Expected {first_cmd!r} "
            f"in the clipboard, got {result['copied']!r} — a ✓ that copies the wrong "
            "command is a silent lie the user only finds when they paste garbage into "
            "their terminal (the file:// ✓-only guard can't see this)."
        )
        assert "✓" in (result["chipText"] or ""), (
            f"[@{width}px] the copy chip did not flash ✓ after a successful copy in the "
            f"panel (chip text {result['chipText']!r})."
        )
    finally:
        ctx.close()


def test_provider_health_copy_chip_copies_right_command_in_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel, the missing-provider install-copy chip writes
    the EXACT displayed command to the clipboard (not just a ✓ flash) and the rows fit
    the 393 AND 320 px panel widths."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for width in (393, 320):
            # Each width gets its own profile/home dir — the persistent context +
            # stub-host NativeMessagingHosts dir can't be reused across two boots.
            _drive_one_width(p, tmp_path / f"w{width}", monkeypatch, width)
