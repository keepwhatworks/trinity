"""Muted `.meta` text dimmed by inline `opacity` must STILL clear WCAG AA (4.5:1) IN
THE REAL SIDE PANEL — measured from the COMPUTED color composited over the real
background AND multiplied by the EFFECTIVE opacity chain, not the bare `color`.

FOUNDER SYMPTOM (panel sweep 2026-06-21): driving the real /stats side panel and
reading the pixels, the memory-health status chips ("STALE", "PRE-THREAD-AWARE") and
the home→/stats "View full stats →" nav link rendered `.meta` (--text-muted #5e666f /
--action #3f777c) with an inline `opacity: 0.7`. The palette was deliberately pushed
so --text-muted/--action clear AA when OPAQUE (~5.3 / ~4.9:1) — but the extra
`opacity: 0.7` double-dims them to ~2.7-2.9:1, well below the 4.5 body floor, on
exactly the small functional labels a user must read (a status word, a nav link).

ROOT CAUSE / CLASS: stacking inline `opacity` on `.meta` text whose color token is
already AA-calibrated as the de-emphasis primitive. The opacity was a SECOND,
uncalibrated dimming layer. Fix = drop the redundant `opacity` from every `.meta`
text site (14 in launchpad_template.py); the --text-muted token IS the de-emphasis.

WHY THE EXISTING GUARD WAS BLIND: test_meta_text_clears_aa_on_tinted_cards_in_panel
samples `getComputedStyle(el).color` — which is the OPAQUE token color; `opacity` is a
SEPARATE property the GPU applies at composite time and is NOT folded into `color`. So
that guard composited #5e666f as fully opaque (~5.3:1, passing) while the real pixels
were 2.9:1. This guard reads the OPACITY CHAIN (every ancestor's opacity multiplied)
and folds it into the foreground the way the GPU does — the missing dimension.

Mutation-proven: re-add `opacity: 0.7` to the memory-health status `<span class=meta>`
(or to the stats nav link), rebuild the sidepanel bundle, and this reds (~2.9). Drop
it and it clears (~5.3).

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
    """Seed a synthetic home with a STALE core memory (so the memory-health card and
    its status chips render), stub the native host, load the real extension, open the
    side panel, return (ctx, ext_id, page) after the launchpad mounts."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    # Make core.md older than lens.md so _memory_health surfaces a "stale" issue —
    # that's what renders the status chips this guard measures.
    core = home / "core.md"
    if core.exists():
        old = time.time() - 10000
        import os

        os.utime(core, (old, old))

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
    page.wait_for_timeout(4000)
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


def _over(top, bot, alpha=None):
    a = alpha if alpha is not None else (top[3] if len(top) > 3 else 1.0)
    return [top[i] * a + bot[i] * (1 - a) for i in range(3)]


# Pull EVERY visible .meta node, with its computed color, font metrics, the full
# ancestor background stack, AND the EFFECTIVE opacity chain (each ancestor's opacity
# multiplied) — the dimension getComputedStyle(el).color hides and the prior guard
# never read.
_SAMPLE = r"""() => {
  function vis(el){ const r = el.getBoundingClientRect();
    return r.height > 0 && r.width > 0 && el.offsetParent !== null; }
  function effAlpha(el){ let a = 1, e = el;
    while (e) { const o = parseFloat(getComputedStyle(e).opacity);
      if (!isNaN(o)) a *= o; e = e.parentElement; } return a; }
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  return [...document.querySelectorAll('p.meta, span.meta, .meta')]
    .filter(vis)
    .map(el => { const s = getComputedStyle(el);
      return { txt: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 40),
               color: s.color, fs: parseFloat(s.fontSize), fw: s.fontWeight,
               ea: effAlpha(el), bgs: stack(el) }; });
}"""


def _below_aa(frame):
    """Every visible `.meta` whose EFFECTIVE (opacity-folded) contrast is below its AA
    floor (4.5 normal, 3.0 large), measured from the real composite."""
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
        # Fold the effective opacity chain into the foreground the way the GPU does.
        fgc = _over(fg, bg, d["ea"])
        r = _contrast(fgc, bg)
        large = d["fs"] >= 18.66 or (d["fs"] >= 14 and int(d["fw"]) >= 700)
        floor = 3.0 if large else 4.5
        if r < floor - 0.02:  # small float-composite tolerance
            key = (d["txt"], round(d["ea"], 2))
            if key in seen:
                continue
            seen.add(key)
            rows.append((round(r, 3), d["fs"], round(d["ea"], 2), d["txt"], [round(x) for x in bg]))
    return rows


def test_meta_opacity_does_not_dim_below_aa_in_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel (HOME + /stats), no visible `.meta` text may
    fall below WCAG AA once its inline `opacity` is folded in — the dimension the
    color-only tinted-card guard was blind to. (Founder symptom: memory-health status
    chips + the 'View full stats →' nav link painted at ~2.9:1 because `opacity: 0.7`
    double-dimmed the AA-calibrated --text-muted/--action tokens.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # HOME first — the "View full stats →" nav link lives on the home view.
            home_link = lf.evaluate(
                "() => [...document.querySelectorAll('p.meta a')].some(a => /View full stats/.test(a.textContent))"
            )
            assert home_link, (
                "PRECONDITION: the home→/stats 'View full stats →' .meta nav link never "
                "rendered — the contrast sample would miss the link the founder symptom "
                "named (seed/payload regression?)."
            )
            bad_home = _below_aa(lf)
            assert not bad_home, (
                "panel HOME: .meta text below WCAG AA 4.5 once inline opacity is folded "
                "in (the 'View full stats →' nav link painted at ~2.8:1 under opacity:0.7 "
                "over the AA-calibrated --action token): "
                + "; ".join(f"{r:.2f}:1 @{fs}px α={ea} '{t}' on rgb{bg}" for r, fs, ea, t, bg in bad_home)
            )

            # Flip to /stats — the memory-health card + its status chips live here.
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(1000)
            sf = page.frames[-1]

            mh = sf.evaluate(
                "() => { const c = document.querySelector('section.card.memory-health-card');"
                "  return !!c && [...c.querySelectorAll('span.meta')].some(s => /STALE|MISSING|STALE|AWARE|PENDING|DRIFT/i.test(s.textContent)); }"
            )
            assert mh, (
                "PRECONDITION: the memory-health card + its status chips ('STALE' etc.) "
                "never rendered on /stats — the contrast sample would be vacuous (the "
                "stale-core seed didn't take?)."
            )
            bad_stats = _below_aa(sf)
            assert not bad_stats, (
                "panel /stats: .meta text below WCAG AA 4.5 once inline opacity is folded "
                "in (memory-health status chips 'STALE'/'PRE-THREAD-AWARE' painted at "
                "~2.9:1 under opacity:0.7 over the AA-calibrated --text-muted token): "
                + "; ".join(f"{r:.2f}:1 @{fs}px α={ea} '{t}' on rgb{bg}" for r, fs, ea, t, bg in bad_stats)
            )
        finally:
            ctx.close()
