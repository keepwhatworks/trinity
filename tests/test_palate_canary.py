"""The collapse detector halts unless it can prove the palate is still alive.

P4 of the compression plan, shape fixed by council_6ac26dafe733d16a. If a
feedback edge is ever wired into lens construction, the prospective palate is
the one instrument independent enough to notice collapse — provided its
pre-edge record is never pooled with post-edge trials (amd_0183) and the loop
cannot reach its labels (amd_0185).

The bar is frozen NOW, while no edge exists and nothing optimises against it:
0.773, the Wilson lower bound of the pre-edge record (289/354). A detector
built after the edge would be tuned on the data it must judge.

The property these tests pin: every state that is not a demonstrated "alive"
HALTS. No boundary halts. Too few trials halts. A corrupt boundary halts. Only
a post-edge cohort that clears the frozen floor on enough trials lets a loop
write.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.me import palate_canary as pc


def _trial(verdict, at="2026-09-01T00:00:00+00:00"):
    return json.dumps({"act_id": "r_x", "verdict": verdict, "gap": 0.1,
                       "scored_at": at, "snapshot_built_at": at})


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "me").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestEverythingThatIsNotAliveHalts:
    def test_no_boundary_halts(self, home):
        v = pc.canary_verdict()
        assert v.status == "no-boundary" and v.halted

    def test_too_few_post_edge_trials_halts(self, home):
        pc.stamp_edge_boundary(edge="test", at="2026-08-01T00:00:00+00:00")
        (home / "me" / "palate_trials.jsonl").write_text(
            "\n".join(_trial("correct") for _ in range(10)))
        v = pc.canary_verdict()
        assert v.status == "insufficient" and v.halted
        assert v.decided == 10

    def test_a_corrupt_boundary_halts_rather_than_reading_as_absent(self, home):
        (home / "me" / "edge_boundary.json").write_text('"not an object"')
        v = pc.canary_verdict()
        assert v.halted, "an unreadable boundary must not license writes"

    def test_a_healthy_cohort_is_alive(self, home):
        pc.stamp_edge_boundary(edge="test", at="2026-08-01T00:00:00+00:00")
        rows = [_trial("correct") for _ in range(90)] + [_trial("incorrect") for _ in range(10)]
        (home / "me" / "palate_trials.jsonl").write_text("\n".join(rows))
        v = pc.canary_verdict()
        assert v.status == "alive" and not v.halted
        assert v.wilson_lo > pc.PRE_EDGE_WILSON_LO

    def test_a_degraded_cohort_collapses(self, home):
        pc.stamp_edge_boundary(edge="test", at="2026-08-01T00:00:00+00:00")
        rows = [_trial("correct") for _ in range(60)] + [_trial("incorrect") for _ in range(40)]
        (home / "me" / "palate_trials.jsonl").write_text("\n".join(rows))
        v = pc.canary_verdict()
        assert v.status == "collapsed" and v.halted
        assert v.wilson_lo < pc.PRE_EDGE_WILSON_LO


class TestTheEpochBoundaryIsIrrevocable:
    def test_pre_edge_trials_are_never_pooled(self, home):
        """amd_0183: the 81.6% record may not mix with adaptive-lens trials."""
        pc.stamp_edge_boundary(edge="test", at="2026-08-01T00:00:00+00:00")
        rows = ([_trial("incorrect", "2026-07-01T00:00:00+00:00") for _ in range(500)]
                + [_trial("correct") for _ in range(90)]
                + [_trial("incorrect") for _ in range(10)])
        (home / "me" / "palate_trials.jsonl").write_text("\n".join(rows))
        v = pc.canary_verdict()
        assert v.decided == 100, "pre-boundary trials leaked into the post-edge cohort"
        assert v.status == "alive"

    def test_a_boundary_cannot_be_moved_after_the_fact(self, home):
        pc.stamp_edge_boundary(edge="first", at="2026-08-01T00:00:00+00:00")
        with pytest.raises(ValueError, match="already stamped"):
            pc.stamp_edge_boundary(edge="second", at="2026-09-01T00:00:00+00:00")


class TestTheFloorIsFrozen:
    def test_the_bar_is_the_pre_edge_wilson_lower_bound(self):
        # 289/354 = 81.6%, Wilson [0.773, 0.853]. Frozen before any edge exists.
        assert pc.PRE_EDGE_WILSON_LO == 0.773
        assert abs(pc._wilson_lo(289, 354) - 0.773) < 0.002, (
            "the frozen floor no longer matches the pre-edge record it was derived "
            "from — re-deriving it after an edge exists is tuning on the data")
