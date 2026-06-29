"""Browser guard: the browser-capture <details> disclosure must not leak a DEAD
"expand" affordance onto the HOME launchpad in its non-stale (healthy / empty) states.

UX sweep iter (2026-06-22) — found driving the REAL Chrome side panel populated
home at 393px. The browser-capture / import-export surface is a <details> whose
INNER cards carry the `stats-card` view-class (so the home/stats split,
`.lp-view-home .stats-card{display:none}`, hides them on home). But the <details>
WRAPPER (`.demoted-card-wrapper`) carried NO stats-card token, so on home its
clickable <summary> — "Browser capture · No web chats captured yet … · expand" —
stayed VISIBLE while every non-stale child was display:none. Tapping "expand" on
home opened the disclosure to NOTHING: a dead-end affordance that invited an
interaction and produced no observable change.

The author's own intent (the `details_open` comment in launchpad_template.py)
assumed the whole surface was hidden on home ("On home those cards are
display:none … so the attribute is a no-op") — true for the inner cards, false
for the summary that leaked.

This is the un-addressed SIBLING of UX iter 82, which made only the STALE state
(silent capture breakage + the #147 Repair CTA) reachable on home. The
healthy/empty disclosure was left leaking a summary that expands to an empty panel.

THE FIX: the <details> wrapper now gets the `stats-card` token CONDITIONALLY via
`:class="{ 'stats-card': !pageData.browserCapture.stale }"` — so in the healthy /
empty states the WHOLE disclosure (summary included) is hidden on home, while the
STALE state keeps no stats-card token (stays reachable on home, per iter 82). All
states keep the wrapper on /stats (force-open, cards visible).

This guard drives the REAL home + stats renders in the HEALTHY state and reads the
rendered DOM (offsetParent visibility), NOT a source string. The wrapper stays in
the DOM on home (string-presence tests stay green) — the assertion is that it is
NOT VISIBLE there, and IS visible/usable on /stats.

Mutation-proven: drop the `:class` stats-card conditional from the bundled
sandbox/launchpad.html → the home summary leaks back visible → the home assertion
REDS with the founder symptom ("expand on home opens an empty panel"). The
present-on-home + visible-on-stats preconditions pass FIRST, so the bite is the
home-visibility, not a vacuous "wrapper missing".

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_healthy(home: Path) -> Path:
    """Render the REAL portal pages (home launchpad.html + stats.html) with a few
    seeded captures whose mtime is ~1h ago (stale=False → the healthy analytics
    state, where the disclosure is stats-only and must be fully hidden on home)."""
    conv = home / "conversations" / "claude"
    conv.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (conv / f"conv{i}.json").write_text(
            json.dumps({"id": f"conv{i}", "messages": [{"role": "user", "content": "hi"}]}),
            encoding="utf-8",
        )
    recent = time.time() - 3600  # ~1h ago → has_data, NOT stale
    for f in conv.glob("*.json"):
        os.utime(f, (recent, recent))
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "launchpad.html").exists() and (pages / "stats.html").exists()
    return pages


_PROBE = """() => {
  const det = document.querySelector('details.demoted-card-wrapper');
  const summ = det && det.querySelector('summary');
  // Try to expand — the founder symptom is the user clicking "expand".
  if (det) det.open = true;
  const cards = [...document.querySelectorAll('section.browser-capture-card')];
  return {
    wrapperPresent: !!det,
    wrapperVisible: det ? det.offsetParent !== null : null,
    summaryVisible: summ ? summ.offsetParent !== null : null,
    summaryText: summ ? summ.textContent.replace(/\\s+/g, ' ').trim().slice(0, 120) : '',
    anyCardVisible: cards.some(c => c.offsetParent !== null
        && getComputedStyle(c).display !== 'none'),
    viewClass: (document.querySelector('.launchpad-shell') || {}).className || '',
  };
}"""


def _probe(pages: Path, page_name: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--headless=new"])
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 393, "height": 852}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / page_name}")
            page.wait_for_timeout(1000)
            d = page.evaluate(_PROBE)
            d["errors"] = errs
            return d
        finally:
            browser.close()


def test_healthy_browser_capture_disclosure_is_not_a_dead_end_on_home():
    """HEALTHY state (captures flowing, not stale): the demoted browser-capture
    <details> is stats-only, so on HOME the WHOLE disclosure — including its
    clickable <summary> — must be hidden. A visible "expand" summary whose every
    child is display:none on home is a dead-end affordance (tap → empty panel).
    On /stats the same disclosure must be present and its card usable."""
    pytest.importorskip("playwright.sync_api")
    tmp = Path(__import__("tempfile").mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    pages = _render_healthy(home)

    home_v = _probe(pages, "launchpad.html")
    stats_v = _probe(pages, "stats.html")

    assert not home_v["errors"], f"JS errors on home: {home_v['errors'][:3]}"
    assert "lp-view-home" in home_v["viewClass"], f"home not in home view: {home_v['viewClass']!r}"
    assert "lp-view-stats" in stats_v["viewClass"], f"stats not in stats view: {stats_v['viewClass']!r}"

    # PRECONDITION 1 (bite, NOT vacuous): the wrapper IS in the home DOM — this is a
    # VISIBILITY test, not a removal. If the wrapper vanished entirely the home
    # assertion below would pass for the wrong reason.
    assert home_v["wrapperPresent"], (
        "the browser-capture <details> wrapper is absent from the home DOM — the "
        "disclosure surface was removed, not hidden; this guard would be vacuous"
    )
    # PRECONDITION 2 (bite): on /stats the disclosure is present, visible, and its
    # card is usable — the surface still exists, just relocated off home.
    assert stats_v["wrapperVisible"] and stats_v["summaryVisible"] and stats_v["anyCardVisible"], (
        "the browser-capture disclosure is not usable on /stats (wrapper/summary/card "
        f"not visible): {stats_v!r} — the fix must hide it on HOME only, not everywhere"
    )

    # THE BUG: on HOME the healthy disclosure summary must NOT leak a dead "expand"
    # affordance. After det.open=true, no part of the disclosure may be visible.
    assert not home_v["summaryVisible"], (
        "the healthy browser-capture <details> SUMMARY is visible on the HOME launchpad "
        "— a DEAD DISCLOSURE: it invites 'expand' but every child card is stats-only "
        "(display:none on home), so tapping it opens an empty panel (no observable "
        f"change). It must be hidden on home in the non-stale states. Summary: "
        f"{home_v['summaryText']!r}"
    )
    assert not home_v["wrapperVisible"], (
        "the healthy browser-capture <details> wrapper is still visible on HOME after "
        f"expand — the stats-only disclosure leaked onto the council home: {home_v!r}"
    )
    assert not home_v["anyCardVisible"], (
        "a healthy browser-capture card became visible on the HOME launchpad — the "
        "home/stats split is supposed to keep analytics off the clean council home; "
        f"only the STALE (silent-breakage) state is reachable on home: {home_v!r}"
    )
