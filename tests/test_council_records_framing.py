"""A council records the SHAPE of the prompt its members answered.

Registered as §2 of the compression-turn plan ("framing in the ledger key") and
unbuilt until 2026-09-03. The plan's own boundary is kept: RECORDING framing is
instrumentation and safe, SELECTING framing from outcomes is a policy that
stays behind the airgap. Nothing here reads framing back into a decision.

Why it was worth building now. Measured across 10,375 bundles on disk, framing
already varies three ways (74.3% goal+context+instructions, 20.6%
goal+instructions, 5.0% goal only). That is the opposite of effort, whose
rotation was never switched on so no model ever had a second level to compare
against. The contrast existed the whole time and was simply never recorded.

The load-bearing test here is the DRIFT one: the label is built from the same
conditionals the renderer uses, so if a section is added to one and not the
other, a run gets mislabelled silently. That test reads the rendered prompt and
holds the label to it.
"""
from __future__ import annotations

import pytest

from trinity_local.council_runtime import (
    _FRAMING_SECTIONS,
    member_prompt_framing,
    render_member_prompt,
)
from trinity_local.council_schema import PromptBundle


def _bundle(**kw) -> PromptBundle:
    base = dict(bundle_id="b1", task_cluster_id="c1", task_text="compare two options")
    base.update(kw)
    return PromptBundle(**base)


class TestTheLabel:
    def test_bare_task_is_named_not_empty(self):
        assert member_prompt_framing(_bundle()) == "task_only", (
            "an empty label would read as 'framing not recorded' rather than "
            "'this council had no framing'"
        )

    def test_each_section_appears_in_render_order(self):
        assert member_prompt_framing(_bundle(goal="pick one")) == "goal"
        assert member_prompt_framing(
            _bundle(goal="g", comparison_instructions="i")
        ) == "goal+comparison_instructions"
        assert member_prompt_framing(
            _bundle(goal="g", context_excerpt="c", comparison_instructions="i")
        ) == "goal+context_excerpt+comparison_instructions"

    def test_the_three_measured_shapes_are_all_distinguishable(self):
        shapes = {
            member_prompt_framing(_bundle(goal="g", context_excerpt="c", comparison_instructions="i")),
            member_prompt_framing(_bundle(goal="g", comparison_instructions="i")),
            member_prompt_framing(_bundle(goal="g")),
        }
        assert len(shapes) == 3, (
            "the corpus holds these three at 74.3/20.6/5.0 percent; collapsing any "
            "two would erase the contrast this field exists to record"
        )


class TestItCannotDriftFromTheRenderer:
    """The whole risk: a section added to the renderer but not the label."""

    @pytest.mark.parametrize("kw", [
        {},
        {"goal": "pick one"},
        {"goal": "g", "context_excerpt": "c"},
        {"goal": "g", "comparison_instructions": "i"},
        {"goal": "g", "context_excerpt": "c", "comparison_instructions": "i"},
    ])
    def test_the_label_matches_what_was_actually_rendered(self, kw):
        b = _bundle(**kw)
        rendered = render_member_prompt(b)
        label = member_prompt_framing(b)
        HEADINGS = {"goal": "Goal:", "context_excerpt": "Context:",
                    "comparison_instructions": "Instructions:"}
        for name, heading in HEADINGS.items():
            in_prompt = heading in rendered
            in_label = name in label.split("+")
            assert in_prompt == in_label, (
                f"{name}: rendered={in_prompt} but label={in_label}. The label is "
                f"built from the same conditionals as the renderer; they have "
                f"drifted, so councils are being recorded with the wrong framing."
            )

    def test_the_section_list_covers_every_optional_field_the_renderer_uses(self):
        b = _bundle(goal="g", context_excerpt="c", comparison_instructions="i")
        rendered = render_member_prompt(b)
        for heading in ("Goal:", "Context:", "Instructions:"):
            assert heading in rendered
        assert set(_FRAMING_SECTIONS) == {"goal", "context_excerpt", "comparison_instructions"}, (
            "a new optional section was added to the renderer without extending "
            "_FRAMING_SECTIONS, so every council carrying it is mislabelled"
        )
