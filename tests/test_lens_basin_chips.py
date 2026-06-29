"""Tick #36 — lens card → topology cross-link via basins_spanned.

Each paired lens carries basins_spanned (list of topology basin ids
the tension lives across). The launchpad lens card now renders these
as chip deep-links to the topology view (?basin=<id>), closing the
forward-arc gap "lens card on the launchpad → link to the prompts
that surfaced it" — by linking to the topology basins instead.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestCrossMemoryChipSharedClass:
    """Tick #37 — three chip surfaces (cortex card, lens card, recent
    council card) now share a single .cross-memory-chip base class.
    Bumping the look in launchpad_template.py CSS updates all three.
    Guard against a future inline-style refactor re-introducing the
    duplication."""

    def test_shared_base_class_present_in_template(self, isolated_home):
        from trinity_local.launchpad_template import render_launchpad_html
        page_data = _minimal_page_data_with_lens(basins_spanned=["b03"])
        html = render_launchpad_html(page_data=page_data)
        assert ".cross-memory-chip" in html, (
            ".cross-memory-chip base class missing — chip surfaces will "
            "render without their shared visual treatment"
        )
        # Modifier classes the call sites depend on must exist too.
        assert ".cross-memory-chip--label" in html, "--label modifier missing"
        assert ".cross-memory-chip--id" in html, "--id modifier missing"
        assert ".cross-memory-chip--inline" in html, "--inline modifier missing"
        assert ".cross-memory-chip--pill" in html, "--pill modifier missing"

    def test_cortex_chip_uses_shared_class(self, isolated_home):
        from trinity_local.launchpad_template import render_launchpad_html
        page_data = _minimal_page_data_with_lens(basins_spanned=[])
        html = render_launchpad_html(page_data=page_data)
        # The cortex chip template (only present when cortexRules has
        # rules with topology_basin) gets the shared class even if not
        # rendered — it's the literal class attribute we check.
        assert "cortex-topology-chip cross-memory-chip" in html, (
            "cortex chip not threading the shared base — will lose its "
            "consistent styling when the CSS gets bumped"
        )

    def test_lens_chip_uses_shared_class(self, isolated_home):
        from trinity_local.launchpad_template import render_launchpad_html
        page_data = _minimal_page_data_with_lens(basins_spanned=["b03"])
        html = render_launchpad_html(page_data=page_data)
        assert "lens-basin-chip cross-memory-chip" in html, (
            "lens chip not threading the shared base"
        )

    # test_recent_card_chips_use_shared_class removed 2026-05-21.
    # The council-xlink chips on recent cards (→ pick / → routing /
    # → topology + PICK/ROUTING/SHARE PNG buttons) were deleted in
    # commit 8f1fd95 per user direction "what are the buttons under
    # it doing? remove them". The shared-class invariant for the
    # cortex + lens chips that REMAIN is still guarded by
    # test_cortex_chip_uses_shared_class + test_lens_chip_uses_shared_class
    # above — only the deleted recent-card surface is sunset.


class TestTopologyBasinLabels:
    """Tick #38 — server-side topics.json → basin label map for chip
    tooltips. Replaces opaque 'b03' hover text with the basin's top
    TF-IDF terms so the user knows what a basin contains before
    they click. Empty {} when topics.json missing/malformed."""

    def test_cold_install_returns_empty(self, isolated_home):
        from trinity_local.launchpad_data import _topology_basin_labels
        # No topics.json → empty map (must not raise).
        assert _topology_basin_labels() == {}

    def test_labels_extracted_per_basin(self, isolated_home):
        import json
        (isolated_home / "memories" / "topics.json").write_text(
            json.dumps({
                "basins": [
                    {"id": "b00", "top_terms": ["forecast", "signal", "indicator", "lagging"]},
                    {"id": "b01", "top_terms": ["code", "refactor"]},
                ]
            }),
            encoding="utf-8",
        )
        from trinity_local.launchpad_data import _topology_basin_labels
        labels = _topology_basin_labels()
        # Top-3 terms joined with the same separator the topology graph uses.
        assert labels["b00"] == "forecast · signal · indicator", (
            f"b00 label drifted from top-3 contract: {labels.get('b00')!r}"
        )
        assert labels["b01"] == "code · refactor", "b01 label missing or wrong"

    def test_basin_with_no_terms_is_omitted(self, isolated_home):
        import json
        (isolated_home / "memories" / "topics.json").write_text(
            json.dumps({"basins": [
                {"id": "b00", "top_terms": ["forecast"]},
                {"id": "b01", "top_terms": []},  # no terms → no label
                {"id": "b02"},  # missing top_terms field entirely
            ]}),
            encoding="utf-8",
        )
        from trinity_local.launchpad_data import _topology_basin_labels
        labels = _topology_basin_labels()
        assert "b00" in labels
        assert "b01" not in labels, "basin with empty top_terms shouldn't get a label"
        assert "b02" not in labels, "basin without top_terms field shouldn't get a label"

    def test_malformed_topics_doesnt_crash(self, isolated_home):
        (isolated_home / "memories" / "topics.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        from trinity_local.launchpad_data import _topology_basin_labels
        # Must return empty, not raise — the launchpad render must not
        # die when a memory file is mid-write or hand-edited.
        assert _topology_basin_labels() == {}

    def test_basinHoverLabel_threads_through_page_data(self, isolated_home):
        """The Vue method basinHoverLabel reads from
        pageData.topologyBasinLabels. Guard the data-key contract
        between Python and Vue."""
        from trinity_local.launchpad_template import render_launchpad_html
        page_data = _minimal_page_data_with_lens(basins_spanned=["b03"])
        page_data["topologyBasinLabels"] = {"b03": "forecast · signal"}
        html = render_launchpad_html(page_data=page_data)
        # The method definition must reference the data field by that
        # exact name.
        assert "topologyBasinLabels: pageData.topologyBasinLabels" in html, (
            "pageData.topologyBasinLabels not threaded into Vue data"
        )
        assert "basinHoverLabel(bid)" in html, "lens chip doesn't call basinHoverLabel"
        assert "basinHoverLabel(r.topology_basin)" in html, (
            "cortex chip doesn't call basinHoverLabel"
        )


class TestLensBasinChipTemplate:
    """The Vue template must render basins_spanned as deep-link chips
    when present. v-if guards the row so lenses without basins_spanned
    (legacy data) don't render an empty 'Spans' label."""

    def test_template_renders_basins_spanned_row(self, isolated_home):
        from trinity_local.launchpad_template import render_launchpad_html
        # Minimal page_data with a paired lens that has basins_spanned.
        page_data = _minimal_page_data_with_lens(basins_spanned=["b03", "b05", "b12"])
        html = render_launchpad_html(page_data=page_data)
        # Vue v-for binds in templates render at runtime — we can only
        # assert the template carries the right loop bindings + URL
        # contract. The runtime side is the browser smoke (Surface 27).
        assert "p.basins_spanned" in html, (
            "template doesn't read p.basins_spanned — chips won't render"
        )
        assert "lens-basin-chip" in html, "lens-basin-chip class missing"
        assert "memory.html?file=topics.json&basin=" in html, (
            "template doesn't construct topics.json ?basin= deep-link — "
            "chips will land on the topology page with no basin focused"
        )
        assert "encodeURIComponent(bid)" in html, (
            "basin id not url-encoded — special chars in basin ids would break the link"
        )

    def test_template_v_if_guards_empty_basins(self, isolated_home):
        from trinity_local.launchpad_template import render_launchpad_html
        page_data = _minimal_page_data_with_lens(basins_spanned=[])
        html = render_launchpad_html(page_data=page_data)
        # The v-if must check both presence AND length so an empty
        # array doesn't render an orphan "Spans" label.
        assert "p.basins_spanned && p.basins_spanned.length" in html, (
            "v-if guard doesn't check .length — empty basins_spanned "
            "will render an orphan 'Spans' label with no chips"
        )


