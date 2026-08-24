"""A degraded council must not teach routing.

Founder feedback doc v3 (2026-08-24), and confirmed on this repo's own data
first: council_2797722d0cf6e1e3 lost two of three members to a timeout, the
sole survivor was named winner, and the outcome carried a routing_lesson. A
verdict selected by latency is a walkover, not a comparison — and until this
guard, aggregate_routing_table counted it identically to a full contest because
nothing downstream ever read metadata.failed_members.

The trust ledger was already safe (it requires both claim sides occupied by
distinct labs). The routing table was not. This closes the gap and keeps the
exclusion DISCLOSED via councils_skipped_degraded rather than silent.
"""
from __future__ import annotations

from trinity_local.personal_routing import aggregate_routing_table


def _council(winner="claude", failed=None):
    c = {
        "task_type": "coding",
        "chairman_winner": winner,
        "routing_label": {"task_type": "coding", "winner": winner,
                          "provider_scores": {winner: {"overall": 8}}},
    }
    if failed is not None:
        c["metadata"] = {"failed_members": failed}
    return c


class TestDegradedExclusion:
    def test_a_degraded_council_is_excluded(self):
        t = aggregate_routing_table([_council(failed=["claude", "codex"])])
        assert t["councils_aggregated"] == 0
        assert t["councils_skipped_degraded"] == 1
        assert not t["by_task_type"], "a walkover taught the routing table"

    def test_a_full_council_still_counts(self):
        t = aggregate_routing_table([_council(), _council(winner="codex")])
        assert t["councils_aggregated"] == 2
        assert t["councils_skipped_degraded"] == 0
        assert t["by_task_type"]

    def test_an_empty_failed_list_is_not_degraded(self):
        """failed_members: [] means everyone answered — must not exclude."""
        t = aggregate_routing_table([_council(failed=[])])
        assert t["councils_aggregated"] == 1

    def test_corrupt_metadata_does_not_crash_or_exclude(self):
        c = _council()
        c["metadata"] = "not a dict"
        t = aggregate_routing_table([c])
        assert t["councils_aggregated"] == 1

    def test_the_mix_is_disclosed(self):
        t = aggregate_routing_table([
            _council(), _council(failed=["antigravity"]), _council(winner="codex")])
        assert t["councils_aggregated"] == 2
        assert t["councils_skipped_degraded"] == 1
