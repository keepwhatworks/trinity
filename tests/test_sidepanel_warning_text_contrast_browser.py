"""Failure-message WARNING text must clear WCAG AA (4.5:1) IN THE REAL SIDE PANEL —
measured from the COMPUTED color over the full composited background.

FOUNDER SYMPTOM (panel sweep 2026-06-20): driving the real /stats side panel and
reading the pixels, the bulk-import error banner ("⚠ No exports detected at that
path.") painted `--warning` #bd9658 at 13px over an EFFECTIVE background of
~rgb(240,245,246) — the block's `rgba(79,144,149,0.06)` teal tint composited over the
card. That lands at 2.49:1, FAR below the 4.5 AA-normal floor for body text. The
amber that READS as a fill (a 3px border-left, an icon) is unreadable as the
failure-message body copy a user must parse to know what to fix. Same defect on the
memory-health Refresh/Repair "couldn't dispatch" lines (also `--warning` 12px text).

ROOT CAUSE / CLASS: `--warning` (#bd9658) was doing double duty as BOTH a fill color
(borders/backgrounds — fine, no contrast floor) AND readable text (2.3–2.6:1 on
light). The fix split it the way `--accent`/`--accent-deep` already are: a new
`--warning-text` deep amber (#79591b, 4.84–6.22:1 on every light bg) for the text
sites; `--warning` stays the fill.

The existing panel-contrast guard (test_meta_text_clears_aa_on_tinted_cards_in_panel)
samples only `.meta`/`.lede` nodes — the import error banner is a bare <div>, so it
was BLIND to this. This drives the genuine chrome-extension://…/sidepanel.html, fires
the real import-export-dry-run dispatch (stubbed to return the structured "no exports"
error the CLI prints), then samples the COMPUTED color + the entire ancestor
background stack of the rendered ⚠ banner, composites it the way the GPU does, and
asserts the real ratio >= 4.5.

Mutation-proven: point the import error block's `color:` back at `var(--warning,
#bd9658)`, rebuild the sidepanel bundle, and this reds (~2.49). `--warning-text`
clears it (~5.86).

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


# Locate the rendered ⚠ import-error banner, return its computed color + the full
# ancestor background stack (so the test composites the EXACT painted background).
_SAMPLE_WARN = r"""() => {
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  const cand = [...document.querySelectorAll('div')].find(d =>
    /No exports detected/.test(d.textContent) && /⚠/.test(d.textContent) && d.children.length <= 2);
  if (!cand) return null;
  const r = cand.getBoundingClientRect();
  const s = getComputedStyle(cand);
  return { txt: (cand.innerText || '').replace(/\s+/g,' ').slice(0,40),
           color: s.color, fs: parseFloat(s.fontSize),
           visible: r.width > 0 && r.height > 0 && cand.offsetParent !== null,
           bgs: stack(cand) };
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


def test_import_error_warning_text_clears_aa_in_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel /stats, the bulk-import failure banner's
    WARNING body text must clear WCAG AA 4.5:1 — measured from the COMPUTED color
    composited over the real background, not a source string. (Founder symptom: the
    '⚠ No exports detected…' amber #bd9658 painted at 2.49:1 — unreadable-grade.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # Flip to /stats so the bulk-import card is in view.
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(900)
            sf = page.frames[-1]

            # Stub the dispatcher to return the structured "no exports" error the real
            # CLI prints to stdout on a wrong-folder path (the common mistake) so the
            # ⚠ warning banner actually renders.
            sf.evaluate(
                """() => {
                  window.__TRINITY_DISPATCH__ = {
                    dispatch: (opts) => {
                      opts.onResult({ ok: false, error: '', stdout: JSON.stringify({
                        error: 'No exports detected at that path.',
                        hint: 'Point at an extracted Takeout folder or a conversations.json file.'
                      })});
                    }
                  };
                }"""
            )
            # The bulk-import card lives inside the collapsed "Browser capture"
            # <details> disclosure (only auto-open when the page is BUILT with
            # view=stats; the sandbox bundle is built view=home). Pop it open the way
            # a real user does before the path input is reachable.
            sf.evaluate(
                "() => { const d = document.querySelector('details.demoted-card-wrapper');"
                " if (d) d.open = true; }"
            )
            page.wait_for_timeout(200)

            # Fill the import path + click Probe.
            inp = sf.locator("input[placeholder='/Users/you/Downloads/Takeout']").first
            assert inp.count(), "the bulk-import card / path input never rendered on /stats (seed regression?)"
            inp.fill("/tmp/nonexistent-takeout")
            sf.locator("button:has-text('Probe')").first.click(timeout=5000)
            page.wait_for_timeout(400)

            d = sf.evaluate(_SAMPLE_WARN)
            # PRECONDITION / non-vacuous: the warning banner must actually be on screen.
            assert d and d["visible"], (
                "the ⚠ import-error WARNING banner never rendered after a failed probe "
                "— the contrast sample would be vacuous (dispatch stub / probe flow regression?)."
            )

            ratio, eff_bg = _measure(d)
            assert ratio >= 4.5, (
                f"panel /stats bulk-import failure banner: WARNING body text is {ratio:.2f}:1 "
                f"(text {d['txt']!r}, color {d['color']}, {d['fs']}px, effective bg {eff_bg}) "
                f"— below WCAG AA 4.5 for body text. FOUNDER SYMPTOM: the '⚠ No exports detected…' "
                f"amber #bd9658 painted at 2.49:1, unreadable-grade. The failure-message text must "
                f"use --warning-text (deep amber), not the --warning fill amber."
            )
        finally:
            ctx.close()
