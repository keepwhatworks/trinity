"""launchpad lens card: accumulation chip surfaces support + stability (#200).

Completes the RENDER verb across surfaces — the memory viewer's lens.md
already shows "Supported by N decisions · stable since <date>" (#198);
this is the same durability signal on the launchpad lens card. Two
layers under test:

- Template: the support chip binding renders count + low-confidence
  branch (n<LOW_CONFIDENCE_BELOW), same confidence-honesty pattern as
  the n= axis labels.
- Data: _load_taste_lenses enriches each paired lens with registry
  support matched by (pole_a, pole_b), graceful when the registry is
  empty.
"""
from __future__ import annotations

import pytest


def _render(paired_lenses):
    from trinity_local.launchpad_template import render_launchpad_html
    return render_launchpad_html(
        page_data={"tasteLenses": {"paired_lenses": paired_lenses}},
    )


class TestSupportChipTemplate:
    def test_support_count_binding_present(self):
        html = _render([{
            "pole_a": "speed", "pole_b": "rigor", "verdict": "accepted",
            "support_count": 9, "first_seen": "2026-05-01T00:00:00+00:00",
            "low_confidence": False,
        }])
        # The chip only renders when support_count is set, and shows the count.
        assert "p.support_count" in html
        assert "decisions" in html

    def test_low_confidence_branch_in_template(self):
        html = _render([{
            "pole_a": "a", "pole_b": "b", "verdict": "accepted",
            "support_count": 1, "low_confidence": True,
        }])
        # The amber low-confidence branch + caveat copy is in the binding.
        assert "p.low_confidence" in html
        assert "low confidence" in html

    def test_chip_carries_stability_tooltip(self):
        html = _render([{
            "pole_a": "a", "pole_b": "b", "verdict": "accepted",
            "support_count": 6, "first_seen": "2026-05-10T00:00:00+00:00",
            "low_confidence": False,
        }])
        assert "stable since" in html
        assert "distinct decision" in html


@pytest.mark.usefixtures("patch_trinity_home")
class TestLoadTasteLensesEnrichment:
    def _seed(self):
        from trinity_local.me.pair_mining import save_lenses
        from trinity_local.me.lens_registry import reconcile
        from trinity_local.me.pair_mining import LensPair
        accepted = [
            LensPair(pole_a="speed", pole_b="rigor", failure_a="sloppy", failure_b="slow",
                     tension_decisions=["d1", "d2", "d3"], basins_spanned=["b0"], verdict="accepted"),
        ]
        save_lenses(accepted, [])
        reconcile(accepted)

    def test_paired_lens_carries_registry_support(self):
        from trinity_local.launchpad_data import _load_taste_lenses
        self._seed()
        data = _load_taste_lenses()
        assert data is not None
        pl = data["paired_lenses"][0]
        assert pl["support_count"] == 3
        assert "first_seen" in pl and "last_confirmed" in pl
        assert pl["low_confidence"] is False  # 3 >= LOW_CONFIDENCE_BELOW

    def test_low_confidence_flag_set_when_thin(self):
        from trinity_local.launchpad_data import _load_taste_lenses
        from trinity_local.me.pair_mining import LensPair, save_lenses
        from trinity_local.me.lens_registry import reconcile
        accepted = [LensPair(pole_a="x", pole_b="y", failure_a="", failure_b="",
                             tension_decisions=["d1"], verdict="accepted")]
        save_lenses(accepted, [])
        reconcile(accepted)
        pl = _load_taste_lenses()["paired_lenses"][0]
        assert pl["support_count"] == 1
        assert pl["low_confidence"] is True

    def test_no_registry_still_renders_card_without_support(self):
        # lenses.json present but registry never reconciled → card renders,
        # support keys simply absent (graceful).
        from trinity_local.launchpad_data import _load_taste_lenses
        from trinity_local.me.pair_mining import LensPair, save_lenses
        save_lenses([LensPair(pole_a="p", pole_b="q", failure_a="", failure_b="", verdict="accepted")], [])
        pl = _load_taste_lenses()["paired_lenses"][0]
        assert "support_count" not in pl
