"""The transcript-download SUCCESS status line in the side-panel SHELL must clear WCAG
AA (4.5:1) IN THE REAL SIDE PANEL — measured from the COMPUTED color over the full
composited background.

FOUNDER SYMPTOM (panel sweep 2026-06-22, the 357 conditional-banner sibling class): when
the native host is ABSENT the side panel falls back to its standalone "Download my
transcripts" card. On a successful download, sidepanel-shell.js sets
`status.className = "status ok"` + "Saved N conversation(s) → Downloads/…". That line
painted `--success` #4f9095 (the brand FILL teal) at 12.5px on the panel's `--bg` #eaecef
= **3.08:1**, below the 4.5 AA-normal floor for small readable text. The teal that READS
as a fill (dots/bars/borders) is unreadable as the success-confirmation copy a user must
parse to know the download landed. Its popup sibling (`.status.ok` in popup.html) already
uses the readable `--action-hover` deep teal; this hand-maintained shell twin was missed.

ROOT CAUSE / CLASS: `--success` (#4f9095) is the FILL token — readable as a fill, NOT as
12.5px text on the light panel bg (3.08:1). The fix points `.status.ok` at the readable
`--action-hover` deep teal (#34666b → 5.45:1 on --bg), mirroring popup.html's `.status.ok`.
`--success` stays the fill token. This is structurally invisible to the global
contrast-AA guard, which reads only the INITIAL DOM — `.status.ok` mounts ONLY after a
download-success interaction.

The shell page (sidepanel.html) is hand-maintained (NOT generated), so no bundle rebuild:
the served extension file is the file under test. This boots the genuine
chrome-extension://…/sidepanel.html with NO native host registered (→ detectHost()
returns "absent" → the standalone download card renders), drives the EXACT success line
sidepanel-shell.js runs (className "status ok" + the Saved text), then samples the
COMPUTED color + the full ancestor background stack and asserts the real ratio >= 4.5.

Mutation-proven: point `.status.ok`'s `color:` back at `var(--success)` in
browser-extension/sidepanel.html and this reds (~3.08); `--action-hover` clears it (~5.45).

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"


def _boot_panel_no_host(p, tmp_path, monkeypatch, width=393, height=852):
    """Load the real extension WITHOUT registering a native-messaging host, open the side
    panel, and wait for the host-absent STANDALONE card (#standalone) to render. Returns
    (ctx, ext_id, page)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    ud = tmp_path / "profile"
    ud.mkdir(parents=True, exist_ok=True)
    # NOTE: deliberately NO NativeMessagingHosts manifest — so detectHost()'s `ping`
    # send fails and the shell falls back to initStandalone() (the download card).

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

    page = ctx.new_page()
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    # detectHost() must resolve "absent" and initStandalone() reveal #standalone.
    page.wait_for_selector("#standalone:not([hidden])", timeout=20000)
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


# Drive the EXACT success line sidepanel-shell.js runs on a completed download
# (downloadTranscripts(): status.className = "status ok"; status.textContent = "Saved …"),
# then return the computed color + the full ancestor background stack so the test
# composites the EXACT painted background the way the GPU does. We touch only the
# element's className/text — the CSS rule under test (.status.ok) is unchanged.
_SAMPLE_OK = r"""() => {
  function stack(el){ const o = []; let e = el;
    while (e) { o.push(getComputedStyle(e).backgroundColor); e = e.parentElement; } return o; }
  const status = document.getElementById('dl-status');
  if (!status) return null;
  status.className = 'status ok';
  status.textContent = 'Saved 12 conversation(s) → Downloads/trinity-transcripts.json';
  const r = status.getBoundingClientRect();
  const s = getComputedStyle(status);
  return { txt: (status.textContent || '').slice(0, 40), color: s.color,
           fs: parseFloat(s.fontSize), fw: s.fontWeight,
           visible: r.width > 0 && r.height > 0 && status.offsetParent !== null,
           bgs: stack(status) };
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


def test_download_ok_status_clears_aa_in_panel(tmp_path, monkeypatch):
    """In the REAL side-panel SHELL standalone download card, the SUCCESS status line
    ("Saved N conversation(s) → Downloads/…") must clear WCAG AA 4.5:1 — measured from
    the COMPUTED color composited over the real background. (Founder symptom: the .status.ok
    line painted --success #4f9095 brand teal at 3.08:1, sub-AA, exactly when a download
    SUCCEEDS.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, _ext_id, page = _boot_panel_no_host(p, tmp_path, monkeypatch)
        try:
            d = page.evaluate(_SAMPLE_OK)
            # PRECONDITION / non-vacuous: the standalone status node must be on screen.
            assert d and d["visible"], (
                "#dl-status never rendered in the host-absent standalone card "
                "— the .status.ok contrast sample would be vacuous (initStandalone regression?)."
            )
            assert float(d["fw"]) < 700 and d["fs"] <= 16, (
                f"the .status.ok line is no longer the small-text status copy the AA-normal "
                f"floor applies to (got {d['fs']}px/{d['fw']}) — re-check the contrast threshold."
            )

            ratio, eff_bg = _measure(d)
            assert ratio >= 4.5, (
                f"side-panel download-SUCCESS status: '{d['txt']}' is {ratio:.2f}:1 "
                f"(color {d['color']}, {d['fs']}px/{d['fw']}, effective bg {eff_bg}) "
                f"— below WCAG AA 4.5 for small text. FOUNDER SYMPTOM: .status.ok painted "
                f"--success #4f9095 (brand teal) at 3.08:1, sub-AA, exactly when a download "
                f"SUCCEEDS. The success line must use --action-hover (deep teal #34666b), not "
                f"the --success fill teal (mirrors popup.html's .status.ok)."
            )
        finally:
            ctx.close()
