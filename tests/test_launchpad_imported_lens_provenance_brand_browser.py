"""Real-browser guard: the launchpad taste card's IMPORTED-lens provenance chip
names the MODEL BRAND (Claude / GPT / Gemini), never the raw CLI/import slug
(codex / antigravity / gemini).

The #275 raw-slug-display class on a NEW surface. Per CLAUDE.md (and the
2026-06-06 #275 founder call) every USER-FACING provider display reads as the
model BRAND — `codex → GPT`, `antigravity`/`gemini → Gemini`, `claude → Claude`
— folded through `formatProviderLabel`/`provider_model_brand` on the council,
routing, eval, live-council, and popup surfaces.

The taste card's IMPORTED-lens provenance chip (`<lens> via X`) was the
un-branded sibling. An imported lens is created by `trinity-local lens-import
--provider codex ./lens-from-codex.json` — and `docs/lens-from-provider.md`
literally instructs `--provider codex` / `--provider gemini` with
`"source_provider": "claude | codex | gemini"`. `provider_import.read_provider_import`
stores that value RAW + lowercased (`(cli_override or payload.source_provider or
"unknown").strip().lower()`), `lens_import._provider_dict_to_lens_pair` wraps it
as `dual_evidence={"source_provider": [source_provider]}` with `verdict="imported"`,
and `me.pair_mining.load_lenses` (→ `_load_pairs`, `LensPair(**row)`) PRESERVES both
fields on the launchpad read. The chip rendered `'via ' + source_provider[0]`
VERBATIM → the launchpad taste card painted **"via codex"** / **"via gemini"** for
a real user who followed the documented import flow.

Fix: wrap `source_provider[0]` with `formatProviderLabel(...)` at BOTH provenance
render sites (the paired-lens chip + the ordering chip), exactly like every other
provider-display surface in the same template.

This is a REAL-BROWSER, painted-text assertion (not a source string-presence
check): it drives petite-vue hydration, asserts the imported chips PAINT (visible
bounding box + non-empty text — the BITE precondition, so a chip that never mounts
can't pass vacuously), then asserts the painted text reads the BRAND and NO chip
leaks the raw slug.

MUTATION-PROVEN against `render_launchpad_html` (src/trinity_local/
launchpad_template.py): drop the `formatProviderLabel(...)` wrapper from the chip
ternary (revert to `… source_provider[0]) || 'provider'`) → the chip paints "via
codex" / "via gemini" → the brand assertion red AND the no-raw-slug assertion red.
Restored after verifying red.

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
_PAIR_A = "explicit-contracts"
_PAIR_B = "implicit-conventions"
_ORD_A = "shipping-velocity"
_ORD_B = "aesthetic-polish"

# Raw slugs the documented `lens-import --provider X` stores; the brands they MUST
# fold to on the chip (formatProviderLabel: codex→GPT, gemini→antigravity→Gemini).
_SLUG_TO_BRAND = {"codex": "GPT", "gemini": "Gemini"}


def _seed_imported_lenses(home) -> None:
    """A populated taste card where the paired lens AND the ordering are
    IMPORTED from providers — both carry the slug-bearing provenance chip under
    test (`verdict="imported"` + `dual_evidence.source_provider=[slug]`)."""
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
    # Imported PAIRED lens — source_provider a raw slug (codex), as the
    # documented `lens-import --provider codex` stores it.
    (me / "lenses.json").write_text(json.dumps({"lenses": [{
        "pole_a": _PAIR_A, "pole_b": _PAIR_B,
        "failure_a": "ceremony", "failure_b": "surprise",
        "tension_decisions": ["d1", "d2"],
        "dual_evidence": {"source_provider": ["codex"], "confidence": ["high"]},
        "basins_spanned": ["b00", "b01"], "verdict": "imported",
        "horizon": "strategic"}]}), encoding="utf-8")
    # Imported ORDERING — a different slug (gemini → Gemini) so the second render
    # site is exercised with its own brand fold.
    (me / "orderings.json").write_text(json.dumps({"orderings": [{
        "pole_a": _ORD_A, "pole_b": _ORD_B,
        "failure_a": "", "failure_b": "",
        "tension_decisions": ["d3"],
        "dual_evidence": {"source_provider": ["gemini"]},
        "basins_spanned": ["b02"], "verdict": "imported_ordering",
        "horizon": "tactical"}]}), encoding="utf-8")
    write_portal_html()  # launchpad + vendored petite-vue


def test_imported_provenance_chip_reads_brand_not_raw_slug(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_imported_lenses(tmp_path)

    # Source-sanity: the loader must actually preserve the imported provenance
    # the chip renders — otherwise the browser assertion is vacuous (the chip
    # would fall back to the 'lens' branch). This is the precondition that makes
    # the cell discriminating.
    from trinity_local.me.pair_mining import load_lenses, load_orderings
    loaded_pairs = [(p.verdict, p.dual_evidence.get("source_provider"))
                    for p in load_lenses()]
    loaded_ords = [(o.verdict, o.dual_evidence.get("source_provider"))
                   for o in load_orderings()]
    assert ("imported", ["codex"]) in loaded_pairs, (
        f"SEED PRECONDITION FAILED: imported paired lens didn't round-trip with "
        f"its source_provider slug (loaded: {loaded_pairs!r}). The chip would "
        f"render 'lens', not 'via X', and this guard would pass vacuously."
    )
    assert ("imported_ordering", ["gemini"]) in loaded_ords, (
        f"SEED PRECONDITION FAILED: imported ordering didn't round-trip "
        f"(loaded: {loaded_ords!r})."
    )

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
                "mounted — the seeded imported lens did not surface, so the chip "
                "assertion below would pass vacuously. Fix the seed first."
            )

            # Every PAINTED span in the taste card whose trimmed text starts with
            # "via " is a provenance chip on an imported lens/ordering. Capture the
            # painted text (BITE precondition: at least one must render).
            via_chips = page.eval_on_selector_all(
                ".taste-card span",
                """els => els
                    .filter(e => {
                        const r = e.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;  // actually painted
                    })
                    .map(e => (e.textContent || '').trim())
                    .filter(t => t.indexOf('via ') === 0)""",
            )
        finally:
            browser.close()

    # BITE: at least one imported provenance chip must actually paint a "via …"
    # text — a chip that never renders can't prove the slug got branded.
    assert via_chips, (
        "no painted 'via X' provenance chip found on the taste card — the imported "
        "paired-lens / ordering chips didn't bind; the brand-display assertion "
        "would be vacuous"
    )

    # CLASS INVARIANT (#275): every imported provenance chip reads the MODEL BRAND,
    # never the raw CLI/import slug. The chip text is "via <Brand>".
    expected = {"via " + b for b in _SLUG_TO_BRAND.values()}  # {'via GPT','via Gemini'}
    bad = [t for t in via_chips if t not in expected]
    assert not bad, (
        f"a taste-card IMPORTED-lens provenance chip painted the RAW import slug "
        f"instead of the model brand (chips: {via_chips!r}; offending: {bad!r}) — "
        f"the #275 raw-slug-display class regressed on the launchpad home: a user "
        f"who ran `trinity-local lens-import --provider codex` sees 'via codex', "
        f"not 'via GPT' (and 'via gemini', not 'via Gemini')."
    )
    # Explicit no-raw-slug check so the failure message names the exact founder
    # symptom even if formatProviderLabel is reverted to a partial mapping.
    leaked = [t for t in via_chips if t in ("via codex", "via gemini", "via antigravity")]
    assert not leaked, (
        f"raw import slug leaked onto the taste card provenance chip: {leaked!r} "
        f"(must read 'via GPT' / 'via Gemini')"
    )
