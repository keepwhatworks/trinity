"""Input `::placeholder` text must clear WCAG AA (4.5:1) IN THE REAL SIDE PANEL —
measured from the COMPUTED ::placeholder color composited over the input's own
background, not a source string.

FOUNDER SYMPTOM (panel sweep 2026-06-20): driving the real sandboxed side panel and
reading the pixels, NO `::placeholder` color was ever set in SHARED_CSS, so every
Trinity input inherited the browser DEFAULT placeholder (#757575). That default
composites to only 4.45:1 on the near-white `--surface` (#fafbfc) and 3.89:1 on a
tinted input (#eaecef) — BELOW the 4.5 AA-normal floor the rest of the palette was
pushed to clear (the 2026-06-16 AA push). The placeholders carry INSTRUCTIONAL copy
on Trinity's primary inputs — the main composer ("Ask a council question…"), the
council search box, the refine directive textarea, the Takeout import path — so a
low-vision user can't read the guidance. The popup shell already pinned its own
`textarea#task::placeholder` to --text-muted; the SHARED_CSS surfaces (launchpad +
council) were the asymmetric miss.

The flat-token palette guard (test_palette_meets_wcag_aa_for_body_text) checks
--text-muted as a SOLID color but never the ::placeholder pseudo-element, and a
string-presence test can't tell whether the rule actually WINS in the cascade or
composites above the floor against the real input background. This drives the
genuine chrome-extension://…/sidepanel.html, reads the COMPUTED ::placeholder color
of each real input + its input background, composites it the way the GPU does, and
asserts the real ratio >= 4.5.

Mutation-proven: delete the `::placeholder { color: var(--text-muted) }` rule from
design_system.SHARED_CSS (so inputs fall back to the #757575 browser default),
rebuild, and this reds (~4.45 on the composer, ~4.45 on the search box). The
--text-muted #5e666f rule clears it (~5.62).

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


def _over(top, bot):
    a = top[3] if len(top) > 3 else 1.0
    return [top[i] * a + bot[i] * (1 - a) for i in range(3)]


# Pull every visible input/textarea, its ::placeholder computed color + opacity, and
# the full ancestor background stack so the test composites the EXACT background the
# placeholder text paints over.
_SAMPLE = r"""() => {
  function vis(el){ const r = el.getBoundingClientRect();
    return r.height > 0 && r.width > 0 && el.offsetParent !== null; }
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  return [...document.querySelectorAll('input[placeholder], textarea[placeholder]')]
    .filter(vis)
    .map(el => { const ph = getComputedStyle(el, '::placeholder');
      return { txt: (el.getAttribute('placeholder') || '').replace(/\s+/g, ' ').slice(0, 44),
               color: ph.color, opacity: parseFloat(ph.opacity), bgs: stack(el) }; });
}"""


def _placeholders_below_aa(frame):
    """Return [(ratio, text, eff_bg), …] for every visible placeholder below 4.5:1,
    measured from the real ::placeholder computed color (with its opacity folded in)
    composited over the input's own background stack."""
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
        # Fold the ::placeholder opacity (Firefox default <1; we force 1) into alpha
        op = d["opacity"] if d["opacity"] == d["opacity"] else 1.0
        eff = list(fg[:3]) + [(fg[3] if len(fg) > 3 else 1.0) * op]
        fgc = _over(eff, bg)
        r = _contrast(fgc, bg)
        if r < 4.5:
            key = d["txt"]
            if key in seen:
                continue
            seen.add(key)
            rows.append((round(r, 3), d["txt"], [round(x) for x in bg]))
    return rows


def test_input_placeholders_clear_aa_in_panel(tmp_path, monkeypatch):
    """In the REAL sandboxed side panel, every visible input/textarea placeholder
    must clear WCAG AA 4.5:1 — measured from the COMPUTED ::placeholder color over
    the input's real background, not a source string. Names the founder symptom: the
    unset placeholder fell back to the #757575 browser default (4.45 on --surface)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            # PRECONDITION / BITE: the main composer placeholder must actually be on the
            # page, or the sample is vacuous. (The composer is the primary input — a
            # brand-new user reads "Ask a council question…" before typing anything.)
            composer = lf.evaluate(
                "() => { const e = document.querySelector('#council-prompt');"
                " return e ? (e.getAttribute('placeholder') || '') : ''; }"
            )
            assert "council question" in composer.lower(), (
                "the main composer placeholder ('Ask a council question…') never rendered "
                f"in the panel — the placeholder-contrast sample is vacuous (got {composer!r})."
            )

            bad = _placeholders_below_aa(lf)
            assert not bad, (
                "side panel: input ::placeholder text below WCAG AA 4.5 — the unset "
                "placeholder fell back to the #757575 browser default (4.45 on --surface, "
                "3.89 on a tinted input), below the AA floor the palette was pushed to clear. "
                f"Offenders (ratio, placeholder, effective-bg): {bad}"
            )
        finally:
            ctx.close()
