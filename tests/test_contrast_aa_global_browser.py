"""GLOBAL WCAG-AA text-contrast guard (the cross-cutting class-killer).

WHY THIS EXISTS — the palette was pushed to AA per-surface (the 2026-06-16 "push to
AA" + the panel-sweep splits: --warning/--warning-text, --success/--success-text, the
::placeholder pin, the opacity-on-meta drops, the white-on-wordmark-teal pill
repoints). But CONTRAST is a CROSS-CUTTING concern at (surface × state × text-element):
every one of those fixes was found by DRIVING ONE surface (almost always the side
panel), and a text element composited over an rgba TINT — or drawing the WCAG-EXEMPT
WORDMARK teal (--accent #4f9095) as FUNCTIONAL text — on a surface/state the per-surface
pass never drove stays sub-AA. The per-surface contrast tests are all sidepanel-only;
the file:// launchpad / memory-viewer / live-council / review surfaces were never
swept element-by-element. This is the same lesson the GLOBAL overflow guard
(test_long_token_overflow_global_browser) taught: a single guard that drives EVERY
render surface and reads the REAL composited pixels catches the (surface × state) gaps
+ the parallel-surface drift a per-surface census keeps missing.

WHAT IT CAUGHT on its first run (2026-06-22, this iter) — all the SAME class
(the WCAG-exempt WORDMARK teal / a FILL-only token drawn as readable functional text,
or an AA-calibrated token double-dimmed by stacked opacity / a low-alpha tint):
  • memory viewer .memory-name (the file-list label on ALL 7 tabs), .viewer-rebuild-chip,
    .pick-basin, .pick-xlink + ~6 sibling rules — color: var(--accent) (#4f9095, the
    wordmark teal) at 3.08–3.65:1. Repointed every functional-TEXT --accent → --primary
    (#3f777c, the readable teal the viewer already ships); the rebuild chip → --primary-
    hover (#34666b) because its own grey --bg fill pulls --primary to 4.29.
  • launchpad memory-chip <code> (core.md/lens.md/…), the memory-health-list issue <code>,
    the "Inspect →" link — var(--accent) as code/link text at 3.28:1 → --accent-deep.
  • launchpad cross-bootstrap "Cross-bootstrap · optional" eyebrow + the
    INSTALL-extension link + the lens-verdict "rejected"/"ordering" pills + the
    browser-capture / bulk-import eyebrows — color: #7fa0ad (--info, a FILL token: the
    3px border-left + the eval bars) drawn as TEXT at 2.2–2.7:1 → --accent-deep #34666b.
  • launchpad .taste-block-label — rgba(79,144,149,0.7) (the wordmark teal at 0.7 alpha)
    2.3:1, and the browser-capture eyebrow's opacity:0.7 double-dimming --accent-deep
    → dropped the alpha/opacity, used the opaque AA token.
  • live council + launchpad .status-message / .launch-status .button.ghost — --action
    (#3f777c) on the .launch-status TEAL WASH (rgba(79,144,149,0.05)) composites to
    4.08:1 → --action-hover (#34666b, 5.2:1 on the wash). The launchpad twin was the
    parallel-surface drift (its busy operation panel renders the SAME wash).

THE SURFACES (the reachability-gated render_*_html / render_*_page entrypoints,
excluding the dead render_unified_council_page #311) — reuses the GLOBAL overflow
guard's seed/build harness so a NEW surface inherits BOTH guards at once:
  • launchpad HOME + /stats   — render_launchpad_html / render_stats_html
  • live council page         — render_live_council_page (RUNNING / FAILED / COMPLETED)
  • post-hoc review page      — render_review_html
  • memory viewer             — render_memory_viewer_html, every .md/.json tab

THE DOCUMENTED EXEMPTIONS (legitimate, NOT silenced failures — each excluded BY NAME
with a reason; the guard never blanket-skips a sub-AA functional-text node):
  1. The WCAG-EXEMPT WORDMARK / brand mark. The brand teal --accent (#4f9095) is a
     decorative mark, WCAG-exempt as a logotype (1.4.3 / 1.4.11 exception). It is NOT
     used as functional TEXT after this iter's fixes — but if a future wordmark element
     paints it, it's exempt. We do NOT keep a wordmark text node in the swept surfaces,
     so there is nothing to exclude here today; named for completeness + so a reviewer
     knows the brand teal is intentionally not held to 4.5 AS THE MARK.
  2. Decorative connector arrows — .taste-failure-arrow / td.cheat-arrow (the "→" glyph
     between "pole → failure" / "task → winner"). A single non-text connector glyph at
     intentional reduced opacity; the MEANING is carried by the fully-AA text on either
     side (WCAG 1.4.3 — incidental / decorative). Excluded by class.
  3. The hover-revealed copy affordance — descendants of .copy-badge ("Copy"). At rest
     it sits at opacity:0.7 (a quiet corner overlay on a code block) and brightens to
     opacity:1 on :hover AND :focus-visible (keyboard) — progressive disclosure, not a
     persistent low-contrast label. Excluded by ancestor class.
  4. SVG <text> / <tspan> graph labels (the topology force-graph node labels). SVG text
     paints via `fill`/`stroke`/`paint-order`, NOT CSS `color` — the labels use
     `fill: rgba(255,255,255,.92)` with a `stroke: rgba(0,0,0,.7)` halo (high-contrast
     white-on-dark-halo by design). getComputedStyle().color is irrelevant to SVG ink,
     so reading it would FALSE-flag. Excluded by tag.

It composites the FULL ancestor background stack AND folds the EFFECTIVE OPACITY CHAIN
into the foreground (the two dimensions the panel sweep proved matter — a tint under
the text, and stacked `opacity` the GPU applies SEPARATELY from `color`). Floors:
4.5:1 normal, 3.0:1 large (>=18px, or >=14px and bold >=700) — the codebase convention.

Parametrized over (surface, width) so future surfaces are swept automatically.

MUTATION-PROVEN to BITE (recorded in the iter report): reverting any one of the source
fixes above reds EXACTLY that surface's case while the others stay green — e.g.
re-pointing memory_viewer .memory-name `color: var(--primary)` back to `var(--accent)`
reds every mv-* case (~3.65), or pointing .status-message back to `var(--action)` reds
live-running (~4.08); the launchpad/review cases stay green.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Reuse the GLOBAL overflow guard's surface harness (seed + per-surface builders +
# _browser/_serve). Importing it keeps the two global guards on ONE surface list, so a
# new render surface added there is swept by BOTH. The overflow guard overlays long
# tokens into the free-text fields; that's harmless for contrast (it only changes the
# TEXT, never the color tokens), and it means both guards run against identical pages.
import tests.test_long_token_overflow_global_browser as ovf  # noqa: E402

# One representative width per surface (1280 desktop) — contrast is width-independent
# for these token/tint composites (unlike overflow), so a single width keeps the suite
# fast while still driving every (surface × state). 393 is added for the launchpad
# views, where a media query could in principle swap a color at the phone width.
_WIDTHS = [1280, 393]

# AA floors (the codebase convention, matching the per-surface panel guards):
_FLOOR_NORMAL = 4.5
_FLOOR_LARGE = 3.0

# ── DOCUMENTED EXEMPTIONS (see module docstring §1-4) — each is a legitimate non-
# functional-text case, excluded by a tight selector, NOT a silenced sub-AA failure.
# Element-CLASS exemptions (the element's own class contains one of these):
_EXEMPT_OWN_CLASS = (
    "taste-failure-arrow",   # §2 decorative "→" connector glyph (opacity-dimmed)
    "cheat-arrow",           # §2 decorative "→" connector glyph (opacity-dimmed)
)
# ANCESTOR-class exemptions (the element OR an ancestor carries this class):
_EXEMPT_ANCESTOR_CLASS = (
    "copy-badge",            # §3 hover/focus-revealed copy affordance (opacity:0.7 at rest)
)
# Tag exemptions (SVG text paints via fill/stroke, not CSS color — §4):
_EXEMPT_TAGS = ("text", "tspan")


# The DOM walk: every element with DIRECT visible text, its computed color + font, the
# full ancestor background stack, and the effective opacity chain — PLUS the exemption
# flags so the Python side can attribute a skip to a documented reason. Mirrors the
# per-surface panel guards' composite math, generalized to ALL text + ALL surfaces.
_SAMPLE = r"""(args) => {
  const [exemptOwn, exemptAnc, exemptTags] = args;
  function vis(el){
    const r = el.getBoundingClientRect();
    if (r.height <= 0 || r.width <= 0) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    return true;
  }
  function directText(el){
    let t = '';
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.textContent;
    return t.replace(/\s+/g, ' ').trim();
  }
  function clsOf(el){
    return (el.className && el.className.toString) ? el.className.toString() : '';
  }
  function hasAnc(el, names){
    let e = el;
    while (e) { const c = clsOf(e); for (const n of names) if (c.indexOf(n) !== -1) return true; e = e.parentElement; }
    return false;
  }
  function bgStack(el){ const o=[]; let e=el; while(e){ o.push(getComputedStyle(e).backgroundColor); e=e.parentElement; } return o; }
  function opChain(el){ let op=1; let e=el; while(e){ const v=parseFloat(getComputedStyle(e).opacity); if(!isNaN(v)) op*=v; e=e.parentElement; } return op; }
  const out=[];
  document.querySelectorAll('*').forEach(el => {
    const txt = directText(el);
    if (!txt) return;
    if (!vis(el)) return;
    const tag = el.tagName.toLowerCase();
    if (exemptTags.indexOf(tag) !== -1) return;          // §4 SVG text
    const own = clsOf(el);
    let exOwn = false; for (const n of exemptOwn) if (own.indexOf(n) !== -1) exOwn = true;
    if (exOwn) return;                                   // §2 decorative arrow
    if (hasAnc(el, exemptAnc)) return;                   // §3 copy-badge subtree
    const s = getComputedStyle(el);
    // WCAG exempts disabled controls from contrast.
    const disabled = el.disabled === true || el.getAttribute('aria-disabled') === 'true';
    if (disabled) return;
    out.push({
      tag, cls: own.slice(0,50), txt: txt.slice(0,45),
      color: s.color, fs: parseFloat(s.fontSize), fw: s.fontWeight,
      bgs: bgStack(el), op: opChain(el),
    });
  });
  return out;
}"""


def _parse(s):
    import re
    nums = re.findall(r"[\d.]+", s or "")
    return [float(x) for x in nums] if nums else None


def _over(top, bot):
    a = top[3] if len(top) > 3 else 1.0
    return [top[i] * a + bot[i] * (1 - a) for i in range(3)]


def _lum(rgb):
    lin = [c / 255 for c in rgb]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in lin]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(fg, bg):
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _below_aa(rows):
    """Return [(ratio, fontpx, fontweight, tag, class, text, eff_bg), …] for every
    visible text element below its WCAG AA floor — measured from the COMPUTED color
    composited over the full ancestor background stack, with the effective opacity
    chain folded into the foreground the way the GPU composites it."""
    out, seen = [], set()
    for d in rows:
        fg = _parse(d["color"])
        if not fg:
            continue
        bg = [255.0, 255.0, 255.0]
        for b in reversed(d["bgs"]):
            pv = _parse(b)
            if pv and (len(pv) < 4 or pv[3] > 0):
                bg = _over(pv, bg)
        op = d["op"]
        fg_op = fg[:3] + ([fg[3] * op] if len(fg) > 3 else [op])
        fgc = _over(fg_op, bg)
        r = _contrast(fgc, bg)
        large = d["fs"] >= 18 or (d["fs"] >= 14 and int(d["fw"]) >= 700)
        floor = _FLOOR_LARGE if large else _FLOOR_NORMAL
        # 0.05 tolerance for sub-pixel/rounding noise in the composite, matching the
        # per-surface panel guards (they assert >= floor on the rounded ratio).
        if r < floor - 0.05:
            key = (d["cls"], d["txt"][:24], round(r, 1))
            if key in seen:
                continue
            seen.add(key)
            out.append((round(r, 2), d["fs"], d["fw"], d["tag"], d["cls"],
                        d["txt"], [round(x) for x in bg]))
    return out


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.parametrize("surface_id,builder,settle_ms", ovf._SURFACES,
                         ids=[s[0] for s in ovf._SURFACES])
def test_no_sub_aa_text(surface_id, builder, settle_ms, width):
    pytest.importorskip("playwright.sync_api")

    home = Path(tempfile.mkdtemp(prefix=f"trinity-aa-{surface_id}-"))
    httpd = None
    sp, browser = ovf._browser()
    try:
        url, httpd = builder(home)
        page = browser.new_context(
            viewport={"width": width, "height": 1400}
        ).new_page()
        page.goto(url)
        page.wait_for_timeout(settle_ms)
        rows = page.evaluate(
            _SAMPLE, [list(_EXEMPT_OWN_CLASS), list(_EXEMPT_ANCESTOR_CLASS), list(_EXEMPT_TAGS)]
        )
        braces = page.evaluate("() => document.body.innerText.includes('{{')")
        page.close()
    finally:
        browser.close()
        sp.stop()
        if httpd is not None:
            httpd.shutdown()

    # PRECONDITION A: petite-vue (where present) mounted — raw mustache means the
    # bindings never ran, so the computed colors are unbound template text and the
    # contrast check is vacuous.
    assert not braces, (
        f"[{surface_id} @{width}] raw petite-vue '{{{{ }}}}' leaked — the page never "
        "mounted, so the contrast check is vacuous"
    )

    # PRECONDITION B: the page actually rendered a meaningful amount of text (a blank
    # render would pass with 0 offenders). Every surface paints well over a dozen text
    # nodes when populated; floor at 8 so a seed/render regression can't green this.
    assert len(rows) >= 8, (
        f"[{surface_id} @{width}] only {len(rows)} visible text element(s) sampled — "
        "the seed/render path looks broken; a near-blank page would pass the contrast "
        "assertion vacuously. Fix the fixture before trusting the result."
    )

    # THE BITE: no visible FUNCTIONAL text element may fall below its WCAG AA floor,
    # measured from the COMPUTED color composited over the real (possibly rgba-tinted)
    # background with the opacity chain folded in. The founder symptom this iter: the
    # WCAG-exempt WORDMARK teal --accent #4f9095 (and the FILL-only --info #7fa0ad) were
    # drawn as readable functional text at 2.2–3.65:1 on surfaces the per-surface AA
    # push never drove. Each offender below is a real sub-AA readability bug; deepen the
    # text to the AA-readable token (--primary / --accent-deep / --action-hover), keeping
    # the brand teal as the fill — do NOT add it to the exemption list to green this.
    bad = _below_aa(rows)
    assert not bad, (
        f"[{surface_id} @{width}] visible text below its WCAG AA floor "
        f"({_FLOOR_NORMAL} normal / {_FLOOR_LARGE} large), measured from the COMPUTED "
        f"color composited over the real tinted background + opacity chain. Offenders "
        f"(ratio, fontpx, weight, tag, class, text, effective-bg):\n  "
        + "\n  ".join(repr(b) for b in bad)
    )
