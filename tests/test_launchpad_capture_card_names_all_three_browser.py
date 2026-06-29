"""Browser guard: the EMPTY-STATE browser-capture card must pitch ALL THREE capture
web UIs (claude.ai / chatgpt.com / gemini.google.com), not a stale two-provider subset.

UX sweep — the empty-state browser-capture card (the surface that SELLS a brand-new
user on installing the v1.6 capture extension) was authored 2026-05-15 (v1.6 Surface
33, 6a6b4871) — BEFORE the #135 gemini.google.com Chrome-capture adapter shipped. Its
copy never caught up: it headed "Capture every Claude / ChatGPT conversation
automatically" and the body listed only "claude.ai or chatgpt.com" — DROPPING Gemini
entirely. Driven for real (empty home → has_data:False → empty-state card) the rendered
card named two of the three providers while the ADJACENT import card right below it
correctly read "Import old Claude / ChatGPT / Gemini exports". So a new user saw Gemini
supported for IMPORT but apparently NOT for live capture — on the exact card whose job
is to convert them to the capture extension, contradicting the product's "Ask all
three" positioning and the shipped #135 Gemini capture path
(parse_captured_gemini_conversation + adapters/gemini.js).

THE CLASS: every user-facing capture-source copy site that should name the full capture
trio but stalled at two. The launchpad empty-capture card was the most visible; the CLI
doctor (`status`) carried the same drop in two `browser_capture` check details. All
were synced to the canonical trio (the same "claude.ai / chatgpt.com /
gemini.google.com" the populated card + mcp_server + status header already use).

This guard drives the REAL portal render and reads the RENDERED DOM of the empty-state
card (not a source string): it asserts the card names all three provider brands AND all
three capture domains, with Gemini specifically present. Mutation: revert the heading to
"Claude / ChatGPT" + body to "claude.ai or chatgpt.com" → this guard REDS with the
founder symptom (the capture pitch silently omits Gemini), while the existing
capture-card guards (stale-on-home / mobile-bar) stay green — proving the bite is the
provider-completeness of the empty-state PITCH, not the card's show-gate or layout.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _render_empty(home: Path) -> Path:
    """Render the REAL portal pages against an EMPTY home — no conversations means
    browserCapture.has_data is False, so the EMPTY-STATE capture pitch card renders
    (v-if="!pageData.browserCapture.has_data")."""
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
    assert (pages / "stats.html").exists(), "stats.html not rendered"
    return pages


# Read the empty-state capture card's full text (heading + body) from the rendered DOM.
# The empty card is the one carrying the install command + "Full ritual in
# browser-extension/README.md" — distinguish it from a populated/stale card by its
# install-command code block.
_PROBE = """() => {
  const cards = [...document.querySelectorAll('section.browser-capture-card')];
  // The empty-state card is the one that renders the install command (md-code-block)
  // and has no per-provider .bc-provider-row.
  const empty = cards.find(c =>
      c.querySelector('.md-code-block') && !c.querySelector('.bc-provider-row'));
  const importCard = [...document.querySelectorAll('h2')]
      .map(h => h.textContent.replace(/\\s+/g, ' ').trim())
      .find(t => /Import old/i.test(t)) || '';
  return {
    found: !!empty,
    text: empty ? empty.innerText.replace(/\\s+/g, ' ').trim() : '',
    heading: empty ? (empty.querySelector('h2')?.innerText || '').replace(/\\s+/g, ' ').trim() : '',
    importHeading: importCard,
  };
}"""


def _probe(stats_html: Path) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 900, "height": 1600}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{stats_html}")
            page.wait_for_timeout(1000)
            d = page.evaluate(_PROBE)
            d["errors"] = errs
            return d
        finally:
            browser.close()


def test_empty_capture_card_names_all_three_web_uis():
    pytest.importorskip("playwright.sync_api")
    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    pages = _render_empty(home)

    d = _probe(pages / "stats.html")
    assert not d["errors"], f"JS errors on /stats: {d['errors'][:3]}"
    # Non-vacuous precondition: the empty-state pitch card actually rendered AND the
    # adjacent import card (the contrast that already names all three) is present, so
    # the completeness assertions below aren't a hollow pass on a missing card.
    assert d["found"], (
        "the empty-state browser-capture pitch card did not render against an empty "
        "home — the has_data:False show-gate regressed; the provider-completeness "
        "check would be vacuous"
    )
    assert "Gemini" in d["importHeading"], (
        "the sibling import card no longer names Gemini — fixture/contrast precondition "
        f"broke (import heading: {d['importHeading']!r})"
    )

    text = d["text"]
    # THE BITE: the capture PITCH must name the full shipped capture trio (#135 added
    # the gemini.google.com adapter). Brand names in the heading + domains in the body.
    for brand in ("Claude", "ChatGPT", "Gemini"):
        assert brand in text, (
            f"the empty-state browser-capture card omits {brand!r} — the capture pitch "
            "silently lists a SUBSET of the providers Trinity actually captures. This is "
            "the stale pre-#135 'Capture every Claude / ChatGPT conversation' copy that "
            "drops Gemini on the exact card that sells the capture extension, "
            f"contradicting 'Ask all three'. (card text: {text!r})"
        )
    for domain in ("claude.ai", "chatgpt.com", "gemini.google.com"):
        assert domain in text, (
            f"the empty-state browser-capture card body omits {domain!r} — it tells the "
            "new user which web UIs get captured but drops one of the three shipped "
            "capture domains (Gemini's gemini.google.com landed with #135). "
            f"(card text: {text!r})"
        )
    # Specifically pin Gemini (the dropped one) in the HEADING, where the stale copy
    # read "Capture every Claude / ChatGPT conversation".
    assert "Gemini" in d["heading"], (
        "the empty-state capture card HEADING still omits Gemini — it read 'Capture "
        "every Claude / ChatGPT conversation automatically', the stale two-provider "
        f"pitch. (heading: {d['heading']!r})"
    )
