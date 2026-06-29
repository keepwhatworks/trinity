"""Real-browser value guard: a BUILT lens SURFACES on the launchpad taste card —
it must not silently degrade to the "Run lens" empty CTA.

The product's lead narrative is "build a lens from your existing transcripts
first; councils come later" — so the lens-built-but-no-councils home is the FIRST
populated state a real user sees. The launchpad's "YOUR TASTE, DISTILLED" card has
two branches: `<section class="card taste-card" v-if="tasteLenses">` (the built
state — "The patterns in how you think" + the paired lenses) versus a separate
"Run lens to extract…" empty-CTA card when `tasteLenses` is null.

`tasteLenses` is `_load_taste_lenses()`, which calls `me.pair_mining.load_lenses()`
— and that reader silently returns `[]` on ANY `me/lenses.json` whose rows don't
satisfy `LensPair(**row)` (a renamed/added field after a schema change, a
corrupted write). When it returns empty, the taste card flips to the "Run lens"
CTA: a user who DID build a lens sees "Run lens to extract…" as if Trinity learned
nothing — the product's core value rendered invisible. That's the #295 lens
clobber-guard's sibling at the RENDER layer ([[lens_clobber_guard]] guards the
lens FILE from shrinking to empty; nothing guarded the lens from being silently
UN-SURFACED on the launchpad).

`test_file_substrate_render` already renders this same lens-but-no-councils home
but only asserts "no console errors / no template leak / vendor loads" — it never
asserts the taste card SURFACES the lens. This pins that value invariant in a real
browser: the `.taste-card` (built-state) element is present, the lens's pole text
renders INSIDE it, and the "Run lens to extract" empty CTA is ABSENT.

Found dogfooding partial-state homes in the browser (the lens-built / no-councils
state) — the canonical first-populated state had no value-bearing render guard.
Slow + browser marked; skips without Playwright/chromium; runs in the CI `browser`
job. Mutation-proven: make `load_lenses()` return `[]` (the schema-drift
regression) → `tasteLenses` is null → `.taste-card` disappears and "Run lens to
extract" appears → the assertions red. (Verified by hand during authoring.)
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Distinctive poles so the in-card assertion can't collide with unrelated page
# copy. Real LensPair schema (no support/stability fields — those make
# LensPair(**row) raise TypeError and the reader drops the row).
_POLE_A = "rigorous-derivation"
_POLE_B = "expedient-heuristic"


def _seed_built_lens_no_councils(home) -> None:
    """The product's lead-narrative state: a lens built from imported transcripts,
    zero councils yet. Writes the minimum the launchpad taste card reads."""
    from trinity_local.launchpad_page import write_portal_html

    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (mem / "lens.md").write_text(
        "# Lens\n\n## Lenses (paired tensions)\n\n### 1. "
        f"{_POLE_A} ↔ {_POLE_B}\n- Leans {_POLE_A}.\n", encoding="utf-8")
    (mem / "topics.json").write_text(json.dumps({"basins": [
        {"id": f"b{i:02d}", "label": f"topic {i}", "top_terms": ["x", "y"],
         "centroid": [1.0 if j == i else 0.0 for j in range(4)],
         "representatives": [{"id": f"r{i}", "snippet": "a prompt"}], "size": 5}
        for i in range(4)]}), encoding="utf-8")
    (me / "lenses.json").write_text(json.dumps({"lenses": [{
        "pole_a": _POLE_A, "pole_b": _POLE_B,
        "failure_a": "over-engineered", "failure_b": "fragile",
        "tension_decisions": [], "dual_evidence": {},
        "basins_spanned": ["b00", "b01"], "verdict": "accepted",
        "horizon": "tactical"}]}), encoding="utf-8")
    (me / "orderings.json").write_text(json.dumps({"orderings": []}), encoding="utf-8")
    write_portal_html()  # launchpad + vendored petite-vue


def test_built_lens_surfaces_on_taste_card(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_built_lens_no_councils(tmp_path)

    page_path = tmp_path / "portal_pages" / "launchpad.html"
    errs: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(1200)  # petite-vue hydration

            taste_card = page.query_selector(".taste-card")
            taste_card_text = taste_card.inner_text() if taste_card else ""
            body = page.inner_text("body")
        finally:
            browser.close()

    # The built-state taste card must be present (tasteLenses was truthy).
    assert taste_card is not None, (
        "the built lens did not surface: `.taste-card` (v-if=tasteLenses) is "
        "absent — _load_taste_lenses()/load_lenses() returned empty for a valid "
        "me/lenses.json, so a user who built a lens sees the 'Run lens' empty CTA"
    )
    # The lens's actual content (its poles) must render INSIDE the card — not just
    # an empty shell. Scope to the card so the hero banner's pole echo can't
    # satisfy this by accident.
    assert _POLE_A in taste_card_text and _POLE_B in taste_card_text, (
        f"the taste card rendered but without the lens poles ({_POLE_A!r} / "
        f"{_POLE_B!r}) — the paired-lens block didn't bind. Card text: "
        f"{taste_card_text[:300]!r}"
    )
    # The empty-state CTA must be ABSENT — its presence means the card flipped to
    # the no-lens branch (the silent-degrade failure mode).
    assert "Run lens to extract" not in body, (
        "the 'Run lens to extract' empty-state CTA is present even though a lens "
        "is built — the taste card degraded to the no-lens branch"
    )
    assert not errs, f"console/page errors on the built-lens launchpad: {errs[:4]}"


# Extreme-scale fixture for the unbounded-render cap guard (Iter 251). Every
# count is the RENDER-INDEPENDENT discriminating seed — checked against the
# rendered DOM in the assertions, never the other way round. Far above the
# server-side caps (_TASTE_PAIRED_CAP=8, _TASTE_ORDERINGS_CAP=8,
# _TASTE_DECISIONS_PER_LENS_CAP=4) so an uncapped render is unmistakable.
_N_PAIRED = 40
_N_DECISIONS_EACH = 8
_N_ORDER = 40


def _seed_extreme_lens(home) -> None:
    """A deep multi-domain power-user lens: many accepted paired tensions, each
    with many justification decisions, plus many domain-local orderings. This is
    the state the taste card renders ALL of, in the main page flow, on the
    minimal HOME view — the unbounded-render wall (measured 18,244px uncapped)."""
    from trinity_local.launchpad_page import write_portal_html

    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (mem / "lens.md").write_text(
        "# Lens\n\n## Lenses (paired tensions)\n\n### 1. clarity-0 ↔ cleverness-0\n- body.\n",
        encoding="utf-8")
    paired = []
    decisions = []
    for i in range(_N_PAIRED):
        dids = [f"d_{i:03d}_{j}" for j in range(_N_DECISIONS_EACH)]
        for j, did in enumerate(dids):
            decisions.append({
                "id": did, "privileged": f"clarity-{i}", "sacrificed": f"cleverness-{i}",
                "valence": "positive", "basin": f"b{i % 48:02d}",
                "verbatim": f"simpler framing {i}.{j}", "prompt_id": f"p_{i}_{j}"})
        paired.append({
            "pole_a": f"clarity-{i}", "pole_b": f"cleverness-{i}",
            "failure_a": f"oversimplification-{i}", "failure_b": f"obscurity-{i}",
            "tension_decisions": dids, "dual_evidence": {"source_provider": ["claude"]},
            "basins_spanned": [f"b{i % 48:02d}", f"b{(i + 1) % 48:02d}"],
            "verdict": "accepted", "horizon": "tactical"})
    orderings = [{
        "pole_a": f"action-{i}", "pole_b": f"description-{i}",
        "failure_a": "", "failure_b": "", "tension_decisions": [],
        "dual_evidence": {}, "basins_spanned": [f"b{i % 48:02d}"],
        "verdict": "preserve_as_ordering", "horizon": "tactical"} for i in range(_N_ORDER)]
    (me / "lenses.json").write_text(json.dumps({"lenses": paired}), encoding="utf-8")
    (me / "orderings.json").write_text(json.dumps({"orderings": orderings}), encoding="utf-8")
    (me / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d) for d in decisions), encoding="utf-8")
    write_portal_html()


def test_taste_card_caps_extreme_lens_no_render_wall(tmp_path, monkeypatch):
    """JACKPOT guard (Iter 251 unbounded-render class): the launchpad taste card
    renders one row per paired tension + one <details> per justification decision
    + one row per ordering, ALL in the main page flow (no scroll container) on the
    minimal HOME view. A deep power-user lens (40 paired × 8 decisions + 40
    orderings) walled the home into an 18,244px scroll uncapped — the recurring
    "iterate a long-tailed collection with no floor/cap → a 20k px wall" bug-shape.
    Caps live in _load_taste_lenses (_TASTE_PAIRED_CAP / _TASTE_ORDERINGS_CAP /
    _TASTE_DECISIONS_PER_LENS_CAP); a "+N more → full lens" note + the existing
    "View full lens →" escape keep the cap honest.

    Mutation-prove: remove a cap (e.g. `paired, out["paired_lenses_hidden"] =
    _cap_taste_list(paired, _TASTE_PAIRED_CAP)` → `out["paired_lenses_hidden"] = 0`)
    in src/trinity_local/launchpad_data.py → all _N_PAIRED rows render, the note
    vanishes, scrollHeight blows past the bound → this guard reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_extreme_lens(tmp_path)

    page_path = tmp_path / "portal_pages" / "launchpad.html"
    errs: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(1200)  # petite-vue hydration

            # PRECONDITION A — the taste card PAINTS (mounted, no raw template
            # leak), so the count/height assertions are non-vacuous.
            taste_card = page.query_selector(".taste-card")
            assert taste_card is not None, "taste card absent — extreme lens didn't surface"
            leak = page.evaluate("document.body.innerText.includes('{{')")
            assert not leak, "raw petite-vue '{{' leaked — card did not hydrate"

            metrics = page.evaluate("""() => {
              const block = [...document.querySelectorAll('.taste-block-label')]
                .find(l => /Paired lens/i.test(l.textContent));
              const pairedLis = block ? block.parentElement.querySelectorAll('ol.taste-list > li').length : -1;
              const oblock = [...document.querySelectorAll('.taste-block-label')]
                .find(l => /Orderings/i.test(l.textContent));
              const orderingLis = oblock ? oblock.parentElement.querySelectorAll('ul.taste-list > li').length : -1;
              const decisionChips = document.querySelectorAll('.lens-decision-chip').length;
              const moreNotes = [...document.querySelectorAll('.taste-more-note')]
                .filter(n => n.offsetParent !== null).map(n => n.innerText.trim());
              return {pairedLis, orderingLis, decisionChips,
                      moreNotes, bodyH: document.body.scrollHeight};
            }""")
        finally:
            browser.close()

    assert not errs, f"console/page errors on the extreme-lens launchpad: {errs[:4]}"

    # PRECONDITION B — the seed IS extreme (render-independent fixture constants):
    # if these don't exceed the caps, the cap can't be exercised.
    assert _N_PAIRED > 8 and _N_ORDER > 8 and _N_DECISIONS_EACH > 4

    # BINDING ASSERTIONS — keyed on the seed→DOM cap. Each reds with the founder
    # symptom: the taste card renders the FULL long-tailed lens into a page wall.
    assert 0 < metrics["pairedLis"] <= 8, (
        f"the taste card rendered {metrics['pairedLis']} paired-lens rows for a "
        f"{_N_PAIRED}-tension lens — the unbounded-render wall (no top-N cap): a "
        f"deep power-user lens buries the minimal home into a multi-10k-px scroll"
    )
    assert 0 < metrics["orderingLis"] <= 8, (
        f"the taste card rendered {metrics['orderingLis']} ordering rows for "
        f"{_N_ORDER} orderings — uncapped orderings list walls the home"
    )
    # 8 capped lenses × 4 capped decisions = 32; uncapped would be 40 × 8 = 320.
    assert metrics["decisionChips"] <= 8 * 4, (
        f"the taste card rendered {metrics['decisionChips']} decision <details> "
        f"chips (cap 32) — the per-lens tension_decisions list is uncapped, the "
        f"dominant page-wall component (320 chips uncapped)"
    )
    # The cap must be HONEST — a "+N more" escape note must surface for both the
    # capped paired list and the capped orderings list (not a silent truncation).
    joined = " ".join(metrics["moreNotes"])
    assert any("more" in n and "full lens" in n for n in metrics["moreNotes"]), (
        f"the cap dropped tensions/orderings with NO '+N more → full lens' note — "
        f"a silent truncation, not an honest cap. Notes: {metrics['moreNotes']!r}"
    )
    assert "42" in joined or "32" in joined, (
        f"the '+N more' note didn't report the hidden count (expected +{_N_PAIRED - 8} "
        f"tensions / +{_N_ORDER - 8} orderings). Notes: {metrics['moreNotes']!r}"
    )
    # SCROLLHEIGHT BOUND — the whole point. Uncapped this lens walls to 18,244px;
    # capped it's ~4,400px. 9000px is a generous ceiling well below the wall and
    # well above the legitimate capped render, so it bites the un-fixed code
    # without flaking on layout drift.
    assert metrics["bodyH"] < 9000, (
        f"the launchpad home is {metrics['bodyH']}px tall on a {_N_PAIRED}-tension "
        f"lens — the unbounded-render wall: the taste card iterates the full "
        f"long-tailed lens with no cap, burying every other home card"
    )
