"""The RUNNING-council per-member "Done" status pill must clear WCAG AA (4.5:1) IN THE
REAL SIDE PANEL — measured from the COMPUTED color over the full composited background.

FOUNDER SYMPTOM (panel sweep 2026-06-21): driving the real side panel through a council
launch and reading the pixels, the per-member `.provider-status-badge.done` "Done" pill
painted `--success` #4f9095 (the brand TEAL) over the badge's green tint
`rgba(45,106,79,0.10)` composited on the launch-status teal wash → an effective bg of
~rgb(222,232,230). That lands at **2.92:1**, FAR below the 4.5 AA-normal floor for the
12px/700 small-text label. The three sibling pills (Running 5.45, Queued 5.24, Failed
7.26) all cleared AA — the "Done" pill was the lone unreadable state, exactly when a
member SUCCEEDS.

ROOT CAUSE / CLASS: `--success` was repointed green (#2d6a4f) → teal (#4f9095) in the
brand-mark sweep, but the three green-TINTED success badges (`.provider-status-badge.done`
on the launchpad AND the live council page, plus `.badge.success`) kept their green tint
+ green border while now drawing TEAL text — teal-on-green at 2.9–3.2:1. Fixed the way
`--warning`/`--warning-text` already split: a new `--success-text` deep green (#2d6a4f,
5.1:1 on the success tint) for the text sites; `--success` stays the FILL (bars/dots/
border-left).

The existing running-council guard (test_running_council_card_fits_320px_panel_and_detail
_is_readable) clicks Launch with a stub that never resolves, so every member stays
'pending' → only the "Queued" badge renders; it was BLIND to the 'done' state's contrast.
This drives the genuine chrome-extension://…/sidepanel.html, fires a real Launch (stubbed
so the optimistic council stays RUNNING and the `.provider-status-list` renders), flips a
real member row's badge to `.done`, then samples the COMPUTED color + the entire ancestor
background stack the way the GPU composites it, and asserts the real ratio >= 4.5.

Mutation-proven: point `.provider-status-badge.done`'s `color:` back at `var(--success)`,
rebuild the sidepanel bundle, and this reds (~2.92). `--success-text` clears it (~5.11).

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import re
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"


def _boot_panel(p, tmp_path, monkeypatch, width=393, height=852):
    """Seed a synthetic home, stub the native host, load the real extension, open the
    side panel, return (ctx, ext_id, page) after the launchpad mounts."""
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
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, ext_id, page


def _luminance(rgb):
    lin = [c / 255 for c in rgb]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in lin]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(fg, bg):
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _parse_rgba(s):
    nums = re.findall(r"[\d.]+", s or "")
    return [float(x) for x in nums] if nums else None


def _over(top, bot):
    a = top[3] if len(top) > 3 else 1.0
    return [top[i] * a + bot[i] * (1 - a) for i in range(3)]


# Stub whose dispatch() NEVER calls onResult => the optimistic council operation stays
# 'running' so the per-member status grid renders and the .done badge is measurable.
_STUB_RUNNING = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) { window.__lastDispatch = opts && opts.extensionAction; },
    probe: function () { return Promise.resolve('present'); },
    subscribe: function () { return function () {}; },
  };
}
"""

# Flip the first member row's badge to .done (the state a poll/optimistic update reaches
# when a provider RESPONDS), then return its computed color + ancestor bg stack so the
# test composites the EXACT painted background the way the GPU does. We mutate the badge's
# className only — the CSS rule under test (.provider-status-badge.done) is unchanged.
_SAMPLE_DONE = r"""() => {
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  const list = document.querySelector('.provider-status-list');
  if (!list) return null;
  const rows = [...list.querySelectorAll('.provider-status-row')];
  let badge = null;
  for (const r of rows) { const b = r.querySelector('.provider-status-badge'); if (b) { badge = b; break; } }
  if (!badge) return null;
  badge.className = 'provider-status-badge done';
  badge.textContent = 'Done';
  const rect = badge.getBoundingClientRect();
  const s = getComputedStyle(badge);
  return { txt: badge.textContent, color: s.color, fs: parseFloat(s.fontSize), fw: s.fontWeight,
           visible: rect.width > 0 && rect.height > 0 && badge.offsetParent !== null,
           bgs: stack(badge) };
}"""


def _measure(d):
    fg = _parse_rgba(d["color"])
    bg = [255.0, 255.0, 255.0]
    for b in reversed(d["bgs"]):
        pv = _parse_rgba(b)
        if pv and (len(pv) < 4 or pv[3] > 0):
            bg = _over(pv, bg)
    fgc = _over(fg, bg)
    return _contrast(fgc, bg), [round(x) for x in bg]


def test_done_member_badge_clears_aa_in_running_council_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel, the per-member "Done" status pill that renders
    when a council member SUCCEEDS must clear WCAG AA 4.5:1 — measured from the COMPUTED
    color composited over the real background, not a source string. (Founder symptom: the
    .done pill painted --success #4f9095 teal over its green tint at 2.92:1, the lone
    unreadable member-status badge.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Drive the RUNNING council state (stubbed — never hits a real council, so the
            # optimistic operation stays running and the per-member grid renders).
            lf.evaluate(_STUB_RUNNING)
            lf.fill("#council-prompt", "Compare three database designs for a chat app")
            lf.locator(".actions button.button.primary, button.button.primary").first.click(timeout=5000)
            page.wait_for_timeout(1500)

            d = lf.evaluate(_SAMPLE_DONE)
            # PRECONDITION / non-vacuous: the per-member grid + a real .done badge must be
            # on screen. (If the running grid never rendered the sample would be vacuous.)
            assert d and d["visible"], (
                "the per-member .provider-status-badge never rendered in the running panel "
                "— the .done contrast sample would be vacuous (Launch / running-grid regression?)."
            )
            assert float(d["fw"]) >= 600 and d["fs"] <= 16, (
                f"the .done badge is no longer the small-text pill the AA-normal floor "
                f"applies to (got {d['fs']}px/{d['fw']}) — re-check the contrast threshold."
            )

            ratio, eff_bg = _measure(d)
            assert ratio >= 4.5, (
                f"RUNNING council panel: the per-member 'Done' pill is {ratio:.2f}:1 "
                f"(text {d['txt']!r}, color {d['color']}, {d['fs']}px/{d['fw']}, effective bg {eff_bg}) "
                f"— below WCAG AA 4.5 for small text. FOUNDER SYMPTOM: the .done badge painted "
                f"--success #4f9095 (brand teal) over its green tint at 2.92:1 — the lone unreadable "
                f"member-status pill, exactly when a provider SUCCEEDS. The 'Done' label must use "
                f"--success-text (deep green #2d6a4f), not the --success fill teal."
            )
        finally:
            ctx.close()
