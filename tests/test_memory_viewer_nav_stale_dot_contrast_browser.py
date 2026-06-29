"""The memory-viewer nav STALE-FILE DOT must clear the WCAG 1.4.11 non-text
contrast floor (3:1) over its real composited background — measured from the
COMPUTED pixels in a real browser.

FOUNDER SYMPTOM (UX sweep 2026-06-23): driving the memory viewer over a seeded home
with a STALE core.md (a `dream --only-distill` skipped, or a bare `lens` run that
leaves vocab/core behind — both organic install states) and reading the nav pixels,
the per-file "needs attention" dot (`.memory-nav-dot`) painted `--warning` #bd9658 —
a 7px ochre circle at 2.74:1 on the white nav (2.46:1 on the active-tab tint), BELOW
the 1.4.11 3:1 floor for a graphical object required to understand the content. The
dot is the ONLY at-a-glance "which files need attention" mark in the nav — its whole
job (per its own CSS comment) is to be spottable BEFORE the user clicks through — so
sub-3:1 defeated its sole purpose for a low-vision user.

ROOT CAUSE / CLASS: `--warning` (#bd9658) is the FILL token (border-left bars, icons —
no contrast floor); the design system already splits a deep `--warning-text` (#79591b)
for the contrast-bearing role (text AND small graphical marks). The launchpad
memory-health row + the viewer's OWN per-file health banner already render this
staleness signal on `--warning-text` (via a 3px border bar); the nav dot was the lone
instance still drawing the sub-3:1 FILL token. Fixed: `.memory-nav-dot` background
`var(--warning)` → `var(--warning-text)` (and `--warning-text` added to the viewer's
local :root, which previously omitted it).

WHY THE EXISTING GUARDS MISSED IT: the GLOBAL contrast guard
(test_contrast_aa_global_browser) collects only TEXT nodes (nodeType === 3) and skips
elements with no direct text — the dot is an empty <span>, so it was invisible to
every contrast guard in the suite (there was no NON-TEXT contrast guard at all). This
is the documented "fill-token used where a contrast-bearing token is required" class
the 2026-06-20/21/22 sweeps repeatedly fixed (--warning/--warning-text,
--success/--success-text, --danger/--danger-text) — the nav dot was the missed sibling.

Mutation-proven: revert `.memory-nav-dot` background to `var(--warning)` → this reds
(~2.46 on the active-tab tint). `var(--warning-text)` clears it (~5.79).

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import importlib.util
import os
import re
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"

_NONTEXT_FLOOR = 3.0  # WCAG 1.4.11 non-text contrast


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_nav_dot", _SEEDER)
    assert spec and spec.loader, "could not load scripts/seed_synthetic_home.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_rgb(s: str) -> tuple[float, float, float, float]:
    """Parse an rgb()/rgba() string → (r, g, b, a) with a in [0,1]."""
    nums = [float(x) for x in re.findall(r"[\d.]+", s)]
    if len(nums) == 3:
        return nums[0], nums[1], nums[2], 1.0
    return nums[0], nums[1], nums[2], nums[3]


def _composite(stack: list[str]) -> tuple[float, float, float]:
    """Composite an ancestor background stack (innermost first) over opaque white,
    folding each layer's alpha — the way the GPU paints it."""
    base = (255.0, 255.0, 255.0)
    # Walk from the outermost opaque layer inward so each over-paints correctly.
    for s in reversed(stack):
        r, g, b, a = _parse_rgb(s)
        if a <= 0:
            continue
        base = tuple(a * f + (1 - a) * bb for f, bb in zip((r, g, b), base))
    return base  # type: ignore[return-value]


def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb: tuple[float, float, float]) -> float:
    r, g, b = (_lin(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    lf, lb = _lum(fg), _lum(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_nav_stale_dot_clears_nontext_contrast_floor(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    mod = _load_seeder()
    mod.seed(home)

    # Make core.md STALE — older than the lens.md it distills — so
    # _memory_health() emits a core.md issue and the nav paints its dot.
    # (An organic state: `dream --only-distill` skipped / a bare `lens` run.)
    core = home / "core.md"
    lens = home / "memories" / "lens.md"
    assert core.exists() and lens.exists(), "seeder must write core.md + lens.md"
    old, new = time.time() - 100_000, time.time()
    os.utime(core, (old, old))
    os.utime(lens, (new, new))

    from trinity_local.launchpad_data import _memory_health

    issue_names = {i.get("name") for i in (_memory_health().get("issues") or [])}
    assert "core.md" in issue_names, (
        "PRECONDITION: the seeded stale core.md must surface a memory-health issue "
        f"so the nav paints a stale dot — got issues for {sorted(issue_names)}"
    )

    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 375, "height": 900})
            page.goto(f"file://{mv}", wait_until="load")
            page.wait_for_timeout(600)

            # PRECONDITION (non-vacuous): the stale dot actually rendered + is
            # visible. Without this a CSS regression that DROPPED the dot would
            # make the contrast assertion pass vacuously.
            sample = page.evaluate(
                """() => {
                  const dot = document.querySelector('.memory-nav-dot');
                  if (!dot) return null;
                  const cs = getComputedStyle(dot);
                  if (cs.display === 'none' || cs.visibility === 'hidden') return null;
                  const fg = cs.backgroundColor;
                  const bgs = [];
                  let el = dot.parentElement;
                  while (el) {
                    bgs.push(getComputedStyle(el).backgroundColor);
                    el = el.parentElement;
                  }
                  return { fg, bgs };
                }"""
            )
            assert sample is not None, (
                "PRECONDITION FAILED: no visible .memory-nav-dot painted on the "
                "stale-file nav — the stale-dot affordance regressed (or the "
                "core.md staleness signal stopped reaching the nav renderer)."
            )

            fg_r, fg_g, fg_b, fg_a = _parse_rgb(sample["fg"])
            assert fg_a > 0, "stale dot background is transparent — undefined CSS var?"
            bg = _composite(sample["bgs"])
            ratio = _ratio((fg_r, fg_g, fg_b), bg)

            assert ratio >= _NONTEXT_FLOOR, (
                "FOUNDER SYMPTOM (UX sweep 2026-06-23): the memory-viewer nav "
                "stale-file dot — the ONLY at-a-glance 'which files need attention' "
                f"mark — drew the FILL token --warning (#bd9658) at {ratio:.2f}:1, "
                f"BELOW the WCAG 1.4.11 {_NONTEXT_FLOOR}:1 non-text floor over its "
                f"composited background {tuple(round(c) for c in bg)}. A low-vision "
                "user can't spot the stale indicator. Use --warning-text (#79591b), "
                "the contrast-bearing token (the per-file banner + launchpad row "
                f"already do). dot fg={sample['fg']}"
            )
        finally:
            browser.close()
