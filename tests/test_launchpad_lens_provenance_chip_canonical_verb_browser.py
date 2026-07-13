"""Real-browser guard: the launchpad taste card's PROVENANCE chip names the
CANONICAL `lens` verb, never the retired `lens-build` alias.

Iter-218 stale-alias-tagline class fix. Per CLAUDE.md, `lens-build` is a
COMPATIBILITY ALIAS that resolves to the canonical `lens` verb (`trinity-local
lens-build --help` prints `usage: trinity-local lens …`); the advertised CLI
surface is `lens` / `council` / `dream` / `status` / `install`. A user-facing
caption/tagline/chip that names the retired alias teaches the user a verb the
product no longer advertises — the SAME class Iter-216 (picks.json "per task
type" → lens basin) and Iter-217 (lens.md/topics.json taglines named
`lens-build`) closed in the memory viewer. This guard closes it on the LAUNCHPAD
HOME taste card, where two provenance chips (one per paired lens, one per
ordering) plus their hover tooltips rendered the literal `lens-build`:

  - accepted paired lens          → chip text `lens` (was `lens-build`)
  - preserve_as_ordering ordering → chip text `lens` (was `lens-build`)
  - both tooltips: "Built from your local rejection corpus via trinity-local lens"

This is a REAL-BROWSER, painted-text assertion (not a source string-presence
check): it drives petite-vue hydration, asserts the chip element PAINTS (visible
bounding box + non-empty text — the BITE precondition, so a chip that never
mounts can't pass vacuously), then asserts the painted text equals the canonical
verb and that NO element on the home renders the `lens-build` alias.

MUTATION-PROVEN against `render_launchpad_html` (src/trinity_local/
launchpad_template.py): revert the chip ternary back to
`p.verdict === 'accepted' ? 'lens-build' : …` (and the ordering sibling) → the
chip paints `lens-build` → `_painted_chip_text == "lens"` red AND the
"no lens-build alias anywhere" assertion red. Restored after verifying red.

Slow + browser marked; skips without Playwright/chromium; runs in the CI
`browser` job.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Distinctive poles so the in-card text assertions can't collide with unrelated
# page copy. Real LensPair / ordering schema (no extra fields — those make
# LensPair(**row) raise TypeError and the reader drops the row).
_PAIR_A = "rigorous-derivation"
_PAIR_B = "expedient-heuristic"
_ORD_A = "shipping-velocity"
_ORD_B = "aesthetic-polish"


def _seed_built_lens_with_orderings(home) -> None:
    """A populated taste card: one locally-built accepted paired lens AND one
    locally-built preserve_as_ordering — both carry the provenance chip whose
    text is the bug under test."""
    from trinity_local.launchpad_page import write_portal_html

    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (mem / "lens.md").write_text(
        "# Lens\n\n## Lenses (paired tensions)\n\n### 1. "
        f"{_PAIR_A} ↔ {_PAIR_B}\n- Leans {_PAIR_A}.\n", encoding="utf-8")
    (mem / "topics.json").write_text(json.dumps({"basins": [
        {"id": f"b{i:02d}", "label": f"topic {i}", "top_terms": ["x", "y"],
         "centroid": [1.0 if j == i else 0.0 for j in range(4)],
         "representatives": [{"id": f"r{i}", "snippet": "a prompt"}], "size": 5}
        for i in range(4)]}), encoding="utf-8")
    (me / "lenses.json").write_text(json.dumps({"lenses": [{
        "pole_a": _PAIR_A, "pole_b": _PAIR_B,
        "failure_a": "over-engineered", "failure_b": "fragile",
        "tension_decisions": [], "dual_evidence": {},
        "basins_spanned": ["b00", "b01"], "verdict": "accepted",
        "horizon": "tactical"}]}), encoding="utf-8")
    (me / "orderings.json").write_text(json.dumps({"orderings": [{
        "pole_a": _ORD_A, "pole_b": _ORD_B,
        "failure_a": "", "failure_b": "",
        "tension_decisions": [], "dual_evidence": {},
        "basins_spanned": ["b02"], "verdict": "preserve_as_ordering",
        "horizon": "tactical"}]}), encoding="utf-8")
    write_portal_html()  # launchpad + vendored petite-vue


def test_provenance_chip_names_canonical_lens_not_alias(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_built_lens_with_orderings(tmp_path)

    page_path = tmp_path / "portal_pages" / "launchpad.html"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(1200)  # petite-vue hydration

            taste_card = page.query_selector(".taste-card")
            assert taste_card is not None, (
                "BITE-PRECONDITION FAILED: `.taste-card` (v-if=tasteLenses) never "
                "mounted — the seeded built lens did not surface, so the chip "
                "assertion below would pass vacuously. Fix the seed before trusting "
                "this guard."
            )

            # The provenance chips: paired-lens chip lives inside .taste-list-title's
            # <li>; both chips are the only inline meta spans carrying the verb text.
            # Find every painted span whose trimmed text is exactly the alias OR the
            # canonical verb, asserting at least one PAINTS (the BITE precondition).
            chip_texts = page.eval_on_selector_all(
                ".taste-card span",
                """els => els
                    .filter(e => {
                        const r = e.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;  // actually painted
                    })
                    .map(e => (e.textContent || '').trim())
                    .filter(t => t === 'lens' || t === 'lens-build')""",
            )
            # Tooltips on those same chips (the hover-explanation path).
            chip_titles = page.eval_on_selector_all(
                ".taste-card span[title]",
                "els => els.map(e => e.getAttribute('title'))",
            )
            body = page.inner_text("body")
        finally:
            browser.close()

    # BITE: at least one provenance chip must actually paint a verb-text — a chip
    # that never renders can't prove the copy is canonical.
    assert chip_texts, (
        "no painted provenance chip with verb text found on the taste card — the "
        "accepted-lens / preserve_as_ordering chips didn't bind; the canonical-verb "
        "assertion would be vacuous"
    )
    # CLASS INVARIANT: every painted provenance chip names the CANONICAL `lens`,
    # never the retired `lens-build` alias (Iter-218).
    assert all(t == "lens" for t in chip_texts), (
        f"a taste-card provenance chip painted the RETIRED `lens-build` alias "
        f"instead of the canonical `lens` verb (chip texts: {chip_texts!r}) — the "
        f"Iter-216/217 stale-alias-tagline class regressed on the launchpad home"
    )
    # The hover tooltips must name the canonical verb too, never the alias.
    joined_titles = " || ".join(t or "" for t in chip_titles)
    assert "via lens-build" not in joined_titles, (
        f"a provenance chip tooltip names the retired `lens-build` alias: "
        f"{joined_titles!r}"
    )
    assert "via trinity-local lens" in joined_titles, (
        f"the locally-built provenance tooltip lost its canonical-verb phrasing: "
        f"{joined_titles!r}"
    )
    # Belt-and-suspenders: the alias must not appear ANYWHERE in the rendered home
    # body (catches the meta line "Refreshes when lens runs." regressing too).
    assert "lens-build" not in body, (
        "the retired `lens-build` alias appears in the rendered launchpad home body "
        "(provenance chip, tooltip, or the 'Refreshes when lens runs.' meta line) — "
        "Iter-218 stale-alias-tagline class regressed"
    )
