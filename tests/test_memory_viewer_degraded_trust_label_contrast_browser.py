"""The memory viewer's "DEGRADED" lens-trust label MUST clear WCAG AA (4.5:1) —
measured from the COMPUTED color over the full composited background.

FOUNDER SYMPTOM (memory-viewer sweep 2026-06-22): driving the REAL memory viewer
in the no-`[mlx]` state — the common path for any user who installed Trinity
without the embedder extras, so `lens_health._embedding_backend()` returns
DEGRADED — and reading the pixels, the lens-TRUST banner over lens.md painted its
"degraded" status label (`.viewer-trust-label`) in `--danger` #bd6a5a at 11px
uppercase over an EFFECTIVE background of ~rgb(230,223,224) (the banner's
`rgba(189,106,90,0.10)` terracotta tint composited over the page). That lands at
2.98:1 — FAR below the 4.5 AA-normal floor for body text. The terracotta that
READS as a fill (the 3px border-left) is unreadable as the one-word status tag
that tells the user their WHOLE lens is a keyword caricature, not their taste.

ROOT CAUSE / CLASS: `--danger` (#bd6a5a) was doing double duty as BOTH a fill color
(border-left/icon — fine, no contrast floor) AND readable text (2.8–3.3:1 on the
danger tint). The `.badge.danger` shared-component label had the same defect (3.26:1).
Their siblings `.badge.success`/`.badge.warning` were ALREADY split into deepened
`--success-text`/`--warning-text` for exactly this reason; `--danger` was the lone
tint color with no `--danger-text`. Fix: a new `--danger-text` deep terracotta
(#99392c, 5.0–5.9:1 on every relevant tint) for the small-text sites; `--danger`
stays the fill.

This renders the REAL memory viewer with the embedder forced to DEGRADED (the dev
env has MLX, so the live probe returns "ok" and the trust banner never paints —
that's WHY this cell was never driven), serves it over file://, opens lens.md,
samples the COMPUTED color + the entire ancestor background stack of the rendered
"degraded" label, composites it the way the GPU does, and asserts the real
ratio >= 4.5.

Mutation-proven: point `.viewer-trust-label`'s `color:` back at `var(--danger)`
(the un-deepened fill) → this reds (~2.98). `--danger-text` clears it (~5.37). The
"label paints" + "label reads 'degraded'" preconditions pass FIRST so the bite is
the contrast, not a vacuous green.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
NB: the memory viewer is a portal page (memory.html), NOT part of the launchpad /
sidepanel bundle, so this guard renders src directly — no rebuild needed.
"""
from __future__ import annotations

import http.server
import re
import socketserver
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_degraded_viewer(home: Path) -> str:
    """Render the REAL memory viewer with the embedder forced to DEGRADED so the
    lens-TRUST banner (and its 'degraded' label under test) actually paints."""
    import sys

    if str(REPO / "src") not in sys.path:
        sys.path.insert(0, str(REPO / "src"))
    import os

    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    (home / "memories").mkdir(parents=True, exist_ok=True)
    # A realistic lens.md so the viewer renders the lens file (the trust banner is
    # TRUST_SCOPED to the semantic files: lens.md / topics.json / core.md / generators.md).
    (home / "memories" / "lens.md").write_text(
        "# Lens — paired tensions\n\n"
        "## Concrete over abstract\n"
        "You reach for a worked example before a definition.\n\n"
        "- ACCEPT: \"Here's the exact diff, with the failing test above it.\"\n"
        "- REJECT: \"In general, one might consider the trade-offs involved.\"\n",
        encoding="utf-8",
    )

    from trinity_local import lens_health as lh
    from trinity_local import memory_viewer as mv

    class _FakeBackend:
        status = lh.DEGRADED
        summary = (
            "Built on the TF-IDF keyword fallback — not real semantic vectors, so "
            "these tensions are a keyword caricature of your taste, not its meaning."
        )
        fix = "pip install 'trinity-local[mlx]' && trinity-local lens-build"

    orig = lh._embedding_backend
    lh._embedding_backend = lambda: _FakeBackend()
    try:
        html = mv.render_memory_viewer_html()
    finally:
        lh._embedding_backend = orig
    out = home / "memory.html"
    out.write_text(html, encoding="utf-8")
    return html


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


