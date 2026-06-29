"""Browser guard: the SILENT capture-breakage state must surface ON THE HOME launchpad.

UX sweep iter 82 — a sibling of the Iter-81 embedder-degraded-on-home defect, the
same #273 "don't let degradation be silent" trigger re-buried by the home/stats split.

The browser-capture card's WHOLE reason to exist (its `_browser_capture` docstring:
"makes silent capture breakage VISIBLE") splits into two states:
  - HEALTHY (stale=false) = "N conversations captured · last 24h" → pure operational
    analytics. `stats-card` → /stats only is correct: the simple home stays a clean
    council surface.
  - STALE (stale=true) = "⚠ no captures in 24h — the provider may have refactored its
    streaming API" + the self-healing "Repair extension" CTA (the #147 flagship). This
    is the SILENT capture-breakage the card exists to surface (the corpus silently
    stopped growing). It is a REGRESSION, not analytics.

THE DEFECT this guard pins: the inner card was tagged `stats-card`, so the home/stats
split (`.lp-view-home .stats-card{display:none}`) hid it on home in BOTH states. The
collapsed <details> summary DID carry an inline "⚠ no captures in 24h" warning — but
in a positive TEAL accent (indistinguishable as a problem) — and worse, when the home
user EXPANDED the <details> to act on it, the inner stale card was STILL display:none
and the "Repair extension" self-heal CTA was UNREACHABLE on home. A surfaced problem
with no reachable action = a dead-end.

THE FIX: the inner card's view-class is now MODE-CONDITIONAL. The STALE state drops
`stats-card` (gets `browser-capture-stale-card`, untagged → reachable on home when the
details is expanded) and turns the chrome red (eyebrow "Capture stalled · corpus not
growing"); HEALTHY keeps `stats-card` (stats-only). The collapsed summary warning is
now red + "expand to repair".

This guard drives the REAL home + stats renders in BOTH states and reads the rendered
DOM (offsetParent visibility + the Repair CTA's reachability), NOT a source string.
Mutation: revert the inner card's `:class` to the static `card ... stats-card` → the
home-expand assertion REDS (the Repair CTA is unreachable on home), reproducing the
founder symptom ("capture broke, the home warning expanded to nothing").
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _render(home: Path, *, stale: bool) -> Path:
    """Render the REAL portal pages (home launchpad.html + stats.html) with a few
    seeded captures whose mtime is >24h ago (stale=True → the silent-breakage state)
    or ~1h ago (stale=False → the healthy analytics state)."""
    conv = home / "conversations" / "claude"
    conv.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (conv / f"conv{i}.json").write_text(
            json.dumps({"id": f"conv{i}", "messages": [{"role": "user", "content": "hi"}]}),
            encoding="utf-8",
        )
    age = 30 * 3600 if stale else 3600
    t = time.time() - age
    for f in conv.glob("*.json"):
        os.utime(f, (t, t))
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


# Expand the <details> (the user clicking "expand to repair") then probe the inner
# browser-capture card AND the "Repair extension" self-heal CTA. offsetParent != null +
# computed display catch the `.lp-view-home .stats-card{display:none}` hide.
_PROBE = """() => {
  const det = document.querySelector('details.demoted-card-wrapper');
  if (det) det.open = true;
  const sec = document.querySelector('section.browser-capture-card');
  const secVisible = sec && sec.offsetParent !== null
      && getComputedStyle(sec).display !== 'none';
  const eb = sec && sec.querySelector('.eyebrow');
  const repairBtn = sec
    ? [...sec.querySelectorAll('button')].find(b => /repair/i.test(b.textContent))
    : null;
  // The collapsed summary's inline warning — its color is the home-without-expanding
  // signal; read it before/after open (textContent is stable either way).
  const summ = det && det.querySelector('summary');
  return {
    present: !!sec,
    visible: secVisible,
    eyebrow: ((eb && eb.textContent) || '').replace(/\\s+/g, ' ').trim(),
    repairReachable: repairBtn ? (repairBtn.offsetParent !== null) : false,
    summaryText: summ ? summ.textContent.replace(/\\s+/g, ' ').trim() : '',
  };
}"""


def _probe(pages: Path, page_name: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1800}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / page_name}")
            page.wait_for_timeout(1200)
            d = page.evaluate(_PROBE)
            d["errors"] = errs
            return d
        finally:
            browser.close()


def test_stale_capture_is_actionable_on_home():
    """STALE state (capture silently broke, corpus not growing): when the home user
    expands the demoted <details>, the inner card AND the self-healing 'Repair
    extension' CTA must be REACHABLE on home — not buried on /stats. Reproduces the
    founder symptom: capture broke, the home warning expanded to nothing."""
    pytest.importorskip("playwright.sync_api")
    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    pages = _render(home, stale=True)

    home_v = _probe(pages, "launchpad.html")
    assert not home_v["errors"], f"JS errors on home: {home_v['errors'][:3]}"
    assert home_v["present"], (
        "the browser-capture card did not render at all in the stale state — "
        "the silent-breakage show-gate regressed"
    )
    assert home_v["visible"], (
        "the SILENTLY-BROKEN browser-capture card (stale=true — no new captures in "
        "24h, the corpus stopped growing) is HIDDEN on the HOME launchpad even when the "
        "user expands the <details>. A user whose capture broke can SEE the warning but "
        "has NO reachable diagnostic/fix on the surface they look at — re-burying "
        "exactly what the card exists to surface. It must show on home (drop the "
        "`stats-card` view-class for the stale state)."
    )
    assert home_v["repairReachable"], (
        "the self-healing 'Repair extension' CTA (#147 flagship) is UNREACHABLE on the "
        "HOME launchpad in the stale state — a surfaced problem with no reachable action "
        "is a dead-end. The user expanded the warning expecting to fix it and got "
        "nothing."
    )
    # The stale card must lead with a problem framing, not a neutral analytics label.
    assert "stalled" in home_v["eyebrow"].lower() or "growing" in home_v["eyebrow"].lower(), (
        "the stale-on-home card still leads with the neutral 'Browser capture' eyebrow "
        f"— a silent regression must read as a problem. (eyebrow: {home_v['eyebrow']!r})"
    )
    # And it's still fully present on /stats (the analytics surface keeps the diagnostic).
    stats_v = _probe(pages, "stats.html")
    assert stats_v["present"] and stats_v["visible"] and stats_v["repairReachable"], (
        "the stale browser-capture card lost its body/Repair CTA on /stats — it must "
        "show on BOTH views"
    )


def test_healthy_capture_card_stays_off_home():
    """SCOPE GUARD: the HEALTHY browser-capture card (stale=false — "N conversations
    captured · last 24h") is pure operational analytics — it must stay OFF the simple
    council home (stats-card), even when the home user expands the demoted <details>.
    Only the silently-BROKEN stale state should surface its body on home."""
    pytest.importorskip("playwright.sync_api")
    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    pages = _render(home, stale=False)

    home_v = _probe(pages, "launchpad.html")
    assert not home_v["errors"], f"JS errors on home: {home_v['errors'][:3]}"
    assert home_v["present"], "the healthy capture card should still be in the home DOM"
    assert not home_v["visible"], (
        "the HEALTHY 'N conversations captured · last 24h' analytics card is showing its "
        "body on the simple home launchpad even when expanded — it belongs on /stats "
        "(the home/stats split); only the silently-BROKEN stale state should surface on "
        "home."
    )
    # It IS fully present on /stats.
    stats_v = _probe(pages, "stats.html")
    assert stats_v["present"] and stats_v["visible"], (
        "the healthy browser-capture card must still render on /stats"
    )