def _minimal_page_data_with_lens(*, basins_spanned: list[str]) -> dict:
    """page_data shape that build_page_data emits, trimmed to what the
    template's lens card branch consumes."""
    return {
        "enabled": False,
        "endpoint": "",
        "anonymous_id": "",
        "autoChainEnabled": False,
        "polishAutoIterate": False,
        "personalRouting": {},
        "globalRouting": {},
        "tasteLenses": {
            "paired_lenses": [{
                "pole_a": "leading proxy",
                "pole_b": "lagging metric",
                "failure_a": "paranoid",
                "failure_b": "consensus follower",
                "basins_spanned": basins_spanned,
                "tension_decisions": [],
                "dual_evidence": {"pole_a": [], "pole_b": []},
                "verdict": "accepted",
            }],
            "orderings": [],
            "rejections": [],
            "vocabulary": [],
            "abstract_lenses": [],
            "rejections_share_text": "",
            "vocabulary_share_text": "",
            "abstract_lenses_share_text": "",
            "combined_share_text": "",
        },
        "cortexRules": None,
        "councilQuerySuggestions": [],
        "providerHealth": {"providers": []},
        "activeOperation": None,
        "memoryHealth": {"issues": [], "ok_count": 4, "total_count": 4},
        "memoryHealthDigest": "",
        "ratingsHistory": {"labels": [], "datasets": []},
        "personalRoutingEmptyState": {},
        "modelLineup": [],
        "providerNames": {},
        "providerLabels": {},
    }
