"""The HOME "Your taste, distilled" lens card's "Copy as text" button must NOT be
a SILENT NO-OP in the ORDERINGS-ONLY pipeline state.

NO-OP / NO-FEEDBACK defect (2026-06-19 UX sweep, USABILITY lens). The lens-build
pipeline has a REAL, expected output where ``me/lenses.json`` is empty (no candidate
pair passed all three tension tests) but ``me/orderings.json`` has data (some pairs
were preserved as DIRECTIONAL orderings — "A > B" preferences without dual evidence).
In that state ``_load_taste_lenses`` still returns a non-None dict, so the taste card
renders — including its "Orderings (preferences without dual evidence)" block AND the
primary "Copy as text" share button.

BUT ``combined_share_text`` was built ONLY from the PAIRED lenses (``if paired:``),
so in the orderings-only state it was the empty string. The button's handler
``copyLens`` opens with ``if (!text) return`` — so clicking the ONLY share affordance
on a card that VISIBLY has shareable content did NOTHING: no clipboard write, no "✓"
flash, no feedback at all. A dead button reads as broken.

ROOT-CAUSE FIX (launchpad_data._load_taste_lenses): build ``combined_share_text`` from
PAIRED **and** ORDERINGS — gate it on the same content the card renders, so the share
button always has something to copy when the card is shown. The paired-only output is
byte-identical to before (additive change).

Guards:
  - test_orderings_only_share_text_is_not_empty — fast CI canary on the data builder.
  - test_copy_as_text_button_gives_feedback_in_orderings_only_state — the BITING
    real-browser interaction guard: seeds an orderings-only $TRINITY_HOME, drives the
    LIVE builder (render_launchpad_html with NO page_data), clicks "Copy as text",
    asserts the button flips to the "✓ Copied" cue (real feedback) — RED on the
    pre-fix ``if paired:`` gate (combined_share_text == "" → copyLens no-ops → no ✓).
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _seed_orderings_only(home: Path) -> None:
    """Write an orderings-only me/ — the real Stage-3 output where no full lens
    passed all three tension tests but some pairs were preserved as orderings."""
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "lenses.json").write_text(json.dumps({"lenses": []}), encoding="utf-8")
    (me / "orderings.json").write_text(
        json.dumps(
            {
                "orderings": [
                    {
                        "pole_a": "ship",
                        "pole_b": "polish",
                        "failure_a": "",
                        "failure_b": "",
                        "verdict": "preserve_as_ordering",
                        "basins_spanned": [],
                    },
                    {
                        "pole_a": "concrete",
                        "pole_b": "abstract",
                        "failure_a": "",
                        "failure_b": "",
                        "verdict": "preserve_as_ordering",
                        "basins_spanned": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_orderings_only_share_text_is_not_empty(tmp_path, monkeypatch):
    """CI canary (no browser): in the orderings-only state the taste card renders
    (and shows the Orderings block + Copy-as-text button), so combined_share_text
    MUST be non-empty — else the only share button is a silent no-op."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_orderings_only(tmp_path / "home")

    from trinity_local.launchpad_data import _load_taste_lenses

    tl = _load_taste_lenses()
    assert tl is not None, "orderings-only home should still surface the taste card"
    assert len(tl["orderings"]) == 2 and not tl["paired_lenses"], (
        "test fixture should be orderings-only (no paired lenses)"
    )
    share = tl.get("combined_share_text") or ""
    assert share.strip(), (
        "combined_share_text is EMPTY in the orderings-only state — the taste card "
        "renders the Orderings block + 'Copy as text' button, but the button copies "
        '"" → copyLens(if !text return) makes it a SILENT NO-OP (no clipboard, no ✓)'
    )
    # The share text must reflect the orderings the card shows (not a placeholder).
    assert "ship" in share and "polish" in share, (
        f"orderings-only share text doesn't include the orderings: {share!r}"
    )


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_copy_as_text_button_gives_feedback_in_orderings_only_state(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    _seed_orderings_only(home)

    # Drive the LIVE builder (no page_data) so the real _load_taste_lenses runs.
    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    html = render_launchpad_html()
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # The card + its Orderings block must actually be on screen.
                shown = page.evaluate(
                    "() => {"
                    " const card = document.querySelector('section.taste-card');"
                    " const blocks = Array.from(document.querySelectorAll('section.taste-card .taste-block-label'));"
                    " const ord = blocks.find(b => /Orderings/i.test(b.textContent));"
                    " const btns = Array.from(document.querySelectorAll('section.taste-card button.taste-share-btn'));"
                    " const copy = btns.find(b => /Copy as text|Copied/i.test(b.textContent));"
                    " return {"
                    "   cardVisible: !!(card && card.offsetParent !== null),"
                    "   orderingVisible: !!(ord && ord.offsetParent !== null),"
                    "   copyBtnLabel: copy ? copy.textContent.trim() : null,"
                    "   copyBtnVisible: !!(copy && copy.offsetParent !== null) }; }"
                )
                assert shown["cardVisible"], "taste card not rendered in orderings-only state"
                assert shown["orderingVisible"], (
                    "Orderings block not visible — fixture/render mismatch"
                )
                assert shown["copyBtnVisible"], "Copy-as-text button not visible"
                assert "Copy as text" in (shown["copyBtnLabel"] or ""), (
                    f"unexpected copy-button label before click: {shown}"
                )

                # Click it. A working share button flips the label to the ✓ cue
                # (copyLens sets copiedKey -> the v-if span shows "✓ Copied …").
                page.evaluate(
                    "() => { const btns = Array.from(document.querySelectorAll('section.taste-card button.taste-share-btn'));"
                    " const copy = btns.find(b => /Copy as text|Copied/i.test(b.textContent)); copy.click(); }"
                )
                page.wait_for_timeout(250)
                after = page.evaluate(
                    "() => { const btns = Array.from(document.querySelectorAll('section.taste-card button.taste-share-btn'));"
                    " const copy = btns.find(b => /Copy as text|Copied/i.test(b.textContent));"
                    " return copy ? copy.textContent.trim() : null; }"
                )
                assert "Copied" in (after or ""), (
                    "clicking 'Copy as text' in the ORDERINGS-ONLY state gave NO "
                    "feedback (label stayed 'Copy as text') — combined_share_text was "
                    "empty so copyLens no-ops. The only share affordance on a card "
                    f"with visible content is a SILENT DEAD BUTTON. Label after: {after!r}"
                )
                assert not errors, f"console errors driving orderings-only taste card: {errors}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
