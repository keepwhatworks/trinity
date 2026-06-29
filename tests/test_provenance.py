"""#262 — provenance: typed (the user's voice) vs. pasted external content.

Structural detector + its use as a substance discount in the thread-signal
score, so a paste-heavy turn doesn't read as the user's authored taste.
"""
from __future__ import annotations

from trinity_local.me.provenance import (
    is_mostly_pasted,
    pasted_fraction,
    typed_substance,
)

_CODE = "fix this:\n```python\ndef f(x):\n    return x * 2\nfor i in range(10):\n    print(f(i))\n```"
_SPEC = (
    "# Housing Act 2026\n\n## Summary\n- Provision A: zoning reform\n"
    "- Provision B: density bonuses\n- Provision C: parking minimums removed\n"
    "> The bill passed committee 12-3."
)
_TYPED = "what should i use gemini 3.5 flash vs opus 3.8? give me a quick take."


class TestPastedFraction:
    def test_typed_question_is_not_pasted(self):
        assert pasted_fraction(_TYPED) == 0.0

    def test_code_block_is_mostly_pasted(self):
        assert pasted_fraction(_CODE) > 0.6

    def test_markdown_spec_is_fully_pasted(self):
        assert pasted_fraction(_SPEC) >= 0.9

    def test_empty_is_zero(self):
        assert pasted_fraction("") == 0.0
        assert pasted_fraction("   \n  ") == 0.0


class TestTypedSubstance:
    def test_typed_keeps_full_length(self):
        assert typed_substance(_TYPED) == len(_TYPED.strip())

    def test_pasted_spec_discounted_to_near_zero(self):
        assert typed_substance(_SPEC) < 0.2 * len(_SPEC)

    def test_question_with_small_paste_keeps_most(self):
        t = "remove selenium, use stagehand? it added: browser.act() API"
        assert typed_substance(t) >= 0.8 * len(t)


class TestMostlyPasted:
    def test_short_paste_not_flagged(self):
        # A short snippet is never "mostly pasted" — too small to be a wall.
        assert is_mostly_pasted("```\nx=1\n```") is False

    def test_long_pasted_wall_flagged(self):
        wall = "\n".join(f"- bullet item number {i} with some content" for i in range(40))
        assert is_mostly_pasted(wall) is True

    def test_long_typed_prose_not_flagged(self):
        prose = "I have been thinking about this problem for a while and " * 20
        assert is_mostly_pasted(prose) is False


class TestThreadSignalUsesProvenance:
    def test_paste_heavy_thread_scores_below_typed_thread(self):
        from trinity_local.me.thread_signal import score_thread

        typed = ["a genuine substantive typed question about strategy " * 30 for _ in range(6)]
        pasted = [_SPEC * 10 for _ in range(6)]
        assert score_thread(pasted) < score_thread(typed)
