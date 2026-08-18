"""res_024 — a tension's basins must resolve against basins that still EXIST.

The mega-basin splitter replaces `b04` with `b04a`/`b04b` in topics.json
(`me/basin_split.py:560`, default ON since af073886) and nothing migrates
`lens_registry.json`. Measured on the live store 2026-08-14: **273 of 526**
tension->basin references (52%) named a basin that no longer existed, and
**49 of 167** tensions (29%) had no live basin at all.

It was invisible because nothing in production reads `basins_spanned` —
council_runtime injects a global slice and never joins to basins. A field
written and never read cannot show a symptom, which is the
producer-asserted / consumer-unverified shape with the roles reversed: the
producer was fine, and there was no consumer to catch it drifting.

These guards pin the resolver's behaviour, especially the two ways it could be
silently wrong: under-matching (a split parent resolving to nothing, which is
the bug) and over-matching (`b0` sweeping up `b01`, which would be worse
because it fabricates edges rather than losing them).
"""
from __future__ import annotations

from trinity_local.me.lens_registry import resolve_spanned_basins


class TestSplitParentsResolveToTheirChildren:
    def test_split_parent_resolves_to_children(self):
        """The defect itself: b04 was split into b04a/b04b and went dead."""
        live = {"b04a", "b04b", "b14"}
        assert resolve_spanned_basins(["b04"], live) == {"b04a", "b04b"}

    def test_unsplit_basin_passes_through(self):
        live = {"b04a", "b14"}
        assert resolve_spanned_basins(["b14"], live) == {"b14"}

    def test_a_genuinely_dead_basin_stays_dead(self):
        """Resolution must not invent an edge. A basin that was deleted rather
        than split has no children and must resolve to nothing."""
        assert resolve_spanned_basins(["b99"], {"b04a", "b14"}) == set()

    def test_prefix_match_must_be_a_split_child_not_any_string_prefix(self):
        """The dangerous direction. `b0` is a prefix of `b01` as a STRING but is
        not its parent — the splitter's children extend the parent with LETTERS.
        Over-matching would fabricate edges, which is worse than the bug being
        fixed, because a lost edge is visible as an orphan and a fabricated one
        is not."""
        assert resolve_spanned_basins(["b0"], {"b01", "b02", "b03"}) == set()

    def test_mixed_list_resolves_each_element_independently(self):
        live = {"b00a", "b00b", "b14", "b21"}
        assert resolve_spanned_basins(["b00", "b14", "b99"], live) == {"b00a", "b00b", "b14"}

    def test_empty_and_none_are_safe(self):
        assert resolve_spanned_basins([], {"b01"}) == set()
        assert resolve_spanned_basins(None, {"b01"}) == set()  # type: ignore[arg-type]


class TestTheResolverActuallyRepairsTheMeasuredDefect:
    def test_orphan_rate_drops_on_the_shape_that_was_measured(self):
        """A regression test built from the real numbers, using a synthetic
        store shaped like the live one: split children in topics.json, pre-split
        parents in the registry.

        Mutation-proof: delete the prefix branch in `resolve_spanned_basins` and
        `after` falls back to `before`, which this asserts against.
        """
        live = {"b00a", "b00b", "b01a", "b01b", "b04a", "b04b", "b14", "b17"}
        spanned = [["b00", "b01"], ["b04"], ["b14"], ["b00", "b99"], ["b02"]]

        before = sum(1 for s in spanned if not [b for b in s if b in live])
        after = sum(1 for s in spanned if not resolve_spanned_basins(s, live))

        assert before == 4, f"fixture no longer reproduces the defect: {before} orphaned"
        assert after == 1, f"resolver should leave only the genuinely-dead one: {after}"
        assert after < before, "the resolver must strictly reduce orphans, or it does nothing"
