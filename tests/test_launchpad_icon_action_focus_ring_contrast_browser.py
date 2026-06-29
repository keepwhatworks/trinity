"""Browser guard: the launchpad ICON-ACTION focus ring must be VISIBLE to a
keyboard user (WCAG 2.4.7 Focus Visible + 1.4.11 Non-text Contrast, >=3:1).

Found 2026-06-23 (UX sweep) by driving keyboard-focus on the REAL launchpad
settings modal: the `.icon-action` buttons (Copy install command, Reset anonymous
ID, Ingest transcripts, embedder download) painted a focus ring of
`outline: 2px solid rgba(79, 144, 149, 0.28)` — the WORDMARK teal (#4f9095) at 28%
alpha. Composited over the button's own --surface circle (#fafbfc) that ring
resolves to ~rgb(202,221,223) = 1.36:1 against the adjacent surface, FAR below the
3:1 floor a focus indicator must clear. A Tab-using keyboard user (or anyone whose
pointer is unavailable) got a near-invisible ring on the launchpad's only tap
targets for those actions — the classic "I can't tell what's focused" a11y defect.

The sibling icon-button on the SAME template — `.copy-badge:focus-visible` — already
rings SOLID `var(--action)` (#3f777c, 4.89:1 on --surface). The `.icon-action` rule
was the parallel-surface drift. The fix aligns it to the sibling: solid --action.

This guard FOCUSES a real icon-action with the keyboard (so :focus-visible engages),
reads the COMPUTED outline-color the browser actually paints, COMPOSITES any alpha
over the measured background behind the ring, and asserts the contrast clears 3:1.
A pure string/CSS check can't see the composited pixel — only a real engine resolves
the alpha + the layered background.

Mutation-proven: revert the outline back to `rgba(79, 144, 149, 0.28)` and this
reds with "icon-action focus ring 1.36:1 < 3.0".

Slow + browser marked; skips without Playwright/chromium. file:// render path —
NO bundle rebuild needed (this drives the launchpad_template CSS directly).
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _rel_lum(rgb: tuple[float, float, float]) -> float:
    def f(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (f(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = _rel_lum(a), _rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _parse_rgb(css: str) -> tuple[float, float, float, float]:
    """Parse 'rgb(r, g, b)' / 'rgba(r, g, b, a)' → (r, g, b, a)."""
    inner = css[css.index("(") + 1 : css.index(")")]
    parts = [p.strip() for p in inner.split(",")]
    r, g, b = (float(parts[i]) for i in range(3))
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return r, g, b, a


def _composite(
    fg: tuple[float, float, float], alpha: float, bg: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        fg[0] * alpha + bg[0] * (1 - alpha),
        fg[1] * alpha + bg[1] * (1 - alpha),
        fg[2] * alpha + bg[2] * (1 - alpha),
    )


def test_icon_action_keyboard_focus_ring_clears_3to1(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import trinity_local.launchpad_page as lp

    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env gate
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            page.set_viewport_size({"width": 393, "height": 852})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            # The icon-action buttons live in the settings modal (Reset anonymous ID /
            # Ingest transcripts) — open it via the gear, then Tab to the first one so
            # :focus-visible engages exactly as it does for a real keyboard user.
            gear = page.query_selector('button[aria-label="Open settings"]')
            assert gear, "settings gear (aria-label='Open settings') not found"
            gear.focus()
            gear.click()
            page.wait_for_timeout(400)

            visible = page.evaluate(
                """() => [...document.querySelectorAll('.icon-action')]
                    .filter(e => { const r = e.getBoundingClientRect();
                                   return r.width > 0 && r.height > 0; }).length"""
            )
            assert visible >= 1, (
                "no visible .icon-action button after opening settings — the "
                "focus-ring guard cannot run (precondition); the icon-action "
                "render site moved"
            )

            # Keyboard-focus the first visible icon-action (Tab from the gear walks
            # into the modal; we land on an icon-action). Use a real key path so
            # Chromium's :focus-visible heuristic treats it as keyboard focus.
            facts = page.evaluate(
                """() => {
                  const els = [...document.querySelectorAll('.icon-action')]
                      .filter(e => { const r = e.getBoundingClientRect();
                                     return r.width > 0 && r.height > 0; });
                  const el = els[0];
                  el.focus();
                  const cs = getComputedStyle(el);
                  const r = el.getBoundingClientRect();
                  // sample the background just OUTSIDE the button (where the
                  // outline-offset ring is painted): first non-transparent bg in
                  // the stack at a point left of the button, vertically centered.
                  const px = r.left - Math.max(2, parseFloat(cs.outlineOffset) || 2);
                  const py = r.top + r.height / 2;
                  let bg = null;
                  for (const node of document.elementsFromPoint(px, py)) {
                    const c = getComputedStyle(node).backgroundColor;
                    if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') { bg = c; break; }
                  }
                  if (!bg) bg = getComputedStyle(document.body).backgroundColor;
                  return {
                    label: el.getAttribute('aria-label') || el.title || '',
                    focusVisible: el.matches(':focus-visible'),
                    outlineStyle: cs.outlineStyle,
                    outlineWidth: cs.outlineWidth,
                    outlineColor: cs.outlineColor,
                    bgBehind: bg,
                    // the button's OWN fill is the inner adjacent color
                    selfBg: cs.backgroundColor,
                  };
                }"""
            )

            assert facts["focusVisible"], (
                "the focused .icon-action did not match :focus-visible, so the "
                f"focus-ring rule never applied — cannot validate it: {facts}"
            )
            assert facts["outlineStyle"] != "none" and facts["outlineWidth"] != "0px", (
                "the focused .icon-action paints NO outline — a keyboard user has "
                f"no focus indicator at all: {facts}"
            )

            fr, fg, fb, fa = _parse_rgb(facts["outlineColor"])

            # The ring's adjacent colors: the page bg behind it AND the button fill it
            # surrounds. Composite the (possibly-translucent) outline over each and take
            # the WORST contrast — a focus indicator must clear 3:1 against *every*
            # adjacent color (WCAG 1.4.11).
            adjacents = []
            for css_bg in (facts["bgBehind"], facts["selfBg"]):
                try:
                    br, bg_, bb, _ = _parse_rgb(css_bg)
                except Exception:
                    continue
                adjacents.append((br, bg_, bb))

            assert adjacents, f"could not parse any adjacent background: {facts}"

            worst = min(
                _contrast(_composite((fr, fg, fb), fa, adj), adj) for adj in adjacents
            )

            assert worst >= 3.0, (
                f"icon-action focus ring {worst:.2f} < 3.0 — keyboard users get a "
                f"near-invisible focus indicator on {facts['label']!r} "
                f"(WCAG 2.4.7 / 1.4.11 fail). outline={facts['outlineColor']!r} "
                f"over adjacents {adjacents}. The founder symptom: the Tab focus ring "
                f"on Reset-anonymous-ID / Ingest / Copy-install is the wordmark teal "
                f"at 0.28 alpha (1.36:1)."
            )

            assert not errs, f"JS errors during icon-action focus test: {errs[:3]}"
        finally:
            browser.close()
