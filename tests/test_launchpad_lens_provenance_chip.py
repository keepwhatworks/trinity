"""launchpad lens card: provenance chip surfaces lens vs import source.

Same honesty pattern as the trust-band on cortex rules + n= on axis
bars: name the SOURCE of each claim instead of presenting all lenses
as equally weighted. Lens shapes:

- verdict='accepted' → built via `trinity-local lens` (local rejection
  corpus distillation). Chip reads 'lens', sage color. (NOTE: the chip
  names the CANONICAL verb `lens`, NOT the retired `lens-build` alias —
  Iter-218 stale-alias-tagline fix.)
- verdict='imported' → came from provider-side loop (lens-prompt →
  paste into provider → lens-import). Chip reads 'via <provider>',
  steel color.

The chip is small/inline (not opacity demotion — different epistemic
status, not low-confidence — but visible so the user knows).
"""
from __future__ import annotations


def _render(paired_lenses):
    from trinity_local.launchpad_template import render_launchpad_html
    return render_launchpad_html(
        page_data={"tasteLenses": {"paired_lenses": paired_lenses}},
    )


def test_accepted_lens_renders_lens_chip():
    html = _render([{
        "pole_a": "specificity",
        "pole_b": "abstraction",
        "verdict": "accepted",
        "dual_evidence": {"source_provider": ["lens"]},
        "tension_decisions": ["d1", "d2", "d3"],
    }])
    # Vue conditional emits the CANONICAL verb text 'lens' for accepted —
    # not the retired `lens-build` alias (Iter-218 stale-alias fix). Scope the
    # negative assertion to the chip BINDING (not the whole doc, which carries
    # `lens-build` in a hit-area CSS comment — the rendered painted text is what
    # matters, and the browser guard pins that directly).
    assert "'accepted' ? 'lens'" in html
    assert "'accepted' ? 'lens-build'" not in html
    # The conditional binding shape exists for a future refactor to keep
    assert "p.verdict === 'accepted'" in html


def test_imported_lens_renders_via_provider_chip():
    html = _render([{
        "pole_a": "generator",
        "pole_b": "generated",
        "verdict": "imported",
        "dual_evidence": {"source_provider": ["claude"]},
        "tension_decisions": ["evidence-1"],
    }])
    # The "via X" pattern is rendered by Vue at runtime, but the binding
    # shape is in the template source. The provenance-naming machinery
    # is the contract.
    assert "via " in html
    assert "p.dual_evidence" in html and "source_provider" in html


def test_orderings_also_render_provenance_chip():
    """Orderings (preferences without dual evidence) get the same
    provenance treatment as paired lenses — same source-mismatch
    surface, same fix shipped a868f9b extended to the sibling
    section. Verdict values for orderings:
      'preserve_as_ordering' = local `trinity-local lens` (Stage 4b)
      'imported_ordering'    = via lens-import
    """
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(
        page_data={
            "tasteLenses": {
                "orderings": [
                    {
                        "pole_a": "shipping velocity",
                        "pole_b": "aesthetic polish",
                        "verdict": "preserve_as_ordering",
                        "dual_evidence": {"source_provider": ["lens"]},
                    },
                    {
                        "pole_a": "generator",
                        "pole_b": "generated",
                        "verdict": "imported_ordering",
                        "dual_evidence": {"source_provider": ["claude"]},
                    },
                ],
            },
        },
    )
    # Conditional emits the CANONICAL 'lens' for preserve_as_ordering —
    # not the retired `lens-build` alias (Iter-218 stale-alias fix). Scope the
    # negative to the chip binding (the doc carries `lens-build` in a CSS comment).
    assert "'preserve_as_ordering' ? 'lens'" in html
    assert "'preserve_as_ordering' ? 'lens-build'" not in html
    # 'via X' branch present for imported_ordering
    assert "via " in html
    # The local branch keyword surfaces in the binding
    assert "o.verdict === 'preserve_as_ordering'" in html


def test_chip_carries_explanatory_title_for_each_verdict():
    """Tooltip on hover should explain the provenance — same UX
    pattern as the trust-band tooltip on cortex rules."""
    html = _render([{
        "pole_a": "A", "pole_b": "B",
        "verdict": "accepted",
        "dual_evidence": {"source_provider": ["lens"]},
        "tension_decisions": [],
    }])
    # Both branches of the ternary appear in the template — confirms
    # the tooltip path renders for either verdict. The local-source
    # tooltip names the CANONICAL verb, not the `lens-build` alias.
    assert "Built from your local rejection corpus via trinity-local lens" in html
    assert "via lens-build" not in html
    assert "Imported from a provider" in html
