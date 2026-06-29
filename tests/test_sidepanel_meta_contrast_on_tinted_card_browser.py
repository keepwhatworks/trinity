"""`.meta` body text on the rgba-TINTED accent cards must clear WCAG AA (4.5:1) IN THE
REAL SIDE PANEL — measured from the COMPUTED color + the full composited background.

FOUNDER SYMPTOM (panel sweep 2026-06-19): driving the real sandboxed side panel and
reading the pixels, the capture card's "The extension is installed…" body (and the
stats hero's "← Back to the council" link) painted `--text-muted` #616a73 over an
EFFECTIVE background of ~rgb(226,230,234) — the card's `rgba(43,80,112,0.04)` blue
tint composited over the GREY `--bg-base` (#eaecef), not the near-white `--surface`.
That lands at 4.38:1, BELOW the 4.5 AA-normal floor for body text.

The flat-token palette guard (test_palette_meets_wcag_aa_for_body_text) only checks
`--text-muted` over bg_base / surface / surface_muted as solid colors — where #616a73
squeaks by at 4.65 — so it was BLIND to the tinted-card composite. And a file:// render
can't catch the panel-specific aggravation: the panel SHELL body is grey, so a sandbox
page that inherits/composites differently than file:// would diverge. This drives the
genuine chrome-extension://…/sidepanel.html (the real sandboxed panel), samples the
COMPUTED color + the entire ancestor background stack of the actual rendered `.meta`
node, composites it the way the GPU does, and asserts the real ratio >= 4.5.

Mutation-proven: revert COLORS["text_muted"] to "#616a73", rebuild the extension
sidepanel bundle, and this reds (~4.38 on the capture card). #5e666f clears it (~4.64).

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
    """Seed a synthetic home, stub the native host (delegating every non-launchpad_data
    query to the REAL capture-host handlers), load the real extension, open the side
    panel, and return (ctx, ext_id, page) after the launchpad mounts."""
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
    """Composite `top` (rgb or rgba) over opaque `bot` (rgb)."""
    a = top[3] if len(top) > 3 else 1.0
    return [top[i] * a + bot[i] * (1 - a) for i in range(3)]


# Pulls EVERY visible .meta / .lede / .cold-open node, with its computed color and the
# full ancestor background stack, so the test composites the EXACT background the GPU
# paints under the text — including the panel shell's grey body behind the tinted card.
_SAMPLE = r"""() => {
  function vis(el){ const r = el.getBoundingClientRect();
    return r.height > 0 && r.width > 0 && el.offsetParent !== null; }
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  return [...document.querySelectorAll('p.meta, span.meta, .meta, .lede, .cold-open')]
    .filter(vis)
    .map(el => { const s = getComputedStyle(el);
      return { txt: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 40),
               color: s.color, fs: parseFloat(s.fontSize), fw: s.fontWeight,
               bgs: stack(el) }; });
}"""


def _below_aa(frame):
    """Return [(ratio, fs, text, eff_bg), …] for every visible meta/lede below its AA
    floor (4.5 normal, 3.0 large), measured from the real computed color + composite."""
    rows = []
    seen = set()
    for d in frame.evaluate(_SAMPLE):
        fg = _parse_rgba(d["color"])
        if not fg:
            continue
        bg = [255.0, 255.0, 255.0]
        for b in reversed(d["bgs"]):
            pv = _parse_rgba(b)
            if pv and (len(pv) < 4 or pv[3] > 0):
                bg = _over(pv, bg)
        fgc = _over(fg, bg)
        r = _contrast(fgc, bg)
        large = d["fs"] >= 18.66 or (d["fs"] >= 14 and int(d["fw"]) >= 700)
        floor = 3.0 if large else 4.5
        if r < floor:
            key = d["txt"]
            if key in seen:
                continue
            seen.add(key)
            rows.append((round(r, 3), d["fs"], d["txt"], [round(x) for x in bg]))
    return rows


def test_meta_text_clears_aa_on_tinted_cards_in_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel (HOME + /stats), no visible `.meta` / `.lede`
    body text may fall below its WCAG AA floor — measured from the COMPUTED color
    composited over the real (tinted-over-grey-shell) background, not a source string."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # The capture card carries the canonical tinted-card .meta body. Make the
            # sample non-vacuous: assert it actually rendered before judging contrast.
            has_capture = lf.evaluate(
                "() => [...document.querySelectorAll('section.card')].some(c =>"
                " c.offsetParent !== null &&"
                " /Wire up browser capture|extension is installed|Native-Messaging/i.test(c.innerText))"
            )
            assert has_capture, (
                "the rgba(43,80,112,0.04)-tinted capture card never rendered in the panel "
                "home — the contrast sample would be vacuous (seed/payload regression?)."
            )

            home_bad = _below_aa(lf)
            assert not home_bad, (
                "panel HOME: .meta/.lede body text below WCAG AA 4.5 on a tinted accent "
                f"card (founder symptom: capture-card #616a73 → 4.38 over the blue tint on "
                f"the grey shell). Offenders (ratio, fontpx, text, effective-bg): {home_bad}"
            )

            # Flip to /stats (in-place view toggle) — the stats hero is the same
            # rgba(43,80,112,0.04) tint and carries the "← Back to the council" .meta link.
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(900)
            sf = page.frames[-1]
            stats_bad = _below_aa(sf)
            assert not stats_bad, (
                "panel /stats: .meta/.lede body text below WCAG AA 4.5 on a tinted accent "
                f"card (e.g. the stats-hero '← Back to the council' link). "
                f"Offenders (ratio, fontpx, text, effective-bg): {stats_bad}"
            )
        finally:
            ctx.close()