def _measure(d):
    fg = _parse_rgba(d["color"])
    bg = [255.0, 255.0, 255.0]
    for b in reversed(d["bgs"]):
        pv = _parse_rgba(b)
        if pv and (len(pv) < 4 or pv[3] > 0):
            bg = _over(pv, bg)
    fgc = _over(fg, bg)
    return _contrast(fgc, bg), [round(x) for x in bg]


# Sample the rendered "degraded" label: its computed color + the full ancestor
# background stack (so we composite the EXACT painted background, not a guess).
_SAMPLE = r"""() => {
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  const label = document.querySelector('.viewer-trust-label');
  if (!label) return null;
  const r = label.getBoundingClientRect();
  const s = getComputedStyle(label);
  return { txt: (label.innerText || '').trim().toLowerCase(),
           color: s.color, fs: parseFloat(s.fontSize),
           visible: r.width > 0 && r.height > 0 && label.offsetParent !== null,
           bgs: stack(label) };
}"""


@pytest.mark.parametrize("width", [375, 393, 768])
def test_degraded_trust_label_clears_aa(tmp_path, width):
    """In the REAL memory viewer (no-[mlx] DEGRADED state), the lens-TRUST banner's
    "degraded" status label must clear WCAG AA 4.5:1 — measured from the COMPUTED
    color composited over the real background. (Founder symptom: the --danger #bd6a5a
    label painted at 2.98:1 — unreadable-grade — telling the user their lens can't be
    trusted in a color they can barely read.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir(parents=True)
    _render_degraded_viewer(home)

    # Serve over http so file:// vendor-fetch quirks don't matter (the page renders
    # its content from the inlined __TRINITY_MEMORIES__ regardless).
    handler = http.server.SimpleHTTPRequestHandler

    class _Quiet(handler):
        def log_message(self, format, *args):  # match BaseHTTPRequestHandler; silence server log
            del format, args

        def translate_path(self, path):  # serve from the home dir
            from urllib.parse import unquote, urlparse

            rel = unquote(urlparse(path).path).lstrip("/")
            return str(home / rel)

    with socketserver.TCPServer(("127.0.0.1", 0), _Quiet) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with sync_playwright() as sp:
                try:
                    browser = sp.chromium.launch()
                except Exception as exc:  # pragma: no cover - env-dependent
                    pytest.skip(f"no launchable chromium: {exc}")
                try:
                    page = browser.new_context(
                        viewport={"width": width, "height": 900}
                    ).new_page()
                    page.goto(
                        f"http://127.0.0.1:{port}/memory.html?file=lens.md",
                        wait_until="load",
                    )
                    page.wait_for_timeout(700)
                    d = page.evaluate(_SAMPLE)
                finally:
                    browser.close()
        finally:
            httpd.shutdown()

    # PRECONDITION 1 (non-vacuous): the degraded trust banner's label is on screen.
    assert d and d["visible"], (
        f"@{width}px the lens-TRUST 'degraded' label never rendered on lens.md — the "
        "contrast sample would be vacuous (the forced-DEGRADED backend or the "
        "TRUST_SCOPED gate regressed)."
    )
    # PRECONDITION 2: it is in fact the 'degraded' status tag we mean to measure.
    assert "degraded" in d["txt"], (
        f"@{width}px the sampled .viewer-trust-label does not read 'degraded' "
        f"(got {d['txt']!r}) — wrong node / banner regression."
    )

    ratio, eff_bg = _measure(d)
    assert ratio >= 4.5, (
        f"@{width}px the memory viewer's lens-TRUST 'degraded' label is {ratio:.2f}:1 "
        f"(color {d['color']}, {d['fs']}px, effective bg {eff_bg}) — below WCAG AA 4.5 "
        f"for body text. FOUNDER SYMPTOM: the --danger #bd6a5a label painted at 2.98:1, "
        f"unreadable-grade. The status label must use --danger-text (deep terracotta), "
        f"not the --danger fill terracotta (the border-left stays --danger)."
    )
